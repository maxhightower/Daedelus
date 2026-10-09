"""Secret scrubbing for live-verification evidence (V2.1.1).

Everything a live run writes (results manifest, benchmark reports, connector reports, recorded
responses, logs) passes through :func:`scrub_text` before it lands in an evidence directory.
Two layers:

* the exact values of known secret environment variables (API keys, connector tokens), so a
  secret that leaks into an error message or a model response is removed whatever its format;
* patterns for credential shapes that may come from elsewhere: provider API keys, bearer and
  basic authorization headers, cookies, OAuth access/refresh tokens, JWTs, Daedelus worker
  credentials, and URL query parameters named like secrets.

Scrubbing is deliberately over-eager: a redacted harmless string is a far smaller problem than a
published credential.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

SECRET_ENV = ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "GEMINI_API_KEY", "GOOGLE_API_KEY",
              "DAEDELUS_GOOGLE_TOKEN", "DAEDELUS_MSGRAPH_TOKEN", "DAEDELUS_API_TOKENS",
              "DAEDELUS_WORKER_TOKENS", "DAEDELUS_WORKER_TOKEN", "DAEDELUS_WORKER_CREDENTIAL",
              "GITHUB_TOKEN", "GH_TOKEN", "ACTIONS_RUNTIME_TOKEN", "AWS_SECRET_ACCESS_KEY",
              "AWS_SESSION_TOKEN")
MASK = "[REDACTED]"

PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"sk-ant-[A-Za-z0-9_\-]{8,}"), MASK),                       # Anthropic keys
    (re.compile(r"AIza[0-9A-Za-z_\-]{20,}"), MASK),                          # Google API keys
    (re.compile(r"ya29\.[0-9A-Za-z_\-\.]{10,}"), MASK),                       # Google OAuth access
    (re.compile(r"1//[0-9A-Za-z_\-]{20,}"), MASK),                           # Google refresh
    (re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}"), MASK),                     # GitHub tokens
    (re.compile(r"\bddw1\.[A-Za-z0-9_]+\.[A-Za-z0-9_\-]{16,}"), MASK),       # worker credentials
    (re.compile(r"\beyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}"), MASK),  # JWT
    (re.compile(r"(?i)\b(authorization|proxy-authorization)\s*[:=]\s*[^\r\n,;\"']+"),
     r"\1: " + MASK),
    (re.compile(r"(?i)\b(bearer|basic)\s+[A-Za-z0-9_\-\.=:/+]{8,}"), r"\1 " + MASK),
    (re.compile(r"(?i)\b(x-api-key|x-goog-api-key|api[-_]?key)\b(\"?\s*[:=]\s*\"?)"
                r"[A-Za-z0-9_\-\.]{8,}"), r"\1\2" + MASK),
    (re.compile(r"(?i)\b(set-cookie|cookie)\s*:\s*[^\r\n]+"), r"\1: " + MASK),
    (re.compile(r"(?i)\b(dd_session)=[^;\s\"']+"), r"\1=" + MASK),
    (re.compile(r"(?i)([?&](?:key|token|access_token|refresh_token|api_key|sig|signature|"
                r"ticket|code)=)[^&\s\"'#]+"), r"\1" + MASK),
    (re.compile(r"(?i)\b(refresh_token|access_token|client_secret|id_token)(\"?\s*[:=]\s*\"?)"
                r"[A-Za-z0-9_\-\./+=]{8,}"), r"\1\2" + MASK),
]
TEXT_SUFFIXES = {".json", ".md", ".txt", ".log", ".xml", ".csv", ".yml", ".yaml", ".html"}


def _secret_values(env=None) -> list[str]:
    env = os.environ if env is None else env
    vals = []
    for k in SECRET_ENV:
        for v in (env.get(k) or "").split(","):
            v = v.strip()
            if len(v) >= 8:  # never scrub trivially short values (would mangle text)
                vals.append(v)
    return sorted(set(vals), key=len, reverse=True)


def scrub_text(text: str, env=None) -> str:
    for v in _secret_values(env):
        text = text.replace(v, MASK)
    for pat, rep in PATTERNS:
        text = pat.sub(rep, text)
    return text


def scrub_tree(root: Path, env=None) -> list[str]:
    """Scrub every text file under ``root`` in place; returns the files that changed."""
    changed = []
    for p in sorted(Path(root).rglob("*")):
        if not p.is_file() or p.suffix.lower() not in TEXT_SUFFIXES or p.is_symlink():
            continue
        try:
            old = p.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        new = scrub_text(old, env)
        if new != old:
            p.write_text(new, encoding="utf-8")
            changed.append(str(p.relative_to(root)))
    return changed
