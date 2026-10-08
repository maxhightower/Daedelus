"""Spreadsheet adapter (.xlsx via openpyxl).

Components: workbook -> worksheets -> tables / named ranges / charts. Cells are addressed by
operation parameters inside a component (a write to a named range or table is refused if it
falls outside that range).

Formulas: openpyxl writes formulas but never calculates them. After every change the adapter
asks LibreOffice (headless, on a *copy*) to recalculate and stores the resulting values in
the sidecar, keyed by the file's hash. Measurements and previews show those values marked
``calculated``; if LibreOffice is unavailable the values are reported as *not calculated*
(never guessed). The native file is saved with "recalculate on open" so Excel/LibreOffice
compute fresh values when a person opens it.

Charts: openpyxl does not load existing charts, so Daedelus keeps a spec for every chart it
creates and rebuilds them on each save. Workbooks with charts, macros, pivot tables, external
links or images that Daedelus did not create are refused for editing (they would be lost).
"""

from __future__ import annotations

import csv
import io
import shutil
import tempfile
from pathlib import Path
from typing import Any

from ...models import Component, PlannedOperation, ValidationReport
from ..base import Adapter, AdapterError, AdapterInfo, ApplyResult, InspectResult, OperationSpec
from . import common as oc

ENTRY = "workbook.xlsx"
ERRORS = ("#REF!", "#DIV/0!", "#NAME?", "#VALUE!", "#N/A", "#NUM!", "#NULL!", "Err:")
CELL = {"type": "string", "pattern": "[A-Za-z]{1,3}[0-9]{1,7}"}
REF = {"type": "string", "pattern": r"\$?[A-Za-z]{1,3}\$?[0-9]{1,7}(:\$?[A-Za-z]{1,3}\$?[0-9]{1,7})?"}
HEX = {"type": "string", "pattern": "#[0-9a-fA-F]{6}"}
SCALAR = {}  # any JSON scalar (string, number, boolean, null); strings starting with '=' are formulas


def _openpyxl():
    try:
        import openpyxl  # noqa: F401
    except ImportError as exc:  # pragma: no cover - dependency is in the base install
        raise AdapterError("openpyxl is not installed") from exc
    import openpyxl

    return openpyxl


def _bounds(ref: str):
    from openpyxl.utils.cell import range_boundaries

    c1, r1, c2, r2 = range_boundaries(ref.replace("$", ""))
    return c1, r1, c2 or c1, r2 or r1


def _inside(ref: str, outer: str) -> bool:
    a, b = _bounds(ref), _bounds(outer)
    return a[0] >= b[0] and a[1] >= b[1] and a[2] <= b[2] and a[3] <= b[3]


def _cells(ws, ref: str):
    c1, r1, c2, r2 = _bounds(ref)
    for row in ws.iter_rows(min_row=r1, max_row=r2, min_col=c1, max_col=c2):
        yield from row


def _cell_sig(c) -> list[Any]:
    f = c.font
    return [c.coordinate, c.value if not hasattr(c.value, "isoformat") else c.value.isoformat(),
            c.number_format, bool(f.b), bool(f.i),
            (c.fill.fgColor.rgb if c.fill and c.fill.fill_type else None)]


