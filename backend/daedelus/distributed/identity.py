"""Per-worker identities and credentials (V2.1).

V2 authenticated every worker with one shared secret: any holder could register, lease jobs
and read their inputs, and no single worker could be revoked. V2.1 gives each worker its own
credential:

* A **credential** has an id (``wkr_...``), which *is* the worker identity, and a random
  secret. Only ``sha256(secret)`` is stored. The wire form is ``ddw1.<id>.<secret>``.
* **Provisioned** credentials are created by an operator (``daedelus cluster credential
  create``) and may restrict the adapters and capabilities the worker can advertise.
* **Enrolled** credentials are issued to a worker that registers with a *join token*
  (the V2 ``DAEDELUS_WORKER_TOKENS`` secret). A join token can do nothing except enrol; every
  other worker call needs the per-worker credential. The hosted profile disables enrolment
  unless ``DAEDELUS_ALLOW_WORKER_ENROLLMENT=1``.
* **Revocation** takes effect on the next request: the credential stops authenticating and
  every lease it holds is revoked (the job is re-queued for another worker).
* **Rotation** issues a new secret. The previous secret stays valid for a short grace period
  (default 0 s for operator rotation, 60 s for a worker rotating itself) so in-flight requests
  finish.

The store shares the queue's SQLite database so that revocation and lease revocation happen
in one transaction.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import sqlite3
import time
from dataclasses import dataclass
from typing import Any

from ..models import new_id, now_iso

PREFIX = "ddw1"

SCHEMA = """
CREATE TABLE IF NOT EXISTS worker_creds (
  id TEXT PRIMARY KEY, name TEXT, kind TEXT, state TEXT, secret_hash TEXT,
  prev_hash TEXT, prev_until REAL, adapters TEXT, capabilities TEXT,
  created_at TEXT, rotated_at TEXT, revoked_at TEXT, revoked_reason TEXT,
  last_used REAL, last_peer TEXT);
