"""Read-only extraction for office formats: spreadsheets (CSV, XLSX, ODS), documents (DOCX,
ODT) and presentations (PPTX, ODP).

Uses only the standard library (zip + XML). These sources can be previewed, bound and
used as directives; native editing of these formats is **not** provided in V1.
"""

from __future__ import annotations

import csv
import io
import re
import zipfile
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET

MAX_ROWS, MAX_COLS = 60, 30

NS = {
    "s": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
    "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
    "w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main",
    "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
    "table": "urn:oasis:names:tc:opendocument:xmlns:table:1.0",
    "text": "urn:oasis:names:tc:opendocument:xmlns:text:1.0",
    "office": "urn:oasis:names:tc:opendocument:xmlns:office:1.0",
    "draw": "urn:oasis:names:tc:opendocument:xmlns:drawing:1.0",
}


def _col_index(ref: str) -> int:
    letters = re.match(r"[A-Z]+", ref)
    n = 0
    for ch in (letters.group(0) if letters else "A"):
        n = n * 26 + (ord(ch) - 64)
    return n - 1


def read_spreadsheet(path: Path) -> dict[str, Any]:
    ext = path.suffix.lower()
    sheets: list[dict[str, Any]] = []
    if ext in (".csv", ".tsv"):
        text = path.read_text(encoding="utf-8", errors="replace")
        dialect = csv.excel_tab if ext == ".tsv" else csv.Sniffer().sniff(text[:4096]) \
            if text.strip() else csv.excel
        rows = list(csv.reader(io.StringIO(text), dialect))
        sheets.append({"name": path.stem, "rows": [r[:MAX_COLS] for r in rows[:MAX_ROWS]],
                       "row_count": len(rows),
                       "col_count": max((len(r) for r in rows), default=0)})
    elif ext in (".xlsx", ".xlsm"):
        with zipfile.ZipFile(path) as z:
            shared: list[str] = []
            if "xl/sharedStrings.xml" in z.namelist():
                root = ET.fromstring(z.read("xl/sharedStrings.xml"))
                for si in root.findall("s:si", NS):
                    shared.append("".join(t.text or "" for t in si.iter(f"{{{NS['s']}}}t")))
            wb = ET.fromstring(z.read("xl/workbook.xml"))
            rels = ET.fromstring(z.read("xl/_rels/workbook.xml.rels"))
            targets = {r.get("Id"): r.get("Target") for r in rels}
            sheets_el = wb.find("s:sheets", NS)
            for sh in (list(sheets_el) if sheets_el is not None else []):
                rid = sh.get(f"{{{NS['r']}}}id")
                target = targets.get(rid, "")
                target = target.lstrip("/")
                target = target if target.startswith("xl/") else f"xl/{target}"
                if target not in z.namelist():
                    continue
                root = ET.fromstring(z.read(target))
                rows: list[list[str]] = []
                for row in root.iter(f"{{{NS['s']}}}row"):
                    vals: dict[int, str] = {}
                    for c in row.findall("s:c", NS):
                        v = c.find("s:v", NS)
                        t = c.get("t")
                        if t == "inlineStr":
                            val = "".join(x.text or "" for x in c.iter(f"{{{NS['s']}}}t"))
                        elif v is None:
                            continue
                        elif t == "s":
                            val = shared[int(v.text or 0)]
                        else:
                            val = v.text or ""
                        vals[_col_index(c.get("r", "A1"))] = val
                    if vals:
                        width = min(max(vals) + 1, MAX_COLS)
                        rows.append([vals.get(i, "") for i in range(width)])
                sheets.append({"name": sh.get("name"), "rows": rows[:MAX_ROWS],
                               "row_count": len(rows),
                               "col_count": max((len(r) for r in rows), default=0)})
    elif ext == ".ods":
        root = _odf_content(path)
        for tbl in root.iter(f"{{{NS['table']}}}table"):
            rows = []
            for tr in tbl.iter(f"{{{NS['table']}}}table-row"):
                cells = []
                for tc in tr.findall("table:table-cell", NS):
                    rep = int(tc.get(f"{{{NS['table']}}}number-columns-repeated", "1"))
                    txt = " ".join("".join(p.itertext()) for p in tc.findall("text:p", NS))
                    cells.extend([txt] * min(rep, MAX_COLS))
                while cells and not cells[-1]:
                    cells.pop()
                if cells:
                    rows.append(cells[:MAX_COLS])
            sheets.append({"name": tbl.get(f"{{{NS['table']}}}name"), "rows": rows[:MAX_ROWS],
                           "row_count": len(rows),
                           "col_count": max((len(r) for r in rows), default=0)})
    else:
        raise ValueError(f"unsupported spreadsheet format {ext}")
    flat = "\n".join(",".join(r) for s in sheets for r in s["rows"])
    return {"format": ext[1:], "sheets": sheets, "sheet_names": [s["name"] for s in sheets],
            "text": flat[:50_000]}


