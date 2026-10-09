"""Worker process: ``daedelus worker --control URL``.

Credentials (V2.1): ``DAEDELUS_WORKER_CREDENTIAL`` (a per-worker credential created by an
operator, ``ddw1.<id>.<secret>``; may also be read from ``DAEDELUS_WORKER_CREDENTIAL_FILE``)
or ``DAEDELUS_WORKER_TOKEN`` (a join token: the worker enrols and receives its own credential,
kept in memory). A worker whose credential is revoked stops.

Transport: the control URL must be ``https://`` unless it is a loopback address or
``DAEDELUS_ALLOW_INSECURE_CONTROL=1`` (development compose networks only; refused in the hosted
profile). Server certificates are always verified - against the system store, or the CA bundle
in ``DAEDELUS_CONTROL_CA`` for a private CA. Verification is never disabled.

Loop: register -> long-poll for a lease -> fetch the job's blobs (verified by hash, cached)
-> run the adapter method in a sandboxed child process (``sandbox.py``) -> heartbeat while it
runs (extends the lease, receives cancellation) -> upload new blobs -> complete. The child is
killed on cancellation, deadline or lease loss. The worker never executes anything except the
fixed adapter methods.

Fault injection for tests (DAEDELUS_WORKER_FAULT, comma-separated): ``crash_after_lease``,
``stall_heartbeat``, ``corrupt_upload``, ``duplicate_complete``, ``slow:<seconds>``.
"""

from __future__ import annotations

import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Any

import httpx

from .. import __version__, profile
from .cas import BlobStore, materialize, snapshot
from .identity import parse_credential
from .models import MUTATING, JobResult, Lease
from .sandbox import Sandbox, choose, required_adapters


def new_group_kwargs() -> dict[str, Any]:
    """Start a child in its own process group/session so the whole tree can be killed."""
    if os.name == "nt":
        return {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
    return {"start_new_session": True}


def kill_tree(proc: subprocess.Popen) -> None:
    """Kill a child and everything it started (Blender, LibreOffice...)."""
    if proc.poll() is None:
        if os.name == "nt":
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                           capture_output=True)
        else:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()


# answers from a proxy or load balancer while the control plane is restarting
TRANSIENT = {502, 503, 504}


class LeaseRevoked(Exception):
    pass


class CredentialRevoked(Exception):
    """The control plane no longer accepts this worker's credential."""


def control_client_kwargs(control: str) -> dict[str, Any]:
    """httpx settings for talking to the control plane: TLS verified, plain HTTP refused
    unless the URL is loopback or the development override is set."""
    from urllib.parse import urlparse
    import ipaddress
    import ssl
    u = urlparse(control)
    if u.scheme not in ("http", "https"):
        raise ValueError(f"control URL must be http(s): {control}")
    if u.scheme == "http":
        host = (u.hostname or "").strip("[]")
        try:
            loop = ipaddress.ip_address(host).is_loopback
        except ValueError:
            loop = host == "localhost"
        if not loop and (profile.hosted() or not profile.flag("DAEDELUS_ALLOW_INSECURE_CONTROL")):
            raise ValueError(f"refusing plain-HTTP control URL {control}: use https:// "
                             "(DAEDELUS_ALLOW_INSECURE_CONTROL=1 is accepted only outside the "
                             "hosted profile, for isolated development networks)")
        return {}
    ca = os.environ.get("DAEDELUS_CONTROL_CA")
    if ca:
        ctx = ssl.create_default_context(cafile=ca)
    else:
        ctx = ssl.create_default_context()
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    cert, key = os.environ.get("DAEDELUS_WORKER_TLS_CERT"), os.environ.get("DAEDELUS_WORKER_TLS_KEY")
    if cert:  # optional mutual TLS (client certificate checked by the proxy)
        ctx.load_cert_chain(cert, key or None)
    return {"verify": ctx}


