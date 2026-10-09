"""V1.1 demonstrations (A-E): semantic understanding + agent loop on real artifacts.

Every demonstration records *what kind* of verification it is:

- ``integration``: real application behaviour (Blender, OpenRaster, git, pytest) driven by the
  deterministic local provider - no AI model involved;
- ``synthetic-fixture``: a hand-written provider response replayed through the live parsing
  path (proves plumbing, not model capability);
- ``live``: a real model provider was called;
- ``blocked``: the verification needs something unavailable here (named in ``blocked_by``).

Run: ``daedelus demo-v11 --out evidence/v1_1`` (add ``--live anthropic|gemini`` to also run the
live gates when credentials exist).
"""

from __future__ import annotations

import json
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

from . import ingest
from .adapters import get_adapter
from .artifacts import create_artifact, native_path
from .editing import apply_manual_edit
from .engine import Engine
from .models import (PlannedOperation, SourceBinding, SourceSegment, TargetSelector, Workflow,
                     WorkflowEdge, WorkflowNode)
from .providers import get_provider
from .semantic import service as semsvc
from .store import ProjectStore, Workspace

ASSETS = Path(__file__).with_name("demo_assets") / "v11"
TRUNK_REGION = [0.03, 0.0, 0.5, 0.97]  # the trunk occupies the left half of the photograph


class Demo:
    def __init__(self, did: str, title: str, verification: str):
        self.id, self.title, self.verification = did, title, verification
        self.checks: list[dict[str, Any]] = []
        self.files: dict[str, str] = {}
        self.notes: list[str] = []
        self.blocked_by: str | None = None
        self.metrics: dict[str, Any] = {}

    def check(self, name: str, ok: bool, detail: Any = "") -> bool:
        self.checks.append({"name": name, "ok": bool(ok), "detail": str(detail)[:500]})
        print(f"  [{'PASS' if ok else 'FAIL'}] {self.id} {name}" + (f" - {detail}" if not ok
                                                                    else ""))
        return bool(ok)

    @property
    def status(self) -> str:
        if self.blocked_by:
            return "blocked"
        return "passed" if self.checks and all(c["ok"] for c in self.checks) else "failed"

    def dump(self) -> dict[str, Any]:
        return {"id": self.id, "title": self.title, "verification": self.verification,
                "status": self.status, "blocked_by": self.blocked_by, "checks": self.checks,
                "files": self.files, "notes": self.notes, "metrics": self.metrics}


def _wf(st: ProjectStore, name: str, art_id: str, cfg: dict[str, Any],
        source_ids: list[str] | None = None, validate: list[str] | None = None) -> Workflow:
    nodes = [WorkflowNode(id="src", type="sources", label="Sources",
                          config={"source_ids": source_ids or []}),
             WorkflowNode(id="art", type="artifact", label="Artifact",
                          config={"artifact_id": art_id}),
             WorkflowNode(id="agent", type="agent", label="Agent", config=cfg)]
    edges = [WorkflowEdge(id="e1", source="art", source_port="artifact", target="agent",
                          target_port="artifact"),
             WorkflowEdge(id="e2", source="src", source_port="sources", target="agent",
                          target_port="sources")]
    if validate:
        nodes.append(WorkflowNode(id="validate", type="validate", label="Validate",
                                  config={"checks": validate, "fail_on_error": True}))
        edges.append(WorkflowEdge(id="e3", source="agent", source_port="revision",
                                  target="validate", target_port="revision"))
    wf = Workflow(name=name, nodes=nodes, edges=edges)
    st.save_workflow(wf)
    return wf


def _copy(st: ProjectStore, rel: str | None, out: Path, name: str, demo: Demo) -> None:
    if not rel:
        return
    src = st.abs(rel)
    if src.exists():
        out.mkdir(parents=True, exist_ok=True)
        dst = out / name
        (shutil.copytree(src, dst, dirs_exist_ok=True) if src.is_dir()
         else shutil.copy2(src, dst))
        demo.files[name] = str(dst)


