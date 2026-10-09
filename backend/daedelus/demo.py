"""Multimodal, component-scoped demonstration project.

Everything here goes through the same public services the studio UI uses
(source registration, bindings, artifact creation, workflows). The fixtures
(a table, a layered background, a manifest repository) are *data*: no engine,
adapter or planner code knows about tables, legs or backgrounds.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFilter

from . import ingest
from .artifacts import create_artifact
from .models import (
    Constraint,
    SourceBinding,
    TargetSelector,
    Workflow,
    WorkflowEdge,
    WorkflowNode,
)
from .store import ProjectStore, Workspace

VIDEO_URL = "https://www.youtube.com/watch?v=vLzY4ApZZcE"  # "Bevel Modifier | Blender Tutorial"

STYLE_GUIDE = """# Project style guide

Applies to every asset in the scene.

mood: warm, calm, late afternoon
base_color: #8a5a3b
tint: #f0c08a
roughness: 0.55
bevel: 0.006
saturation: -0.1

Keep silhouettes simple; avoid ornament.
"""

STYLE_GUIDE_V2 = """# Project style guide (revision 2)

Applies to every asset in the scene.

mood: cool, overcast morning
base_color: #4f6b78
tint: #9fc3d8
roughness: 0.35
bevel: 0.01
saturation: -0.25

Keep silhouettes simple; avoid ornament.
"""

CODE_CONVENTIONS = """# Manifest conventions

indent: 2
sort_keys: true

