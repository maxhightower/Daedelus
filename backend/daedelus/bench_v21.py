"""V2.1 creative benchmark (handoff section 5.5): unfamiliar references, real native artifacts.

    daedelus bench-v21 --provider heuristic --out evidence/v2_1/live_ai/deterministic
    daedelus bench-v21 --provider anthropic --out evidence/v2_1/live_ai/claude   # live, opt-in
    daedelus bench-v21 --provider gemini --video-url <public URL> --out ...       # live, opt-in

Tasks (no object-specific recipes in this file - only sources, bindings, instructions,
constraints and checks):

A  3D object from visual references: a real photograph (coffee cup, CC0), a stylized
   illustration and a written spec -> a new Blender object, rendered and measured.
B  Targeted modification: an existing multi-component Blender model; a new real photograph
   (rocket launch, public domain) bound ONLY to one component's colour/material -> the other
   components must be byte-for-byte preserved (component states).
C  Layered image editing: an OpenRaster poster with several layers; a real photograph (cat,
   CC0) bound ONLY to one layer -> the other layers must be preserved.
D  Document-guided code: a requirement document + a small repository -> a schema-valid plan,
   a real git diff and the allowlisted tests passing.
E  Video-guided technique: a video (file or public URL) demonstrating a technique -> temporal
   observations with timestamps and a resulting validated operation. Needs a provider with
   video understanding and a video input; otherwise BLOCKED.

Every task runs twice on fresh projects: ``single`` (one plan, no evaluation loop) and
``iterative`` (Evaluate -> Revise, at most 3 corrections) under a fixed budget, so the effect
of revision can be measured. Deterministic measurements stay authoritative; a model's visual
evaluation is recorded as an *assessment*.

Verification labels: ``deterministic`` (the local heuristic provider - no AI model),
``live`` (real model calls happened), ``blocked`` (credentials or inputs missing),
``failed`` (ran, did not meet the acceptance conditions).
"""

from __future__ import annotations

import hashlib
import json
import shutil
import time
from pathlib import Path
from typing import Any

from . import ingest
from .adapters import get_adapter
from .artifacts import create_artifact, native_path
from .engine import Engine
from .models import SourceBinding, SourceSegment, TargetSelector
from .providers import get_provider
from .scenarios_v11 import _wf
from .semantic import service as semsvc
from .store import ProjectStore, Workspace

ASSETS = Path(__file__).with_name("demo_assets") / "v21"
CUP_REGION = [0.27, 0.03, 0.43, 0.73]
ROCKET_REGION = [0.46, 0.28, 0.09, 0.66]
BUDGET = {"max_model_calls": 24, "max_cost_usd": 2.0, "max_tokens": 600_000,
          "max_seconds": 900, "max_iterations": 3, "call_timeout_s": 180, "max_retries": 2}
RUBRIC = ("relevance", "structural_fidelity", "constraint_adherence", "preservation",
          "usability")


def _sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


class Run:
    def __init__(self, task: str, mode: str, provider: str):
        self.task, self.mode, self.provider = task, mode, provider
        self.checks: list[dict[str, Any]] = []
        self.record: dict[str, Any] = {"task": task, "mode": mode, "provider": provider}
        self.rubric: dict[str, dict[str, Any]] = {k: {"score": None, "basis": "needs review"}
                                                  for k in RUBRIC}

    def check(self, name: str, ok: Any, detail: Any = "") -> bool:
        self.checks.append({"name": name, "ok": bool(ok), "detail": str(detail)[:600]})
        print(f"  [{'PASS' if ok else 'FAIL'}] {self.task}/{self.mode} {name}"
              + ("" if ok else f" - {str(detail)[:200]}"), flush=True)
        return bool(ok)

    def score(self, dim: str, score: int | None, basis: str, note: str = "") -> None:
        self.rubric[dim] = {"score": score, "basis": basis, "note": note}

    def dump(self) -> dict[str, Any]:
        return {**self.record, "checks": self.checks, "rubric": self.rubric,
                "passed": bool(self.checks) and all(c["ok"] for c in self.checks)}


# ---------------------------------------------------------------------------- helpers
def _sources(st: ProjectStore, srcs) -> list[dict[str, Any]]:
    out = []
    for s in srcs:
        p = st.abs(s.locator.path) if s.locator.path else None
        out.append({"id": s.id, "name": s.name, "media_type": s.media_type.value,
                    "sha256": s.content_hash or (_sha(p) if p and p.is_file() else None),
                    "origin": s.provenance.origin if s.provenance else None})
    return out