def _dump(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=1, default=str), encoding="utf-8")


def _analyses(st: ProjectStore, out: Path, demo: Demo) -> None:
    for s in st.list_sources():
        for a in semsvc.analyses_for(st, s.id):
            p = out / "analyses" / f"{s.name.replace(' ', '_')}_{a.provider}_{a.id}.json"
            _dump(p, a.model_dump())
            demo.files.setdefault("analyses", str(out / "analyses"))


# ---------------------------------------------------------------------------- A
def demo_a(st: ProjectStore, out: Path, provider: str) -> tuple[Demo, dict[str, Any]]:
    d = Demo("A", "Image-guided 3D creation (stylised tree, Blender)",
             "integration" if provider == "heuristic" else "live")
    photo = ingest.register_file(st, ASSETS / "tree_trunk_photo.jpg", name="Twisted trunk photo")
    style = ingest.register_file(st, ASSETS / "island_tree_01_render.png",
                                 name="Stylised tree reference")
    guide = ingest.register_text(st, "Game asset guide", (
        "# Game asset guide\n"
        "Stylized low-poly look for a mobile game.\n"
        "The model must have no more than 400 polygons.\n"
        "Keep the trunk visibly twisted.\n"))
    art, _ = create_artifact(st, name="Tree", adapter="blender", template="empty",
                             params={"root_id": "tree", "name": "Tree"})
    tgt = TargetSelector(scope="artifact", artifact_id=art.id)
    bindings = [
        SourceBinding(source_id=photo.id, role="reference",
                      aspects=["geometry", "shape", "color"], target=tgt,
                      segment=SourceSegment(kind="region", region=TRUNK_REGION,
                                            note="the trunk (left half of the photograph)"),
                      instructions="structure of the trunk"),
        SourceBinding(source_id=style.id, role="inspiration", aspects=["style", "color"],
                      target=tgt),
        SourceBinding(source_id=guide.id, role="constraint", constraint="hard",
                      aspects=["geometry", "style"], target=tgt),
    ]
    for b in bindings:
        st.save_binding(b)
    wf = _wf(st, "Create tree", art.id, {
        "instructions": "Create a stylized twisted tree from the references.",
        "provider": provider, "understand": True, "fan_out": False,
        "loop": {"enabled": True, "max_iterations": 3}})
    t0 = time.perf_counter()
    ex = Engine(st).execute(wf.id)
    d.metrics["seconds"] = round(time.perf_counter() - t0, 2)
    nr = ex.run("agent")
    d.check("workflow succeeded", ex.status.value == "succeeded", ex.error)
    art = st.get_artifact(art.id)
    rev = st.get_revision(art.head_revision_id)
    ids = {c.id for c in art.components}
    d.check("trunk, branches and canopy were created as components",
            {"trunk", "canopy", "branch_1"} <= ids, sorted(ids))
    anas = {s.name: semsvc.analyses_for(st, s.id) for s in (photo, style, guide)}
    seg_ana = [a for a in anas["Twisted trunk photo"] if a.segment]
    d.check("the photograph was analysed as the bound trunk region",
            bool(seg_ana) and seg_ana[0].segment["region"] == TRUNK_REGION)
    lim = anas["Game asset guide"][0].derived_constraints if anas["Game asset guide"] else []
    d.check("the guide's polygon budget was extracted with its source text",
            any(c.property == "poly_count" and c.value == 400 for c in lim), lim)
    loop = nr.outputs.get("loop", {})
    d.check("evaluate/revise loop ran and reached its objective",
            loop.get("status") == "achieved", loop.get("stop_reason"))
    polys = rev.measurements.get("tree", {}).get("poly_count")
    d.check("final model satisfies the polygon budget (measured)", polys is not None
            and polys <= 400, polys)
    d.check("native .blend reopens", rev.validation and any(
        c.name == "file_reopens" and c.passed for c in rev.validation.checks))
    d.check("render and GLB previews produced", {"render", "glb"} <= set(rev.previews))
    props = get_adapter("blender").inspect(native_path(st, art), art.entry).properties
    trunk_col = (props.get("trunk", {}).get("material") or {}).get("base_color")
    canopy_col = (props.get("canopy", {}).get("material") or {}).get("base_color")
    d.check("trunk and canopy have distinct materials from the references",
            trunk_col and canopy_col and trunk_col != canopy_col, (trunk_col, canopy_col))
    plan = (nr.units[0].plan or {}) if nr.units else {}
    d.metrics.update(poly_count=polys, iterations=loop.get("iterations_run"),
                     operations=len(plan.get("operations", [])), provider=plan.get("provider"),
                     trunk_color=trunk_col, canopy_color=canopy_col)
    d.notes += plan.get("notes", [])
    o = out / "A"
    for r in st.list_revisions(art.id):
        _copy(st, r.previews.get("render"), o, f"render_rev{r.number}.png", d)
    _copy(st, rev.previews.get("glb"), o, "tree.glb", d)
    _copy(st, f"{art.native_dir}/{art.entry}", o, "tree.blend", d)
    _dump(o / "plan.json", plan)
    _dump(o / "loop.json", loop)
    pkgs = list(st.execution_dir(ex.id).glob("plan_request_agent_*.json"))
    if pkgs:
        _dump(o / "context_package.json",
              json.loads(pkgs[0].read_text(encoding="utf-8")).get("semantic"))
        d.files["context_package"] = str(o / "context_package.json")
    _analyses(st, o, d)
    for b in bindings:  # creation bindings are retired once the object exists
        b.enabled = False
        st.save_binding(b)
    return d, {"art": art, "photo": photo}


