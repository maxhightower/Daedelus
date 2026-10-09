"""Scripted demonstration of scoped influence, selective execution and reproducibility.

Each scenario changes ONE input through the public services, checks the impact
analysis, executes the workflow incrementally and verifies which components
changed. Results are written as JSON + Markdown evidence with preview images.
"""

from __future__ import annotations

import json
import shutil
import time
from pathlib import Path
from typing import Any

from . import ingest
from .demo import STYLE_GUIDE_V2, create_demo_project
from .engine import Engine
from .models import Constraint, MediaType, RunStatus, SourceBinding, Workflow
from .store import ProjectStore, Workspace


class Recorder:
    def __init__(self, out: Path, store: ProjectStore):
        self.out, self.store = out, store
        self.items: list[dict[str, Any]] = []
        (out / "images").mkdir(parents=True, exist_ok=True)

    def check(self, scenario: str, name: str, ok: bool, detail: Any = "") -> bool:
        self.items.append({"scenario": scenario, "check": name, "passed": bool(ok),
                           "detail": detail})
        print(f"  [{'PASS' if ok else 'FAIL'}] {scenario}: {name}"
              + (f" - {detail}" if detail and not ok else ""))
        return ok

    def image(self, artifact_id: str, label: str) -> list[str]:
        a = self.store.get_artifact(artifact_id)
        rev = self.store.get_revision(a.head_revision_id)
        out = []
        for key in ("render", "composite"):
            if key in rev.previews:
                dst = self.out / "images" / f"{label}_{a.name.lower().replace(' ', '_')}_r" \
                                            f"{rev.number}.png"
                shutil.copy(self.store.abs(rev.previews[key]), dst)
                out.append(dst.name)
        return out


def _heads(store: ProjectStore) -> dict[str, str | None]:
    return {a.id: a.head_revision_id for a in store.list_artifacts()}


def _states(store: ProjectStore, aid: str) -> dict[str, str]:
    a = store.get_artifact(aid)
    return store.get_revision(a.head_revision_id).component_states


def _changed(before: dict[str, str], after: dict[str, str]) -> list[str]:
    return sorted(k for k in set(before) | set(after) if before.get(k) != after.get(k))


def _stale_units(imp: dict[str, Any]) -> dict[str, list[str]]:
    return {n["node_id"]: [u["unit"].split("#")[-1] for u in n.get("units", []) if u["stale"]]
            for n in imp["nodes"]}


def _units(ex, node: str) -> dict[str, str]:
    return {u.unit.split("#")[-1]: u.status.value for u in ex.run(node).units}


