"""Side-by-side comparison of creative benchmark reports (V2.1.1, handoff section 15).

    python -m daedelus.bench_compare --out evidence/v2_1_1/comparison \
        deterministic=evidence/v2_1_1/regression/bench_deterministic/bench_report.json \
        claude=evidence/v2_1_1/claude/bench/bench_report.json \
        gemini=evidence/v2_1_1/gemini/bench/bench_report.json

A label whose report is missing or blocked is shown as BLOCKED with its reason. Per-task
results are reported with their sample size (one run per mode); nothing here declares a
provider superior.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

DIMENSIONS = ("understanding", "planning", "execution", "constraints", "scope", "iteration",
              "reliability", "runtime_s", "cost")


def _load(path: str) -> dict[str, Any] | None:
    p = Path(path)
    if not p.is_file():
        return None
    return json.loads(p.read_text())


def _n(checks, pred=lambda c: True):
    sel = [c for c in checks if pred(c)]
    return sum(c["ok"] for c in sel), len(sel)


def summarise_run(r: dict[str, Any]) -> dict[str, Any]:
    checks = r.get("checks") or []
    ok, tot = _n(checks)
    v21 = _n(checks, lambda c: c.get("since", "2.1") == "2.1")
    plans = r.get("plans") or []
    ops = [o for p in plans for o in (p.get("operations") or [])]
    invalid = [u for u in r.get("validation_decisions") or [] if u.get("status") == "failed"]
    used = (r.get("budget") or {}).get("used") or {}
    loop = r.get("loop") or {}
    return {"task": r["task"], "mode": r["mode"], "verification": r.get("verification"),
            "blocked_by": r.get("blocked_by"), "checks": f"{ok}/{tot}",
            "checks_v21": f"{v21[0]}/{v21[1]}", "passed": r.get("passed"),
            "model_observations": r.get("model_observation_count"),
            "operations": len(ops), "invalid_units": len(invalid),
            "preservation": (r.get("rubric") or {}).get("preservation", {}).get("score"),
            "constraint_adherence": (r.get("rubric") or {}).get("constraint_adherence",
                                                                 {}).get("score"),
            "live_calls": r.get("live_calls", 0), "cost": used.get("cost"),
            "unpriced_calls": used.get("unpriced_calls"), "seconds": r.get("seconds"),
            "loop_stop": loop.get("stop_reason"), "iterations": loop.get("iterations_run"),
            "failure": (r.get("failure_analysis") or {}).get("category"),
            "assessment": (r.get("model_assessment") or {}).get("status")}


def iteration_effect(single: dict[str, Any], it: dict[str, Any]) -> str:
    """'improved' / 'unchanged' / 'regressed' by passed checks (iterative vs single)."""
    if single.get("blocked_by") or it.get("blocked_by"):
        return "n/a (blocked)"
    a = int(single["checks"].split("/")[0])
    b = int(it["checks"].split("/")[0])
    if b > a:
        return "improved"
    if b < a:
        return "regressed"
    return "unchanged"


def compare(reports: dict[str, dict[str, Any] | None]) -> dict[str, Any]:
    out: dict[str, Any] = {"labels": {}, "tasks": {}}
    for label, rep in reports.items():
        if rep is None:
            out["labels"][label] = {"status": "BLOCKED", "reason": "no report (not run)"}
            continue
        if rep.get("status") == "blocked":
            out["labels"][label] = {"status": "BLOCKED", "reason": rep.get("blocked_by")}
            continue
        out["labels"][label] = {"status": rep.get("status"), "provider": rep.get("provider"),
                                "campaign": rep.get("campaign"),
                                "runs": len(rep.get("runs") or [])}
        for r in rep.get("runs") or []:
            out["tasks"].setdefault(r["task"], {}).setdefault(label, {})[r["mode"]] = \
                summarise_run(r)
    for t, by in out["tasks"].items():
        for label, modes in by.items():
            if "single" in modes and "iterative" in modes:
                modes["iteration_effect"] = iteration_effect(modes["single"], modes["iterative"])
    return out


def to_markdown(cmp: dict[str, Any]) -> str:
    L = ["# Creative benchmark comparison (v2.1.1)", "",
         "One run per task and mode for each provider (sample size n=1 per cell): differences "
         "are observations, not statistically supported rankings. Deterministic checks are "
         "authoritative; model assessments are labelled and never counted as checks.", "",
         "## Coverage", "", "| Label | Status | Detail |", "|---|---|---|"]
    for label, s in cmp["labels"].items():
        detail = s.get("reason") or (f"{s.get('runs')} runs; campaign "
                                     f"{(s.get('campaign') or {}).get('used')}")
        L.append(f"| {label} | {s['status']} | {str(detail)[:220]} |")
    L += ["", "## Per task", ""]
    for t in sorted(cmp["tasks"]):
        L += [f"### Task {t}", "", "| Label | Mode | Verification | Checks | v2.1 checks | Ops | "
              "Invalid units | Live calls | Cost | Seconds | Loop | Failure (automatic) | "
              "Assessment |", "|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
        for label, modes in cmp["tasks"][t].items():
            for mode in ("single", "iterative"):
                m = modes.get(mode)
                if not m:
                    continue
                L.append(f"| {label} | {mode} | {m['verification']}"
                         + (f" ({m['blocked_by']})" if m.get("blocked_by") else "")
                         + f" | {m['checks']} | {m['checks_v21']} | {m['operations']} | "
                         f"{m['invalid_units']} | {m['live_calls']} | {m['cost'] or '—'} | "
                         f"{m['seconds'] or '—'} | {m['loop_stop'] or '—'} | "
                         f"{m['failure'] or '—'} | {m['assessment'] or '—'} |")
            if "iteration_effect" in modes:
                L.append(f"| {label} | iterative vs single | | **{modes['iteration_effect']}** "
                         "| | | | | | | | | |")
        L.append("")
    return "\n".join(L) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("reports", nargs="+", help="label=path/to/bench_report.json")
    a = ap.parse_args(argv)
    reps = {}
    for item in a.reports:
        label, _, path = item.partition("=")
        reps[label] = _load(path)
    cmp = compare(reps)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "comparison.json").write_text(json.dumps(cmp, indent=1, default=str))
    (out / "comparison.md").write_text(to_markdown(cmp))
    print(f"wrote {out / 'comparison.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