# ---------------------------------------------------------------------------- B
def demo_b(st: ProjectStore, out: Path, provider: str, prev: dict[str, Any]) -> Demo:
    d = Demo("B", "Component-specific visual adaptation (one reference changes one component)",
             "integration" if provider == "heuristic" else "live")
    art, photo = prev["art"], prev["photo"]
    flowers = ingest.register_file(st, ASSETS / "jacaranda_tree_render.png",
                                   name="Jacaranda reference")
    trunk_b = SourceBinding(source_id=photo.id, role="reference", aspects=["color", "material"],
                            segment=SourceSegment(kind="region", region=TRUNK_REGION),
                            target=TargetSelector(scope="component", artifact_id=art.id,
                                                  component_id="trunk"))
    canopy_b = SourceBinding(source_id=flowers.id, role="inspiration", aspects=["color"],
                             segment=SourceSegment(kind="region", region=[0.15, 0.0, 0.7, 0.45]),
                             target=TargetSelector(scope="component", artifact_id=art.id,
                                                   component_id="canopy"))
    st.save_binding(trunk_b)
    st.save_binding(canopy_b)
    wf = _wf(st, "Adapt components", art.id, {"provider": provider, "understand": True,
                                              "fan_out": True},
             source_ids=[photo.id, flowers.id])
    eng = Engine(st)
    ex1 = eng.execute(wf.id)
    d.check("first run succeeded", ex1.status.value == "succeeded", ex1.error)
    ran1 = {u.unit.split("#")[-1]: u.status.value for u in ex1.run("agent").units}
    d.check("trunk and canopy units both executed", ran1.get("trunk") == "succeeded" and
            ran1.get("canopy") == "succeeded", ran1)
    before = get_adapter("blender").inspect(native_path(st, art), art.entry)
    # the user changes ONE reference: the trunk now uses the dark base of the trunk
    trunk_b.segment = SourceSegment(kind="region", region=[0.1, 0.72, 0.35, 0.25],
                                    note="bark at the base")
    st.save_binding(trunk_b)
    impact = eng.impact(wf.id)
    stale = [u["unit"].split("#")[-1] for n in impact["nodes"] for u in n.get("units", [])
             if u["stale"]]
    d.check("impact preview: only the trunk unit is stale", stale == ["trunk"], stale)
    ex2 = eng.execute(wf.id)
    d.check("second run succeeded", ex2.status.value == "succeeded", ex2.error)
    ran2 = {u.unit.split("#")[-1]: u.status.value for u in ex2.run("agent").units}
    d.check("only the trunk unit executed; canopy skipped",
            ran2.get("trunk") == "succeeded" and ran2.get("canopy") == "skipped", ran2)
    art = st.get_artifact(art.id)
    rev = st.get_revision(art.head_revision_id)
    d.check("revision changed exactly the trunk", rev.changed_components == ["trunk"],
            rev.changed_components)
    after = get_adapter("blender").inspect(native_path(st, art), art.entry)
    d.check("canopy and branches are byte-for-byte unchanged in the scene",
            all(before.states[c] == after.states[c] for c in ("canopy", "branch_1", "branch_2")))
    d.check("component preservation validated", rev.validation and any(
        c.name == "component_preservation" and c.passed for c in rev.validation.checks))
    o = out / "B"
    for r in st.list_revisions(art.id)[-2:]:
        _copy(st, r.previews.get("render"), o, f"render_rev{r.number}.png", d)
    _dump(o / "impact_after_reference_change.json", impact)
    _dump(o / "attribution.json", [a.model_dump() for a in rev.attribution])
    d.metrics.update(trunk_color_before=(before.properties["trunk"].get("material") or {})
                     .get("base_color"),
                     trunk_color_after=(after.properties["trunk"].get("material") or {})
                     .get("base_color"))
    return d