def run_scenarios(workspace: Path, out: Path, *, include_video: bool = True) -> dict[str, Any]:
    t0 = time.time()
    out.mkdir(parents=True, exist_ok=True)
    ws = Workspace(workspace)
    project, store, info = create_demo_project(ws, out / "inputs", include_video=include_video)
    eng = Engine(store)
    rec = Recorder(out, store)
    A, B, S, wid = info["artifacts"], info["bindings"], info["sources"], info["workflow_id"]
    table, bg, man = A["table"], A["background"], A["manifest"]
    gallery: list[dict[str, Any]] = []

    def snap(label: str, note: str) -> None:
        gallery.append({"label": label, "note": note,
                        "images": rec.image(table, label) + rec.image(bg, label)})

    snap("00_initial", "fixtures as created (revision 1)")

    # ---------------------------------------------------------------- S0 initial
    print("S0 initial execution")
    ex0 = eng.execute(wid)
    rec.check("S0 initial run", "execution succeeded", ex0.status == RunStatus.succeeded,
              ex0.error)
    for aid in (table, bg, man):
        a = store.get_artifact(aid)
        rec.check("S0 initial run", f"{a.name} has a new revision",
                  store.get_revision(a.head_revision_id).number == 2)
    rev_t = store.get_revision(store.get_artifact(table).head_revision_id)
    rec.check("S0 initial run", "legs height preserved (hard constraint)",
              any(c.name == "constraint" and c.passed and "height preserve" in c.detail
                  for c in rev_t.validation.checks),
              [c.detail for c in rev_t.validation.checks if c.name == "constraint"])
    val = ex0.run("validate").outputs.get("report", {})
    man_rep = next((r for r in val.get("artifacts", []) if r["artifact_id"] == man), {})
    tests = next((c for c in man_rep.get("report", {}).get("checks", []) if c["name"] == "tests"),
                 {})
    rec.check("S0 initial run", "manifest tests executed and passed", tests.get("passed") is True,
              tests.get("detail"))
    man_rev = store.get_revision(store.get_artifact(man).head_revision_id)
    rec.check("S0 initial run", "manifest change has a reviewable diff",
              bool(man_rev.diff) and '"artifacts"' in (man_rev.diff or ""))
    (out / "manifest_revision2.diff").write_text(man_rev.diff or "", encoding="utf-8")
    snap("01_after_initial_run", "after first execution of the workflow")

    ex_again = eng.execute(wid)
    rec.check("S0 initial run", "immediate re-run executes nothing (all units up to date)",
              all(s == "skipped" for n in ("model_agent", "paint_agent", "code_agent")
                  for s in _units(ex_again, n).values()),
              {n: _units(ex_again, n) for n in ("model_agent", "paint_agent", "code_agent")})

    # ---------------------------------------------------------------- S1 leg ref
    print("S1 change the leg inspiration source")
    heads, t_before, b_before = _heads(store), _states(store, table), _states(store, bg)
    b = store.get_binding(B["legs"])
    store.save_binding(b.model_copy(update={"source_id": S["leg_insp_b"]}))
    imp = eng.impact(wid)
    st = _stale_units(imp)
    rec.check("S1 leg source", "impact: only the legs unit of the 3D agent is stale",
              st.get("model_agent") == ["legs"] and st.get("paint_agent") == [], st)
    ex1 = eng.execute(wid)
    changed_t = _changed(t_before, _states(store, table))
    rec.check("S1 leg source", "execution succeeded", ex1.status == RunStatus.succeeded, ex1.error)
    rec.check("S1 leg source", "only leg components changed in the table",
              bool(changed_t) and set(changed_t) <= {"legs", "leg_fl", "leg_fr", "leg_bl",
                                                     "leg_br"}, changed_t)
    rec.check("S1 leg source", "tabletop and table root untouched",
              "tabletop" not in changed_t and "table" not in changed_t)
    rec.check("S1 leg source", "background not rebuilt (no new revision, unit skipped)",
              _heads(store)[bg] == heads[bg] and set(_units(ex1, "paint_agent").values())
              == {"skipped"}, _units(ex1, "paint_agent"))
    rec.check("S1 leg source", "manifest re-ran because an upstream revision changed",
              _units(ex1, "code_agent").get("repo") == "succeeded")
    snap("02_leg_source_changed", "leg inspiration swapped (tapered -> flared teal)")

    # ---------------------------------------------------------------- S2 background
    print("S2 change the background inspiration")
    heads, b_before = _heads(store), _states(store, bg)
    b = store.get_binding(B["sky"])
    store.save_binding(b.model_copy(update={"source_id": S["bg_dawn"]}))
    st = _stale_units(eng.impact(wid))
    rec.check("S2 background", "impact: only the sky unit of the 2D agent is stale",
              st.get("paint_agent") == ["sky"] and st.get("model_agent") == [], st)
    ex2 = eng.execute(wid)
    rec.check("S2 background", "execution succeeded", ex2.status == RunStatus.succeeded, ex2.error)
    rec.check("S2 background", "table not rebuilt",
              _heads(store)[table] == heads[table] and
              set(_units(ex2, "model_agent").values()) == {"skipped"}, _units(ex2, "model_agent"))
    ch = _changed(b_before, _states(store, bg))
    rec.check("S2 background", "only the sky layer changed", ch == ["sky"], ch)
    snap("03_background_changed", "sky binding now points at the dawn image")

    # ---------------------------------------------------------------- S3 global
    print("S3 change the global style guide")
    heads, t_before, b_before = _heads(store), _states(store, table), _states(store, bg)
    src = store.get_source(S["style"])
    p = store.abs(src.locator.path)
    p.write_text(STYLE_GUIDE_V2, encoding="utf-8")
    import hashlib
    src.content_hash = hashlib.sha256(p.read_bytes()).hexdigest()
    store.save_source(src)
    ingest.ingest(store, src)
    imp = eng.impact(wid)
    rec.check("S3 global guideline", "impact: table AND background identified as affected",
              set(imp["affected_artifacts"]) >= {table, bg}, imp["affected_artifacts"])
    ex3 = eng.execute(wid)
    rec.check("S3 global guideline", "execution succeeded", ex3.status == RunStatus.succeeded,
              ex3.error)
    rec.check("S3 global guideline", "both artifacts received new revisions",
              _heads(store)[table] != heads[table] and _heads(store)[bg] != heads[bg])
    snap("04_guideline_changed", "style guide edited (warm -> cool)")

    # ---------------------------------------------------------------- S4 role change
    print("S4 change a source's role (inspiration -> reference)")
    heads, t_before = _heads(store), _states(store, table)
    leg_src_hash = store.get_source(S["leg_insp_b"]).content_hash
    b = store.get_binding(B["legs"])
    store.save_binding(b.model_copy(update={"role": "reference"}))
    st = _stale_units(eng.impact(wid))
    rec.check("S4 role change", "impact: only the legs unit is stale",
              st.get("model_agent") == ["legs"] and st.get("paint_agent") == [], st)
    ex4 = eng.execute(wid)
    rec.check("S4 role change", "execution succeeded", ex4.status == RunStatus.succeeded, ex4.error)
    rec.check("S4 role change", "source media unchanged",
              store.get_source(S["leg_insp_b"]).content_hash == leg_src_hash)
    leg_plan = next(u.plan for u in ex4.run("model_agent").units if u.unit.endswith("#legs"))
    interp = leg_plan["interpretations"].get(B["legs"], "")
    rec.check("S4 role change", "interpretation now uses 'reference (drive)'",
              "reference (drive" in interp, interp)
    ch = _changed(t_before, _states(store, table))
    rec.check("S4 role change", "only leg components changed",
              bool(ch) and set(ch) <= {"legs", "leg_fl", "leg_fr", "leg_bl", "leg_br"}, ch)
    snap("05_role_changed", "leg binding role inspiration -> reference (stronger influence)")

    # ---------------------------------------------------------------- S5 conflict
    print("S5 conflicting hard constraints are reported, not silently resolved")
    table_head = _heads(store)[table]
    cb = store.save_binding(SourceBinding(
        source_id=S["leg_insp_b"], role="constraint", target=b.target, constraint="hard",
        constraints=[Constraint(property="height", op="eq", value=0.9)],
        instructions="Legs must be exactly 0.9 m tall"))
    ex5 = eng.execute(wid)
    err = ex5.run("model_agent").error or ""
    rec.check("S5 conflict", "3D agent failed with an explicit conflict",
              ex5.run("model_agent").status == RunStatus.failed and "conflict" in err, err)
    rec.check("S5 conflict", "table left untouched", _heads(store)[table] == table_head)
    rec.check("S5 conflict", "downstream nodes reported as blocked",
              ex5.run("code_agent").status == RunStatus.blocked)
    store.delete_binding(cb.id)

    # ---------------------------------------------------------------- S6 failure
    print("S6 failures are reported accurately (test command not permitted)")
    proj = store.get_project()
    allowed = list(proj.settings.allowed_commands)
    proj.settings.allowed_commands = []
    store.save_project(proj)
    ex6 = eng.execute(wid)
    err = ex6.run("validate").error or ""
    rec.check("S6 failure", "validation fails because tests were not permitted to run",
              ex6.run("validate").status == RunStatus.failed and "tests" in err, err)
    proj.settings.allowed_commands = allowed
    store.save_project(proj)

    # ---------------------------------------------------------------- S7 replay
    print("S7 reproduce executions from saved inputs")
    for label, ex in (("initial", ex0), ("leg change", ex1), ("guideline change", ex3)):
        rep = eng.replay(ex.id)
        rec.check("S7 reproducibility", f"{label}: re-plan + re-apply reproduce identical states",
                  rep["reproducible"], rep["results"])

    # ---------------------------------------------------------------- S8 persistence
    print("S8 persistence and workflow round trip")
    ws2 = Workspace(workspace)
    st2 = ws2.open(project.id)
    rec.check("S8 persistence", "project reopens with sources, bindings and artifacts",
              len(st2.list_sources()) == len(store.list_sources()) and
              len(st2.list_bindings()) == len(store.list_bindings()) and
              len(st2.list_artifacts()) == 3)
    wf = st2.get_workflow(wid)
    clone = Workflow.model_validate(json.loads(wf.model_dump_json()))
    rec.check("S8 persistence", "workflow export/import is lossless",
              clone.model_dump() == wf.model_dump())
    edited = wf.model_copy(update={"description": wf.description + " (edited)"})
    saved = st2.save_workflow(edited)
    rec.check("S8 persistence", "saving creates a new workflow version",
              saved.version == wf.version + 1 and st2.get_workflow(wid, wf.version).description
              == wf.description)
    multi = [s for s in store.list_sources() if len(store.list_bindings(s.id)) > 1]
    rec.check("S8 persistence", "at least one source carries multiple independent bindings",
              bool(multi), [s.name for s in multi])
    for a in store.list_artifacts():
        native = store.abs(a.native_dir) / a.entry
        rec.check("S8 persistence", f"editable native file exists for {a.name}", native.exists(),
                  str(native))

    # --------------------------------------------------------------- report
    passed = all(i["passed"] for i in rec.items)
    video = store.get_source(S["video"]) if "video" in S else None
    report = {
        "passed": passed, "duration_seconds": round(time.time() - t0, 1),
        "project_id": project.id, "workspace": str(workspace), "info": info,
        "checks": rec.items, "gallery": gallery,
        "video_source": None if video is None else {
            "state": video.processing.state.value, "warnings": video.processing.warnings,
            "title": video.extracted.get("title")},
        "native_files": {a.name: str(store.abs(a.native_dir) / a.entry)
                         for a in store.list_artifacts()},
        "executions": [ex.id for ex in (ex0, ex1, ex2, ex3, ex4, ex5, ex6)],
    }
    (out / "demo_report.json").write_text(json.dumps(report, indent=1, default=str),
                                          encoding="utf-8")
    _markdown(out, report, store)
    return report


