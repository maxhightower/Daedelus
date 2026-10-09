"""Redact the local profile path, account name and any API-key-like strings from text
evidence in place. Binary files (images, Office, .blend) are left untouched.

    python tools/native_validation/redact.py evidence/native_1
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path

TEXT = {".json", ".md", ".txt", ".xml", ".log", ".csv", ".html", ".py", ".ps1"}
KEYS = re.compile(r"(sk-ant-[A-Za-z0-9_\-]{8,}|AIza[0-9A-Za-z_\-]{20,}|ghp_[A-Za-z0-9]{20,})")


def rules() -> list[tuple[re.Pattern, str]]:
    prof = os.environ.get("USERPROFILE") or str(Path.home())
    user = os.environ.get("USERNAME") or Path(prof).name
    out = []
    for p in {prof, prof.replace("\\", "/"), prof.replace("\\", "\\\\")}:
        out.append((re.compile(re.escape(p), re.IGNORECASE), "%USERPROFILE%"))
    out.append((re.compile(rf"(?<![A-Za-z0-9]){re.escape(user)}(?![A-Za-z0-9])", re.IGNORECASE),
                "<user>"))
    out.append((KEYS, "<redacted-key>"))
    return out


def main(root: str) -> int:
    rs, changed = rules(), 0
    for f in Path(root).rglob("*"):
        if not f.is_file() or f.suffix.lower() not in TEXT:
            continue
        s = f.read_text(encoding="utf-8", errors="surrogateescape")
        t = s
        for pat, rep in rs:
            t = pat.sub(rep, t)
        if t != s:
            f.write_text(t, encoding="utf-8", errors="surrogateescape")
            changed += 1
            print("redacted", f)
    print(f"{changed} file(s) changed")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))