def _bindings(bs: list[SourceBinding]) -> list[dict[str, Any]]:
    return [{"id": b.id, "source_id": b.source_id, "role": b.role, "aspects": b.aspects,
             "scope": b.target.scope, "component": b.target.component_id,
             "segment": b.segment.model_dump() if b.segment else None,
             "constraint": b.constraint} for b in bs]


def _execute(st: ProjectStore, run: Run, art_id: str, cfg: dict[str, Any],
             validate: list[str] | None = None):
    wf = _wf(st, f"{run.task}-{run.mode}", art_id, cfg, validate=validate)
    wf.parameters = {"budget": BUDGET}
    st.save_workflow(wf)
    t0 = time.perf_counter()
    ex = Engine(st).execute(wf.id)
    run.record["seconds"] = round(time.perf_counter() - t0, 2)
    nr = ex.run("agent")
    run.record["execution_status"] = ex.status.value
    run.record["error"] = ex.error
    run.record["budget"] = ex.budget
    plans = [u.plan for u in nr.units if u.plan]
    run.record["plans"] = [{"provider": p.get("provider"), "model": p.get("model"),
                            "recorded": p.get("recorded"), "operations": p.get("operations"),
                            "interpretations": p.get("interpretations"),
                            "notes": p.get("notes"), "usage": p.get("usage")} for p in plans]
    run.record["validation_decisions"] = [{"unit": u.unit, "status": u.status.value,
                                           "error": u.error} for u in nr.units]
    loop = (nr.outputs or {}).get("loop")
    if loop:
        run.record["loop"] = {k: loop.get(k) for k in (
            "status", "stop_reason", "iterations_run", "usage", "seconds", "unresolved",
            "best_valid_revision_id", "final_revision_id")}
        run.record["evaluations"] = [
            {"iteration": it["iteration"], "passed": it.get("passed"),
             "findings": [f for u in it["units"] for f in u["evaluation"]["findings"]],
             "revisions": it.get("revisions")} for it in loop.get("iterations", [])]
    run.record["live_calls"] = (ex.budget.get("used") or {}).get("model_calls", 0)
    return ex, nr


def _revisions(st: ProjectStore, art_id: str, out: Path, run: Run) -> list[dict[str, Any]]:
    revs = []
    for r in st.list_revisions(art_id):
        item = {"id": r.id, "number": r.number, "message": r.message,
                "previews": r.previews, "validation": r.validation.model_dump()
                if r.validation else None}
        render = r.previews.get("render")
        if render and st.abs(render).is_file():
            dst = out / f"{run.task}_{run.mode}_rev{r.number}.png"
            shutil.copyfile(st.abs(render), dst)
            item["render_file"] = dst.name
        revs.append(item)
    run.record["revisions"] = revs
    return revs


def _analyses(st: ProjectStore, srcs, run: Run) -> None:
    out = []
    for s in srcs:
        for a in semsvc.analyses_for(st, s.id):
            out.append({"source": s.name, "provider": a.provider, "model": a.model,
                        "pathway": a.pathway, "status": a.status, "summary": a.summary,
                        "observations": [{"kind": o.kind, "basis": o.basis, "text": o.text,
                                          "confidence": o.confidence,
                                          "location": o.location.model_dump()
                                          if o.location else None} for o in a.observations],
                        "derived_constraints": [d.model_dump() for d in a.derived_constraints],
                        "limitations": a.limitations, "usage": a.usage.model_dump()
                        if a.usage else None})
    run.record["observations"] = out
    model_obs = [o for a in out if a["provider"] not in ("heuristic", "local")
                 for o in a["observations"]]
    run.record["model_observation_count"] = len(model_obs)


def _states(st: ProjectStore, art_id: str) -> dict[str, Any]:
    art = st.get_artifact(art_id)
    return get_adapter(art.adapter).inspect(native_path(st, art), art.entry).states