"""


def _h(secret: str) -> str:
    return hashlib.sha256(secret.encode()).hexdigest()


def format_credential(cred_id: str, secret: str) -> str:
    return f"{PREFIX}.{cred_id}.{secret}"


def parse_credential(token: str) -> tuple[str, str] | None:
    parts = (token or "").split(".")
    if len(parts) != 3 or parts[0] != PREFIX or not parts[1].startswith("wkr_") or \
            len(parts[2]) < 32:
        return None
    return parts[1], parts[2]


@dataclass
class WorkerIdentity:
    id: str
    name: str
    kind: str  # provisioned | enrolled
    adapters: list[str] | None  # None = any
    capabilities: list[str] | None  # None = cpu only unless granted

    def allows(self, adapters: list[str], capabilities: list[str]) -> str | None:
        """Reason the advertised adapters/capabilities exceed this credential, or None."""
        if self.adapters is not None:
            extra = sorted(set(adapters) - set(self.adapters))
            if extra:
                return f"credential does not allow adapters {extra}"
        caps = self.capabilities if self.capabilities is not None else ["cpu"]
        extra = sorted(set(capabilities) - set(caps))
        if extra:
            return f"credential does not allow capabilities {extra}"
        return None


class CredentialStore:
    """Worker credentials in the queue database (see module docstring)."""

    def __init__(self, queue):
        self.q = queue
        with self.q._lock:
            self.q._db.executescript(SCHEMA)

    # ---------------------------------------------------------------- admin
    def create(self, name: str, *, kind: str = "provisioned", adapters: list[str] | None = None,
               capabilities: list[str] | None = None) -> tuple[dict[str, Any], str]:
        """Create a credential; returns (record, wire credential). The secret is shown once."""
        cid = new_id("wkr")
        secret = secrets.token_urlsafe(32)
        with self.q._tx() as db:
            db.execute("INSERT INTO worker_creds(id, name, kind, state, secret_hash, adapters, "
                       "capabilities, created_at) VALUES (?,?,?,?,?,?,?,?)",
                       (cid, name, kind, "active", _h(secret),
                        json.dumps(adapters) if adapters is not None else None,
                        json.dumps(capabilities) if capabilities is not None else None,
                        now_iso()))
        return self.get(cid), format_credential(cid, secret)

    def rotate(self, cred_id: str, *, grace_s: float = 0.0) -> str:
        secret = secrets.token_urlsafe(32)
        with self.q._tx() as db:
            r = db.execute("SELECT state, secret_hash FROM worker_creds WHERE id=?",
                           (cred_id,)).fetchone()
            if r is None or r[0] != "active":
                raise KeyError(f"no active credential {cred_id}")
            db.execute("UPDATE worker_creds SET secret_hash=?, prev_hash=?, prev_until=?, "
                       "rotated_at=? WHERE id=?",
                       (_h(secret), r[1] if grace_s > 0 else None,
                        time.time() + grace_s if grace_s > 0 else None, now_iso(), cred_id))
        return format_credential(cred_id, secret)

    def revoke(self, cred_id: str, reason: str = "revoked by operator") -> list[str]:
        """Revoke a credential and every lease it holds. Returns the re-queued job ids."""
        with self.q._tx() as db:
            r = db.execute("SELECT state FROM worker_creds WHERE id=?", (cred_id,)).fetchone()
            if r is None:
                raise KeyError(cred_id)
            db.execute("UPDATE worker_creds SET state='revoked', revoked_at=?, revoked_reason=?, "
                       "prev_hash=NULL, prev_until=NULL WHERE id=?", (now_iso(), reason, cred_id))
            db.execute("DELETE FROM workers WHERE id=?", (cred_id,))
            return self.q._revoke_leases_locked(db, cred_id,
                                                f"worker credential {cred_id} revoked: {reason}")

    def get(self, cred_id: str) -> dict[str, Any]:
        with self.q._lock:
            self.q._db.row_factory = sqlite3.Row
            r = self.q._db.execute("SELECT * FROM worker_creds WHERE id=?", (cred_id,)).fetchone()
            self.q._db.row_factory = None
        if r is None:
            raise KeyError(cred_id)
        return self._public(r)

    def list(self) -> list[dict[str, Any]]:
        with self.q._lock:
            self.q._db.row_factory = sqlite3.Row
            rows = self.q._db.execute("SELECT * FROM worker_creds ORDER BY created_at").fetchall()
            self.q._db.row_factory = None
        return [self._public(r) for r in rows]

    def any_active(self) -> bool:
        with self.q._lock:
            return self.q._db.execute(
                "SELECT 1 FROM worker_creds WHERE state='active' LIMIT 1").fetchone() is not None

    @staticmethod
    def _public(r) -> dict[str, Any]:
        return {"id": r["id"], "name": r["name"], "kind": r["kind"], "state": r["state"],
                "adapters": json.loads(r["adapters"]) if r["adapters"] else None,
                "capabilities": json.loads(r["capabilities"]) if r["capabilities"] else None,
                "created_at": r["created_at"], "rotated_at": r["rotated_at"],
                "revoked_at": r["revoked_at"], "revoked_reason": r["revoked_reason"],
                "last_used": r["last_used"], "last_peer": r["last_peer"]}

    # ---------------------------------------------------------------- authentication
    def authenticate(self, token: str, peer: str = "") -> WorkerIdentity | None:
        parsed = parse_credential(token)
        if parsed is None:
            return None
        cid, secret = parsed
        with self.q._lock:
            self.q._db.row_factory = sqlite3.Row
            r = self.q._db.execute("SELECT * FROM worker_creds WHERE id=?", (cid,)).fetchone()
            self.q._db.row_factory = None
        if r is None or r["state"] != "active":
            return None
        h = _h(secret)
        ok = hmac.compare_digest(h, r["secret_hash"]) or (
            r["prev_hash"] is not None and (r["prev_until"] or 0) > time.time() and
            hmac.compare_digest(h, r["prev_hash"]))
        if not ok:
            return None
        now = time.time()
        if (r["last_used"] or 0) < now - 5:  # throttle bookkeeping writes
            with self.q._tx() as db:
                db.execute("UPDATE worker_creds SET last_used=?, last_peer=? WHERE id=?",
                           (now, peer, cid))
        return WorkerIdentity(id=cid, name=r["name"], kind=r["kind"],
                              adapters=json.loads(r["adapters"]) if r["adapters"] else None,
                              capabilities=json.loads(r["capabilities"]) if r["capabilities"]
                              else None)
