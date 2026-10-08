"""Deterministic Office authoring recipes for the heuristic provider (V1.2).

Selected by the node instructions and the target artifact's adapter:
- spreadsheet: "analysis workbook" - cleaned data sheet, derived column, decade summary with
  formulas, named ranges, an Excel table and two native charts;
- document: "report" - title, summary, data & method, results (text placeholder filled by a
  dependency), discussion, references;
- presentation: "deck" - theme from the bound design guide, title / data / findings / sources
  slides (data chart and findings text are filled by dependencies).

These are fixed templates parameterised by the *contents* of bound sources (rows of a data
file, paragraphs and headings of a research document, colours and fonts of a design guide).
They are labelled as deterministic recipes in every plan; they do not write analysis prose
beyond quoting and arranging the sources. Narrative generation needs a live model.
"""

from __future__ import annotations

import re
from typing import Any

from .. import features
from ..models import PlannedOperation

HEX_RE = re.compile(r"#[0-9a-fA-F]{6}")


def _num(v: Any) -> float | None:
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return float(v)
    try:
        return float(str(v).replace(",", "").strip())
    except ValueError:
        return None


def _entries(req, media: tuple[str, ...]) -> list[Any]:
    return [e for e in req.context.entries if e.applies and e.media_type in media]


def _extract(req, sid: str) -> dict[str, Any]:
    return (req.source_extracts or {}).get(sid, {})