# ---------------------------------------------------------------------------- tasks
def task_a(st: ProjectStore, out: Path, provider: str, mode: str) -> Run:
    run = Run("A", mode, provider)
    photo = ingest.register_file(st, ASSETS / "coffee_photo.png", name="Cafe photograph")
    style = ingest.register_file(st, ASSETS / "stylized_mug_illustration.png",
                                 name="Stylized illustration")
    spec = ingest.register_text(st, "Prop specification", (
        "# Prop specification\n"
        "A coffee mug prop for a stylized cafe scene.\n"
        "Height 0.1 m. The model must have no more than 600 polygons.\n"
        "Match the cup's proportions in the photograph; take colour and style cues from the "
        "illustration.\n"))
    art, _ = create_artifact(st, name="Mug", adapter="blender", template="empty",
                             params={"root_id": "mug", "name": "Mug"})
    tgt = TargetSelector(scope="artifact", artifact_id=art.id)
    bs = [SourceBinding(source_id=photo.id, role="reference",
                        aspects=["geometry", "shape", "proportion"], target=tgt,
                        segment=SourceSegment(kind="region", region=CUP_REGION,
                                              note="the cup, without saucer and spoon")),
          SourceBinding(source_id=style.id, role="inspiration", aspects=["style", "color"],
                        target=tgt),
          SourceBinding(source_id=spec.id, role="constraint", constraint="hard",
                        aspects=["geometry"], target=tgt)]
    for b in bs:
        st.save_binding(b)
    run.record["sources"] = _sources(st, [photo, style, spec])
    run.record["bindings"] = _bindings(bs)
    ex, nr = _execute(st, run, art.id, {
        "instructions": "Create the mug described by the specification from the references.",
        "provider": provider, "understand": True, "fan_out": False,
        "loop": {"enabled": mode == "iterative", "max_iterations": 3}})
    _analyses(st, [photo, style, spec], run)
    revs = _revisions(st, art.id, out, run)
    art = st.get_artifact(art.id)
    head = st.get_revision(art.head_revision_id)
    comps = [c.id for c in art.components if c.id != "mug"]
    polys = head.measurements.get("mug", {}).get("poly_count")
    height = head.measurements.get("mug", {}).get("height")
    run.record["measurements"] = head.measurements.get("mug", {})
    run.check("execution finished", ex.status.value in ("succeeded", "failed"), ex.error)
    run.check("a native Blender object with geometry was created", comps, comps)
    run.check("the .blend file reopens", head.validation and any(
        c.name == "file_reopens" and c.passed for c in head.validation.checks))
    run.check("render produced", head.previews.get("render"))
    ok_poly = bool(comps) and polys is not None and 0 < polys <= 600
    ok_h = height is not None and abs(height - 0.1) <= 0.01
    run.check("polygon budget met (measured)", ok_poly, polys)
    run.check("height 0.1 m met (measured)", ok_h, height)
    run.score("constraint_adherence", 2 if ok_poly and ok_h else (1 if ok_poly or ok_h else 0),
              "measured", f"poly_count={polys}, height={height}")
    run.score("preservation", None, "not applicable (new object)")
    run.record["components"] = comps
    run.record["revision_count"] = len(revs)
    return run


ROCKET = [
    {"id": "rocket", "name": "Rocket", "primitive": "empty"},
    {"id": "body", "name": "Body", "primitive": "cylinder", "parent": "rocket",
     "size": [0.18, 0.18, 1.6], "location": [0, 0, 0.8]},
    {"id": "nose", "name": "Nose", "primitive": "cone", "parent": "rocket",
     "size": [0.18, 0.18, 0.4], "location": [0, 0, 1.8]},
    {"id": "fins", "name": "Fins", "primitive": "cube", "parent": "rocket",
     "size": [0.5, 0.04, 0.3], "location": [0, 0, 0.15]},
    {"id": "tower", "name": "Tower", "primitive": "cube", "parent": "rocket",
     "size": [0.2, 0.2, 2.2], "location": [0.8, 0, 1.1]},
]


