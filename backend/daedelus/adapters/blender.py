"""Blender adapter: real .blend editing through Blender's Python API in a subprocess.

Each call runs an isolated ``blender --background`` worker process; the
engine never imports ``bpy``. Native files stay fully editable .blend scenes
(non-destructive modifiers, named materials, parented objects).
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from ..models import Component, PlannedOperation, ValidationReport
from .base import (
    HEX_COLOR,
    Adapter,
    AdapterError,
    AdapterInfo,
    ApplyResult,
    InspectResult,
    OperationSpec,
)

WORKER = Path(__file__).with_name("blender_worker.py")
VEC3 = {"type": "array", "items": {"type": "number"}, "minItems": 3, "maxItems": 3}
ENTRY = "model.blend"


def find_blender() -> str | None:
    env = os.environ.get("DAEDELUS_BLENDER")
    if env and Path(env).exists():
        return env
    found = shutil.which("blender")
    if found:
        return found
    cands = [Path("/opt/blender/blender"), Path("/Applications/Blender.app/Contents/MacOS/Blender")]
    pf = Path("C:/Program Files/Blender Foundation")
    if pf.is_dir():  # newest installed version first
        cands = sorted(pf.glob("Blender */blender.exe"), reverse=True) + cands
    for cand in cands:
        if cand.exists():
            return str(cand)
    return None


class BlenderAdapter(Adapter):
    name = "blender"
    version = "2"

    def __init__(self, timeout: float = 300.0):
        self.timeout = timeout

    # ------------------------------------------------------------------
    def check_environment(self) -> tuple[bool, str]:
        exe = find_blender()
        if not exe:
            return False, ("Blender executable not found (set DAEDELUS_BLENDER or put "
                           "'blender' on PATH)")
        return True, exe

    def info(self) -> AdapterInfo:
        ok, detail = self.check_environment()
        sub = {"exclude": {"type": "array", "items": {"type": "string"}}}
        return AdapterInfo(
            name=self.name, version=self.version,
            description="3D scenes edited through Blender's Python API (headless worker process).",
            artifact_types=["model3d"], native_formats=[".blend"],
            preview_formats=["png (Cycles CPU render)", "glb"],
            export_formats=["glb", "obj", "stl"],
            scopes=["artifact", "group", "mesh", "curve"],
            measurements=["width", "depth", "height", "vertex_count", "object_count", "poly_count"],
            validation=["component_preservation", "constraints", "op_results", "file_reopens"],
            environment={"requires": "Blender 4.2+ executable", "executable": detail if ok else None},
            available=ok, unavailable_reason=None if ok else detail,
            templates=["empty", "components"],
            operations=[
                OperationSpec(
                    name="set_dimensions", family="proportions", aspects=["geometry", "proportions"],
                    target_kinds=["*"], description="Scale a component (and its subtree) so its "
                    "world bounding box reaches the given dimensions (metres).",
                    params_schema={"type": "object", "additionalProperties": False, "properties": {
                        "width": {"type": "number", "minimum": 0.001, "maximum": 1000},
                        "depth": {"type": "number", "minimum": 0.001, "maximum": 1000},
                        "height": {"type": "number", "minimum": 0.001, "maximum": 1000},
                        "keep_ground": {"type": "boolean"}}}),
                OperationSpec(
                    name="set_material", family="material", aspects=["color", "material", "palette",
                                                                     "style"],
                    target_kinds=["*"], subtree=True,
                    description="Assign a Principled BSDF material to every mesh in the subtree.",
                    params_schema={"type": "object", "additionalProperties": False,
                                   "required": ["base_color"], "properties": {
                                       "base_color": HEX_COLOR,
                                       "roughness": {"type": "number", "minimum": 0, "maximum": 1},
                                       "metallic": {"type": "number", "minimum": 0, "maximum": 1},
                                       **sub}}),
                OperationSpec(
                    name="set_taper", family="shape", aspects=["shape", "geometry", "style"],
                    target_kinds=["*"], subtree=True,
                    description="Non-destructive Simple Deform taper modifier on subtree meshes.",
                    params_schema={"type": "object", "additionalProperties": False,
                                   "required": ["factor"], "properties": {
                                       "factor": {"type": "number", "minimum": -1.5, "maximum": 1.5},
                                       "axis": {"type": "string", "enum": ["X", "Y", "Z"]},
                                       **sub}}),
                OperationSpec(
                    name="set_bevel", family="shape", aspects=["shape", "style", "technique"],
                    target_kinds=["*"], subtree=True,
                    description="Non-destructive bevel modifier on subtree meshes.",
                    params_schema={"type": "object", "additionalProperties": False,
                                   "required": ["width"], "properties": {
                                       "width": {"type": "number", "minimum": 0, "maximum": 0.5},
                                       "segments": {"type": "integer", "minimum": 1, "maximum": 8},
                                       **sub}}),
                OperationSpec(
                    name="set_transform", family="transform", aspects=["layout", "composition"],
                    target_kinds=["*"], description="Set location / rotation (degrees) / scale.",
                    params_schema={"type": "object", "additionalProperties": False, "properties": {
                        "location": {"type": "array", "items": {"type": "number"}, "minItems": 3,
                                     "maxItems": 3},
                        "rotation_euler": {"type": "array", "items": {"type": "number"},
                                           "minItems": 3, "maxItems": 3},
                        "scale": {"type": "array", "items": {"type": "number"}, "minItems": 3,
                                  "maxItems": 3}}}),
                OperationSpec(
                    name="add_primitive", family="create", aspects=["geometry"],
                    target_kinds=["new"], description="Create a new component (idempotent by id).",
                    params_schema={"type": "object", "required": ["id"], "properties": {
                        "id": {"type": "string", "pattern": "[A-Za-z0-9_.-]+"},
                        "name": {"type": "string"},
                        "primitive": {"type": "string",
                                      "enum": ["cube", "cylinder", "cone", "uv_sphere",
                                               "ico_sphere", "torus", "plane", "empty"]},
                        "parent": {"type": "string"},
                        "location": VEC3,
                        "rotation_euler": VEC3,
                        "size": VEC3,
                        "vertices": {"type": "integer", "minimum": 3, "maximum": 128},
                        "top_ratio": {"type": "number", "minimum": 0, "maximum": 1},
                        "detail": {"type": "integer", "minimum": 1, "maximum": 5},
                        "thickness": {"type": "number", "minimum": 0.01, "maximum": 1},
                        "subdivide": {"type": "integer", "minimum": 0, "maximum": 4}}}),
                OperationSpec(
                    name="add_branch", family="create", aspects=["geometry", "shape"],
                    target_kinds=["new"],
                    description="Create a curve component with a round profile through the "
                    "given points (trunks, branches, cables, handles). Radii scale the profile "
                    "per point. Idempotent by id.",
                    params_schema={"type": "object", "required": ["id", "points"],
                                   "additionalProperties": False, "properties": {
                                       "id": {"type": "string", "pattern": "[A-Za-z0-9_.-]+"},
                                       "name": {"type": "string"},
                                       "parent": {"type": "string"},
                                       "points": {"type": "array", "items": VEC3,
                                                  "minItems": 2, "maxItems": 16},
                                       "radius": {"type": "number", "minimum": 0.001,
                                                  "maximum": 5},
                                       "radii": {"type": "array", "items": {
                                           "type": "number", "minimum": 0, "maximum": 10},
                                           "maxItems": 16},
                                       "resolution": {"type": "integer", "minimum": 1,
                                                      "maximum": 24},
                                       "bevel_resolution": {"type": "integer", "minimum": 0,
                                                            "maximum": 8},
                                       "straight": {"type": "boolean"}}}),
                OperationSpec(
                    name="extrude_faces", family="shape", aspects=["shape", "geometry"],
                    target_kinds=["mesh"],
                    description="Extrude the faces facing an axis in steps; each step may "
                    "scale (taper), twist (degrees) and offset the new ring. Destructive on the "
                    "mesh data (checkpointed by the engine).",
                    params_schema={"type": "object", "required": ["distance"],
                                   "additionalProperties": False, "properties": {
                                       "axis": {"type": "string",
                                                "enum": ["Z", "-Z", "X", "-X", "Y", "-Y"]},
                                       "distance": {"type": "number", "minimum": 0.001,
                                                    "maximum": 100},
                                       "steps": {"type": "integer", "minimum": 1, "maximum": 24},
                                       "scale_per_step": {"type": "number", "minimum": 0.05,
                                                          "maximum": 3},
                                       "twist_per_step": {"type": "number", "minimum": -180,
                                                          "maximum": 180},
                                       "offset_per_step": VEC3}}),
                OperationSpec(
                    name="set_subdivision", family="detail", aspects=["shape", "style",
                                                                      "detail"],
                    target_kinds=["*"], subtree=True,
                    description="Non-destructive subdivision surface (levels 0 removes it).",
                    params_schema={"type": "object", "required": ["levels"],
                                   "additionalProperties": False, "properties": {
                                       "levels": {"type": "integer", "minimum": 0, "maximum": 4},
                                       "render_levels": {"type": "integer", "minimum": 0,
                                                         "maximum": 4},
                                       **sub}}),
                OperationSpec(
                    name="set_curve_detail", family="detail", aspects=["detail", "shape"],
                    target_kinds=["*"], subtree=True,
                    description="Curve tessellation and profile: resolution along the curve, "
                    "bevel (profile) resolution and radius.",
                    params_schema={"type": "object", "additionalProperties": False,
                                   "properties": {
                                       "resolution": {"type": "integer", "minimum": 1,
                                                      "maximum": 24},
                                       "bevel_resolution": {"type": "integer", "minimum": 0,
                                                            "maximum": 8},
                                       "radius": {"type": "number", "minimum": 0.001,
                                                  "maximum": 5}, **sub}}),
                OperationSpec(
                    name="set_modifier", family="modifier", aspects=["shape", "detail",
                                                                     "technique"],
                    target_kinds=["*"], subtree=True,
                    description="Configure (or remove) one of a bounded set of non-destructive "
                    "modifiers: decimate (polygon budget), solidify, array, mirror.",
                    params_schema={"type": "object", "required": ["type"],
                                   "additionalProperties": False, "properties": {
                                       "type": {"type": "string", "enum": [
                                           "decimate", "solidify", "array", "mirror"]},
                                       "remove": {"type": "boolean"},
                                       "ratio": {"type": "number", "minimum": 0.01,
                                                 "maximum": 1},
                                       "thickness": {"type": "number", "minimum": -1,
                                                     "maximum": 1},
                                       "count": {"type": "integer", "minimum": 1,
                                                 "maximum": 64},
                                       "offset": VEC3,
                                       "axis": {"type": "string", "enum": ["X", "Y", "Z"]},
                                       **sub}}),
                OperationSpec(
                    name="set_parent", family="structure", aspects=["composition"],
                    target_kinds=["*"],
                    description="Re-parent a component (empty parent = scene root); keeps its "
                    "world transform.",
                    params_schema={"type": "object", "additionalProperties": False,
                                   "properties": {"parent": {"type": "string"}}}),
                OperationSpec(
                    name="set_shading", family="detail", aspects=["style"],
                    target_kinds=["*"], subtree=True,
                    description="Smooth or flat shading on subtree meshes.",
                    params_schema={"type": "object", "required": ["smooth"],
                                   "additionalProperties": False, "properties": {
                                       "smooth": {"type": "boolean"}, **sub}}),
                OperationSpec(
                    name="remove_component", family="structure", aspects=["composition"],
                    target_kinds=["*"], subtree=True,
                    description="Delete a component and its subtree (scope-checked; the engine "
                    "checkpoints and records the removal).",
                    params_schema={"type": "object", "additionalProperties": False,
                                   "properties": {}}),
            ],
        )

    def created_kind(self, op: PlannedOperation) -> str:
        if op.op == "add_branch":
            return "curve"
        return "group" if op.params.get("primitive") == "empty" else "mesh"

    # ------------------------------------------------------------------
    def _run(self, job: dict[str, Any], blend: Path | None = None) -> dict[str, Any]:
        ok, exe = self.check_environment()
        if not ok:
            raise AdapterError(exe)
        with tempfile.TemporaryDirectory(prefix="dd_blender_") as tmp:
            jp, rp = Path(tmp) / "job.json", Path(tmp) / "result.json"
            jp.write_text(json.dumps(job))
            # never run Python embedded in (untrusted) .blend files
            cmd = [exe, "--background", "--factory-startup", "--disable-autoexec"]
            if blend is not None:
                cmd.append(str(blend))
            cmd += ["--python-exit-code", "3", "--python", str(WORKER), "--", str(jp), str(rp)]
            env = {**os.environ, "PYTHONNOUSERSITE": "1"}
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=self.timeout,
                                  env=env)
            if not rp.exists():
                tail = (proc.stdout + proc.stderr)[-2000:]
                raise AdapterError(f"Blender worker produced no result (exit {proc.returncode}): "
                                   f"{tail}")
            res = json.loads(rp.read_text())
            res["_log"] = (proc.stdout[-4000:] if proc.stdout else "")
            if res.get("ok") is False and "error" in res and "results" not in res:
                raise AdapterError(f"Blender worker error: {res['error']}\n"
                                   f"{res.get('traceback', '')[-1500:]}")
            return res

    @staticmethod
    def _inspect_result(res: dict[str, Any]) -> InspectResult:
        return InspectResult(components=[Component(**c) for c in res["components"]],
                             states=res["states"], measurements=res["measurements"],
                             properties=res["properties"])

    def create(self, native_dir: Path, template: str, params: dict[str, Any]) -> str:
        native_dir.mkdir(parents=True, exist_ok=True)
        comps = params.get("components", [])
        if template == "empty":
            comps = [{"id": params.get("root_id", "root"), "name": params.get("name", "Root"),
                      "primitive": "empty"}]
        elif template != "components":
            raise AdapterError(f"unknown blender template: {template}")
        self._run({"action": "create", "components": comps,
                   "blend_path": str(native_dir / ENTRY)})
        return ENTRY

    def inspect(self, native_dir: Path, entry: str) -> InspectResult:
        return self._inspect_result(self._run({"action": "inspect"}, native_dir / entry))

    def inspect_file(self, path: Path) -> dict[str, Any]:
        return self._run({"action": "inspect"}, path)

    def apply(self, native_dir: Path, entry: str, operations: list[PlannedOperation],
              context: dict[str, Any]) -> ApplyResult:
        blend = native_dir / entry
        res = self._run({"action": "apply", "blend_path": str(blend),
                         "operations": [o.model_dump() for o in operations]}, blend)
        return ApplyResult(ok=bool(res.get("ok")), results=res.get("results", []),
                           logs=res.get("_log", ""),
                           error=None if res.get("ok") else next(
                               (r["detail"] for r in res.get("results", [])
                                if r["status"] == "failed"), res.get("error")))

    def preview(self, native_dir: Path, entry: str, out_dir: Path) -> dict[str, Path]:
        out_dir.mkdir(parents=True, exist_ok=True)
        png, glb = out_dir / "preview.png", out_dir / "model.glb"
        self._run({"action": "preview", "png_path": str(png), "glb_path": str(glb),
                   "samples": 24, "size": [480, 360]}, native_dir / entry)
        out = {}
        if png.exists():
            out["render"] = png
        if glb.exists():
            out["glb"] = glb
            out["glb_ids"] = glb  # node extras carry daedelus_id (component picking)
        return out

    def export(self, native_dir: Path, entry: str, fmt: str, out_dir: Path) -> Path:
        out_dir.mkdir(parents=True, exist_ok=True)
        path = out_dir / f"{Path(entry).stem}.{fmt}"
        self._run({"action": "export", "format": fmt, "path": str(path)}, native_dir / entry)
        if not path.exists():
            raise AdapterError(f"export to {fmt} produced no file")
        return path

    def validate(self, native_dir: Path, entry: str, checks: list[str],
                 context: dict[str, Any]) -> ValidationReport:
        rep = ValidationReport()
        if "file_reopens" in checks:
            try:
                res = self.inspect(native_dir, entry)
                rep.add("file_reopens", True, f".blend reopened with {len(res.components)} "
                        "components")
            except AdapterError as exc:
                rep.add("file_reopens", False, str(exc))
        return rep
