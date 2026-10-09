"""Durable job queue (SQLite, WAL) with leases.

State machine::

    queued --lease--> leased --heartbeat--> running --complete--> succeeded
       ^                 |  \\                  |  \\--fail(retryable, attempts left)--> queued
       |                 |   \\--lease expired (worker lost)--> queued | failed (no attempts)
       +-----------------+--------deadline passed--> queued | timed_out (no attempts)
    cancel: queued -> cancelled at once; leased/running -> cancel requested (worker is told on
    its next heartbeat and acknowledges; an unacknowledged cancel completes when the lease
    expires).

Every transition is one ``BEGIN IMMEDIATE`` transaction, so a crash leaves the previous
state. Lease tokens are random per attempt and only their hash is stored: a worker whose
lease was revoked (expired and re-queued) cannot complete, heartbeat or read blobs of the
job any more - its late result is discarded and audited.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

from ..models import now_iso
from .models import TERMINAL, JobRequest, JobResult, JobStatus, Lease, WorkerInfo

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
  id TEXT PRIMARY KEY, project_id TEXT, artifact_id TEXT, adapter TEXT, method TEXT,
  requires TEXT, state TEXT, attempt INTEGER DEFAULT 0, max_attempts INTEGER,
  worker_id TEXT, token_hash TEXT, lease_expires REAL, deadline REAL, timeout_s REAL,
  idem_key TEXT UNIQUE, request TEXT, result TEXT, error TEXT, progress REAL DEFAULT 0,
  message TEXT DEFAULT '', cancel_requested INTEGER DEFAULT 0, publication TEXT,
  created_at TEXT, updated_at TEXT);
CREATE INDEX IF NOT EXISTS jobs_state ON jobs(state, created_at);
CREATE TABLE IF NOT EXISTS events (
  seq INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL, project_id TEXT, job_id TEXT, kind TEXT,
  data TEXT);
CREATE INDEX IF NOT EXISTS events_project ON events(project_id, seq);
CREATE TABLE IF NOT EXISTS workers (id TEXT PRIMARY KEY, info TEXT, last_seen REAL);
"""