class Worker:
    def __init__(self, control: str, token: str, *, name: str | None = None,
                 capabilities: list[str] | None = None, adapters: list[str] | None = None,
                 work_dir: Path | None = None, transport: httpx.BaseTransport | None = None,
                 sandbox: Sandbox | None = None, deployment: str = "",
                 credential_file: Path | None = None):
        self.control = control.rstrip("/")
        self.name = name or f"{socket.gethostname()}-{os.getpid()}"
        self.capabilities = capabilities or ["cpu"]
        if adapters is None:
            from ..adapters import registry
            adapters = [n for n, a in registry().items() if a.check_environment()[0]]
        self.work = Path(work_dir or tempfile.mkdtemp(prefix="dd_worker_"))
        self.work.mkdir(parents=True, exist_ok=True)
        self.work.chmod(0o700)
        self.cache = BlobStore(self.work / "cache")
        self.credential_file = credential_file
        self.sandbox = sandbox or choose(self.work, hide=[credential_file.parent]
                                         if credential_file else None)
        need = required_adapters()
        self.refused_adapters = sorted(a for a in adapters if a in need and
                                       not self.sandbox.network_isolated)
        self.adapters = [a for a in adapters if a not in self.refused_adapters]
        self.deployment = deployment or os.environ.get("DAEDELUS_WORKER_DEPLOYMENT", "process")
        kw = {} if transport is not None else control_client_kwargs(self.control)
        self.http = httpx.Client(base_url=self.control, transport=transport, timeout=120,
                                 headers={"Authorization": f"Bearer {token}"}, **kw)
        self.credential = token if parse_credential(token) else None
        self.id: str | None = None
        self.faults = {f.split(":")[0]: (f.split(":")[1] if ":" in f else "")
                       for f in os.environ.get("DAEDELUS_WORKER_FAULT", "").split(",") if f}
        self._stop = threading.Event()
        self.done = 0
        self.revoked = False
        self.stats = {"bytes_in": 0, "bytes_out": 0, "cache_hits": 0}

    # ---------------------------------------------------------------- protocol
    def _use_credential(self, cred: str) -> None:
        self.credential = cred
        self.http.headers["Authorization"] = f"Bearer {cred}"
        if self.credential_file is not None:
            tmp = self.credential_file.with_suffix(".tmp")
            tmp.write_text(cred, encoding="utf-8")
            tmp.chmod(0o600)
            os.replace(tmp, self.credential_file)

    def register(self) -> str:
        r = self.http.post("/api/cluster/workers", json={
            "name": self.name, "capabilities": self.capabilities, "adapters": self.adapters,
            "version": __version__, "host": socket.gethostname(),
            "isolation": self.sandbox.report, "deployment": self.deployment,
            "features": ["post_inspect", "batch_scope"]})
        if r.status_code == 401 and self.credential:
            raise CredentialRevoked(r.text)
        r.raise_for_status()
        body = r.json()
        if body.get("credential"):  # enrolled with a join token: switch to our own identity
            self._use_credential(body["credential"])
        self.id = body["id"]
        return self.id

    def rotate(self) -> None:
        """Replace this worker's secret (the old one stays valid for a short grace period)."""
        r = self.http.post("/api/cluster/workers/self/rotate")
        if r.status_code == 401:
            raise CredentialRevoked(r.text)
        r.raise_for_status()
        self._use_credential(r.json()["credential"])

    def stop(self) -> None:
        self._stop.set()

    def run(self, *, max_jobs: int | None = None, idle_exit: float | None = None) -> None:
        if self.id is None:
            self.register()
        idle_since = time.time()
        errors = 0
        while not self._stop.is_set():
            try:
                r = self.http.post("/api/cluster/lease", json={"worker_id": self.id, "wait_s": 10})
            except httpx.HTTPError:
                time.sleep(1)
                continue
            if r.status_code in TRANSIENT:  # e.g. a TLS proxy answering while the control
                time.sleep(2)               # plane restarts: wait, do not die
                continue
            if r.status_code >= 500:  # a server error on the poll (e.g. during shutdown):
                errors += 1           # back off and keep the worker alive
                print(f"lease poll failed with {r.status_code}; retrying", file=sys.stderr,
                      flush=True)
                self._stop.wait(min(30.0, 2.0 ** min(errors, 5)))
                continue
            errors = 0
            if r.status_code == 401:  # credential revoked (or rotated away): stop for good
                raise CredentialRevoked(r.text)
            if r.status_code == 404:  # control plane forgot us (fresh database): re-register
                self.register()
                continue
            if r.status_code == 204:
                if idle_exit and time.time() - idle_since > idle_exit:
                    return
                continue
            r.raise_for_status()
            try:
                self.handle(Lease.model_validate(r.json()))
            except Exception as exc:  # one bad job must not take the worker down
                print(f"job handling failed: {type(exc).__name__}: {exc}", file=sys.stderr,
                      flush=True)
            self.done += 1
            idle_since = time.time()
            if max_jobs and self.done >= max_jobs:
                return

    def _call(self, method: str, url: str, *, patience: float = 90.0, **kw) -> httpx.Response:
        """Control-plane call that rides out transient failures (partitions, control-plane
        restarts, 502/503/504 from a proxy in front of it). Other HTTP errors are returned to
        the caller, not retried."""
        t0 = time.time()
        delay = 1.0
        while True:
            try:
                r = self.http.request(method, url, **kw)
                if r.status_code not in TRANSIENT or time.time() - t0 > patience:
                    return r
            except httpx.TransportError:
                if time.time() - t0 > patience or self._stop.is_set():
                    raise
            if self._stop.is_set():
                raise httpx.TransportError("worker stopping")
            time.sleep(delay)
            delay = min(delay * 1.5, 5.0)

    def _h(self, lease: Lease) -> dict[str, str]:
        return {"X-Lease-Token": lease.lease_token}

    def _fetch(self, lease: Lease, sha: str) -> None:
        if self.cache.has(sha):
            self.stats["cache_hits"] += 1
            return
        r = self._call("GET", f"/api/cluster/jobs/{lease.job.id}/blobs/{sha}",
                       headers=self._h(lease))
        if r.status_code in (401, 403, 409):
            raise LeaseRevoked(r.text)
        r.raise_for_status()
        self.cache.put_bytes(r.content, expected=sha)  # verifies the hash
        self.stats["bytes_in"] += len(r.content)

    def _upload(self, lease: Lease, store: BlobStore, shas: set[str]) -> None:
        r = self._call("POST", f"/api/cluster/jobs/{lease.job.id}/missing",
                       json={"shas": sorted(shas)}, headers=self._h(lease))
        if r.status_code in (401, 403, 409):
            raise LeaseRevoked(r.text)
        r.raise_for_status()
        for sha in r.json()["missing"]:
            data = store.read(sha)
            if "corrupt_upload" in self.faults:
                self.faults.pop("corrupt_upload")
                bad = self.http.put(f"/api/cluster/jobs/{lease.job.id}/blobs/{sha}",
                                    content=data + b"tamper", headers=self._h(lease))
                if bad.status_code != 422:
                    raise RuntimeError("control plane accepted a corrupted blob")
            u = self._call("PUT", f"/api/cluster/jobs/{lease.job.id}/blobs/{sha}", content=data,
                           headers=self._h(lease))
            if u.status_code in (401, 403, 409):
                raise LeaseRevoked(u.text)
            u.raise_for_status()
            self.stats["bytes_out"] += len(data)

    # ---------------------------------------------------------------- one job
    def handle(self, lease: Lease) -> None:
        job = lease.job
        if "crash_after_lease" in self.faults:
            os._exit(17)  # simulate a worker dying mid-job (no fail/complete is sent)
        jd = Path(tempfile.mkdtemp(prefix=f"{job.id}_", dir=self.work))
        t0 = time.time()
        self.stats = {"bytes_in": 0, "bytes_out": 0, "cache_hits": 0}
        timings: dict[str, float] = {}
        state = {"cancel": False, "lost": False, "revoked": False, "progress": 0.05,
                 "message": "fetching inputs"}
        hb_stop = threading.Event()

        def heartbeat():
            period = max(1.0, (lease.lease_expires_at - time.time()) / 3)
            while not hb_stop.wait(period):
                if "stall_heartbeat" in self.faults:
                    continue
                try:
                    r = self.http.post(f"/api/cluster/jobs/{job.id}/heartbeat", json={
                        "progress": state["progress"], "message": state["message"]},
                        headers=self._h(lease))
                    if r.status_code in (401, 403, 409):  # revoked / not ours / lease lost
                        state["lost"] = True
                        state["revoked"] = r.status_code == 401
                        return
                    if r.ok and r.json().get("cancel"):
                        state["cancel"] = True
                except httpx.HTTPError:
                    pass  # transient; the lease covers a few missed beats

        hb = threading.Thread(target=heartbeat, daemon=True)
        hb.start()
        try:
            if job.adapter not in self.adapters:  # defence in depth: never run what we refused
                self._fail(lease, f"worker {self.name} does not offer adapter '{job.adapter}' "
                                  f"(refused: {self.refused_adapters})", False)
                return
            try:
                for name, m in (("native", job.native), ("before", job.before)):
                    if m is None:
                        continue
                    for sha in m.blobs():
                        self._fetch(lease, sha)
                    materialize(m, self.cache, jd / name)
                (jd / "files").mkdir()
                for i, k in enumerate(sorted(job.files)):
                    self._fetch(lease, job.files[k])
                    shutil.copyfile(self.cache.path(job.files[k]), jd / "files" / str(i))
                if not (jd / "native").exists():
                    (jd / "native").mkdir()
                (jd / "job.json").write_text(job.model_dump_json(), encoding="utf-8")
            except LeaseRevoked:
                return
            except Exception as exc:
                self._fail(lease, f"input transfer failed: {type(exc).__name__}: {exc}", True)
                return
            timings["fetch"] = round(time.time() - t0, 4)
            state.update(progress=0.2, message=f"running {job.adapter}.{job.method}")
            t_run = time.time()
            proc = self.sandbox.popen([sys.executable, "-m", "daedelus.distributed.runjob",
                                       str(jd)], jd, memory_mb=job.memory_mb,
                                      stdout=subprocess.PIPE,
                                      stderr=subprocess.STDOUT)
            slow = float(self.faults.get("slow") or 0)
            deadline = t0 + job.timeout_s
            while proc.poll() is None or slow > time.time() - t0:
                if state["cancel"] or state["lost"] or time.time() > deadline:
                    self._kill(proc)
                    break
                time.sleep(0.05)
            timings["run"] = round(time.time() - t_run, 4)
            logs = (proc.stdout.read() or b"").decode(errors="replace")[-8000:] if proc.stdout \
                else ""
            if state["lost"]:
                if state["revoked"]:
                    self.revoked = True
                    self._stop.set()  # our credential was revoked: stop taking work
                return  # lease revoked: another attempt owns the job now; drop our result
            if state["cancel"]:
                self._fail(lease, "cancelled on request", False, cancelled=True)
                return
            if time.time() > deadline:
                self._fail(lease, f"timed out after {job.timeout_s} s on {self.name}", True)
                return
            rp = jd / "result.json"
            if not rp.exists():
                rc = proc.returncode
                hint = " (killed: memory or process limit?)" if rc is not None and rc < 0 else ""
                self._fail(lease, f"job runner exited with {rc}{hint} without a result; "
                                  f"{logs[-500:]}", True)
                return
            out = json.loads(rp.read_text(encoding="utf-8"))
            state.update(progress=0.9, message="uploading results")
            t_up = time.time()
            res = JobResult(ok=bool(out.get("ok")), value=out.get("value") or {},
                            error=out.get("error"), logs=logs, worker_id=self.id)
            if out.get("adapter_error"):
                res.value = {"adapter_error": True}
            try:
                if res.ok:
                    store = BlobStore(self.work / "cache")
                    if job.method in MUTATING:
                        res.output = snapshot(jd / "native", store)
                        self._upload(lease, store, res.output.blobs())
                    if job.method in ("preview", "export"):
                        res.artifacts = snapshot(jd / "out", store)
                        self._upload(lease, store, res.artifacts.blobs())
            except LeaseRevoked:
                return  # another attempt owns the job now; our result is not wanted
            timings["upload"] = round(time.time() - t_up, 4)
            if isinstance(out.get("timings"), dict):
                timings.update({f"runner.{k}": v for k, v in out["timings"].items()})
            res.seconds = round(time.time() - t0, 3)
            res.timings = timings
            res.bytes_in, res.bytes_out = self.stats["bytes_in"], self.stats["bytes_out"]
            res.isolation = self.sandbox.profile
            r = self._call("POST", f"/api/cluster/jobs/{job.id}/complete",
                           content=res.model_dump_json(),
                           headers={**self._h(lease), "Content-Type": "application/json"})
            if r.status_code in (401, 403, 409):
                if r.status_code == 401:
                    self.revoked = True
                    self._stop.set()
                return  # lease lost meanwhile: result discarded by the control plane (audited)
            r.raise_for_status()
            if "duplicate_complete" in self.faults:
                self.http.post(f"/api/cluster/jobs/{job.id}/complete",
                               content=res.model_dump_json(),
                               headers={**self._h(lease), "Content-Type": "application/json"})
        finally:
            hb_stop.set()
            shutil.rmtree(jd, ignore_errors=True)

    def _kill(self, proc: subprocess.Popen) -> None:
        kill_tree(proc)

    def _fail(self, lease: Lease, error: str, retryable: bool, *, cancelled: bool = False) -> None:
        try:
            self._call("POST", f"/api/cluster/jobs/{lease.job.id}/fail", json={
                "error": error, "retryable": retryable, "cancelled": cancelled},
                headers=self._h(lease))
        except httpx.HTTPError:
            pass  # the lease will expire and the reaper will requeue/fail the job


