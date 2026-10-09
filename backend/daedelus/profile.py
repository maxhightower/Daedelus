"""Deployment profile: ``development`` (default) or ``hosted`` (``DAEDELUS_PROFILE=hosted``).

The hosted profile turns on the settings an externally reachable deployment needs and refuses
the development conveniences that are unsafe there (V2.1, see SECURITY_MODEL.md):

* the control plane serves HTTPS itself or sits behind a TLS proxy on a non-public bind;
* workers must use per-worker credentials (join-token enrolment off unless explicitly allowed);
* workers verify the control plane's certificate and refuse plain HTTP;
* query-string API tokens and server-side path ingestion are refused;
* session cookies are ``Secure``;
* code jobs require a verified job sandbox (no network, private filesystem).
"""

from __future__ import annotations

import os


def name() -> str:
    return (os.environ.get("DAEDELUS_PROFILE") or "development").strip().lower()


def hosted() -> bool:
    return name() == "hosted"


def flag(var: str) -> bool:
    return (os.environ.get(var) or "").strip().lower() in ("1", "true", "yes", "on")