def _h(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


class LeaseLost(Exception):
    """The caller's lease is no longer valid (expired, re-queued, finished or cancelled)."""


class LeaseOwnerMismatch(LeaseLost):
    """A lease token was presented by a worker other than the one that leased the job."""


class JobQueue:
    def __init__(self, path: Path, *, lease_s: float = 30.0):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.lease_s = lease_s
        self._lock = threading.RLock()
        self._db = sqlite3.connect(str(self.path), check_same_thread=False, timeout=30,
                                   isolation_level=None)
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute("PRAGMA synchronous=FULL")
        self._db.executescript(SCHEMA)

    def close(self) -> None:
        with self._lock:
            self._db.close()

    # ---------------------------------------------------------------- helpers
    def _tx(self):
        q = self

        class _T:
            def __enter__(self_inner):
                q._lock.acquire()
                q._db.execute("BEGIN IMMEDIATE")
                return q._db

            def __exit__(self_inner, et, ev, tb):
                try:
                    q._db.execute("ROLLBACK" if et else "COMMIT")
                finally:
                    q._lock.release()
                return False
        return _T()

    def _event(self, db, job_id: str, project_id: str, kind: str, **data: Any) -> None:
        db.execute("INSERT INTO events(ts, project_id, job_id, kind, data) VALUES (?,?,?,?,?)",
                   (time.time(), project_id, job_id, kind, json.dumps(data, default=str)))

    def _row(self, db, job_id: str) -> sqlite3.Row | None:
        db.row_factory = sqlite3.Row
        r = db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        db.row_factory = None
        return r

    @staticmethod
    def _status(r: sqlite3.Row) -> JobStatus:
        return JobStatus(id=r["id"], state=r["state"], attempt=r["attempt"],
                         max_attempts=r["max_attempts"], worker_id=r["worker_id"],
                         lease_expires_at=r["lease_expires"], progress=r["progress"] or 0,
                         message=r["message"] or "", error=r["error"],
                         created_at=r["created_at"], updated_at=r["updated_at"],
                         method=r["method"], adapter=r["adapter"], project_id=r["project_id"],
                         artifact_id=r["artifact_id"], requires=r["requires"])

    # ---------------------------------------------------------------- submit / read
    def enqueue(self, req: JobRequest) -> JobStatus:
        with self._tx() as db:
            if req.idempotency_key:
                db.row_factory = sqlite3.Row
                r = db.execute("SELECT * FROM jobs WHERE idem_key=?",
                               (req.idempotency_key,)).fetchone()
                db.row_factory = None
                if r is not None and r["state"] in ("failed", "cancelled", "timed_out",
                                                     "conflict"):
                    # a failed attempt must not block an explicit retry: detach its key
                    db.execute("UPDATE jobs SET idem_key=NULL WHERE id=?", (r["id"],))
                elif r is not None:
                    return self._status(r)  # same request submitted twice -> same job
            ts = now_iso()
            db.execute(
                "INSERT INTO jobs(id, project_id, artifact_id, adapter, method, requires, state, "
                "max_attempts, timeout_s, idem_key, request, created_at, updated_at) "
                "VALUES (?,?,?,?,?,?, 'queued', ?,?,?,?,?,?)",
                (req.id, req.project_id, req.artifact_id, req.adapter, req.method, req.requires,
                 req.max_attempts, req.timeout_s, req.idempotency_key, req.model_dump_json(),
                 ts, ts))
            self._event(db, req.id, req.project_id, "queued", method=req.method,
                        adapter=req.adapter, requires=req.requires)
            return self._status(self._row(db, req.id))

    def status(self, job_id: str) -> JobStatus:
        with self._lock:
            r = self._row(self._db, job_id)
        if r is None:
            raise KeyError(job_id)
        return self._status(r)

    def request(self, job_id: str) -> JobRequest:
        with self._lock:
            r = self._row(self._db, job_id)
        if r is None:
            raise KeyError(job_id)
        return JobRequest.model_validate_json(r["request"])

    def result(self, job_id: str) -> JobResult | None:
        with self._lock:
            r = self._row(self._db, job_id)
        return JobResult.model_validate_json(r["result"]) if r and r["result"] else None

    def publication(self, job_id: str) -> dict[str, Any] | None:
        with self._lock:
            r = self._row(self._db, job_id)
        return json.loads(r["publication"]) if r and r["publication"] else None

    def set_publication(self, job_id: str, pub: dict[str, Any]) -> None:
        with self._tx() as db:
            db.execute("UPDATE jobs SET publication=?, updated_at=? WHERE id=?",
                       (json.dumps(pub), now_iso(), job_id))
            r = self._row(db, job_id)
            self._event(db, job_id, r["project_id"], "published", **pub)

    def mark_conflict(self, job_id: str, detail: str) -> None:
        with self._tx() as db:
            db.execute("UPDATE jobs SET state='conflict', error=?, updated_at=? WHERE id=?",
                       (detail, now_iso(), job_id))
            r = self._row(db, job_id)
            self._event(db, job_id, r["project_id"], "conflict", error=detail)

    def list(self, *, project_id: str | None = None, states: set[str] | None = None,
             limit: int = 200) -> list[JobStatus]:
        q = "SELECT * FROM jobs"
        args: list[Any] = []
        if project_id:
            q += " WHERE project_id=?"
            args.append(project_id)
        q += " ORDER BY created_at DESC LIMIT ?"
        args.append(limit)
        with self._lock:
            self._db.row_factory = sqlite3.Row
            rows = self._db.execute(q, args).fetchall()
            self._db.row_factory = None
        out = [self._status(r) for r in rows]
        return [s for s in out if not states or s.state in states]

    # ---------------------------------------------------------------- workers
    def register_worker(self, w: WorkerInfo) -> WorkerInfo:
        w.last_seen = time.time()
        with self._tx() as db:
            db.execute("INSERT OR REPLACE INTO workers(id, info, last_seen) VALUES (?,?,?)",
                       (w.id, w.model_dump_json(), w.last_seen))
        return w

    def touch_worker(self, worker_id: str) -> None:
        with self._tx() as db:
            db.execute("UPDATE workers SET last_seen=? WHERE id=?", (time.time(), worker_id))

    def workers(self, *, alive_within: float | None = None) -> list[WorkerInfo]:
        with self._lock:
            rows = self._db.execute("SELECT info, last_seen FROM workers").fetchall()
        out = []
        for info, seen in rows:
            w = WorkerInfo.model_validate_json(info)
            w.last_seen = seen
            if alive_within is None or time.time() - seen <= alive_within:
                out.append(w)
        return out

    # ---------------------------------------------------------------- leasing
    def lease(self, worker: WorkerInfo) -> Lease | None:
        now = time.time()
        with self._tx() as db:
            db.execute("UPDATE workers SET last_seen=? WHERE id=?", (now, worker.id))
            if worker.draining:
                return None
            db.row_factory = sqlite3.Row
            rows = db.execute("SELECT * FROM jobs WHERE state='queued' ORDER BY created_at "
                              "LIMIT 200").fetchall()
            db.row_factory = None
            for r in rows:
                if r["requires"] not in worker.capabilities or r["adapter"] not in worker.adapters:
                    continue
                token = secrets.token_urlsafe(32)
                attempt = r["attempt"] + 1
                db.execute("UPDATE jobs SET state='leased', attempt=?, worker_id=?, token_hash=?, "
                           "lease_expires=?, deadline=?, progress=0, message='', updated_at=? "
                           "WHERE id=? AND state='queued'",
                           (attempt, worker.id, _h(token), now + self.lease_s,
                            now + float(r["timeout_s"]), now_iso(), r["id"]))
                self._event(db, r["id"], r["project_id"], "leased", worker_id=worker.id,
                            attempt=attempt)
                return Lease(job=JobRequest.model_validate_json(r["request"]), lease_token=token,
                             lease_expires_at=now + self.lease_s, attempt=attempt)
        return None

    def _check(self, db, job_id: str, token: str, worker_id: str | None = None) -> sqlite3.Row:
        r = self._row(db, job_id)
        if r is None:
            raise LeaseLost(f"unknown job {job_id}")
        if r["state"] not in ("leased", "running") or r["token_hash"] is None or \
                not hmac.compare_digest(r["token_hash"], _h(token)):
            raise LeaseLost(f"lease on {job_id} is no longer valid (state {r['state']})")
        if worker_id is not None and r["worker_id"] != worker_id:
            # a valid lease token presented by a different authenticated worker: the token
            # was stolen or misrouted. Refuse; the owner keeps its lease.
            raise LeaseOwnerMismatch(f"lease on {job_id} belongs to another worker")
        return r

    def check_lease(self, job_id: str, token: str, worker_id: str | None = None) -> JobRequest:
        with self._tx() as db:
            r = self._check(db, job_id, token, worker_id)
            return JobRequest.model_validate_json(r["request"])

    def _revoke_leases_locked(self, db, worker_id: str, reason: str) -> list[str]:
        """Revoke every live lease held by a worker (credential revoked): re-queue or fail."""
        db.row_factory = sqlite3.Row
        rows = db.execute("SELECT * FROM jobs WHERE worker_id=? AND state IN ('leased','running')",
                          (worker_id,)).fetchall()
        db.row_factory = None
        out = []
        for r in rows:
            if r["cancel_requested"]:
                db.execute("UPDATE jobs SET state='cancelled', token_hash=NULL, "
                           "lease_expires=NULL, updated_at=? WHERE id=?", (now_iso(), r["id"]))
                self._event(db, r["id"], r["project_id"], "cancelled", error=reason)
            else:
                self._fail_locked(db, r, reason, retryable=True)
            out.append(r["id"])
        return out

    def heartbeat(self, job_id: str, token: str, progress: float | None = None,
                  message: str | None = None, worker_id: str | None = None) -> dict[str, Any]:
        now = time.time()
        with self._tx() as db:
            r = self._check(db, job_id, token, worker_id)
            if r["lease_expires"] is not None and r["lease_expires"] < now:
                raise LeaseLost(f"lease on {job_id} expired")
            db.execute("UPDATE jobs SET state='running', lease_expires=?, progress=?, message=?, "
                       "updated_at=? WHERE id=?",
                       (now + self.lease_s, progress if progress is not None else r["progress"],
                        message if message is not None else r["message"], now_iso(), job_id))
            db.execute("UPDATE workers SET last_seen=? WHERE id=?", (now, r["worker_id"]))
            if progress is not None or message:
                self._event(db, job_id, r["project_id"], "progress", progress=progress,
                            message=message)
            return {"cancel": bool(r["cancel_requested"]), "lease_expires_at": now + self.lease_s,
                    "deadline": r["deadline"]}

    def complete(self, job_id: str, token: str, result: JobResult,
                 worker_id: str | None = None) -> JobStatus:
        with self._tx() as db:
            r = self._row(db, job_id)
            if r is not None and r["state"] == "succeeded" and r["token_hash"] == _h(token) and \
                    (worker_id is None or r["worker_id"] == worker_id):
                return self._status(r)  # duplicate completion of the same attempt: idempotent
            r = self._check(db, job_id, token, worker_id)
            if r["cancel_requested"]:
                state, err = "cancelled", "cancelled while running; result discarded"
            elif not result.ok:
                return self._fail_locked(db, r, result.error or "failed", retryable=False,
                                         result=result)
            else:
                state, err = "succeeded", None
            db.execute("UPDATE jobs SET state=?, result=?, error=?, progress=1, lease_expires=NULL, "
                       "updated_at=? WHERE id=?",
                       (state, result.model_dump_json(), err, now_iso(), job_id))
            self._event(db, job_id, r["project_id"], state, worker_id=r["worker_id"],
                        seconds=result.seconds)
            return self._status(self._row(db, job_id))

    def fail(self, job_id: str, token: str, error: str, *, retryable: bool,
             cancelled: bool = False, worker_id: str | None = None) -> JobStatus:
        with self._tx() as db:
            r = self._check(db, job_id, token, worker_id)
            if cancelled or r["cancel_requested"]:
                db.execute("UPDATE jobs SET state='cancelled', error=?, lease_expires=NULL, "
                           "updated_at=? WHERE id=?", (error, now_iso(), job_id))
                self._event(db, job_id, r["project_id"], "cancelled", error=error)
                return self._status(self._row(db, job_id))
            return self._fail_locked(db, r, error, retryable=retryable)

    def _fail_locked(self, db, r, error: str, *, retryable: bool,
                     result: JobResult | None = None, timed_out: bool = False) -> JobStatus:
        again = retryable and r["attempt"] < r["max_attempts"]
        state = "queued" if again else ("timed_out" if timed_out else "failed")
        db.execute("UPDATE jobs SET state=?, error=?, token_hash=NULL, lease_expires=NULL, "
                   "worker_id=CASE WHEN ?='queued' THEN NULL ELSE worker_id END, result=?, "
                   "updated_at=? WHERE id=?",
                   (state, error, state, result.model_dump_json() if result else None, now_iso(),
                    r["id"]))
        self._event(db, r["id"], r["project_id"], "retry" if again else state, error=error,
                    attempt=r["attempt"])
        return self._status(self._row(db, r["id"]))

    def cancel(self, job_id: str) -> JobStatus:
        with self._tx() as db:
            r = self._row(db, job_id)
            if r is None:
                raise KeyError(job_id)
            if r["state"] == "queued":
                db.execute("UPDATE jobs SET state='cancelled', error='cancelled before start', "
                           "updated_at=? WHERE id=?", (now_iso(), job_id))
                self._event(db, job_id, r["project_id"], "cancelled")
            elif r["state"] in ("leased", "running"):
                db.execute("UPDATE jobs SET cancel_requested=1, updated_at=? WHERE id=?",
                           (now_iso(), job_id))
                self._event(db, job_id, r["project_id"], "cancel_requested")
            return self._status(self._row(db, job_id))

    def reap(self) -> list[JobStatus]:
        """Expire lost leases and over-deadline jobs (run periodically and at startup)."""
        now = time.time()
        changed = []
        with self._tx() as db:
            db.row_factory = sqlite3.Row
            rows = db.execute("SELECT * FROM jobs WHERE state IN ('leased','running') AND "
                              "(lease_expires < ? OR deadline < ?)", (now, now)).fetchall()
            db.row_factory = None
            for r in rows:
                if r["cancel_requested"]:
                    db.execute("UPDATE jobs SET state='cancelled', token_hash=NULL, "
                               "lease_expires=NULL, updated_at=? WHERE id=?", (now_iso(), r["id"]))
                    self._event(db, r["id"], r["project_id"], "cancelled",
                                error="cancel not acknowledged before the lease expired")
                    changed.append(self._status(self._row(db, r["id"])))
                elif r["deadline"] is not None and r["deadline"] < now:
                    changed.append(self._fail_locked(
                        db, r, f"timed out after {r['timeout_s']} s (attempt {r['attempt']})",
                        retryable=True, timed_out=True))
                else:
                    changed.append(self._fail_locked(
                        db, r, f"lease expired: worker {r['worker_id']} stopped heartbeating "
                               f"(attempt {r['attempt']})", retryable=True))
        return changed

    # ---------------------------------------------------------------- events
    def events(self, *, since: int = 0, project_id: str | None = None,
               job_id: str | None = None, limit: int = 500) -> list[dict[str, Any]]:
        q = "SELECT seq, ts, project_id, job_id, kind, data FROM events WHERE seq > ?"
        args: list[Any] = [since]
        if project_id:
            q += " AND project_id=?"
            args.append(project_id)
        if job_id:
            q += " AND job_id=?"
            args.append(job_id)
        q += " ORDER BY seq LIMIT ?"
        args.append(limit)
        with self._lock:
            rows = self._db.execute(q, args).fetchall()
        return [{"seq": s, "ts": ts, "project_id": p, "job_id": j, "kind": k,
                 "data": json.loads(d)} for s, ts, p, j, k, d in rows]

    def is_terminal(self, job_id: str) -> bool:
        return self.status(job_id).state in TERMINAL