Every artifact entry must record its revision and native file path.
"""

MANIFEST_FILES = {
    "scene.json": json.dumps({"title": "Daedelus demo scene", "artifacts": {}}, indent=2) + "\n",
    "scene_manifest.py": '''"""Loads and checks the scene manifest written by the Daedelus workflow."""

import json
import os
from pathlib import Path

REQUIRED = ("artifact_id", "name", "type", "revision", "revision_id", "native")


def load(path="scene.json"):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def problems(manifest, project_root=None):
    root = Path(project_root or os.environ.get("DAEDELUS_PROJECT_ROOT", "."))
    out = []
    for key, entry in manifest.get("artifacts", {}).items():
        for field in REQUIRED:
            if field not in entry:
                out.append(f"{key}: missing {field}")
        if entry.get("revision", 0) < 1:
            out.append(f"{key}: revision must be >= 1")
        native = entry.get("native")
        if native and not (root / native).exists():
            out.append(f"{key}: native file {native} does not exist")
    return out
''',
    "tests/test_manifest.py": '''import scene_manifest


def test_manifest_is_consistent():
    manifest = scene_manifest.load()
    assert manifest["title"]
    assert scene_manifest.problems(manifest) == []


def test_entries_reference_revisions():
    for entry in scene_manifest.load().get("artifacts", {}).values():
        assert entry["revision"] >= 1
''',
}


# ---------------------------------------------------------------------------
# procedural fixture media (honest, controllable test inputs)
# ---------------------------------------------------------------------------

def make_fixture_media(out: Path) -> dict[str, Path]:
    out.mkdir(parents=True, exist_ok=True)
    files: dict[str, Path] = {}

    # 1. table reference: wide, low table seen from the front (silhouette aspect ~2.5)
    im = Image.new("RGB", (640, 400), (236, 233, 226))
    d = ImageDraw.Draw(im)
    wood = (120, 78, 48)
    d.rectangle([70, 150, 570, 172], fill=wood)
    for x in (88, 530):
        d.rectangle([x, 172, x + 22, 350], fill=wood)
    files["table_reference"] = out / "table_reference.png"
    im.save(files["table_reference"])

    # 2. leg inspiration A: tall dark leg, wide at the top, narrow at the floor
    im = Image.new("RGB", (300, 500), (244, 240, 232))
    d = ImageDraw.Draw(im)
    d.polygon([(110, 40), (190, 40), (165, 470), (135, 470)], fill=(58, 36, 24))
    files["leg_inspiration"] = out / "leg_inspiration.png"
    im.save(files["leg_inspiration"])

    # 2b. leg inspiration B: flared teal leg, narrow at the top, wide at the floor
    im = Image.new("RGB", (300, 500), (244, 240, 232))
    d = ImageDraw.Draw(im)
    d.polygon([(132, 40), (168, 40), (196, 470), (104, 470)], fill=(32, 128, 128))
    files["leg_inspiration_b"] = out / "leg_inspiration_b.png"
    im.save(files["leg_inspiration_b"])

    # 3. background inspiration: warm sunset sky
    files["background_sunset"] = out / "background_sunset.png"
    _sky(files["background_sunset"], [(250, 170, 90), (232, 110, 96), (96, 60, 110)],
         sun=(255, 226, 150))
    # 3b. alternative background inspiration: cold dawn
    files["background_dawn"] = out / "background_dawn.png"
    _sky(files["background_dawn"], [(196, 222, 240), (120, 170, 214), (46, 72, 120)],
         sun=(250, 250, 236))

    files["style_guide"] = out / "style_guide.md"
    files["style_guide"].write_text(STYLE_GUIDE, encoding="utf-8")
    files["code_conventions"] = out / "manifest_conventions.md"
    files["code_conventions"].write_text(CODE_CONVENTIONS, encoding="utf-8")
    return files


def _sky(path: Path, stops: list[tuple[int, int, int]], sun: tuple[int, int, int]) -> None:
    w, h = 640, 400
    im = Image.new("RGB", (w, h))
    px = im.load()
    for y in range(h):
        t = y / (h - 1) * (len(stops) - 1)
        i = min(int(t), len(stops) - 2)
        f = t - i
        c = tuple(int(stops[i][k] * (1 - f) + stops[i + 1][k] * f) for k in range(3))
        for x in range(w):
            px[x, y] = c
    glow = Image.new("RGB", (w, h), (0, 0, 0))
    ImageDraw.Draw(glow).ellipse([380, 210, 470, 300], fill=sun)
    glow = glow.filter(ImageFilter.GaussianBlur(6))
    im = Image.composite(glow, im, glow.convert("L").point(lambda v: 255 if v > 40 else 0))
    im.save(path)


# ---------------------------------------------------------------------------
# artifacts
# ---------------------------------------------------------------------------

TABLE_COMPONENTS: list[dict[str, Any]] = [
    {"id": "table", "name": "Table", "primitive": "empty"},
    {"id": "tabletop", "name": "Tabletop", "primitive": "cube", "size": [1.2, 0.7, 0.05],
     "location": [0, 0, 0.745], "parent": "table"},
    {"id": "legs", "name": "Legs", "primitive": "empty", "location": [0, 0, 0], "parent": "table"},
] + [
    {"id": f"leg_{n}", "name": f"Leg {n.upper()}", "primitive": "cube", "size": [0.06, 0.06, 0.72],
     "location": [x, y, 0.36], "parent": "legs"}
    for n, x, y in (("fl", 0.52, -0.28), ("fr", -0.52, -0.28), ("bl", 0.52, 0.28),
                    ("br", -0.52, 0.28))
]

BACKGROUND_LAYERS: list[dict[str, Any]] = [  # top-most first
    {"id": "hills", "name": "Hills", "fill": {"type": "gradient", "colors": ["#6f8f5a", "#3f5a35"]},
     "shape": {"type": "polygon", "points": [[0, 0.72], [0.18, 0.62], [0.42, 0.7], [0.66, 0.58],
                                             [0.86, 0.66], [1, 0.6], [1, 1], [0, 1]]}},
    {"id": "sun", "name": "Sun", "fill": {"type": "solid", "colors": ["#fff2c4"]},
     "shape": {"type": "ellipse", "box": [0.62, 0.18, 0.12, 0.19]}},
    {"id": "sky", "name": "Sky", "fill": {"type": "gradient", "colors": ["#a9c8e6", "#dfe9f2"]}},
]


def build_demo(store: ProjectStore, media_dir: Path, *, include_video: bool = True
               ) -> dict[str, Any]:
    files = make_fixture_media(media_dir)
    S = {}
    S["style"] = ingest.register_file(store, files["style_guide"], name="Project style guide")
    S["table_ref"] = ingest.register_file(store, files["table_reference"],
                                          name="Table reference photo")
    S["leg_insp"] = ingest.register_file(store, files["leg_inspiration"],
                                         name="Leg inspiration (tapered)")
    S["leg_insp_b"] = ingest.register_file(store, files["leg_inspiration_b"],
                                           name="Leg inspiration (flared)")
    S["bg_sunset"] = ingest.register_file(store, files["background_sunset"],
                                          name="Background inspiration (sunset)")
    S["bg_dawn"] = ingest.register_file(store, files["background_dawn"],
                                        name="Background inspiration (dawn)")
    S["conventions"] = ingest.register_file(store, files["code_conventions"],
                                            name="Manifest conventions")
    if include_video:
        S["video"] = ingest.register_url(store, VIDEO_URL, name="Bevel modifier tutorial (video)")

    table, _ = create_artifact(store, name="Table", adapter="blender", template="components",
                               params={"components": TABLE_COMPONENTS})
    background, _ = create_artifact(store, name="Background", adapter="layered2d",
                                    template="layers",
                                    params={"width": 640, "height": 400, "root_id": "background",
                                            "name": "Background", "layers": BACKGROUND_LAYERS})
    manifest, _ = create_artifact(store, name="Scene manifest", adapter="code", template="files",
                                  artifact_type="code",
                                  params={"files": MANIFEST_FILES},
                                  metadata={"test_command": "python -m pytest -q"})

    def comp(a, cid):
        return TargetSelector(scope="component", artifact_id=a.id, component_id=cid)

    def art(a):
        return TargetSelector(scope="artifact", artifact_id=a.id)

    B = {}
    B["style"] = store.save_binding(SourceBinding(
        source_id=S["style"].id, role="guideline", target=TargetSelector(scope="project"),
        aspects=["style", "color", "material", "palette", "mood"], strength=0.8,
        instructions="Global art direction for every artifact."))
    B["table_geometry"] = store.save_binding(SourceBinding(
        source_id=S["table_ref"].id, role="reference", target=art(table),
        aspects=["geometry", "proportions"], strength=0.8,
        instructions="Match the overall proportions of the table in the photo."))
    B["table_eval"] = store.save_binding(SourceBinding(
        source_id=S["table_ref"].id, role="evaluation", target=art(table),
        aspects=["color"], strength=1.0,
        instructions="Benchmark: rendered colours should stay close to this photo."))
    B["legs"] = store.save_binding(SourceBinding(
        source_id=S["leg_insp"].id, role="inspiration", target=comp(table, "legs"),
        aspects=["shape", "color"], strength=0.9, constraint="hard",
        constraints=[Constraint(property="height", op="preserve", tolerance=1e-3,
                                description="Preserve the legs' existing height")],
        instructions="Borrow the taper and the darker wood tone for the legs only."))
    B["bg_palette"] = store.save_binding(SourceBinding(
        source_id=S["bg_sunset"].id, role="reference", target=art(background),
        aspects=["palette", "lighting"], strength=0.7,
        instructions="Overall colour and light of the background."))
    B["sky"] = store.save_binding(SourceBinding(
        source_id=S["bg_sunset"].id, role="inspiration", target=comp(background, "sky"),
        aspects=["palette"], strength=0.8,
        instructions="Sky gradient inspired by this image (same source, second binding)."))
    B["conventions"] = store.save_binding(SourceBinding(
        source_id=S["conventions"].id, role="guideline", target=art(manifest),
        aspects=["code_style", "convention"], strength=1.0))
    if include_video:
        B["video"] = store.save_binding(SourceBinding(
            source_id=S["video"].id, role="technique", target=comp(table, "tabletop"),
            aspects=["technique"], strength=0.6,
            instructions="Edge treatment technique for the tabletop."))

    wf = build_workflow(table.id, background.id, manifest.id)
    wf = store.save_workflow(wf)
    return {"sources": {k: v.id for k, v in S.items()}, "bindings": {k: v.id for k, v in B.items()},
            "artifacts": {"table": table.id, "background": background.id,
                          "manifest": manifest.id},
            "workflow_id": wf.id, "media": {k: str(v) for k, v in files.items()}}


def build_workflow(table_id: str, background_id: str, manifest_id: str) -> Workflow:
    N = WorkflowNode
    nodes = [
        N(id="sources", type="sources", label="All sources", position={"x": 0, "y": 200}),
        N(id="table", type="artifact", label="Table (3D)", config={"artifact_id": table_id},
          position={"x": 0, "y": 0}),
        N(id="background", type="artifact", label="Background (2D)",
          config={"artifact_id": background_id}, position={"x": 0, "y": 400}),
        N(id="manifest", type="artifact", label="Scene manifest (code)",
          config={"artifact_id": manifest_id}, position={"x": 0, "y": 600}),
        N(id="model_agent", type="agent", label="3D agent",
          config={"fan_out": True, "validation": ["file_reopens"],
                  "retry": {"max_attempts": 2, "backoff_seconds": 0}},
          position={"x": 320, "y": 60}),
        N(id="paint_agent", type="agent", label="2D agent",
          config={"fan_out": True, "validation": ["file_reopens"]},
          position={"x": 320, "y": 380}),
        N(id="code_agent", type="agent", label="Manifest agent",
          config={"fan_out": False, "allowed_ops": ["update_json"],
                  "instructions": "manifest: /artifacts\nmanifest_file: scene.json",
                  "validation": ["file_reopens", "json_valid"]},
          position={"x": 640, "y": 560}),
        N(id="validate", type="validate", label="Validate",
          config={"checks": ["file_reopens", "component_preservation", "tests", "json_valid",
                             "evaluation"], "fail_on_error": True},
          position={"x": 960, "y": 300}),
        N(id="export", type="export", label="Export", config={"formats": ["glb", "png"]},
          position={"x": 960, "y": 60}),
    ]
    E = WorkflowEdge
    edges = [
        E(id="e1", source="table", source_port="artifact", target="model_agent",
          target_port="artifact"),
        E(id="e2", source="sources", source_port="sources", target="model_agent",
          target_port="sources"),
        E(id="e3", source="background", source_port="artifact", target="paint_agent",
          target_port="artifact"),
        E(id="e4", source="sources", source_port="sources", target="paint_agent",
          target_port="sources"),
        E(id="e5", source="manifest", source_port="artifact", target="code_agent",
          target_port="artifact"),
        E(id="e6", source="model_agent", source_port="revision", target="code_agent",
          target_port="after"),
        E(id="e7", source="paint_agent", source_port="revision", target="code_agent",
          target_port="after"),
        E(id="e8", source="model_agent", source_port="revision", target="validate",
          target_port="revision"),
        E(id="e9", source="paint_agent", source_port="revision", target="validate",
          target_port="revision"),
        E(id="e10", source="code_agent", source_port="revision", target="validate",
          target_port="revision"),
        E(id="e11", source="model_agent", source_port="revision", target="export",
          target_port="revision"),
        E(id="e12", source="paint_agent", source_port="revision", target="export",
          target_port="revision"),
    ]
    return Workflow(name="Multimodal scene", description="Scoped multimodal sources -> 3D table, "
                    "2D background and a code manifest", nodes=nodes, edges=edges)


def create_demo_project(workspace: Workspace, media_dir: Path, **kw: Any):
    project, store = workspace.create_project("Multimodal demo",
                                              "Component-scoped multimodal demonstration")
    info = build_demo(store, media_dir, **kw)
    return project, store, info