# ---------------------------------------------------------------------------- C
def demo_c(st: ProjectStore, out: Path, provider: str) -> Demo:
    d = Demo("C", "2D semantic editing of one layer and one region (OpenRaster)",
             "integration" if provider == "heuristic" else "live")
    art, _ = create_artifact(st, name="Poster", adapter="layered2d", template="layers", params={
        "width": 480, "height": 320, "root_id": "poster", "name": "Poster", "layers": [
            {"id": "sun", "name": "Sun", "fill": {"type": "solid", "colors": ["#ffd34d"]},
             "shape": {"type": "ellipse", "box": [0.68, 0.08, 0.18, 0.27]}},
            {"id": "hills", "name": "Hills", "fill": {"type": "gradient",
                                                     "colors": ["#4f8a3a", "#2f5a24"]},
             "shape": {"type": "polygon", "points": [[0, 0.7], [0.3, 0.55], [0.6, 0.68],
                                                    [1, 0.5], [1, 1], [0, 1]]}},
            {"id": "sky", "name": "Sky", "fill": {"type": "gradient",
                                                 "colors": ["#6fa8dc", "#cfe2f3"]}}]})
    flowers = next((s for s in st.list_sources() if s.name == "Jacaranda reference"), None) or \
        ingest.register_file(st, ASSETS / "jacaranda_tree_render.png", name="Jacaranda reference")
    st.save_binding(SourceBinding(
        source_id=flowers.id, role="reference", aspects=["color", "palette"],
        segment=SourceSegment(kind="region", region=[0.15, 0.0, 0.7, 0.45],
                              note="the purple blossom"),
        target=TargetSelector(scope="component", artifact_id=art.id, component_id="sky")))
    ad = get_adapter("layered2d")
    before = ad.inspect(native_path(st, art), art.entry)
    wf = _wf(st, "Recolour sky", art.id, {"provider": provider, "understand": True,
                                          "fan_out": True}, source_ids=[flowers.id])
    ex = Engine(st).execute(wf.id)
    d.check("workflow succeeded", ex.status.value == "succeeded", ex.error)
    art = st.get_artifact(art.id)
    rev = st.get_revision(art.head_revision_id)
    d.check("only the sky layer changed", rev.changed_components == ["sky"],
            rev.changed_components)
    after = ad.inspect(native_path(st, art), art.entry)
    d.check("sun and hills layers unchanged",
            all(before.states[c] == after.states[c] for c in ("sun", "hills")))
    # region targeting inside one layer: a feathered warm grade on the left half of the hills
    r2 = apply_manual_edit(st, art.id, [PlannedOperation(
        op="grade_region", component_id="hills",
        params={"region": [0.0, 0.5, 0.5, 0.5], "tint": "#c08040", "tint_strength": 0.5,
                "feather": 0.15})], message="Warm the left hills", base_revision_id=rev.id)
    d.check("region edit changed only the hills layer", r2.changed_components == ["hills"],
            r2.changed_components)
    after2 = ad.inspect(native_path(st, art), art.entry)
    d.check("sky and sun unchanged by the region edit",
            all(after.states[c] == after2.states[c] for c in ("sky", "sun")))
    d.check("OpenRaster file reopens", r2.validation and any(
        c.name == "file_reopens" and c.passed for c in r2.validation.checks))
    o = out / "C"
    for r in st.list_revisions(art.id):
        _copy(st, r.previews.get("render") or r.previews.get("composite"), o,
              f"poster_rev{r.number}.png", d)
    _copy(st, f"{art.native_dir}/{art.entry}", o, "poster.ora", d)
    return d


