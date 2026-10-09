"""API authentication, project scoping and request hardening (V2, V2.1).

Defaults are safe for a single-user desktop:

- the server binds to 127.0.0.1 and refuses a non-loopback bind without API tokens (cli);
- only loopback Host headers are accepted (DNS-rebinding protection) unless
  ``DAEDELUS_ALLOWED_HOSTS`` lists more;
- cross-origin browser access is limited to the desktop shell and loopback origins unless
  ``DAEDELUS_CORS_ORIGINS`` lists more.

With ``DAEDELUS_API_TOKENS`` set (``token`` = all projects, ``token:prj_a|prj_b`` = only those
projects; comma-separated), every ``/api`` request except ``/api/health``, ``/api/auth/config``
and the worker protocol (which uses worker credentials) must be authenticated by one of:

* ``Authorization: Bearer <token>`` (scripts, the desktop shell);
* the ``dd_session`` cookie of a browser session (``sessions.py``); state-changing requests
  must then carry ``X-CSRF-Token`` and, if the browser sends one, an allowed ``Origin``;
* a short-lived ``?ticket=`` scoped to one project's event stream or files (GET only).

V2's ``?token=`` query parameter and raw-token ``dd_token`` cookie are refused (V2.1): a
reusable bearer secret must not appear in URLs. ``DAEDELUS_ALLOW_QUERY_TOKENS=1`` restores
the query parameter outside the hosted profile, for old scripts only.
Scoped tokens can reach only their projects and cannot use cluster administration endpoints.
"""

from __future__ import annotations

import hmac
import os
import re
from dataclasses import dataclass

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

PROJECT_RE = re.compile(r"^/api/projects/([^/]+)")
WORKER_PATHS = re.compile(r"^/api/cluster/(workers|lease|jobs/)")
ADMIN_PATHS = re.compile(r"^/api/cluster/(status|audit|credentials)")
LOOPBACK_HOSTS = ["127.0.0.1", "localhost", "[::1]", "::1", "testserver"]
DEFAULT_ORIGINS = ["tauri://localhost", "http://tauri.localhost", "https://tauri.localhost",
                   "http://127.0.0.1:5173", "http://localhost:5173", "http://127.0.0.1:8765",
                   "http://localhost:8765"]


@dataclass
class TokenGrant:
    token: str
    projects: set[str] | None  # None = all projects (admin)


def parse_tokens(spec: str | None) -> list[TokenGrant]:
    out = []
    for part in (spec or "").split(","):
        part = part.strip()
        if not part:
            continue
        tok, _, scope = part.partition(":")
        if len(tok) < 16:
            raise ValueError("API tokens must be at least 16 characters")
        out.append(TokenGrant(tok, {p for p in scope.split("|") if p} if scope else None))
    return out


def allowed_hosts() -> list[str]:
    extra = [h.strip() for h in os.environ.get("DAEDELUS_ALLOWED_HOSTS", "").split(",")
             if h.strip()]
    return LOOPBACK_HOSTS + extra


def cors_origins() -> list[str]:
    extra = [h.strip() for h in os.environ.get("DAEDELUS_CORS_ORIGINS", "").split(",")
             if h.strip()]
    return DEFAULT_ORIGINS + extra


SAFE_METHODS = ("GET", "HEAD", "OPTIONS")
PUBLIC_PATHS = ("/api/health", "/api/auth/config")


def _bearer(request: Request) -> str:
    auth = request.headers.get("authorization", "")
    return auth[7:].strip() if auth.lower().startswith("bearer ") else ""


def _origin_allowed(request: Request) -> bool:
    origin = request.headers.get("origin")
    if not origin:
        return True  # same-origin navigations and non-browser clients send none
    host = request.headers.get("host", "")
    if origin.split("://", 1)[-1] == host:
        return True
    return origin in cors_origins()


class AuthMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, grants: list[TokenGrant], audit=None, sessions=None):
        super().__init__(app)
        self.grants = grants
        self.audit = audit
        self.sessions = sessions  # callable -> SessionStore (or None)

    def _grant_by_fp(self, fp: str) -> TokenGrant | None:
        from .sessions import token_fp
        return next((g for g in self.grants if hmac.compare_digest(token_fp(g.token), fp)),
                    None)

    def _authenticate(self, request: Request) -> tuple[TokenGrant | None, str, str | None]:
        """(grant, method, error). method: bearer | session | ticket | query."""
        tok = _bearer(request)
        if tok:
            g = next((g for g in self.grants if hmac.compare_digest(tok, g.token)), None)
            return g, "bearer", None if g else "invalid bearer token"
        store = self.sessions() if callable(self.sessions) else self.sessions
        sid = request.cookies.get("dd_session")
        if sid and store is not None:
            sess = store.get(sid)
            g = self._grant_by_fp(sess.token_fp) if sess else None
            if g is None:
                return None, "session", "session expired or its token was withdrawn"
            if request.method not in SAFE_METHODS:
                from .sessions import csrf_ok
                if not csrf_ok(sess, request.headers.get("x-csrf-token")):
                    return None, "csrf", "missing or invalid X-CSRF-Token"
                if not _origin_allowed(request):
                    return None, "csrf", "origin not allowed"
            request.state.session = sess
            return g, "session", None
        ticket = request.query_params.get("ticket")
        if ticket and store is not None:
            fp = store.redeem(ticket, request.method, request.url.path)
            g = self._grant_by_fp(fp) if fp else None
            return g, "ticket", None if g else "invalid, expired or misapplied ticket"
        qtok = request.query_params.get("token")
        if qtok:
            from . import profile
            if profile.flag("DAEDELUS_ALLOW_QUERY_TOKENS") and not profile.hosted():
                g = next((g for g in self.grants if hmac.compare_digest(qtok, g.token)), None)
                return g, "query", None if g else "invalid token"
            return None, "query", ("query-string tokens are disabled: use a session "
                                   "(POST /api/auth/session) or a ticket")
        return None, "none", "authentication required"

    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        request.state.scopes = None  # all projects
        request.state.grant = None
        request.state.session = None
        if not self.grants or not path.startswith("/api/") or path in PUBLIC_PATHS or \
                WORKER_PATHS.match(path):
            return await call_next(request)
        grant, how, err = self._authenticate(request)
        if grant is None:
            self._audit("csrf_rejected" if how == "csrf" else "api_auth_failed", request,
                        method=how)
            return JSONResponse({"detail": err or "authentication required"},
                                status_code=403 if how == "csrf" else 401)
        if grant.projects is not None:
            m = PROJECT_RE.match(path)
            if ADMIN_PATHS.match(path) or (m and m.group(1) not in grant.projects) or \
                    (path == "/api/projects" and request.method != "GET"):
                self._audit("api_scope_denied", request)
                return JSONResponse({"detail": "token not valid for this resource"},
                                    status_code=403)
            request.state.scopes = grant.projects
        request.state.grant = grant
        request.state.auth_method = how
        return await call_next(request)

    def _audit(self, action: str, request: Request, **detail) -> None:
        cl = self.audit() if callable(self.audit) else None
        if cl is not None:
            cl.audit.record(action, "denied", actor=request.client.host if request.client
                            else "?", target=f"{request.method} {request.url.path}", **detail)


def path_source_refusal(p) -> str | None:
    """Server-side path ingestion reads files on the control plane's machine. It is a desktop
    convenience: in the hosted profile it is refused unless ``DAEDELUS_PATH_SOURCE_ROOTS``
    lists directories it may read from; when that variable is set, paths outside it are
    refused in every profile."""
    from pathlib import Path

    from . import profile
    roots = [Path(r).expanduser().resolve() for r in
             os.environ.get("DAEDELUS_PATH_SOURCE_ROOTS", "").split(os.pathsep) if r.strip()]
    if not roots:
        return ("server-side path sources are disabled in the hosted profile (upload the files, "
                "or set DAEDELUS_PATH_SOURCE_ROOTS)") if profile.hosted() else None
    rp = Path(p).expanduser().resolve()
    if not any(rp == r or r in rp.parents for r in roots):
        return "path is outside DAEDELUS_PATH_SOURCE_ROOTS"
    return None


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Response headers for browser-facing deployments (V2.1).

    ``Referrer-Policy: no-referrer`` keeps ticket URLs out of Referer headers; HSTS is sent
    only over HTTPS (or behind a TLS proxy) so plain loopback development is unaffected."""

    async def dispatch(self, request: Request, call_next):
        resp = await call_next(request)
        h = resp.headers
        h.setdefault("X-Content-Type-Options", "nosniff")
        h.setdefault("Referrer-Policy", "no-referrer")
        h.setdefault("X-Frame-Options", "DENY")
        h.setdefault("Cross-Origin-Opener-Policy", "same-origin")
        if request.url.scheme == "https" or request.headers.get("x-forwarded-proto") == "https":
            h.setdefault("Strict-Transport-Security", "max-age=31536000")
        if request.url.path.startswith("/api/") and \
                h.get("content-type", "").startswith("application/json"):
            h.setdefault("Cache-Control", "no-store")  # API data, not files/previews
        return resp
