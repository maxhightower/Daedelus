"""Control-plane cluster service: durable queue, blob storage, worker auth, audit, reaper.

One instance per control plane (``configure()``), stored under ``<workspace>/cluster``.
Worker endpoints are disabled until a worker credential exists (``daedelus cluster credential
create``) or ``DAEDELUS_WORKER_TOKENS`` lists join tokens - a fresh installation exposes no
worker surface. Join tokens only *enrol* a worker, which then receives its own revocable
credential (see ``identity.py``); the hosted profile refuses enrolment unless
``DAEDELUS_ALLOW_WORKER_ENROLLMENT=1``.
"""

from __future__ import annotations

import hmac
import json
import os
import threading
import time
from pathlib import Path
from typing import Any, Callable

from .. import profile
from .cas import BlobStore
from .identity import CredentialStore, WorkerIdentity
from .models import TERMINAL, JobRequest, JobResult, JobStatus, WorkerInfo
from .queue import JobQueue

ALIVE_S = 45.0  # a worker that has not polled/heartbeated for this long is considered gone


class JobFailed(Exception):
    def __init__(self, status: JobStatus, result: JobResult | None = None):
        super().__init__(f"job {status.id} {status.state}: {status.error or ''}".strip())
        self.status = status
        self.result = result


class Audit:
    """Append-only JSONL audit log (fsync per record)."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def record(self, action: str, outcome: str = "ok", *, actor: str = "control-plane",
               target: str = "", **detail: Any) -> None:
        rec = {"ts": time.time(), "actor": actor, "action": action, "target": target,
               "outcome": outcome, **({"detail": detail} if detail else {})}
        line = json.dumps(rec, default=str)
        with self._lock, open(self.path, "a", encoding="utf-8") as f:
            f.write(line + "\n")
            f.flush()
            os.fsync(f.fileno())

    def tail(self, n: int = 200) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        lines = self.path.read_text(encoding="utf-8").splitlines()[-n:]
        return [json.loads(x) for x in lines if x.strip()]


class ClusterService:
    def __init__(self, root: Path, *, lease_s: float | None = None,
                 worker_tokens: list[str] | None = None, reap_interval: float = 2.0):
        self.root = Path(root)
        self.queue = JobQueue(self.root / "queue.db",
                              lease_s=lease_s or float(os.environ.get("DAEDELUS_LEASE_S", "30")))
        self.blobs = BlobStore(self.root / "blobs")
        self.audit = Audit(self.root / "audit.jsonl")
        toks = worker_tokens if worker_tokens is not None else [
            t.strip() for t in os.environ.get("DAEDELUS_WORKER_TOKENS", "").split(",") if t.strip()]
        self._worker_tokens = [t for t in toks if len(t) >= 16]  # join (enrolment) tokens
        self.creds = CredentialStore(self.queue)
        self._stop = threading.Event()
        # restart recovery: leases that expired while the control plane was down are re-queued
        for s in self.queue.reap():
            self.audit.record("reap_on_start", s.state, target=s.id, error=s.error)
        self._reaper = threading.Thread(target=self._reap_loop, args=(reap_interval,),
                                        daemon=True, name="cluster-reaper")
        self._reaper.start()

    # ---------------------------------------------------------------- lifecycle
    def close(self) -> None:
        self._stop.set()
        self._reaper.join(timeout=5)
        self.queue.close()

    def _reap_loop(self, interval: float) -> None:
        while not self._stop.wait(interval):
            try:
                for s in self.queue.reap():
                    self.audit.record("lease_reaped", s.state, target=s.id, error=s.error)
            except Exception as exc:  # never let the reaper die silently
                self.audit.record("reaper_error", "error", error=repr(exc))

    # ---------------------------------------------------------------- auth
    @property
    def enrollment_allowed(self) -> bool:
        if not self._worker_tokens:
            return False
        return not profile.hosted() or profile.flag("DAEDELUS_ALLOW_WORKER_ENROLLMENT")

    @property
    def workers_enabled(self) -> bool:
        return self.enrollment_allowed or self.creds.any_active()

    def check_join_token(self, token: str | None) -> bool:
        return self.enrollment_allowed and bool(token) and any(
            hmac.compare_digest(token, t) for t in self._worker_tokens)

    def authenticate_worker(self, token: str | None, peer: str = "") -> WorkerIdentity | None:
        return self.creds.authenticate(token or "", peer)

    def revoke_worker(self, cred_id: str, reason: str, *, by: str = "operator") -> list[str]:
        jobs = self.creds.revoke(cred_id, reason)
        self.audit.record("worker_revoked", actor=by, target=cred_id, reason=reason,
                          leases_revoked=jobs)
        return jobs

    # ---------------------------------------------------------------- availability
    def live_workers(self) -> list[WorkerInfo]:
        return [w for w in self.queue.workers(alive_within=ALIVE_S) if not w.draining]

    def can_run(self, adapter: str, capability: str) -> WorkerInfo | None:
        return next((w for w in self.live_workers()
                     if adapter in w.adapters and capability in w.capabilities), None)

    # ---------------------------------------------------------------- jobs
    def submit(self, req: JobRequest) -> JobStatus:
        st = self.queue.enqueue(req)
        self.audit.record("job_submitted", target=req.id, project=req.project_id,
                          artifact=req.artifact_id, adapter=req.adapter, method=req.method,
                          requires=req.requires, origin=req.origin)
        return st

    def wait(self, job_id: str, *, timeout: float | None = None,
             on_event: Callable[[dict[str, Any]], None] | None = None,
             cancelled: Callable[[], bool] | None = None,
             poll: float = 0.1) -> JobResult:
        """Block until the job is terminal. Raises JobFailed unless it succeeded."""
        t0 = time.time()
        seq = 0
        cancel_sent = False
        while True:
            for ev in self.queue.events(since=seq, job_id=job_id):
                seq = ev["seq"]
                if on_event:
                    on_event(ev)
            st = self.queue.status(job_id)
            if st.state in TERMINAL:
                res = self.queue.result(job_id)
                if st.state != "succeeded" or res is None:
                    raise JobFailed(st, res)
                return res
            if cancelled and cancelled() and not cancel_sent:
                self.queue.cancel(job_id)
                self.audit.record("job_cancel_requested", target=job_id)
                cancel_sent = True
            if timeout is not None and time.time() - t0 > timeout:
                self.queue.cancel(job_id)
                raise JobFailed(self.queue.status(job_id))
            self.queue.wait_change(max(poll, 0.5) if not cancelled else poll)

    def publish_event(self, project_id: str, kind: str, **data: Any) -> None:
        """Non-job events (executions, revisions) on the same durable stream used by SSE."""
        with self.queue._tx() as db:
            self.queue._event(db, "", project_id, kind, **data)

    def summary(self) -> dict[str, Any]:
        jobs = self.queue.list(limit=1000)
        counts: dict[str, int] = {}
        for j in jobs:
            counts[j.state] = counts.get(j.state, 0) + 1
        return {"workers_enabled": self.workers_enabled,
                "enrollment_allowed": self.enrollment_allowed,
                "workers": [w.model_dump() | {"alive": time.time() - w.last_seen <= ALIVE_S}
                            for w in self.queue.workers()],
                "jobs": counts}


_cluster: ClusterService | None = None


def configure(root: Path, **kw: Any) -> ClusterService:
    global _cluster
    if _cluster is not None and _cluster.root == Path(root):
        return _cluster
    if _cluster is not None:
        _cluster.close()
    _cluster = ClusterService(root, **kw)
    return _cluster


def get_cluster() -> ClusterService | None:
    return _cluster


def reset() -> None:
    global _cluster
    if _cluster is not None:
        _cluster.close()
    _cluster = None