# ---------------------------------------------------------------------------- D
REQUIREMENTS = """# Text utilities - requirements
1. Add a function `slugify(text)` to textutils.py.
2. It must lowercase the text and replace every run of non-alphanumeric characters with a
   single hyphen.
3. Leading and trailing hyphens must be removed.
4. Existing functions must keep working; all tests must pass.
"""

SLUGIFY_PLAN = {
    "operations": [
        {"op": "replace_text", "component_id": "textutils.py", "params_json": json.dumps({
            "old": "def title_case(text):",
            "new": "def slugify(text):\n    import re\n    return re.sub(r\"[^a-z0-9]+\", \"-\","
                       " text.lower()).strip(\"-\")\n\n\ndef title_case(text):"}),
         "derived_from": [], "rationale": "requirements 1-3"},
        {"op": "write_file", "component_id": None, "params_json": json.dumps({
            "path": "tests/test_slugify.py",
            "content": "from textutils import slugify\n\n\ndef test_slugify():\n"
                       "    assert slugify('Hello, World!') == 'hello-world'\n"
                       "    assert slugify('  --A  B--  ') == 'a-b'\n"}),
         "derived_from": [], "rationale": "requirement 4: tests"}],
    "interpretations": [], "notes": ["synthetic fixture standing in for a live planner"]}


