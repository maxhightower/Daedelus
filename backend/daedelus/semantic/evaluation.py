"""Measured findings computed by the engine (never by a model).

- explicit binding constraints (``SourceBinding.constraints``) and derived constraints from
  sources whose binding gives them a *constraint* meaning are ``kind="constraint"``: hard,
  they decide whether a result is acceptable;
- derived limits from guideline/inspiration sources are reported as ``deterministic``
  findings with ``advisory=True``: visible, revisable, never blocking.
"""

from __future__ import annotations

from typing import Any

from .heuristic import check
from .models import Finding


def constraint_findings(pkg: dict[str, Any], measurements: dict[str, dict[str, Any]],
                        unit_cid: str | None) -> list[Finding]:
    out: list[Finding] = []
    for c in pkg.get("constraints", {}).get("explicit", []):
        if not c.get("active", True):
            continue
        con = c["constraint"]
        if con["op"] in ("preserve", "forbid_change"):
            continue  # checked against the pre-execution state by the engine itself
        cid = unit_cid
        actual = (measurements.get(cid or "", {}) or {}).get(con["property"])
        ok = check(con["property"], con["op"], con.get("value"), actual,
                   float(con.get("tolerance") or 1e-3))
        out.append(Finding(
            criterion=f"{con['property']} {con['op']} {con.get('value')} ({c.get('source')})",
            status="uncertain" if ok is None else "pass" if ok else "fail", kind="constraint",
            detail=f"measured {actual}", component_id=cid,
            evidence={"property": con["property"], "op": con["op"], "value": con.get("value"),
                      "actual": actual, "origin": "binding constraint"}))
    for d in pkg.get("constraints", {}).get("derived", []):
        cid = d.get("component_id") or unit_cid
        actual = (measurements.get(cid or "", {}) or {}).get(d["property"])
        ok = check(d["property"], d["op"], d["value"], actual)
        advisory = not d.get("enforced")
        out.append(Finding(
            criterion=f"{d['property']} {d['op']} {d['value']} from '{d['source']}'",
            status="uncertain" if ok is None else "pass" if ok else "fail",
            kind="deterministic" if advisory else "constraint",
            detail=f"measured {actual}; source text: \"{d['text'][:120]}\""
                   + (" (advisory: the binding is not a constraint)" if advisory else ""),
            component_id=cid, binding_id=d.get("binding_id"),
            evidence={"property": d["property"], "op": d["op"], "value": d["value"],
                      "actual": actual, "advisory": advisory, "origin": "derived from source"}))
    return out
