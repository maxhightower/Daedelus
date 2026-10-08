"""API authentication, project scoping and request hardening (V2).

Defaults are safe for a single-user desktop:

- the server binds to 127.0.0.1 and refuses a non-loopback bind without API tokens (cli);
- only loopback Host headers are accepted (DNS-rebinding protection) unless
  ``DAEDELUS_ALLOWED_HOSTS`` lists more;
- cross-origin browser access is limited to the desktop shell and loopback origins unless
  ``DAEDELUS_CORS_ORIGINS`` lists more.

With ``DAEDELUS_API_TOKENS`` set (``token`` = all projects, ``token:prj_a|prj_b`` = only those
projects; comma-separated), every ``/api`` request except ``/api/health`` and the worker
protocol (which uses worker tokens) must present a token as ``Authorization: Bearer``, a
``token`` query parameter (for <img>/EventSource) or the ``dd_token`` cookie. Scoped tokens
can reach only their projects and cannot use cluster administration endpoints.
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
ADMIN_PATHS = re.compile(r"^/api/cluster/(status|audit)")
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


def _token_of(request: Request) -> str:
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return request.query_params.get("token") or request.cookies.get("dd_token") or ""


class AuthMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, grants: list[TokenGrant], audit=None):
        super().__init__(app)
        self.grants = grants
        self.audit = audit

    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        request.state.scopes = None  # all projects
        if not self.grants or not path.startswith("/api/") or path == "/api/health" or \
                WORKER_PATHS.match(path):
            return await call_next(request)
        tok = _token_of(request)
        grant = next((g for g in self.grants if tok and hmac.compare_digest(tok, g.token)), None)
        if grant is None:
            self._audit("api_auth_failed", request)
            return JSONResponse({"detail": "authentication required"}, status_code=401)
        if grant.projects is not None:
            m = PROJECT_RE.match(path)
            if ADMIN_PATHS.match(path) or (m and m.group(1) not in grant.projects) or \
                    (path == "/api/projects" and request.method != "GET"):
                self._audit("api_scope_denied", request)
                return JSONResponse({"detail": "token not valid for this resource"},
                                    status_code=403)
            request.state.scopes = grant.projects
        return await call_next(request)

    def _audit(self, action: str, request: Request) -> None:
        cl = self.audit() if callable(self.audit) else None
        if cl is not None:
            cl.audit.record(action, "denied", actor=request.client.host if request.client
                            else "?", target=f"{request.method} {request.url.path}")