class SpreadsheetAdapter(Adapter):
    name = "spreadsheet"
    version = "1"

    def check_environment(self) -> tuple[bool, str]:
        try:
            _openpyxl()
        except AdapterError as exc:
            return False, str(exc)
        lo = oc.soffice()
        return True, f"openpyxl; LibreOffice {'at ' + lo if lo else 'not found (no recalc/preview)'}"

    def info(self) -> AdapterInfo:
        ok, detail = self.check_environment()
        sheet_kinds = ["sheet"]
        data_kinds = ["sheet", "range", "table"]
        fmt_props = {"bold": {"type": "boolean"}, "italic": {"type": "boolean"},
                     "number_format": {"type": "string"}, "fill": HEX, "font_color": HEX,
                     "align": {"type": "string", "enum": ["left", "center", "right"]}}
        return AdapterInfo(
            name=self.name, version=self.version,
            description="Excel workbooks (.xlsx) edited with openpyxl; formulas recalculated by "
                        "headless LibreOffice on a copy for reporting.",
            artifact_types=["spreadsheet"], native_formats=[".xlsx"],
            preview_formats=["png (LibreOffice render)", "cell grid (JSON)"],
            export_formats=["xlsx", "csv", "pdf"],
            scopes=["workbook", "sheet", "table", "range", "chart"],
            measurements=["rows", "columns", "formula_count", "cell_count", "sum", "mean",
                          "error_count", "calculated"],
            validation=["file_reopens", "formulas_preserved", "libreoffice_opens",
                        "recalculated", "component_preservation"],
            environment={"requires": "openpyxl (LibreOffice optional for recalculation)",
                         "libreoffice": oc.soffice()},
            available=ok, unavailable_reason=None if ok else detail, templates=["blank", "data", "import"],
            operations=[
                OperationSpec(name="add_sheet", family="create", aspects=["structure"],
                              target_kinds=["new"], description="Add a worksheet (idempotent by id).",
                              params_schema={"type": "object", "required": ["id", "title"],
                                             "additionalProperties": False, "properties": {
                                                 "id": {"type": "string", "pattern": "[A-Za-z0-9_.-]+"},
                                                 "title": {"type": "string"},
                                                 "index": {"type": "integer", "minimum": 0}}}),
                OperationSpec(name="rename_sheet", family="structure", aspects=["structure"],
                              target_kinds=sheet_kinds, description="Rename a worksheet (formulas and "
                              "names referring to it are updated by the adapter).",
                              params_schema={"type": "object", "required": ["title"],
                                             "additionalProperties": False,
                                             "properties": {"title": {"type": "string"}}}),
                OperationSpec(name="set_cells", family="data", aspects=["data", "content"],
                              target_kinds=data_kinds,
                              description="Set individual cells; strings starting with '=' are "
                              "formulas. Cells must lie inside the target range/table.",
                              params_schema={"type": "object", "required": ["cells"],
                                             "additionalProperties": False, "properties": {
                                                 "cells": {"type": "object"}}}),
                OperationSpec(name="write_range", family="data", aspects=["data", "content"],
                              target_kinds=data_kinds,
                              description="Write a 2-D block of values/formulas starting at 'start' "
                              "(default: the top-left of the target range/table).",
                              params_schema={"type": "object", "required": ["values"],
                                             "additionalProperties": False, "properties": {
                                                 "start": CELL,
                                                 "values": {"type": "array", "items": {
                                                     "type": "array"}}}}),
                OperationSpec(name="format_range", family="style", aspects=["style", "format"],
                              target_kinds=data_kinds, description="Cell formatting.",
                              params_schema={"type": "object", "additionalProperties": False,
                                             "properties": {"range": REF, **fmt_props,
                                                            "column_widths": {"type": "object"}}}),
                OperationSpec(name="add_table", family="create", aspects=["structure", "data"],
                              target_kinds=["new"], description="Turn a range into an Excel table.",
                              params_schema={"type": "object", "required": ["id", "sheet", "ref"],
                                             "additionalProperties": False, "properties": {
                                                 "id": {"type": "string", "pattern": "[A-Za-z0-9_.-]+"},
                                                 "sheet": {"type": "string"}, "ref": REF,
                                                 "name": {"type": "string"},
                                                 "style": {"type": "string"},
                                                 "parent": {"type": "string"}}}),
                OperationSpec(name="define_name", family="create", aspects=["structure", "data"],
                              target_kinds=["new"], description="Create a workbook defined name "
                              "(named range).",
                              params_schema={"type": "object", "required": ["id", "sheet", "ref"],
                                             "additionalProperties": False, "properties": {
                                                 "id": {"type": "string", "pattern": "[A-Za-z0-9_.-]+"},
                                                 "name": {"type": "string"},
                                                 "sheet": {"type": "string"}, "ref": REF,
                                                 "parent": {"type": "string"}}}),
                OperationSpec(name="set_name_ref", family="structure", aspects=["data"],
                              target_kinds=["range"], description="Move a named range to a new "
                              "reference on its sheet.",
                              params_schema={"type": "object", "required": ["ref"],
                                             "additionalProperties": False,
                                             "properties": {"ref": REF}}),
                OperationSpec(name="add_chart", family="create", aspects=["chart", "data", "style"],
                              target_kinds=["new"],
                              description="Add a native chart fed by a range (data series in "
                              "columns, header row as series names) and an optional category range.",
                              params_schema={"type": "object",
                                             "required": ["id", "sheet", "data", "anchor"],
                                             "additionalProperties": False, "properties": {
                                                 "id": {"type": "string", "pattern": "[A-Za-z0-9_.-]+"},
                                                 "sheet": {"type": "string"},
                                                 "type": {"type": "string", "enum": [
                                                     "bar", "column", "line", "pie", "scatter"]},
                                                 "data": REF, "categories": REF, "anchor": CELL,
                                                 "title": {"type": "string"},
                                                 "x_title": {"type": "string"},
                                                 "y_title": {"type": "string"},
                                                 "width_cm": {"type": "number", "minimum": 4, "maximum": 40},
                                                 "height_cm": {"type": "number", "minimum": 3, "maximum": 30},
                                                 "parent": {"type": "string"}}}),
                OperationSpec(name="update_chart", family="chart", aspects=["chart", "style"],
                              target_kinds=["chart"], description="Change a chart's type, title, "
                              "data or categories.",
                              params_schema={"type": "object", "additionalProperties": False,
                                             "properties": {
                                                 "type": {"type": "string", "enum": [
                                                     "bar", "column", "line", "pie", "scatter"]},
                                                 "data": REF, "categories": REF,
                                                 "title": {"type": "string"},
                                                 "x_title": {"type": "string"},
                                                 "y_title": {"type": "string"}}}),
                OperationSpec(name="insert_rows", family="structure", aspects=["structure"],
                              target_kinds=sheet_kinds,
                              description="Insert rows. Refused when formulas, names, tables or "
                              "charts refer to rows at or below the insertion point (openpyxl "
                              "does not rewrite references).",
                              params_schema={"type": "object", "required": ["index"],
                                             "additionalProperties": False, "properties": {
                                                 "index": {"type": "integer", "minimum": 1},
                                                 "amount": {"type": "integer", "minimum": 1,
                                                            "maximum": 1000}}}),
                OperationSpec(name="delete_component", family="structure", aspects=["structure"],
                              target_kinds=["sheet", "table", "range", "chart"],
                              description="Remove a sheet, table definition, defined name or chart.",
                              params_schema={"type": "object", "additionalProperties": False,
                                             "properties": {}}),
            ])

    def side_effect_scope(self, native_dir: Path, entry: str, op: PlannedOperation) -> list[str]:
        if op.op != "rename_sheet" or not op.component_id:
            return []
        ids = oc.load_ids(native_dir)
        old = self._title_of(ids, op.component_id)
        title_to_id = {e.get("title"): cid for cid, e in ids.get("components", {}).items()
                       if e.get("kind") == "sheet"}
        wb = self._load(native_dir / entry)
        out = []
        for ws in wb.worksheets:
            if any(isinstance(c.value, str) and c.value.startswith("=") and f"{old}!" in c.value
                   for row in ws.iter_rows() for c in row) and ws.title in title_to_id:
                out.append(title_to_id[ws.title])
        return out

    def created_kind(self, op: PlannedOperation) -> str:
        return {"add_sheet": "sheet", "add_table": "table", "define_name": "range",
                "add_chart": "chart"}.get(op.op, "*")

    # ------------------------------------------------------------------ io
    def _load(self, path: Path, data_only: bool = False):
        op = _openpyxl()
        try:
            return op.load_workbook(path, data_only=data_only)
        except Exception as exc:
            raise AdapterError(f"cannot open workbook: {type(exc).__name__}: {exc}") from exc

    def _save(self, wb, native_dir: Path, entry: str, ids: dict[str, Any]) -> None:
        from openpyxl.workbook.properties import CalcProperties

        self._rebuild_charts(wb, ids)
        wb.calculation = CalcProperties(fullCalcOnLoad=True)
        wb.save(native_dir / entry)
        ids["file_hash"] = oc.h((native_dir / entry).read_bytes().hex())
        self._recalc(native_dir, entry, ids)
        oc.save_ids(native_dir, ids)

    def _recalc(self, native_dir: Path, entry: str, ids: dict[str, Any]) -> None:
        """Formula values via LibreOffice on a copy (never written back into the native file)."""
        ids["calc"] = {"file_hash": ids.get("file_hash"), "available": False, "values": {}}
        if oc.soffice() is None:
            ids["calc"]["reason"] = "LibreOffice not available: formula values not calculated"
            return
        with tempfile.TemporaryDirectory(prefix="dd_recalc_") as td:
            out = oc.lo_convert(native_dir / entry, "xlsx:Calc MS Excel 2007 XML", Path(td))
            if out is None:
                ids["calc"]["reason"] = "LibreOffice could not recalculate a copy"
                return
            wb = self._load(out, data_only=True)
            src = self._load(native_dir / entry)
            vals: dict[str, Any] = {}
            for ws in src.worksheets:
                if ws.title not in wb.sheetnames:
                    continue
                cws = wb[ws.title]
                for row in ws.iter_rows():
                    for c in row:
                        if isinstance(c.value, str) and c.value.startswith("="):
                            v = cws[c.coordinate].value
                            vals[f"{ws.title}!{c.coordinate}"] = \
                                v if not hasattr(v, "isoformat") else v.isoformat()
            ids["calc"].update(available=True, values=vals, engine="LibreOffice (headless)")

    def _rebuild_charts(self, wb, ids: dict[str, Any]) -> None:
        from openpyxl.chart import BarChart, LineChart, PieChart, Reference, ScatterChart, Series

        for cid, spec in ids.get("specs", {}).items():
            if spec.get("kind") != "chart":
                continue
            sheet = self._title_of(ids, spec["sheet"])
            if sheet not in wb.sheetnames:
                continue
            ws = wb[sheet]
            t = spec.get("type", "column")
            ch = {"bar": BarChart, "column": BarChart, "line": LineChart, "pie": PieChart,
                  "scatter": ScatterChart}[t]()
            if t in ("bar", "column"):
                ch.type = "bar" if t == "bar" else "col"
            c1, r1, c2, r2 = _bounds(self._ref_of(wb, ids, spec["data"]))
            if t == "scatter":
                xs = Reference(ws, *_xy(self._ref_of(wb, ids, spec.get("categories") or
                                                     spec["data"])))
                for col in range(c1, c2 + 1):
                    ys = Reference(ws, min_col=col, min_row=r1, max_row=r2)
                    s = Series(ys, xs, title_from_data=True)
                    ch.series.append(s)
            else:
                ch.add_data(Reference(ws, min_col=c1, min_row=r1, max_col=c2, max_row=r2),
                            titles_from_data=True)
                if spec.get("categories"):
                    k1, kr1, k2, kr2 = _bounds(self._ref_of(wb, ids, spec["categories"]))
                    ch.set_categories(Reference(ws, min_col=k1, min_row=kr1, max_col=k2,
                                                max_row=kr2))
            if spec.get("title"):
                ch.title = spec["title"]
            if t != "pie":
                if spec.get("x_title"):
                    ch.x_axis.title = spec["x_title"]
                if spec.get("y_title"):
                    ch.y_axis.title = spec["y_title"]
            ch.width = float(spec.get("width_cm", 16))
            ch.height = float(spec.get("height_cm", 8))
            ws.add_chart(ch, spec["anchor"])

    # ------------------------------------------------------------------ identity
    @staticmethod
    def _title_of(ids: dict[str, Any], sheet: str) -> str:
        e = ids["components"].get(sheet)
        return e["title"] if e and e.get("kind") == "sheet" else sheet

    def _ref_of(self, wb, ids: dict[str, Any], ref: str) -> str:
        """A cell range, or the id/name of a defined name -> its A1 range on its sheet."""
        e = ids["components"].get(ref)
        name = e["name"] if e and e.get("kind") == "range" else ref
        if name in wb.defined_names:
            dest = list(wb.defined_names[name].destinations)
            if dest:
                return dest[0][1].replace("$", "")
        return ref.split("!")[-1].replace("$", "")

    def _reconcile(self, wb, ids: dict[str, Any]) -> tuple[list[Component], dict[str, Any],
                                                         list[str]]:
        """Match sidecar ids to the workbook. Returns components, locators and issues."""
        comps = ids.setdefault("components", {})
        issues: list[str] = []
        root = ids.get("root", "workbook")
        out = [Component(id=root, name=ids.get("root_name", "Workbook"), kind="workbook",
                         native_ref=".")]
        loc: dict[str, Any] = {root: {"kind": "workbook"}}
        claimed = set()
        for cid, e in sorted(comps.items()):
            if e["kind"] != "sheet":
                continue
            if e["title"] in wb.sheetnames:
                claimed.add(e["title"])
                loc[cid] = {"kind": "sheet", "title": e["title"]}
            else:
                issues.append(f"sheet component '{cid}' (was '{e['title']}') is missing")
        for i, title in enumerate(wb.sheetnames):
            if title in claimed:
                continue
            cid = oc.unique_id({**comps, **loc}, "sheet_" + oc.slug(title))
            loc[cid] = {"kind": "sheet", "title": title, "unregistered": True}
        by_title = {v["title"]: k for k, v in loc.items() if v.get("kind") == "sheet"}
        for title in wb.sheetnames:
            cid = by_title[title]
            out.append(Component(id=cid, name=title, kind="sheet", parent_id=root,
                                 native_ref=title))
        # tables
        tnames = {}
        for ws in wb.worksheets:
            for tname, tbl in ws.tables.items():
                ref = tbl.ref if hasattr(tbl, "ref") else tbl
                tnames[tname] = (ws.title, ref)
        for cid, e in sorted(comps.items()):
            if e["kind"] == "table":
                if e["name"] in tnames:
                    loc[cid] = {"kind": "table", "name": e["name"], "title": tnames[e["name"]][0],
                                "ref": tnames[e["name"]][1]}
                else:
                    issues.append(f"table component '{cid}' ({e['name']}) is missing")
        known_t = {v["name"] for v in loc.values() if v.get("kind") == "table"}
        for tname, (title, ref) in tnames.items():
            if tname not in known_t:
                loc[oc.unique_id({**comps, **loc}, "table_" + oc.slug(tname))] = {
                    "kind": "table", "name": tname, "title": title, "ref": ref,
                    "unregistered": True}
        # defined names
        dn = {}
        for name, d in wb.defined_names.items():
            dests = list(d.destinations)
            if len(dests) == 1:
                dn[name] = (dests[0][0], dests[0][1].replace("$", ""))
        for cid, e in sorted(comps.items()):
            if e["kind"] == "range":
                if e["name"] in dn:
                    loc[cid] = {"kind": "range", "name": e["name"], "title": dn[e["name"]][0],
                                "ref": dn[e["name"]][1]}
                else:
                    issues.append(f"named range component '{cid}' ({e['name']}) is missing")
        known_n = {v["name"] for v in loc.values() if v.get("kind") == "range"}
        for name, (title, ref) in dn.items():
            if name not in known_n:
                loc[oc.unique_id({**comps, **loc}, "range_" + oc.slug(name))] = {
                    "kind": "range", "name": name, "title": title, "ref": ref,
                    "unregistered": True}
        # charts (Daedelus specs)
        for cid, spec in ids.get("specs", {}).items():
            if spec.get("kind") == "chart":
                title = self._title_of(ids, spec["sheet"])
                if title in wb.sheetnames:
                    loc[cid] = {"kind": "chart", "title": title, "spec": spec}
                else:
                    issues.append(f"chart '{cid}' lost its sheet")
        for cid, v in loc.items():
            if v["kind"] in ("table", "range", "chart"):
                parent = by_title.get(v["title"], root)
                label = v.get("name") or (v.get("spec") or {}).get("title") or cid
                out.append(Component(id=cid, name=label, kind=v["kind"], parent_id=parent,
                                     native_ref=f"{v['title']}!{v.get('ref', '')}".rstrip("!"),
                                     metadata={k: x for k, x in v.items() if k != "spec"}))
        return out, loc, issues

    # ------------------------------------------------------------------ lifecycle
    def create(self, native_dir: Path, template: str, params: dict[str, Any]) -> str:
        if template == "import":
            try:
                ids = oc.import_copy(params["path"], native_dir, ENTRY, params, "workbook",
                                     "Workbook", (".xlsx",))
            except (KeyError, ValueError) as exc:
                raise AdapterError(f"import: {exc}") from exc
            wb = self._load(native_dir / ENTRY)
            self._reconcile(wb, ids)
            ids["file_hash"] = oc.h((native_dir / ENTRY).read_bytes().hex())
            self._recalc(native_dir, ENTRY, ids)
            oc.save_ids(native_dir, ids)
            return ENTRY
        op = _openpyxl()
        native_dir.mkdir(parents=True, exist_ok=True)
        wb = op.Workbook()
        ws = wb.active
        ids = {"version": 1, "root": params.get("root_id", "workbook"),
               "root_name": params.get("name", "Workbook"), "components": {}, "specs": {}}
        sheets = params.get("sheets") or [{"id": "sheet1", "title": "Sheet1"}]
        ws.title = sheets[0]["title"]
        ids["components"][sheets[0]["id"]] = {"kind": "sheet", "title": sheets[0]["title"]}
        for sp in sheets[1:]:
            wb.create_sheet(sp["title"])
            ids["components"][sp["id"]] = {"kind": "sheet", "title": sp["title"]}
        if template == "data":
            for sp in sheets:
                rows = sp.get("rows") or []
                for r in rows:
                    wb[sp["title"]].append(r)
        elif template != "blank":
            raise AdapterError(f"unknown spreadsheet template: {template}")
        self._save(wb, native_dir, ENTRY, ids)
        return ENTRY

    def inspect(self, native_dir: Path, entry: str) -> InspectResult:
        wb = self._load(native_dir / entry)
        ids = oc.load_ids(native_dir)
        comps, loc, issues = self._reconcile(wb, ids)
        calc = ids.get("calc") or {}
        file_hash = oc.h((native_dir / entry).read_bytes().hex())
        calc_ok = calc.get("available") and calc.get("file_hash") == file_hash
        values = calc.get("values", {}) if calc_ok else {}
        covered: dict[str, set[str]] = {}
        for v in loc.values():
            if v["kind"] in ("table", "range"):
                ws = wb[v["title"]]
                covered.setdefault(v["title"], set()).update(
                    c.coordinate for c in _cells(ws, v["ref"]))
        states, meas, props = {}, {}, {}

        def value_of(ws, c):
            if isinstance(c.value, str) and c.value.startswith("="):
                return values.get(f"{ws.title}!{c.coordinate}")
            return c.value

        def summarize(ws, cells) -> dict[str, Any]:
            cells = list(cells)
            nums, errs, formulas = [], 0, 0
            for c in cells:
                if isinstance(c.value, str) and c.value.startswith("="):
                    formulas += 1
                v = value_of(ws, c)
                if isinstance(v, (int, float)) and not isinstance(v, bool):
                    nums.append(float(v))
                elif isinstance(v, str) and v.startswith(ERRORS):
                    errs += 1
            return {"cell_count": sum(1 for c in cells if c.value is not None),
                    "formula_count": formulas, "sum": round(sum(nums), 6) if nums else None,
                    "mean": round(sum(nums) / len(nums), 6) if nums else None,
                    "error_count": errs, "calculated": bool(calc_ok) if formulas else True}

        for comp in comps:
            v = loc.get(comp.id, {})
            if v.get("kind") == "workbook":
                states[comp.id] = oc.h({"sheets": wb.sheetnames, "names": sorted(
                    wb.defined_names.keys())})
                meas[comp.id] = {"sheets": len(wb.sheetnames), "calculated": bool(calc_ok)}
                props[comp.id] = {"sheets": wb.sheetnames, "calc_engine": calc.get("engine"),
                                  "calc_note": calc.get("reason"), "issues": issues}
            elif v.get("kind") == "sheet":
                ws = wb[v["title"]]
                cov = covered.get(ws.title, set())
                own = [_cell_sig(c) for row in ws.iter_rows() for c in row
                       if c.coordinate not in cov and (c.value is not None or c.has_style)]
                states[comp.id] = oc.h({"title": ws.title, "cells": own,
                                        "widths": {k: d.width for k, d in
                                                   ws.column_dimensions.items() if d.width}})
                m = summarize(ws, (c for row in ws.iter_rows() for c in row))
                m.update(rows=ws.max_row, columns=ws.max_column)
                meas[comp.id] = m
                props[comp.id] = {"title": ws.title, "dimensions": ws.dimensions}
            elif v.get("kind") in ("table", "range"):
                ws = wb[v["title"]]
                cells = list(_cells(ws, v["ref"]))
                states[comp.id] = oc.h({"ref": v["ref"], "cells": [_cell_sig(c) for c in cells]})
                c1, r1, c2, r2 = _bounds(v["ref"])
                m = summarize(ws, cells)
                m.update(rows=r2 - r1 + 1, columns=c2 - c1 + 1)
                meas[comp.id] = m
                grid = [[value_of(ws, c) for c in row] for row in ws.iter_rows(
                    min_row=r1, max_row=r2, min_col=c1, max_col=c2)]
                vals = [[x if not hasattr(x, "isoformat") else x.isoformat() for x in r]
                        for r in grid[:200]]
                # authored content -> state; calculated results -> values_hash (a change in
                # computed values is not an edit of this component, but dependents must update)
                m["values_hash"] = oc.h(vals)
                props[comp.id] = {"sheet": ws.title, "ref": v["ref"], "name": v.get("name"),
                                  "values": vals}
            elif v.get("kind") == "chart":
                states[comp.id] = oc.h(v["spec"])
                # what the chart plots (calculated values) - its dependents follow the data
                plotted = []
                for key in ("data", "categories"):
                    if v["spec"].get(key):
                        ref = self._ref_of(wb, ids, v["spec"][key])
                        cws = wb[v["title"]]
                        plotted.append([[value_of(cws, c) for c in row] for row in cws.iter_rows(
                            min_row=_bounds(ref)[1], max_row=_bounds(ref)[3],
                            min_col=_bounds(ref)[0], max_col=_bounds(ref)[2])])
                meas[comp.id] = {"series": max(1, _bounds(self._ref_of(wb, ids, v["spec"]["data"]))[2]
                                               - _bounds(self._ref_of(wb, ids, v["spec"]["data"]))[0] + 1)}
                meas[comp.id]["values_hash"] = oc.h(plotted)
                props[comp.id] = {**v["spec"], "data_ref": self._ref_of(wb, ids, v["spec"]["data"])}
        return InspectResult(components=comps, states=states, measurements=meas,
                             properties=props, aggregates=[ids.get("root", "workbook")])

    # ------------------------------------------------------------------ apply
    def apply(self, native_dir: Path, entry: str, operations: list[PlannedOperation],
              context: dict[str, Any]) -> ApplyResult:
        path = native_dir / entry
        ids = oc.load_ids(native_dir)
        own = {"__daedelus_charts__"} if any(s.get("kind") == "chart"
                                             for s in ids.get("specs", {}).values()) else set()
        bad = oc.unsupported_features(path, "xlsx", own)
        if bad:
            return ApplyResult(ok=False, error="editing refused - the workbook contains features "
                               "this adapter cannot preserve: " + ", ".join(bad) +
                               ". An explicit, approved conversion is required.")
        wb = self._load(path)
        comps, loc, issues = self._reconcile(wb, ids)
        results = []
        for i, op in enumerate(operations):
            try:
                if op.component_id and op.component_id not in loc and \
                        self.op_spec(op.op).target_kinds != ["new"]:
                    raise AdapterError(f"component '{op.component_id}' not found (missing or "
                                       f"ambiguous): {'; '.join(issues) or 'unknown id'}")
                detail = self._apply_one(wb, ids, loc, op)
                comps, loc, issues = self._reconcile(wb, ids)
                results.append({"index": i, "op": op.op, "component_id": op.component_id,
                                "status": "applied", "detail": detail})
            except Exception as exc:
                results.append({"index": i, "op": op.op, "component_id": op.component_id,
                                "status": "failed", "detail": f"{type(exc).__name__}: {exc}"})
                return ApplyResult(ok=False, results=results, error=results[-1]["detail"])
        self._save(wb, native_dir, entry, ids)
        return ApplyResult(ok=True, results=results)

    def _target_ref(self, loc, op) -> tuple[str, str | None]:
        v = loc[op.component_id]
        if v["kind"] == "sheet":
            return v["title"], None
        return v["title"], v["ref"]

    def _apply_one(self, wb, ids, loc, op: PlannedOperation) -> str:
        p = op.params
        comps = ids["components"]
        if op.op == "add_sheet":
            if p["id"] in comps:
                return f"sheet {p['id']} already exists (idempotent no-op)"
            if p["title"] in wb.sheetnames:
                raise AdapterError(f"a sheet titled '{p['title']}' already exists")
            wb.create_sheet(p["title"], p.get("index"))
            comps[p["id"]] = {"kind": "sheet", "title": p["title"]}
            return f"added sheet {p['title']}"
        if op.op == "rename_sheet":
            v = loc[op.component_id]
            old, new = v["title"], p["title"]
            if new in wb.sheetnames:
                raise AdapterError(f"a sheet titled '{new}' already exists")
            wb[old].title = new
            q = (lambda t: f"'{t}'" if not t.isidentifier() else t)
            for ws in wb.worksheets:
                for row in ws.iter_rows():
                    for c in row:
                        if isinstance(c.value, str) and c.value.startswith("="):
                            c.value = c.value.replace(f"{q(old)}!", f"{q(new)}!")
            for name, d in list(wb.defined_names.items()):
                d.attr_text = d.attr_text.replace(f"{q(old)}!", f"{q(new)}!")
            comps.setdefault(op.component_id, {"kind": "sheet"})["title"] = new
            return f"renamed sheet {old} -> {new} (formula and name references updated)"
        if op.op in ("set_cells", "write_range"):
            title, bound = self._target_ref(loc, op)
            ws = wb[title]
            if op.op == "set_cells":
                items = list(p["cells"].items())
            else:
                from openpyxl.utils.cell import coordinate_from_string, get_column_letter
                from openpyxl.utils import column_index_from_string

                start = p.get("start") or (bound.split(":")[0] if bound else "A1")
                col, row = coordinate_from_string(start)
                c0 = column_index_from_string(col)
                items = [(f"{get_column_letter(c0 + j)}{row + i}", val)
                         for i, r in enumerate(p["values"]) for j, val in enumerate(r)]
            for addr, val in items:
                if bound and not _inside(addr, bound):
                    raise AdapterError(f"{addr} lies outside the target {op.component_id} "
                                       f"({bound})")
                if isinstance(val, (dict, list)):
                    raise AdapterError(f"{addr}: only scalar values are allowed")
                ws[addr] = val
            nf = sum(1 for _, v in items if isinstance(v, str) and v.startswith("="))
            return f"wrote {len(items)} cell(s) on {title} ({nf} formula(s))"
        if op.op == "format_range":
            from openpyxl.styles import Alignment, Font, PatternFill

            title, bound = self._target_ref(loc, op)
            ws = wb[title]
            ref = p.get("range") or bound or ws.dimensions
            if bound and not _inside(ref, bound):
                raise AdapterError(f"{ref} lies outside the target {op.component_id} ({bound})")
            n = 0
            for c in _cells(ws, ref):
                if "bold" in p or "italic" in p or "font_color" in p:
                    f = c.font
                    c.font = Font(name=f.name, size=f.size, bold=p.get("bold", f.b),
                                  italic=p.get("italic", f.i),
                                  color=(p["font_color"][1:].upper() if p.get("font_color")
                                         else f.color))
                if p.get("number_format"):
                    c.number_format = p["number_format"]
                if p.get("fill"):
                    c.fill = PatternFill("solid", fgColor=p["fill"][1:].upper())
                if p.get("align"):
                    c.alignment = Alignment(horizontal=p["align"])
                n += 1
            for col, w in (p.get("column_widths") or {}).items():
                ws.column_dimensions[col].width = float(w)
            return f"formatted {n} cell(s) on {title}"
        if op.op == "add_table":
            from openpyxl.worksheet.table import Table, TableStyleInfo

            if p["id"] in comps:
                return f"table {p['id']} already exists (idempotent no-op)"
            title = self._title_of(ids, p["sheet"])
            name = p.get("name") or oc.slug(p["id"]).replace(".", "_").replace("-", "_")
            t = Table(displayName=name, ref=p["ref"].replace("$", ""))
            t.tableStyleInfo = TableStyleInfo(name=p.get("style", "TableStyleMedium2"),
                                              showRowStripes=True)
            wb[title].add_table(t)
            comps[p["id"]] = {"kind": "table", "name": name}
            return f"table {name} on {title}!{p['ref']}"
        if op.op == "define_name":
            from openpyxl.workbook.defined_name import DefinedName

            if p["id"] in comps:
                return f"name {p['id']} already exists (idempotent no-op)"
            title = self._title_of(ids, p["sheet"])
            name = p.get("name") or p["id"].replace("-", "_")
            c1, r1, c2, r2 = _bounds(p["ref"])
            from openpyxl.utils import get_column_letter as L

            q = f"'{title}'" if not title.isidentifier() else title
            ref = f"{q}!${L(c1)}${r1}:${L(c2)}${r2}"
            wb.defined_names[name] = DefinedName(name, attr_text=ref)
            comps[p["id"]] = {"kind": "range", "name": name}
            return f"named range {name} = {ref}"
        if op.op == "set_name_ref":
            v = loc[op.component_id]
            c1, r1, c2, r2 = _bounds(p["ref"])
            from openpyxl.utils import get_column_letter as L

            q = f"'{v['title']}'" if not v["title"].isidentifier() else v["title"]
            wb.defined_names[v["name"]].attr_text = f"{q}!${L(c1)}${r1}:${L(c2)}${r2}"
            return f"named range {v['name']} -> {p['ref']}"
        if op.op == "add_chart":
            if p["id"] in ids["specs"]:
                return f"chart {p['id']} already exists (idempotent no-op)"
            spec = {k: v for k, v in p.items() if k not in ("id", "parent")}
            spec.update(kind="chart", type=p.get("type", "column"))
            self._check_chart(wb, ids, spec)
            ids["specs"][p["id"]] = spec
            return f"chart {p['id']} ({spec['type']}) on {self._title_of(ids, p['sheet'])}"
        if op.op == "update_chart":
            spec = ids["specs"][op.component_id]
            spec.update(p)
            self._check_chart(wb, ids, spec)
            return f"chart {op.component_id} updated: {sorted(p)}"
        if op.op == "insert_rows":
            v = loc[op.component_id]
            ws = wb[v["title"]]
            idx, amount = int(p["index"]), int(p.get("amount", 1))
            refs = []
            for x in loc.values():
                if x.get("title") == ws.title and x.get("ref") and _bounds(x["ref"])[3] >= idx:
                    refs.append(x.get("name") or x["ref"])
            prefixes = (f"{ws.title}!", f"'{ws.title}'!")
            for other in wb.worksheets:
                for row in other.iter_rows():
                    for c in row:
                        if not (isinstance(c.value, str) and c.value.startswith("=")):
                            continue
                        if other.title == ws.title and c.row >= idx - 1:
                            refs.append(c.coordinate)
                        elif other.title != ws.title and any(x in c.value for x in prefixes):
                            refs.append(f"{other.title}!{c.coordinate}")  # cross-sheet refs
            for name, d in wb.defined_names.items():
                if any(x in (d.attr_text or "") for x in prefixes):
                    refs.append(name)
            for s in ids["specs"].values():
                if s.get("kind") == "chart" and self._title_of(ids, s["sheet"]) == ws.title:
                    refs.append(f"chart {s.get('title', '')}")
            if refs:
                raise AdapterError("insert refused: references would not be rewritten "
                                   f"({', '.join(map(str, refs[:6]))})")
            ws.insert_rows(idx, amount)
            return f"inserted {amount} row(s) at {idx} on {ws.title}"
        if op.op == "delete_component":
            v = loc[op.component_id]
            if v["kind"] == "sheet":
                del wb[v["title"]]
            elif v["kind"] == "table":
                del wb[v["title"]].tables[v["name"]]
            elif v["kind"] == "range":
                del wb.defined_names[v["name"]]
            elif v["kind"] == "chart":
                ids["specs"].pop(op.component_id, None)
            comps.pop(op.component_id, None)
            return f"removed {v['kind']} {op.component_id}"
        raise AdapterError(f"unknown operation {op.op}")

    def values(self, native_dir: Path, entry: str, ref: str) -> list[list[Any]]:
        """Values of a range (calculated values for formulas; None if not calculated)."""
        wb = self._load(native_dir / entry)
        ids = oc.load_ids(native_dir)
        sheet, _, r = ref.rpartition("!")
        title = self._title_of(ids, sheet.strip("'")) if sheet else wb.sheetnames[0]
        ws = wb[title]
        calc = ids.get("calc") or {}
        fh = oc.h((native_dir / entry).read_bytes().hex())
        vals = calc.get("values", {}) if calc.get("available") and calc.get(
            "file_hash") == fh else {}
        c1, r1, c2, r2 = _bounds(self._ref_of(wb, ids, r))
        out = []
        for row in ws.iter_rows(min_row=r1, max_row=r2, min_col=c1, max_col=c2):
            out.append([vals.get(f"{ws.title}!{c.coordinate}") if isinstance(c.value, str)
                        and c.value.startswith("=") else c.value for c in row])
        return out

    def render_chart(self, native_dir: Path, entry: str, chart_id: str, out_png: Path) -> Path:
        """Render one chart to PNG: a temporary workbook holds only this chart (first sheet)
        and its data (calculated values); LibreOffice renders it; whitespace is cropped."""
        from openpyxl.utils import get_column_letter as L
        from PIL import Image, ImageChops

        ids = oc.load_ids(native_dir)
        spec = ids.get("specs", {}).get(chart_id)
        if not spec or spec.get("kind") != "chart":
            raise AdapterError(f"chart '{chart_id}' not found")
        wb0 = self._load(native_dir / entry)
        data_ref = self._ref_of(wb0, ids, spec["data"])
        sheet = self._title_of(ids, spec["sheet"])
        q = f"'{sheet}'!" if not sheet.isidentifier() else f"{sheet}!"
        data = self.values(native_dir, entry, q + data_ref)
        if any(v is None for r in data[1:] for v in r):
            raise AdapterError("chart data contains uncalculated formula values (LibreOffice "
                               "recalculation unavailable)")
        cats = None
        if spec.get("categories"):
            cats = self.values(native_dir, entry, q + self._ref_of(wb0, ids, spec["categories"]))
        op = _openpyxl()
        wb = op.Workbook()
        cws = wb.active
        cws.title = "Chart"
        dws = wb.create_sheet("D")
        for i, r in enumerate(data):
            for j, v in enumerate(r):
                dws.cell(i + 1, j + 2, v)
        if cats:
            for i, r in enumerate(cats):
                dws.cell(i + 2, 1, r[0])
        tmp_ids = {"components": {"d": {"kind": "sheet", "title": "D"}}, "specs": {
            "c": {**spec, "sheet": "D", "anchor": "A1",
                  "data": f"B1:{L(1 + len(data[0]))}{len(data)}",
                  "categories": f"A2:A{len(data)}" if cats else None}}}
        self._rebuild_charts(wb, tmp_ids)
        # move the chart to the (empty) first sheet so the first PDF page is the chart
        ch = dws._charts.pop()
        cws.add_chart(ch, "A1")
        with tempfile.TemporaryDirectory() as td:
            src = Path(td) / "chart.xlsx"
            wb.save(src)
            pdf = oc.lo_convert(src, "pdf", Path(td))
            if pdf is None:
                raise AdapterError("chart rendering needs LibreOffice")
            pngs = oc.pdf_to_pngs(pdf, Path(td), "c", max_pages=1, width=1400)
            if not pngs:
                raise AdapterError("could not rasterise the chart")
            im = Image.open(pngs[0]).convert("RGB")
            bg = Image.new("RGB", im.size, (255, 255, 255))
            box = ImageChops.difference(im, bg).getbbox()
            if box:
                pad = 8
                im = im.crop((max(0, box[0] - pad), max(0, box[1] - pad),
                              min(im.width, box[2] + pad), min(im.height, box[3] + pad)))
            out_png.parent.mkdir(parents=True, exist_ok=True)
            im.save(out_png)
        return out_png

    def _check_chart(self, wb, ids, spec) -> None:
        title = self._title_of(ids, spec["sheet"])
        if title not in wb.sheetnames:
            raise AdapterError(f"chart sheet '{spec['sheet']}' not found")
        for key in ("data", "categories"):
            if spec.get(key):
                _bounds(self._ref_of(wb, ids, spec[key]))  # raises on invalid references

    # ------------------------------------------------------------------ preview / export
    def preview(self, native_dir: Path, entry: str, out_dir: Path) -> dict[str, Path]:
        out_dir.mkdir(parents=True, exist_ok=True)
        out: dict[str, Path] = {}
        insp = self.inspect(native_dir, entry)
        grid = self._grid(native_dir, entry)
        gp = out_dir / "grid.json"
        gp.write_text(__import__("json").dumps(grid, default=str))
        out["grid"] = gp
        pdf = oc.lo_convert(native_dir / entry, "pdf", out_dir)
        if pdf is not None:
            out["pdf"] = pdf
            pngs = oc.pdf_to_pngs(pdf, out_dir, "page", max_pages=6, width=900)
            if pngs:
                out["render"] = pngs[0]
                for i, p in enumerate(pngs[1:], 2):
                    out[f"page{i}"] = p
        _ = insp
        return out

    def _grid(self, native_dir: Path, entry: str, max_rows: int = 200, max_cols: int = 26):
        """Cells for the studio grid: formulas and calculated values kept distinct."""
        wb = self._load(native_dir / entry)
        ids = oc.load_ids(native_dir)
        calc = ids.get("calc") or {}
        fh = oc.h((native_dir / entry).read_bytes().hex())
        values = calc.get("values", {}) if calc.get("available") and calc.get(
            "file_hash") == fh else {}
        sheets = []
        for ws in wb.worksheets:
            rows = []
            for row in ws.iter_rows(min_row=1, max_row=min(ws.max_row, max_rows),
                                    max_col=min(ws.max_column, max_cols)):
                cells = []
                for c in row:
                    v = c.value
                    if hasattr(v, "isoformat"):
                        v = v.isoformat()
                    if isinstance(v, str) and v.startswith("="):
                        key = f"{ws.title}!{c.coordinate}"
                        cells.append({"f": v, "v": values.get(key), "calc": key in values})
                    else:
                        cells.append({"v": v, "b": bool(c.font.b)} if v is not None else None)
                rows.append(cells)
            sheets.append({"title": ws.title, "rows": rows, "max_row": ws.max_row,
                           "max_col": ws.max_column})
        charts = []
        for cid, spec in ids.get("specs", {}).items():
            if spec.get("kind") != "chart":
                continue
            title = self._title_of(ids, spec["sheet"])
            try:
                data = self.values(native_dir, entry, f"{title}!{spec['data']}")
                cats = (self.values(native_dir, entry, f"{title}!{spec['categories']}")
                        if spec.get("categories") else [])
            except Exception as exc:  # reported, never hidden
                charts.append({"id": cid, "sheet": title, "error": str(exc)})
                continue
            names = [str(x) for x in data[0]] if data else []
            series = {n: [r[j] for r in data[1:]] for j, n in enumerate(names)}
            charts.append({"id": cid, "sheet": title, "type": spec.get("type", "column"),
                           "title": spec.get("title"), "anchor": spec.get("anchor"),
                           "categories": [str(r[0]) for r in cats], "series": series})
        return {"sheets": sheets, "charts": charts, "calculated": bool(values) or not any(
            isinstance(c, dict) and "f" in c for s in sheets for r in s["rows"] for c in r if c),
            "calc_note": calc.get("reason") or calc.get("engine")}

    def export(self, native_dir: Path, entry: str, fmt: str, out_dir: Path) -> Path:
        out_dir.mkdir(parents=True, exist_ok=True)
        if fmt == "xlsx":
            dst = out_dir / entry
            shutil.copy2(native_dir / entry, dst)
            return dst
        if fmt == "csv":
            wb = self._load(native_dir / entry)
            ws = wb.worksheets[0]
            dst = out_dir / f"{Path(entry).stem}.csv"
            buf = io.StringIO()
            w = csv.writer(buf)
            for row in ws.iter_rows(values_only=True):
                w.writerow(["" if v is None else v for v in row])
            dst.write_text(buf.getvalue())
            return dst
        if fmt == "pdf":
            pdf = oc.lo_convert(native_dir / entry, "pdf", out_dir)
            if pdf is None:
                raise AdapterError("PDF export needs LibreOffice")
            return pdf
        raise AdapterError(f"spreadsheet cannot export {fmt}")

    def diff(self, before_dir: Path, after_dir: Path, entry: str) -> str | None:
        try:
            a = self._load(before_dir / entry)
            b = self._load(after_dir / entry)
        except AdapterError:
            return None
        lines = []
        for title in sorted(set(a.sheetnames) | set(b.sheetnames)):
            if title not in a.sheetnames:
                lines.append(f"+ sheet {title}")
                continue
            if title not in b.sheetnames:
                lines.append(f"- sheet {title}")
                continue
            wa, wb_ = a[title], b[title]
            mr, mc = max(wa.max_row, wb_.max_row), max(wa.max_column, wb_.max_column)
            for r in range(1, mr + 1):
                for c in range(1, mc + 1):
                    va, vb = wa.cell(r, c).value, wb_.cell(r, c).value
                    if va != vb:
                        lines.append(f"{title}!{wa.cell(r, c).coordinate}: {va!r} -> {vb!r}")
        return "\n".join(lines[:2000]) or None

    def validate(self, native_dir: Path, entry: str, checks: list[str],
                 context: dict[str, Any]) -> ValidationReport:
        rep = ValidationReport()
        path = native_dir / entry
        try:
            wb = self._load(path)
            rep.add("file_reopens", True, f"workbook reopened ({len(wb.sheetnames)} sheet(s))")
        except AdapterError as exc:
            rep.add("file_reopens", False, str(exc))
            return rep
        ids = oc.load_ids(native_dir)
        nform = sum(1 for ws in wb.worksheets for row in ws.iter_rows() for c in row
                    if isinstance(c.value, str) and c.value.startswith("="))
        rep.add("formulas_preserved", True, f"{nform} formula(s) stored as formulas",
                formula_count=nform)
        _, _, issues = self._reconcile(wb, ids)
        rep.add("component_identity", not issues, "; ".join(issues) or "all components resolved")
        if "libreoffice_opens" in checks or "file_reopens" in checks:
            if oc.soffice():
                with tempfile.TemporaryDirectory() as td:
                    ok = oc.lo_convert(path, "pdf", Path(td)) is not None
                rep.add("libreoffice_opens", ok, "LibreOffice rendered a copy to PDF" if ok else
                        "LibreOffice could not open a copy")
        calc = ids.get("calc") or {}
        if nform:
            if calc.get("available"):
                errs = {k: v for k, v in calc["values"].items()
                        if isinstance(v, str) and v.startswith(ERRORS)}
                rep.add("recalculated", not errs, f"{len(calc['values'])} formula value(s) "
                        f"calculated by {calc.get('engine')}" + (f"; errors: {errs}" if errs
                                                                 else ""), errors=errs)
            else:
                rep.add("recalculated", True, "formulas not calculated here ("
                        + (calc.get("reason") or "no engine") + "); Excel recalculates on open",
                        calculated=False)
        return rep


def _xy(ref: str) -> tuple:
    c1, r1, c2, r2 = _bounds(ref)
    return (c1, r1, c1, r2)