def task_b(st: ProjectStore, out: Path, provider: str, mode: str) -> Run:
    run = Run("B", mode, provider)
    art, _ = create_artifact(st, name="Launch scene", adapter="blender", template="components",
                             params={"components": ROCKET})
    photo = ingest.register_file(st, ASSETS / "rocket_launch_photo.jpg", name="Launch photo")
    b = SourceBinding(source_id=photo.id, role="reference", aspects=["color", "material"],
                      target=TargetSelector(scope="component", artifact_id=art.id,
                                            component_id="body"),
                      segment=SourceSegment(kind="region", region=ROCKET_REGION,
                                            note="the launch vehicle's body"))
    st.save_binding(b)
    run.record["sources"] = _sources(st, [photo])
    run.record["bindings"] = _bindings([b])
    before = _states(st, art.id)
    ex, nr = _execute(st, run, art.id, {
        "instructions": "Update only the bound component's colour and material from the photo.",
        "provider": provider, "understand": True, "fan_out": True,
        "loop": {"enabled": mode == "iterative", "max_iterations": 3}})
    _analyses(st, [photo], run)
    _revisions(st, art.id, out, run)
    after = _states(st, art.id)
    others = ["nose", "fins", "tower"]
    changed_body = before.get("body") != after.get("body")
    preserved = all(before.get(c) == after.get(c) for c in others)
    run.check("execution finished", ex.status.value in ("succeeded", "failed"), ex.error)
    run.check("the bound component changed", changed_body)
    run.check("unrelated components preserved exactly (component states)", preserved,
              {c: before.get(c) == after.get(c) for c in others})
    run.score("preservation", 2 if preserved else 0, "measured (component state digests)")
    return run


POSTER = [
    {"id": "background", "name": "Background", "fill": {"type": "solid",
                                                        "colors": ["#1d2433"]}},
    {"id": "subject", "name": "Subject", "fill": {"type": "solid", "colors": ["#888888"]},
     "shape": {"type": "ellipse", "box": [0.2, 0.15, 0.6, 0.7]}},
    {"id": "frame", "name": "Frame", "fill": {"type": "solid", "colors": ["#e8d9a8"]},
     "shape": {"type": "rect", "box": [0.0, 0.9, 1.0, 0.1]}},
]


def task_c(st: ProjectStore, out: Path, provider: str, mode: str) -> Run:
    run = Run("C", mode, provider)
    art, _ = create_artifact(st, name="Poster", adapter="layered2d", template="layers",
                             params={"width": 320, "height": 400, "root_id": "poster",
                                     "layers": POSTER})
    photo = ingest.register_file(st, ASSETS / "cat_photo.png", name="Cat photograph")
    b = SourceBinding(source_id=photo.id, role="reference", aspects=["color", "palette",
                                                                      "content"],
                      target=TargetSelector(scope="component", artifact_id=art.id,
                                            component_id="subject"))
    st.save_binding(b)
    run.record["sources"] = _sources(st, [photo])
    run.record["bindings"] = _bindings([b])
    before = _states(st, art.id)
    ex, nr = _execute(st, run, art.id, {
        "instructions": "Rework the bound layer from the photograph; leave other layers alone.",
        "provider": provider, "understand": True, "fan_out": True,
        "loop": {"enabled": mode == "iterative", "max_iterations": 3}})
    _analyses(st, [photo], run)
    _revisions(st, art.id, out, run)
    after = _states(st, art.id)
    preserved = all(before.get(c) == after.get(c) for c in ("background", "frame"))
    run.check("execution finished", ex.status.value in ("succeeded", "failed"), ex.error)
    run.check("the bound layer changed", before.get("subject") != after.get("subject"))
    run.check("unrelated layers preserved exactly", preserved)
    a2 = st.get_artifact(art.id)
    ora = native_path(st, a2) / a2.entry
    run.check("the OpenRaster file is a valid zip with stack.xml", ora.is_file() and
              __import__("zipfile").is_zipfile(ora) and "stack.xml" in
              __import__("zipfile").ZipFile(ora).namelist())
    run.score("preservation", 2 if preserved else 0, "measured (layer state digests)")
    return run


REPO = {
    "textutil.py": "def slugify(text):\n    raise NotImplementedError\n",
    "test_textutil.py": ("from textutil import slugify\n\n\n"
                         "def test_basic():\n    assert slugify('Hello, World!') == "
                         "'hello-world'\n\n\n"
                         "def test_accents_and_spaces():\n    assert slugify('  Crème brûlée  "
                         "recipes ') == 'creme-brulee-recipes'\n"),
}
REQUIREMENT = ("# Requirement: URL slugs\n"
               "Implement `slugify(text)` in textutil.py. It must lowercase the text, remove "
               "accents (NFKD normalisation, drop combining marks), replace every run of "
               "characters that are not ASCII letters or digits with one hyphen and strip "
               "leading/trailing hyphens. The existing tests must pass. Use only the Python "
               "standard library.\n")