def demo_d(st: ProjectStore, out: Path, provider: str, fixtures: Path) -> Demo:
    live = provider != "heuristic"
    d = Demo("D", "Document-guided code modification (git + pytest)",
             "live" if live else "synthetic-fixture")
    art, _ = create_artifact(st, name="textutils", adapter="code", template="files", params={
        "files": {"textutils.py": "def title_case(text):\n    return text.title()\n",
                  "tests/test_textutils.py": "from textutils import title_case\n\n\n"
                                             "def test_title_case():\n"
                                             "    assert title_case('a b') == 'A B'\n",
                  "conftest.py": ""}},
        metadata={"test_command": "python -m pytest -q"})
    req = ingest.register_text(st, "Requirements", REQUIREMENTS)
    st.save_binding(SourceBinding(source_id=req.id, role="instruction", aspects=["code"],
                                  target=TargetSelector(scope="artifact", artifact_id=art.id)))
    comps = {c.id: c for c in art.components}
    if not live:
        fx = fixtures / "plan_demo_d.json"
        fx.parent.mkdir(parents=True, exist_ok=True)
        resp = json.loads(json.dumps(SLUGIFY_PLAN))
        for o in resp["operations"]:
            if o["component_id"] == "textutils.py":
                o["component_id"] = next((c.id for c in comps.values()
                                          if c.native_ref == "textutils.py"), "textutils.py")
            elif o["component_id"] is None:
                o["component_id"] = ""  # a new file: no existing component targeted
        fx.write_text(json.dumps({"contract": "plan", "origin": "synthetic",
                                  "provider": "anthropic", "model": None,
                                  "match": {"artifact_name": "textutils"}, "response": resp}),
                     encoding="utf-8")
        d.notes.append("planner response is a SYNTHETIC fixture (no live model available): it "
                       "proves the plan -> git -> tests path, not natural-language understanding")
    wf = _wf(st, "Implement requirements", art.id, {
        "provider": provider if live else "replay", "understand": True,
        "analysis_provider": provider if live else "heuristic", "fan_out": False,
        "instructions": "Implement the bound requirements."}, validate=["tests", "file_reopens"])
    ex = Engine(st).execute(wf.id)
    d.check("workflow succeeded (plan applied, tests run)", ex.status.value == "succeeded",
            ex.error)
    ana = semsvc.analyses_for(st, req.id)
    reqs = [o for a in ana for o in a.observations if o.kind == "requirement"]
    d.check("requirements extracted from the document with line locations",
            len(reqs) >= 3 and all(o.location and o.location.line for o in reqs), len(reqs))
    art = st.get_artifact(art.id)
    rev = st.get_revision(art.head_revision_id)
    d.check("git commit recorded", bool(rev.vcs_commit), rev.vcs_commit)
    d.check("reviewable diff contains slugify", "def slugify" in (rev.diff or ""))
    vr = ex.run("validate")
    rep = (vr.outputs or {}).get("report") or {}
    tests = [c for r in rep.get("artifacts", []) for c in r["report"]["checks"]
             if c.get("name") == "tests"]
    d.check("allowlisted test command executed and passed", tests and tests[0]["passed"],
            tests[:1])
    o = out / "D"
    o.mkdir(parents=True, exist_ok=True)
    (o / "change.diff").write_text(rev.diff or "", encoding="utf-8")
    d.files["diff"] = str(o / "change.diff")
    _dump(o / "validation.json", rep)
    _analyses(st, o, d)
    return d


# ---------------------------------------------------------------------------- E
def demo_e(st: ProjectStore, out: Path, live_provider: str | None) -> Demo:
    d = Demo("E", "Video as optional instructional input", "blocked")
    o = out / "E"
    # a real local video (rendered here so the input is genuine media, not metadata)
    vid = _make_video(o / "input_clip.mp4")
    if vid is None:
        d.blocked_by = "ffmpeg not available to prepare the video input"
        return d
    src = ingest.register_file(st, vid, name="Bevel demo clip")
    heur = semsvc.analyze_source(st, src, "heuristic")
    d.check("local analyser measures frames but reports no steps (no false understanding)",
            heur.status in ("partial", "unavailable") and not [
                x for x in heur.observations if x.kind in ("step", "operation")],
            heur.status)
    _dump(o / "analysis_heuristic.json", heur.model_dump())
    cand = [p for p in ([live_provider] if live_provider else ["gemini", "anthropic"])]
    ran_live = False
    for name in cand:
        prov = get_provider(name)
        ok, why = prov.available()
        if not ok:
            d.notes.append(f"{name}: {why}")
            continue
        ana = semsvc.analyze_source(st, src, name)
        _dump(o / f"analysis_{name}.json", ana.model_dump())
        steps = [x for x in ana.observations if x.kind in ("step", "operation")]
        d.check(f"{name}: timestamped procedural observations extracted",
                bool(steps) and any(x.location and x.location.start_seconds is not None
                                    for x in steps), len(steps))
        d.verification = "live"
        ran_live = True
        break
    if not ran_live:
        d.blocked_by = ("live video understanding needs a multimodal provider with credentials "
                        "(Gemini for video files/URLs, or Claude for sampled frames); none "
                        "available: " + "; ".join(d.notes))
    return d


