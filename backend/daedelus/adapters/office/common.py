"""Shared helpers for the Office adapters (XLSX / DOCX / PPTX).

Identity: Office formats do not give every addressable element a stable id that survives
editing in other applications. Each artifact therefore keeps a small Daedelus sidecar,
``.daedelus/ids.json`` next to the native file, mapping component ids to *native* locators
(sheet titles + positions, table and defined names, chart anchors, Word bookmarks, slide ids
and shape ids). On every inspection the sidecar is reconciled with the file: elements that
disappeared are reported *missing*, elements matched more than once *ambiguous*, and
operations on them are refused instead of being applied to the wrong element.

LibreOffice (headless) is used, when present, only on *copies*: to recalculate formulas for
reporting, to check that another office suite opens the file, and to render previews. The
native file Daedelus writes is never round-tripped through LibreOffice.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import zipfile
from pathlib import Path
from typing import Any

SIDECAR = Path(".daedelus") / "ids.json"
SLUG_RE = re.compile(r"[^A-Za-z0-9_.-]+")


def slug(s: str, fallback: str = "item") -> str:
    out = SLUG_RE.sub("_", s.strip()).strip("_").lower()
    return out[:48] or fallback


def h(obj: Any) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, default=str).encode()).hexdigest()


def load_ids(native_dir: Path) -> dict[str, Any]:
    p = native_dir / SIDECAR
    if p.exists():
        return json.loads(p.read_text())
    return {"version": 1, "components": {}, "specs": {}}


def save_ids(native_dir: Path, data: dict[str, Any]) -> None:
    p = native_dir / SIDECAR
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, indent=1, sort_keys=True))


def unique_id(existing: dict[str, Any], base: str) -> str:
    cid, n = base, 2
    while cid in existing:
        cid, n = f"{base}_{n}", n + 1
    return cid


# ---------------------------------------------------------------------------- LibreOffice
def soffice() -> str | None:
    for name in ("soffice", "libreoffice"):
        p = shutil.which(name)
        if p:
            return p
    for cand in ("/usr/bin/soffice", "/Applications/LibreOffice.app/Contents/MacOS/soffice",
                 "C:/Program Files/LibreOffice/program/soffice.exe"):
        if Path(cand).exists():
            return cand
    return None


def lo_convert(src: Path, fmt: str, out_dir: Path, timeout: int = 180) -> Path | None:
    """Convert a *copy* of ``src`` with headless LibreOffice; returns the output or None."""
    exe = soffice()
    if exe is None:
        return None
    out_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="dd_lo_") as prof, \
            tempfile.TemporaryDirectory(prefix="dd_lo_in_") as tin:
        copy = Path(tin) / src.name
        shutil.copy2(src, copy)
        env = {**os.environ, "HOME": prof}
        cmd = [exe, f"-env:UserInstallation=file://{prof}", "--headless", "--norestore",
               "--convert-to", fmt, "--outdir", str(out_dir), str(copy)]
        try:
            subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, env=env)
        except (subprocess.TimeoutExpired, OSError):
            return None
    ext = fmt.split(":")[0]
    out = out_dir / f"{src.stem}.{ext}"
    return out if out.exists() and out.stat().st_size > 0 else None


def pdf_to_pngs(pdf: Path, out_dir: Path, prefix: str, max_pages: int = 12,
                width: int = 640) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    if shutil.which("pdftoppm"):
        subprocess.run(["pdftoppm", "-png", "-scale-to", str(width), "-l", str(max_pages),
                        str(pdf), str(out_dir / prefix)], capture_output=True, timeout=180)
        return sorted(out_dir.glob(f"{prefix}*.png"))
    try:
        import pypdfium2 as pdfium  # optional

        doc = pdfium.PdfDocument(str(pdf))
        outs = []
        for i in range(min(len(doc), max_pages)):
            page = doc[i]
            img = page.render(scale=width / page.get_width()).to_pil()
            p = out_dir / f"{prefix}-{i + 1}.png"
            img.save(p)
            outs.append(p)
        return outs
    except ImportError:
        return []


# ---------------------------------------------------------------------------- safety
UNSUPPORTED_PARTS = {
    "xlsx": [("xl/vbaProject.bin", "VBA macros"), ("xl/externalLinks/", "external workbook links"),
             ("xl/pivotTables/", "pivot tables"), ("xl/slicers/", "slicers"),
             ("xl/media/", "embedded images"), ("xl/embeddings/", "embedded objects")],
    "docx": [("word/vbaProject.bin", "VBA macros"), ("word/embeddings/", "embedded OLE objects"),
             ("customXml/", None)],
    "pptx": [("ppt/vbaProject.bin", "VBA macros"), ("ppt/embeddings/", "embedded OLE objects"),
             ("ppt/media/", None)],
}


def import_copy(src: Path, native_dir: Path, entry: str, params: dict[str, Any],
                default_root: str, default_name: str, suffixes: tuple[str, ...]) -> dict[str, Any]:
    """Copy an existing file in unchanged as the native entry; ids are assigned on inspect.

    The file is not rewritten here: unsupported parts it contains (macros, embeddings...) stay
    intact and make later edits refuse instead of silently dropping them."""
    src = Path(src)
    if src.suffix.lower() not in suffixes:
        raise ValueError(f"cannot import {src.suffix or 'a file without extension'}; "
                         f"expected {', '.join(suffixes)}")
    native_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, native_dir / entry)
    ids = {"version": 1, "root": params.get("root_id", default_root),
           "root_name": params.get("name", default_name), "components": {}, "specs": {},
           "imported_from": src.name}
    save_ids(native_dir, ids)
    return ids


def unsupported_features(path: Path, kind: str, own: set[str] | None = None) -> list[str]:
    """Parts the adapter cannot round-trip safely (and did not create itself)."""
    own = own or set()
    found = []
    try:
        with zipfile.ZipFile(path) as z:
            names = z.namelist()
    except zipfile.BadZipFile:
        return ["not a valid Office Open XML package"]
    for prefix, label in UNSUPPORTED_PARTS[kind]:
        if label is None:
            continue
        hits = [n for n in names if n.startswith(prefix) and n not in own]
        if hits:
            found.append(f"{label} ({len(hits)} part(s))")
    if kind == "xlsx":
        # charts are rebuilt from Daedelus specs on every save; foreign charts would be lost
        charts = [n for n in names if n.startswith("xl/charts/chart")]
        if charts and not own & {"__daedelus_charts__"}:
            found.append(f"charts not created by Daedelus ({len(charts)})")
    return found


def zip_part_hashes(path: Path, prefixes: tuple[str, ...]) -> dict[str, str]:
    out = {}
    with zipfile.ZipFile(path) as z:
        for n in sorted(z.namelist()):
            if n.startswith(prefixes):
                out[n] = hashlib.sha256(z.read(n)).hexdigest()[:16]
    return out