def workbook(req) -> tuple[list[PlannedOperation], list[str]] | None:
    data = next((e for e in _entries(req, ("spreadsheet",))), None)
    if data is None:
        return None
    ex = _extract(req, data.binding.source_id)
    sheets = ex.get("sheets") or []
    if not sheets or len(sheets[0].get("rows") or []) < 3:
        return None
    rows = sheets[0]["rows"]
    if sheets[0].get("truncated"):
        return None  # never build an analysis from a silently truncated extract
    header = [str(h).strip() for h in rows[0]]
    # first column = period (year), first numeric column after it = value
    body = rows[1:]
    vcol = next((j for j in range(1, len(header)) if sum(_num(r[j]) is not None
                                                         for r in body if len(r) > j)
                 > len(body) * 0.8), None)
    if vcol is None:
        return None
    clean, dropped = [], 0
    for r in body:
        y = _num(r[0]) if r else None
        v = _num(r[vcol]) if len(r) > vcol else None
        if y is None or v is None or v < 0:
            dropped += 1
            continue
        clean.append([int(y), round(v, 3)])
    clean.sort()
    n = len(clean)
    last = n + 1
    vname = header[vcol] or "Value"
    for ln in req.instructions.splitlines():  # e.g. "Value label: CO2 (ppm)"
        if ln.lower().startswith("value label:"):
            vname = ln.split(":", 1)[1].strip() or vname
    wsheets = [c for c in req.artifact.components if c.kind == "sheet"]
    ops: list[PlannedOperation] = []
    if wsheets:  # reuse the (empty) first sheet of a new workbook
        dsid = wsheets[0].id
        ops.append(PlannedOperation(op="rename_sheet", component_id=dsid,
                                    params={"title": "Data"}))
    else:
        dsid = "data"
        ops.append(PlannedOperation(op="add_sheet", params={"id": dsid, "title": "Data"}))
    ops.append(PlannedOperation(op="add_sheet", params={"id": "summary", "title": "Summary"}))
    ops += [PlannedOperation(op="write_range", component_id=dsid, params={
        "start": "A1", "values": [[header[0] or "Year", vname, "Change"]] +
        [[y, v, f"=B{i + 2}-B{i + 1}" if i else None] for i, (y, v) in enumerate(clean)]},
        rationale=f"cleaned data from '{data.source_name}': {n} rows, {dropped} dropped "
                  "(missing/non-numeric/negative)"),
        PlannedOperation(op="format_range", component_id=dsid, params={
            "range": "A1:C1", "bold": True, "fill": "#dbe5f1", "column_widths": {"A": 10, "B": 14,
                                                                             "C": 12}}),
        PlannedOperation(op="format_range", component_id=dsid, params={
            "range": f"B2:C{last}", "number_format": "0.00"}),
        PlannedOperation(op="define_name", params={"id": "dataset", "name": "Dataset",
                                                   "sheet": dsid, "ref": f"A1:C{last}"}),
        PlannedOperation(op="add_table", params={"id": "data_table", "name": "DataTable",
                                                 "sheet": dsid, "ref": f"A1:C{last}"}),
        PlannedOperation(op="add_chart", params={
            "id": "trend_chart", "sheet": dsid, "type": "line", "data": f"B1:B{last}",
            "categories": f"A2:A{last}", "anchor": "E2", "title": f"{vname} by year",
            "x_title": header[0] or "Year", "y_title": vname})]
    decades = sorted({(y // 10) * 10 for y, _ in clean})
    summary = [["Decade", f"Mean {vname}", "Change vs previous decade"]]
    for k, d in enumerate(decades):
        mean = (f"=AVERAGEIFS(Data!$B$2:$B${last},Data!$A$2:$A${last},\">={d}\","
                f"Data!$A$2:$A${last},\"<{d + 10}\")")
        summary.append([f"{d}s", mean, f"=B{k + 2}-B{k + 1}" if k else None])
    srow = len(summary)
    ops += [
        PlannedOperation(op="write_range", component_id="summary", params={
            "start": "A1", "values": summary}, rationale="decade means computed by formulas"),
        PlannedOperation(op="format_range", component_id="summary", params={
            "range": "A1:C1", "bold": True, "fill": "#dbe5f1",
            "column_widths": {"A": 10, "B": 16, "C": 24}}),
        PlannedOperation(op="format_range", component_id="summary", params={
            "range": f"B2:C{srow}", "number_format": "0.00"}),
        PlannedOperation(op="define_name", params={"id": "summary_values", "name": "SummaryValues",
                                                   "sheet": "summary", "ref": f"A1:C{srow}"}),
        PlannedOperation(op="define_name", params={"id": "decade_means", "name": "DecadeMeans",
                                                   "sheet": "summary", "ref": f"A1:B{srow}"}),
        PlannedOperation(op="add_chart", params={
            "id": "decade_chart", "sheet": "summary", "type": "column", "data": f"B1:B{srow}",
            "categories": f"A2:A{srow}", "anchor": "E2", "title": f"Mean {vname} by decade",
            "y_title": vname}),
    ]
    return ops, ["deterministic recipe 'analysis workbook' (heuristic provider): cleaning, "
                 "formulas and charts follow a fixed template parameterised by the bound data",
                 f"value column: '{vname}', {n} clean rows, {len(decades)} decades"]


def _paragraphs(ex: dict[str, Any]) -> list[tuple[str | None, str]]:
    """(section heading, paragraph) pairs from extracted text."""
    text = ex.get("text") or "\n\n".join(p.get("text", "") for p in ex.get("pages", []))
    out, sec = [], None
    for block in re.split(r"\n\s*\n", text):
        b = block.strip()
        if not b:
            continue
        if b.startswith("#"):
            lines = b.splitlines()
            sec = lines[0].lstrip("#").strip()
            rest = " ".join(x.strip() for x in lines[1:] if x.strip())
            if rest:
                out.append((sec, rest))
            continue
        out.append((sec, " ".join(x.strip() for x in b.splitlines())))
    return out


def report(req) -> tuple[list[PlannedOperation], list[str]] | None:
    docs = _entries(req, ("text", "pdf", "document"))
    research = next((e for e in docs if e.binding.role in ("reference", "context",
                                                          "evaluation", "guideline")), None)
    if research is None:
        return None
    paras = _paragraphs(_extract(req, research.binding.source_id))
    if not paras:
        return None

    def pick(*names):
        return [p for s, p in paras if s and any(n in s.lower() for n in names)]

    intro = pick("summary", "abstract", "introduction") or [paras[0][1]]
    disc = pick("discussion", "finding", "conclusion") or [p for _, p in paras[1:3]]
    method = pick("method", "data") or []
    sources = [e for e in req.context.entries if e.relation != "descendant"]
    root = req.component.id if req.component else req.artifact.root_id()
    ops = [
        PlannedOperation(op="add_heading", params={"id": "summary", "text": "Summary",
                                                   "level": 1, "parent": root}),
        PlannedOperation(op="add_paragraph", params={
            "id": "summary_text", "parent": "summary",
            "text": intro[0]}, rationale=f"quoted from '{research.source_name}'"),
        PlannedOperation(op="add_heading", params={"id": "data_method", "text": "Data and method",
                                                   "level": 1}),
        PlannedOperation(op="add_paragraph", params={
            "id": "data_method_text", "parent": "data_method",
            "text": (method[0] + " " if method else "") +
            "All figures in this report are computed in the linked analysis workbook; "
            "tables and charts update when the workbook changes."}),
        PlannedOperation(op="add_heading", params={"id": "results", "text": "Results",
                                                   "level": 1}),
        PlannedOperation(op="add_paragraph", params={
            "id": "results_text", "parent": "results",
            "text": "(filled from the workbook by a dependency)"}),
        PlannedOperation(op="add_heading", params={"id": "discussion", "text": "Discussion",
                                                   "level": 1}),
    ] + [PlannedOperation(op="add_paragraph", params={"id": f"discussion_p{i + 1}",
                                                      "parent": "discussion", "text": p},
                          rationale=f"quoted from '{research.source_name}'")
         for i, p in enumerate(disc[:3])] + [
        PlannedOperation(op="add_heading", params={"id": "references", "text": "References",
                                                   "level": 1}),
    ] + [PlannedOperation(op="add_paragraph", params={
        "id": f"ref_{i + 1}", "parent": "references", "style": "List Number",
        "text": f"{e.source_name} ({e.media_type}; used as {e.binding.role})"})
        for i, e in enumerate(sources)]
    return ops, ["deterministic recipe 'report' (heuristic provider): sections are arranged "
                 "from quoted source paragraphs; no prose is generated"]


def _guide(req) -> dict[str, Any]:
    theme: dict[str, Any] = {}
    for e in req.context.entries:
        if not e.applies or e.binding.role not in ("guideline", "reference", "inspiration",
                                                   "constraint"):
            continue
        ex = _extract(req, e.binding.source_id)
        text = ex.get("text") or ""
        for line in text.splitlines():
            low = line.lower()
            m = HEX_RE.search(line)
            if m:
                c = m.group(0).lower()
                if "background" in low:
                    theme.setdefault("background", c)
                elif "title" in low or "primary" in low or "heading" in low:
                    theme.setdefault("title_color", c)
                elif "accent" in low or "highlight" in low or "chart" in low:
                    theme.setdefault("accent", c)
                elif "body" in low or "text" in low:
                    theme.setdefault("body_color", c)
            fm = re.search(r"font[^:]*:\s*([A-Za-z][A-Za-z0-9 ]+)", line, re.I)
            if fm:
                key = "title_font" if "title" in low or "heading" in low else "body_font"
                theme.setdefault(key, fm.group(1).strip())
        if e.media_type == "image" and "accent" not in theme:
            pal = (e.summary or {}).get("palette") or []
            sat = [p for p in pal if isinstance(p, str)]
            if sat:
                theme["accent"] = max(sat, key=lambda c: features.color_distance(c, "#808080"))
    return theme


def deck(req) -> tuple[list[PlannedOperation], list[str]] | None:
    theme = _guide(req)
    title = next((ln.split(":", 1)[1].strip() for ln in req.instructions.splitlines()
                  if ln.lower().startswith("title:")), req.artifact.name)
    root = req.component.id if req.component else req.artifact.root_id()
    sources = [e for e in req.context.entries if e.relation != "descendant"]
    ops = []
    if theme:
        ops.append(PlannedOperation(op="apply_theme", component_id=root, params=theme,
                                    rationale="theme from the bound design guide"))
    ops += [
        PlannedOperation(op="add_slide", params={"id": "title_slide", "layout": "title",
                                                 "title": title,
                                                 "subtitle": "Research analysis"}),
        PlannedOperation(op="add_slide", params={"id": "data_slide", "layout": "title_only",
                                                 "title": "What the data shows"}),
        PlannedOperation(op="add_slide", params={"id": "findings", "layout": "title_content",
                                                 "title": "Key findings"}),
        PlannedOperation(op="add_slide", params={"id": "sources_slide", "layout": "title_only",
                                                 "title": "Sources"}),
        PlannedOperation(op="add_textbox", params={
            "id": "sources_list", "slide": "sources_slide", "box": [0.08, 0.25, 0.84, 0.6],
            "paragraphs": [f"{e.source_name} — {e.binding.role}" for e in sources] or ["—"],
            "size_pt": 16}),
        PlannedOperation(op="add_shape", params={
            "id": "accent_bar", "slide": "title_slide", "box": [0.0, 0.92, 1.0, 0.04],
            "shape": "rectangle", **({"fill": theme["accent"]} if theme.get("accent") else {})}),
    ]
    return ops, ["deterministic recipe 'deck' (heuristic provider): fixed slide structure; "
                 f"theme from the design guide: {theme or 'none found'}"]


def plan_office(req) -> tuple[list[PlannedOperation], list[str], str] | None:
    text = req.instructions.lower()
    sub = [c for c in req.artifact.components
           if c.parent_id == (req.component.id if req.component else req.artifact.root_id())]
    ad = req.adapter.name
    if ad == "spreadsheet" and re.search(r"workbook|spreadsheet|analys", text) and not any(
            c.kind in ("table", "chart", "range") for c in req.artifact.components):
        r = workbook(req)
        return (r[0], r[1], "analysis workbook") if r else None
    if ad == "document" and "report" in text and not any(c.id == "summary" for c in sub):
        r = report(req)
        return (r[0], r[1], "report") if r else None
    if ad == "presentation" and re.search(r"deck|presentation|slides", text) and \
            not any(c.kind == "slide" for c in sub):
        r = deck(req)
        return (r[0], r[1], "deck") if r else None
    return None