def task_d(st: ProjectStore, out: Path, provider: str, mode: str) -> Run:
    run = Run("D", mode, provider)
    proj = st.get_project()
    proj.settings.allowed_commands = list({*proj.settings.allowed_commands, "python -m pytest"})
    st.save_project(proj)
    art, _ = create_artifact(st, name="textutil", adapter="code", template="files",
                             params={"files": REPO},
                             metadata={"test_command": "python -m pytest -q"})
    req = ingest.register_text(st, "Requirement", REQUIREMENT)
    b = SourceBinding(source_id=req.id, role="constraint", constraint="hard",
                      aspects=["behavior", "code"],
                      target=TargetSelector(scope="artifact", artifact_id=art.id))
    st.save_binding(b)
    run.record["sources"] = _sources(st, [req])
    run.record["bindings"] = _bindings([b])
    ex, nr = _execute(st, run, art.id, {
        "instructions": "Implement the requirement in the repository.",
        "provider": provider, "understand": True, "fan_out": False,
        "loop": {"enabled": mode == "iterative", "max_iterations": 3}},
        validate=["tests"])
    _analyses(st, [req], run)
    a2 = st.get_artifact(art.id)
    nat = native_path(st, a2)
    import subprocess
    first = subprocess.run(["git", "-C", str(nat), "rev-list", "--max-parents=0", "HEAD"],
                           capture_output=True, text=True).stdout.strip().splitlines()[:1]
    diff = subprocess.run(["git", "-C", str(nat), "diff", *(first or []), "HEAD"],
                          capture_output=True, text=True).stdout[-8000:] if first else ""
    run.record["git_diff"] = diff
    vr = ex.run("validate")
    rep = json.dumps(vr.outputs or {})
    tests_ok = vr.status.value == "succeeded" and "passed" in rep and "failed" not in rep
    run.check("a schema-valid plan was produced", run.record["plans"] and all(
        u["status"] != "failed" for u in run.record["validation_decisions"]),
        run.record["validation_decisions"])
    run.check("a real git diff of textutil.py against the starting commit exists",
              "a/textutil.py" in diff, diff[:300])
    run.check("allowlisted tests pass", tests_ok, rep[:400])
    run.score("constraint_adherence", 2 if tests_ok else 0, "measured (tests)")
    run.score("preservation", 0 if "a/test_textutil.py" in diff else 2,
              "measured (the existing tests were not edited)")
    return run


def task_e(st: ProjectStore, out: Path, provider: str, mode: str, video: str | None) -> Run:
    run = Run("E", mode, provider)
    prov = get_provider(provider)
    paths = set(prov.pathways)
    can = any("video" in p and "frames" not in p for p in paths) and prov.live
    if not video:
        run.record["blocked_by"] = ("no video input: pass --video-file or --video-url (a real, "
                                    "appropriately licensed video demonstrating a technique)")
        return run
    if not can:
        run.record["blocked_by"] = (f"provider '{provider}' cannot interpret video content "
                                    "(pathways: " + ", ".join(sorted(paths)) + ")")
        return run
    ok, why = prov.available()
    if not ok:
        run.record["blocked_by"] = f"provider '{provider}' unavailable: {why}"
        return run
    if video.startswith(("http://", "https://")):
        src = ingest.register_url(st, video, name="Technique video")
    else:
        src = ingest.register_file(st, Path(video), name="Technique video")
    ana = semsvc.analyze_source(st, src, provider)
    timed = [o for o in ana.observations if o.location and o.location.start_seconds is not None]
    run.record["sources"] = _sources(st, [src])
    _analyses(st, [src], run)
    run.check("the model analysed the video content (not metadata)",
              ana.pathway.startswith("video") and ana.status != "unavailable", ana.pathway)
    run.check("observations carry timestamps from the video", timed, len(timed))
    art, _ = create_artifact(st, name="Technique target", adapter="blender",
                             template="components", params={"components": ROCKET[:3]})
    b = SourceBinding(source_id=src.id, role="technique", aspects=["geometry", "style"],
                      target=TargetSelector(scope="component", artifact_id=art.id,
                                            component_id="body"))
    st.save_binding(b)
    ex, nr = _execute(st, run, art.id, {
        "instructions": "Apply the demonstrated technique (not the demonstrated asset) to the "
                        "bound component.", "provider": provider, "understand": True,
        "fan_out": True, "loop": {"enabled": mode == "iterative", "max_iterations": 3}})
    run.check("a validated operation resulted", ex.status.value == "succeeded" and
              any(p.get("operations") for p in run.record["plans"]), ex.error)
    return run