def _markdown(out: Path, report: dict[str, Any], store: ProjectStore) -> None:
    lines = ["# Daedelus V0 demonstration evidence", "",
             f"Overall: **{'PASS' if report['passed'] else 'FAIL'}** "
             f"({sum(c['passed'] for c in report['checks'])}/{len(report['checks'])} checks, "
             f"{report['duration_seconds']} s)", "", "## Sources and bindings", "",
             "| Source | Media | State | Role | Aspects | Target | Strength | Constraint |",
             "|---|---|---|---|---|---|---|---|"]
    arts = {a.id: a.name for a in store.list_artifacts()}
    for b in store.list_bindings():
        s = store.get_source(b.source_id)
        t = b.target
        target = "project" if t.scope == "project" else (
            arts.get(t.artifact_id, t.artifact_id) + (f" → {t.component_id}" if t.component_id
                                                       else ""))
        cons = b.constraint + ("; " + ", ".join(f"{c.property} {c.op}" for c in b.constraints)
                               if b.constraints else "")
        lines.append(f"| {s.name} | {s.media_type.value} | {s.processing.state.value} | {b.role} | "
                     f"{', '.join(b.aspects)} | {target} | {b.strength} | {cons} |")
    if report.get("video_source"):
        v = report["video_source"]
        lines += ["", f"Video source: state **{v['state']}**, title `{v['title']}`; "
                      f"{'; '.join(v['warnings'])}"]
    lines += ["", "## Checks", "", "| Scenario | Check | Result |", "|---|---|---|"]
    for c in report["checks"]:
        lines.append(f"| {c['scenario']} | {c['check']} | {'PASS' if c['passed'] else 'FAIL'} |")
    lines += ["", "## Gallery", ""]
    for g in report["gallery"]:
        lines.append(f"### {g['label']}: {g['note']}")
        lines += [f"![{img}](images/{img})" for img in g["images"]] + [""]
    lines += ["## Editable native files", ""] + [f"- {k}: `{v}`" for k, v in
                                                  report["native_files"].items()]
    (out / "demo_report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
