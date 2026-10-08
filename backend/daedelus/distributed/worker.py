"""Worker process: ``daedelus worker --control URL`` (token in DAEDELUS_WORKER_TOKEN).

Loop: register -> long-poll for a lease -> fetch the job's blobs (verified by hash, cached)
-> run the adapter method in a child process -> heartbeat while it runs (extends the lease,
receives cancellation) -> upload new blobs -> complete. The child is killed on cancellation,
deadline or lease loss. The worker never executes anything except the fixed adapter methods.

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

from .. import __version__
from .cas import BlobStore, materialize, snapshot
from .models import MUTATING, JobResult, Lease


class Worker:
    def __init__(self, control: str, token: str, *, name: str | None = None,
                 capabilities: list[str] | None = None, adapters: list[str] | None = None,
                 work_dir: Path | None = None, transport: httpx.BaseTransport | None = None):
        self.control = control.rstrip("/")
        self.name = name or f"{socket.gethostname()}-{os.getpid()}"
        self.capabilities = capabilities or ["cpu"]
        if adapters is None:
            from ..adapters import registry
            adapters = [n for n, a in registry().items() if a.check_environment()[0]]
        self.adapters = adapters
        self.work = Path(work_dir or tempfile.mkdtemp(prefix="dd_worker_"))
        self.cache = BlobStore(self.work / "cache")
        self.http = httpx.Client(base_url=self.control, transport=transport, timeout=120,
                                 headers={"Authorization": f"Bearer {token}"})
        self.id: str | None = None
        self.faults = {f.split(":")[0]: (f.split(":")[1] if ":" in f else "")
                       for f in os.environ.get("DAEDELUS_WORKER_FAULT", "").split(",") if f}
        self._stop = threading.Event()
        self.done = 0

    # ---------------------------------------------------------------- protocol
    def register(self) -> str:
        r = self.http.post("/api/cluster/workers", json={
            "name": self.name, "capabilities": self.capabilities, "adapters": self.adapters,
            "version": __version__, "host": socket.gethostname()})
        r.raise_for_status()
        self.id = r.json()["id"]
        return self.id

    def stop(self) -> None:
        self._stop.set()

    def run(self, *, max_jobs: int | None = None, idle_exit: float | None = None) -> None:
        if self.id is None:
            self.register()
        idle_since = time.time()
        while not self._stop.is_set():
            try:
                r = self.http.post("/api/cluster/lease", json={"worker_id": self.id, "wait_s": 10})
            except httpx.HTTPError:
                time.sleep(1)
                continue
            if r.status_code == 404:  # control plane forgot us (fresh database): re-register
                self.register()
                continue
            if r.status_code == 204:
                if idle_exit and time.time() - idle_since > idle_exit:
                    return
                continue
            r.raise_for_status()
            self.handle(Lease.model_validate(r.json()))
            self.done += 1
            idle_since = time.time()
            if max_jobs and self.done >= max_jobs:
                return

    def _h(self, lease: Lease) -> dict[str, str]:
        return {"X-Lease-Token": lease.lease_token}

    def _fetch(self, lease: Lease, sha: str) -> None:
        if self.cache.has(sha):
            return
        r = self.http.get(f"/api/cluster/jobs/{lease.job.id}/blobs/{sha}", headers=self._h(lease))
        r.raise_for_status()
        self.cache.put_bytes(r.content, expected=sha)  # verifies the hash

    def _upload(self, lease: Lease, store: BlobStore, shas: set[str]) -> None:
        r = self.http.post(f"/api/cluster/jobs/{lease.job.id}/missing", json={"shas": sorted(shas)},
                           headers=self._h(lease))
        r.raise_for_status()
        for sha in r.json()["missing"]:
            data = store.read(sha)
            if "corrupt_upload" in self.faults:
                self.faults.pop("corrupt_upload")
                bad = self.http.put(f"/api/cluster/jobs/{lease.job.id}/blobs/{sha}",
                                    content=data + b"tamper", headers=self._h(lease))
                if bad.status_code != 422:
                    raise RuntimeError("control plane accepted a corrupted blob")
            u = self.http.put(f"/api/cluster/jobs/{lease.job.id}/blobs/{sha}", content=data,
                              headers=self._h(lease))
            u.raise_for_status()

    # ---------------------------------------------------------------- one job
    def handle(self, lease: Lease) -> None:
        job = lease.job
        if "crash_after_lease" in self.faults:
            os._exit(17)  # simulate a worker dying mid-job (no fail/complete is sent)
        jd = Path(tempfile.mkdtemp(prefix=f"{job.id}_", dir=self.work))
        t0 = time.time()
        state = {"cancel": False, "lost": False, "progress": 0.05, "message": "fetching inputs"}
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
                    if r.status_code == 409:
                        state["lost"] = True
                        return
                    if r.ok and r.json().get("cancel"):
                        state["cancel"] = True
                except httpx.HTTPError:
                    pass  # transient; the lease covers a few missed beats

        hb = threading.Thread(target=heartbeat, daemon=True)
        hb.start()
        try:
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
                (jd / "job.json").write_text(job.model_dump_json())
            except Exception as exc:
                self._fail(lease, f"input transfer failed: {type(exc).__name__}: {exc}", True)
                return
            state.update(progress=0.2, message=f"running {job.adapter}.{job.method}")
            proc = subprocess.Popen([sys.executable, "-m", "daedelus.distributed.runjob", str(jd)],
                                    start_new_session=True, stdout=subprocess.PIPE,
                                    stderr=subprocess.STDOUT)
            slow = float(self.faults.get("slow") or 0)
            deadline = t0 + job.timeout_s
            while proc.poll() is None or slow > time.time() - t0:
                if state["cancel"] or state["lost"] or time.time() > deadline:
                    self._kill(proc)
                    break
                time.sleep(0.1)
            logs = (proc.stdout.read() or b"").decode(errors="replace")[-8000:] if proc.stdout \
                else ""
            if state["lost"]:
                return  # lease revoked: another attempt owns the job now; drop our result
            if state["cancel"]:
                self._fail(lease, "cancelled on request", False, cancelled=True)
                return
            if time.time() > deadline:
                self._fail(lease, f"timed out after {job.timeout_s} s on {self.name}", True)
                return
            rp = jd / "result.json"
            if not rp.exists():
                self._fail(lease, f"job runner exited with {proc.returncode} without a result; "
                                  f"{logs[-500:]}", True)
                return
            out = json.loads(rp.read_text())
            state.update(progress=0.9, message="uploading results")
            res = JobResult(ok=bool(out.get("ok")), value=out.get("value") or {},
                            error=out.get("error"), logs=logs, seconds=round(time.time() - t0, 3),
                            worker_id=self.id)
            if out.get("adapter_error"):
                res.value = {"adapter_error": True}
            if res.ok:
                store = BlobStore(self.work / "cache")
                if job.method in MUTATING:
                    res.output = snapshot(jd / "native", store)
                    self._upload(lease, store, res.output.blobs())
                if job.method in ("preview", "export"):
                    res.artifacts = snapshot(jd / "out", store)
                    self._upload(lease, store, res.artifacts.blobs())
            r = self.http.post(f"/api/cluster/jobs/{job.id}/complete", content=res.model_dump_json(),
                               headers={**self._h(lease), "Content-Type": "application/json"})
            if r.status_code == 409:
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
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass
        proc.wait(timeout=10)

    def _fail(self, lease: Lease, error: str, retryable: bool, *, cancelled: bool = False) -> None:
        try:
            self.http.post(f"/api/cluster/jobs/{lease.job.id}/fail", json={
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
    a = ap.parse_args(argv)
    token = os.environ.get("DAEDELUS_WORKER_TOKEN", "")
    if not token:
        print("DAEDELUS_WORKER_TOKEN is required", file=sys.stderr)
        return 2
    caps = [c.strip() for c in a.capabilities.split(",") if c.strip()]
    if "gpu" in caps and not os.environ.get("DAEDELUS_GPU_VERIFIED"):
        print("refusing to advertise 'gpu' without DAEDELUS_GPU_VERIFIED=1 (set it only on a "
              "host with a working GPU render device)", file=sys.stderr)
        return 2
    w = Worker(a.control, token, name=a.name, capabilities=caps,
               adapters=[x.strip() for x in a.adapters.split(",")] if a.adapters else None,
               work_dir=Path(a.work_dir) if a.work_dir else None)
    for i in range(60):
        try:
            w.register()
            break
        except httpx.HTTPError as exc:
            print(f"control plane not reachable yet ({exc}); retrying", file=sys.stderr)
            time.sleep(2)
    else:
        return 1
    print(json.dumps({"worker": w.id, "name": w.name, "capabilities": w.capabilities,
                      "adapters": w.adapters}), flush=True)
    signal.signal(signal.SIGTERM, lambda *_: w.stop())
    w.run(max_jobs=a.max_jobs)
    return 0