TASKS = {"A": task_a, "B": task_b, "C": task_c, "D": task_d}


def run(out: Path, provider: str = "heuristic", tasks: str = "ABCDE",
        video: str | None = None, evaluator: str | None = None) -> dict[str, Any]:
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    prov = get_provider(provider)
    ok, why = prov.available()
    rep: dict[str, Any] = {"provider": provider, "provider_live": prov.live,
                           "provider_available": ok, "provider_detail": why,
                           "budget_per_run": BUDGET, "runs": [], "started": time.time()}
    if prov.live and not ok:
        rep["status"] = "blocked"
        rep["blocked_by"] = (f"live provider '{provider}' unavailable: {why}. Required: "
                             + {"anthropic": "ANTHROPIC_API_KEY (or ANTHROPIC_AUTH_TOKEN / an "
                                             "`ant auth login` profile) and the anthropic SDK",
                                "gemini": "GEMINI_API_KEY / GOOGLE_API_KEY (or Vertex AI "
                                          "settings) and the google-genai SDK"}.get(provider,
                                                                                     "credentials"))
        _write(out, rep)
        print(f"BLOCKED: {rep['blocked_by']}")
        return rep
    ws = Workspace(out / "workspace")
    for t in tasks:
        for mode in ("single", "iterative"):
            _, st = ws.create_project(f"bench {t} {mode}")
            print(f"== task {t} ({mode})", flush=True)
            try:
                r = task_e(st, out, provider, mode, video) if t == "E" else \
                    TASKS[t](st, out, provider, mode)
            except Exception as exc:  # a crash is a result too
                r = Run(t, mode, provider)
                r.check("task completed without exceptions", False, repr(exc))
            d = r.dump()
            d["verification"] = ("blocked" if d.get("blocked_by") else
                                 "live" if d.get("live_calls") else "deterministic")
            if d.get("blocked_by"):
                d["passed"] = None
            rep["runs"].append(d)
    rep["seconds"] = round(time.time() - rep.pop("started"), 1)
    rep["status"] = "live" if any(r["verification"] == "live" for r in rep["runs"]) else \
        "deterministic"
    _write(out, rep)
    return rep


def _write(out: Path, rep: dict[str, Any]) -> None:
    (out / "bench_report.json").write_text(json.dumps(rep, indent=1, default=str))
    L = [f"# V2.1 creative benchmark — provider `{rep['provider']}`", "",
         f"Status: **{rep.get('status')}**"
         + (f" — {rep['blocked_by']}" if rep.get("blocked_by") else ""), "",
         "Budget per run: `" + json.dumps(rep["budget_per_run"]) + "`", ""]
    if rep.get("runs"):
        L += ["| Task | Mode | Verification | Checks | Live calls | Cost | Seconds | Rubric "
              "(measured) |", "|---|---|---|---|---|---|---|---|"]
        for r in rep["runs"]:
            used = (r.get("budget") or {}).get("used") or {}
            rub = ", ".join(f"{k}={v['score']}" for k, v in r["rubric"].items()
                            if v["score"] is not None) or "—"
            n_ok = sum(c["ok"] for c in r["checks"])
            L.append(f"| {r['task']} | {r['mode']} | {r['verification']}"
                     + (f" ({r['blocked_by']})" if r.get("blocked_by") else "")
                     + f" | {n_ok}/{len(r['checks'])} | {r.get('live_calls', 0)} | "
                     f"{used.get('cost', '—')} | {r.get('seconds', '—')} | {rub} |")
        L += ["", "Rubric dimensions not measurable by the harness (relevance, structural "
              "fidelity, usability) are left for human review and are never filled in from a "
              "model's own assessment.", ""]
        for r in rep["runs"]:
            L += [f"## {r['task']} / {r['mode']}", ""]
            for c in r["checks"]:
                L.append(f"- [{'x' if c['ok'] else ' '}] {c['name']}"
                         + ("" if c["ok"] else f" — {c['detail'][:200]}"))
            L.append("")
    (out / "bench_report.md").write_text("\n".join(L) + "\n")
