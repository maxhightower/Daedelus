"""Browser sessions and short-lived connection tickets (V2.1).

V2 let the studio pass its API token as ``?token=`` (for ``<img>`` and ``EventSource``) and
kept it in ``sessionStorage``. A reusable bearer secret in a URL ends up in browser history,
proxy logs and screenshots. V2.1 replaces that with:

* **Sessions.** ``POST /api/auth/session`` with ``Authorization: Bearer <api token>`` sets an
  ``HttpOnly``, ``SameSite=Strict`` cookie (``Secure`` over HTTPS and in the hosted profile)
  holding a random session id. Only the id's hash is stored. A session carries the scopes of
  the token that created it and dies when that token is removed from the configuration, at
  logout, after ``DAEDELUS_SESSION_HOURS`` (default 12) or after 2 h idle.
* **CSRF.** Cookie-authenticated requests that change state must send ``X-CSRF-Token`` equal
  to the session's CSRF token (returned by the session endpoints, never in a cookie the
  server trusts) and, when the browser sends an ``Origin``, an allowed origin.
* **Tickets.** Where a header cannot be set and no cookie is available (a desktop shell
  talking to a remote server), ``POST /api/auth/ticket`` issues a ticket for one project and
  one purpose: ``events`` (the SSE stream; 60 s to connect, single use) or ``files`` (project
  files and revision previews; 10 min, read-only). Tickets are accepted only on ``GET`` of the
  matching paths.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import secrets
import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
  id_hash TEXT PRIMARY KEY, token_fp TEXT, csrf TEXT, created REAL, expires REAL,
  last_seen REAL, agent TEXT);
CREATE TABLE IF NOT EXISTS tickets (
  hash TEXT PRIMARY KEY, token_fp TEXT, project TEXT, purpose TEXT, expires REAL,
  uses_left INTEGER);
"""
IDLE_S = 2 * 3600
TICKET_TTL = {"events": 60.0, "files": 600.0}
TICKET_PATHS = {
    "events": lambda pid: re.compile(rf"^/api/projects/{re.escape(pid)}/events$"),
    "files": lambda pid: re.compile(
        rf"^/api/projects/{re.escape(pid)}/(files/.+|revisions/[^/]+/(glb|files))$"),
}


def _h(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()


def token_fp(token: str) -> str:
    return _h("api-token:" + token)


@dataclass
class Session:
    token_fp: str
    csrf: str
    expires: float


class SessionStore:
    def __init__(self, path: Path):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._db = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None,
                                   timeout=30)
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.executescript(SCHEMA)
        self.hours = float(os.environ.get("DAEDELUS_SESSION_HOURS", "12") or 12)

    def close(self) -> None:
        with self._lock:
            self._db.close()

    # ---------------------------------------------------------------- sessions
    def create(self, fp: str, agent: str = "") -> tuple[str, Session]:
        sid = secrets.token_urlsafe(32)
        csrf = secrets.token_urlsafe(24)
        now = time.time()
        exp = now + self.hours * 3600
        with self._lock:
            self._db.execute("DELETE FROM sessions WHERE expires < ? OR last_seen < ?",
                             (now, now - IDLE_S))
            self._db.execute("INSERT INTO sessions VALUES (?,?,?,?,?,?,?)",
                             (_h(sid), fp, csrf, now, exp, now, agent[:200]))
        return sid, Session(fp, csrf, exp)

    def get(self, sid: str) -> Session | None:
        if not sid:
            return None
        now = time.time()
        with self._lock:
            r = self._db.execute("SELECT token_fp, csrf, expires, last_seen FROM sessions "
                                 "WHERE id_hash=?", (_h(sid),)).fetchone()
            if r is None or r[2] < now or r[3] < now - IDLE_S:
                return None
            if r[3] < now - 60:
                self._db.execute("UPDATE sessions SET last_seen=? WHERE id_hash=?",
                                 (now, _h(sid)))
        return Session(r[0], r[1], r[2])

    def delete(self, sid: str) -> None:
        with self._lock:
            self._db.execute("DELETE FROM sessions WHERE id_hash=?", (_h(sid),))

    # ---------------------------------------------------------------- tickets
    def ticket(self, fp: str, project: str, purpose: str) -> tuple[str, float]:
        if purpose not in TICKET_TTL:
            raise ValueError(f"unknown ticket purpose {purpose!r}")
        t = secrets.token_urlsafe(24)
        exp = time.time() + TICKET_TTL[purpose]
        with self._lock:
            self._db.execute("DELETE FROM tickets WHERE expires < ?", (time.time(),))
            self._db.execute("INSERT INTO tickets VALUES (?,?,?,?,?,?)",
                             (_h(t), fp, project, purpose, exp,
                              1 if purpose == "events" else 1_000_000))
        return t, exp

    def redeem(self, ticket: str, method: str, path: str) -> str | None:
        """Token fingerprint behind a valid ticket for this GET request, else None."""
        if method not in ("GET", "HEAD") or not ticket:
            return None
        with self._lock:
            r = self._db.execute("SELECT token_fp, project, purpose, expires, uses_left FROM "
                                 "tickets WHERE hash=?", (_h(ticket),)).fetchone()
            if r is None or r[3] < time.time() or r[4] <= 0:
                return None
            if not TICKET_PATHS[r[2]](r[1]).match(path):
                return None
            self._db.execute("UPDATE tickets SET uses_left=uses_left-1 WHERE hash=?",
                             (_h(ticket),))
        return r[0]


def csrf_ok(session: Session, header: str | None) -> bool:
    return bool(header) and hmac.compare_digest(session.csrf, header)


def describe(session: Session, scopes) -> dict:
    return {"authenticated": True, "csrf": session.csrf, "expires": session.expires,
            "projects": sorted(scopes) if scopes is not None else None}


def dumps(o) -> str:
    return json.dumps(o)