def _odf_content(path: Path) -> ET.Element:
    with zipfile.ZipFile(path) as z:
        return ET.fromstring(z.read("content.xml"))


def read_document(path: Path) -> dict[str, Any]:
    ext = path.suffix.lower()
    paragraphs: list[dict[str, str]] = []
    if ext == ".docx":
        with zipfile.ZipFile(path) as z:
            root = ET.fromstring(z.read("word/document.xml"))
        for p in root.iter(f"{{{NS['w']}}}p"):
            style = p.find("w:pPr/w:pStyle", NS)
            txt = "".join(t.text or "" for t in p.iter(f"{{{NS['w']}}}t"))
            if txt.strip():
                paragraphs.append({"style": style.get(f"{{{NS['w']}}}val") if style is not None
                                   else "", "text": txt})
    elif ext == ".odt":
        root = _odf_content(path)
        for el in root.iter():
            if el.tag in (f"{{{NS['text']}}}p", f"{{{NS['text']}}}h"):
                txt = "".join(el.itertext())
                if txt.strip():
                    paragraphs.append({"style": "Heading" if el.tag.endswith("}h") else "",
                                       "text": txt})
    else:
        raise ValueError(f"unsupported document format {ext}")
    text = "\n".join(p["text"] for p in paragraphs)
    return {"format": ext[1:], "paragraphs": paragraphs[:400],
            "headings": [p["text"] for p in paragraphs if "eading" in (p["style"] or "")][:50],
            "text": text[:200_000]}


def read_presentation(path: Path) -> dict[str, Any]:
    ext = path.suffix.lower()
    slides: list[dict[str, Any]] = []
    if ext == ".pptx":
        with zipfile.ZipFile(path) as z:
            names = sorted((n for n in z.namelist() if re.fullmatch(r"ppt/slides/slide\d+\.xml", n)),
                           key=lambda n: int(re.findall(r"\d+", n)[-1]))
            for n in names:
                root = ET.fromstring(z.read(n))
                texts = [t.text for t in root.iter(f"{{{NS['a']}}}t") if t.text and t.text.strip()]
                slides.append({"index": len(slides) + 1, "title": texts[0] if texts else "",
                               "texts": texts[:40]})
    elif ext == ".odp":
        root = _odf_content(path)
        for page in root.iter(f"{{{NS['draw']}}}page"):
            texts = ["".join(p.itertext()) for p in page.iter(f"{{{NS['text']}}}p")]
            texts = [t for t in texts if t.strip()]
            slides.append({"index": len(slides) + 1, "title": texts[0] if texts else "",
                           "texts": texts[:40]})
    else:
        raise ValueError(f"unsupported presentation format {ext}")
    return {"format": ext[1:], "slides": slides, "slide_count": len(slides),
            "text": "\n".join(t for s in slides for t in s["texts"])[:100_000]}