def _make_video(path: Path) -> Path | None:
    if not shutil.which("ffmpeg"):
        return None
    path.parent.mkdir(parents=True, exist_ok=True)
    # 6 s synthetic screen-capture-like clip: a cube whose edge radius grows (a bevel)
    cmd = ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
           "color=c=#3a3d44:s=320x240:d=6", "-vf",
           "drawbox=x=110:y=70:w=100:h=100:color=#c8c8c8:t=fill,"
           "drawtext=text='Bevel %{eif\\:t*10\\:d}':x=10:y=10:fontcolor=white:fontsize=18",
           "-pix_fmt", "yuv420p", str(path)]
    r = subprocess.run(cmd, capture_output=True, encoding="utf-8", errors="replace", timeout=120)
    if r.returncode != 0:
        cmd[cmd.index("-vf") + 1] = "drawbox=x=110:y=70:w=100:h=100:color=#c8c8c8:t=fill"
        r = subprocess.run(cmd, capture_output=True,
                           encoding="utf-8", errors="replace", timeout=120)
    return path if r.returncode == 0 and path.exists() else None


# ---------------------------------------------------------------------------- runner
def run(out: Path, provider: str = "heuristic", live: str | None = None,
        workspace: Path | None = None) -> dict[str, Any]:
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    ws = Workspace(workspace or out / "workspace")
    _, st = ws.create_project("V1.1 multimodal intelligence", "semantic demonstrations A-E")
    fixtures = out / "fixtures"
    import os

    os.environ["DAEDELUS_FIXTURES"] = str(fixtures)
    demos = []
    t0 = time.perf_counter()
    print("Demo A")
    a, prev = demo_a(st, out, provider)
    demos.append(a)
    print("Demo B")
    demos.append(demo_b(st, out, provider, prev))
    print("Demo C")
    demos.append(demo_c(st, out, provider))
    print("Demo D")
    demos.append(demo_d(st, out, live or provider, fixtures))
    print("Demo E")
    demos.append(demo_e(st, out, live))
    report = {"provider": provider, "live_provider": live, "seconds":
              round(time.perf_counter() - t0, 1),
              "providers": [get_provider(n).describe() for n in
                            ("heuristic", "anthropic", "gemini", "replay")],
              "demos": [x.dump() for x in demos]}
    _dump(out / "demo_v11_report.json", report)
    _write_md(out / "demo_v11_report.md", report)
    ws.close()
    return report


def _write_md(path: Path, rep: dict[str, Any]) -> None:
    lines = ["# V1.1 demonstrations", "",
             f"Planner provider: `{rep['provider']}`; live provider: "
             f"`{rep['live_provider'] or 'none'}`; duration {rep['seconds']} s.", ""]
    for p in rep["providers"]:
        lines.append(f"- provider `{p['name']}`: {'available' if p['available'] else 'unavailable'}"
                     f" ({p['detail']})")
    for d in rep["demos"]:
        lines += ["", f"## {d['id']}. {d['title']}", "",
                  f"Status: **{d['status'].upper()}** - verification type: {d['verification']}"]
        if d["blocked_by"]:
            lines.append(f"Blocked by: {d['blocked_by']}")
        for c in d["checks"]:
            lines.append(f"- [{'x' if c['ok'] else ' '}] {c['name']}"
                         + (f" - {c['detail']}" if not c["ok"] and c["detail"] else ""))
        for n in d["notes"]:
            lines.append(f"- note: {n}")
        if d["metrics"]:
            lines.append(f"- metrics: `{json.dumps(d['metrics'])}`")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