def main(argv: list[str] | None = None) -> int:
    import argparse

    ap = argparse.ArgumentParser(prog="daedelus worker")
    ap.add_argument("--control", default=os.environ.get("DAEDELUS_CONTROL_URL",
                                                        "http://127.0.0.1:8765"))
    ap.add_argument("--name", default=os.environ.get("DAEDELUS_WORKER_NAME"))
    ap.add_argument("--capabilities", default=os.environ.get("DAEDELUS_WORKER_CAPABILITIES", "cpu"))
    ap.add_argument("--adapters", default=os.environ.get("DAEDELUS_WORKER_ADAPTERS"))
    ap.add_argument("--work-dir", default=os.environ.get("DAEDELUS_WORKER_DIR"))
    ap.add_argument("--max-jobs", type=int, default=None)
    ap.add_argument("--rotate-hours", type=float,
                    default=float(os.environ.get("DAEDELUS_WORKER_ROTATE_HOURS", "0") or 0),
                    help="replace this worker's credential secret periodically")
    a = ap.parse_args(argv)
    cred_file = os.environ.get("DAEDELUS_WORKER_CREDENTIAL_FILE")
    token = os.environ.get("DAEDELUS_WORKER_CREDENTIAL", "")
    if not token and cred_file and Path(cred_file).is_file():
        token = Path(cred_file).read_text(encoding="utf-8").strip()
    if not token:
        token = os.environ.get("DAEDELUS_WORKER_TOKEN", "")
    if not token:
        print("a worker credential (DAEDELUS_WORKER_CREDENTIAL / _FILE) or a join token "
              "(DAEDELUS_WORKER_TOKEN) is required", file=sys.stderr)
        return 2
    if profile.hosted() and not parse_credential(token):
        print("the hosted profile requires a per-worker credential (join-token enrolment is "
              "for development)", file=sys.stderr)
        return 2
    caps = [c.strip() for c in a.capabilities.split(",") if c.strip()]
    if "gpu" in caps and not os.environ.get("DAEDELUS_GPU_VERIFIED"):
        print("refusing to advertise 'gpu' without DAEDELUS_GPU_VERIFIED=1 (set it only on a "
              "host with a working GPU render device)", file=sys.stderr)
        return 2
    try:
        w = Worker(a.control, token, name=a.name, capabilities=caps,
                   adapters=[x.strip() for x in a.adapters.split(",")] if a.adapters else None,
                   work_dir=Path(a.work_dir) if a.work_dir else None,
                   credential_file=Path(cred_file) if cred_file else None)
    except (ValueError, RuntimeError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    for i in range(60):
        try:
            w.register()
            break
        except CredentialRevoked as exc:
            print(f"worker credential rejected: {exc}", file=sys.stderr)
            return 3
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 403:  # refused by policy: retrying cannot help
                try:
                    why = exc.response.json().get("detail")
                except ValueError:
                    why = exc.response.text[:300]
                print(f"registration refused by the control plane: {why}", file=sys.stderr)
                return 3
            print(f"control plane not ready yet ({exc}); retrying", file=sys.stderr)
        except httpx.HTTPError as exc:
            print(f"control plane not reachable yet ({exc}); retrying", file=sys.stderr)
            time.sleep(2)
    else:
        return 1
    print(json.dumps({"worker": w.id, "name": w.name, "capabilities": w.capabilities,
                      "adapters": w.adapters, "refused_adapters": w.refused_adapters,
                      "isolation": w.sandbox.report.get("profile"),
                      "isolation_controls": w.sandbox.report.get("controls")}), flush=True)
    signal.signal(signal.SIGTERM, lambda *_: w.stop())
    if a.rotate_hours > 0:
        def rotator():
            while not w._stop.wait(a.rotate_hours * 3600):
                try:
                    w.rotate()
                except Exception as exc:  # keep working with the current secret
                    print(f"credential rotation failed: {exc}", file=sys.stderr, flush=True)
        threading.Thread(target=rotator, daemon=True).start()
    try:
        w.run(max_jobs=a.max_jobs)
    except CredentialRevoked as exc:
        print(f"worker credential revoked; stopping: {exc}", file=sys.stderr, flush=True)
        return 3
    if w.revoked:
        print("worker credential revoked; stopping", file=sys.stderr, flush=True)
        return 3
    return 0
