"""Deterministic creation recipes for the heuristic provider.

When a node's instructions ask to *create* an object of a known kind ("a stylised tree",
"a desk lamp") on an artifact that has no geometry yet, the heuristic provider emits a
fixed construction recipe from the adapter's catalogue, parameterised by *measurements*
of the bound references (silhouette proportions, palette) and by limits found in bound
documents (polygon budget, height). It does not understand the references: it reads
numbers. Recipes are labelled as such in every plan they produce.
"""

from __future__ import annotations

import colorsys
import re
from typing import Any

from .. import features
from ..models import PlannedOperation

RECIPES = {"tree": ("tree", "trunk", "oak", "pine", "bonsai"),
           "lamp": ("lamp", "lantern", "light fixture")}


def match(instructions: str) -> str | None:
    text = instructions.lower()
    if not re.search(r"\b(create|make|build|model|generate)\b", text):
        return None
    for name, words in RECIPES.items():
        if any(re.search(rf"\b{w}\b", text) for w in words):
            return name
    return None


def _entries(sem: dict[str, Any], cats: tuple[str, ...]) -> list[dict[str, Any]]:
    return [e for e in sem.get("entries", []) if e.get("applies") and e["category"] in cats]


def _colors(entries) -> list[str]:
    out = []
    for e in entries:
        fg = [o["value"]["hex"] for o in e["observations"]
              if o["kind"] == "color" and o["value"].get("foreground")]
        allc = [o["value"]["hex"] for o in e["observations"]
                if o["kind"] == "color" and o["value"].get("hex")]
        out += fg or allc
    return out


def _sil(entries) -> dict[str, Any] | None:
    for e in entries:
        for o in e["observations"]:
            if o["kind"] == "proportion" and o["value"].get("aspect"):
                return o["value"]
    return None


def _greenest(colors: list[str]) -> str | None:
    best, score = None, -1.0
    for c in colors:
        r, g, b = features.hex_to_rgb(c)
        h, s, v = colorsys.rgb_to_hsv(r, g, b)
        sc = s * (1 - min(abs(h - 0.3), 0.5) * 2) * (0.4 + v)
        if sc > score:
            best, score = c, sc
    return best if score > 0.05 else None


def _darkest(colors: list[str]) -> str | None:
    return min(colors, key=lambda c: sum(features.hex_to_rgb(c)), default=None)


def _poly_budget(sem: dict[str, Any]) -> float | None:
    vals = [d["value"] for d in sem.get("constraints", {}).get("derived", [])
            if d["property"] == "poly_count" and d["op"] == "lte"]
    return min(vals) if vals else None


def _height(sem: dict[str, Any], default: float) -> tuple[float, str]:
    for d in sem.get("constraints", {}).get("derived", []):
        if d["property"] == "height" and d["op"] in ("eq", "lte"):
            return float(d["value"]) * (0.95 if d["op"] == "lte" else 1.0), \
                f"height from '{d['text'][:60]}'"
    return default, f"default height {default} m"


def _words(sem: dict[str, Any], pattern: str) -> bool:
    rx = re.compile(pattern, re.I)
    for e in sem.get("entries", []):
        if rx.search(e.get("instructions") or ""):
            return True
        for o in e["observations"]:
            if o["kind"] in ("instruction", "requirement", "quote") and rx.search(o["text"]):
                return True
    return False


def tree(root: str, sem: dict[str, Any], instructions: str):
    refs = _entries(sem, ("reference",))
    insp = _entries(sem, ("inspiration",))
    sil = _sil(refs) or _sil(insp) or {}
    height, hnote = _height(sem, 3.0)
    aspect = float(sil.get("aspect") or 0.6)
    canopy_w = max(0.6, min(2.2, height * aspect * 0.9))
    twisted = bool(re.search(r"twist", instructions, re.I)) or _words(sem, r"twist")
    taper = float(sil.get("taper") or 0.0)
    scale_step = 0.82 if taper < 0.1 else 0.9  # silhouette narrowing upward -> stronger taper
    budget = _poly_budget(sem)
    low_poly = budget is not None and budget < 2000 or _words(sem, r"low[- ]?poly|stylized|stylised")
    trunk_h = height * 0.55
    r0 = max(0.06, height * 0.055)
    bark = _darkest(_colors(refs)) or "#5b3a24"
    leaf = _greenest(_colors(insp) + _colors(refs)) or "#4f8a3a"
    steps = 4
    ops = [
        PlannedOperation(op="add_primitive", params={
            "id": "trunk", "name": "Trunk", "parent": root, "primitive": "cylinder",
            "vertices": 8 if low_poly else 12, "size": [r0 * 2, r0 * 2, trunk_h / (steps + 1)],
            "location": [0, 0, trunk_h / (steps + 1) / 2]},
            rationale=f"trunk base radius {r0:.2f} m ({hnote})"),
        PlannedOperation(op="extrude_faces", component_id="trunk", params={
            "axis": "Z", "distance": trunk_h * steps / (steps + 1), "steps": steps,
            "scale_per_step": scale_step, "twist_per_step": 18.0 if twisted else 0.0},
            rationale=("twisted, tapering trunk" if twisted else "tapering trunk") +
            f" (reference silhouette taper {taper:+.2f})"),
    ]
    for i, (dx, dy, dz) in enumerate([(0.55, 0.15, 0.25), (-0.45, -0.25, 0.15),
                                      (0.1, -0.5, 0.3)][:2 if low_poly else 3]):
        z0 = trunk_h * (0.62 + 0.1 * i)
        ops.append(PlannedOperation(op="add_branch", params={
            "id": f"branch_{i + 1}", "name": f"Branch {i + 1}", "parent": root,
            "points": [[0, 0, z0], [dx * height * 0.25, dy * height * 0.25, z0 + dz * height * 0.3],
                       [dx * height * 0.45, dy * height * 0.45, z0 + dz * height * 0.55]],
            "radius": r0 * 0.45, "radii": [1.0, 0.6, 0.25],
            "resolution": 4 if low_poly else 8, "bevel_resolution": 1 if low_poly else 3},
            rationale="branch curve following the trunk direction"))
    ops += [
        PlannedOperation(op="add_primitive", params={
            "id": "canopy", "name": "Canopy", "parent": root, "primitive": "ico_sphere",
            "detail": 1 if low_poly else 2, "size": [canopy_w, canopy_w, canopy_w * 0.75],
            "location": [0, 0, trunk_h + canopy_w * 0.3]},
            rationale=f"canopy width {canopy_w:.2f} m from reference aspect {aspect:.2f}"),
        PlannedOperation(op="set_material", component_id="trunk",
                         params={"base_color": bark, "roughness": 0.9},
                         rationale=f"bark colour {bark}: darkest measured foreground colour"),
        PlannedOperation(op="set_material", component_id="canopy",
                         params={"base_color": leaf, "roughness": 0.7},
                         rationale=f"leaf colour {leaf}: greenest measured colour of the "
                                   "inspiration references"),
        PlannedOperation(op="set_shading", component_id=root, params={"smooth": not low_poly},
                         rationale="flat shading for a stylised low-poly look" if low_poly
                         else "smooth shading"),
    ]
    for i in range(2 if low_poly else 3):
        ops.append(PlannedOperation(op="set_material", component_id=f"branch_{i + 1}",
                                    params={"base_color": bark, "roughness": 0.9},
                                    rationale="branches share the bark colour"))
    if not low_poly and (budget is None or budget > 4000):
        ops.append(PlannedOperation(op="set_subdivision", component_id="canopy",
                                    params={"levels": 1}, rationale="soften the canopy"))
    notes = [f"deterministic recipe 'tree' (heuristic provider - parameters come from "
             f"measurements, not from recognising the references)",
             f"polygon budget: {budget if budget is not None else 'none found'}; "
             f"low-poly: {low_poly}; twisted: {twisted}"]
    return ops, notes


def lamp(root: str, sem: dict[str, Any], instructions: str):
    refs = _entries(sem, ("reference", "inspiration"))
    height, hnote = _height(sem, 1.5)
    colors = _colors(refs)
    body = _darkest(colors) or "#333333"
    shade = max(colors, key=lambda c: sum(features.hex_to_rgb(c)), default="#e8dcc0")
    ops = [
        PlannedOperation(op="add_primitive", params={
            "id": "base", "name": "Base", "parent": root, "primitive": "cylinder",
            "size": [height * 0.2, height * 0.2, height * 0.03],
            "location": [0, 0, height * 0.015]}, rationale=hnote),
        PlannedOperation(op="add_branch", params={
            "id": "pole", "name": "Pole", "parent": root, "straight": True,
            "points": [[0, 0, height * 0.03], [0, 0, height * 0.8]], "radius": height * 0.012},
            rationale="straight pole"),
        PlannedOperation(op="add_primitive", params={
            "id": "shade", "name": "Shade", "parent": root, "primitive": "cone",
            "top_ratio": 0.55, "size": [height * 0.3, height * 0.3, height * 0.2],
            "location": [0, 0, height * 0.88]}, rationale="truncated-cone shade"),
        PlannedOperation(op="set_material", component_id="base", params={"base_color": body}),
        PlannedOperation(op="set_material", component_id="pole", params={"base_color": body}),
        PlannedOperation(op="set_material", component_id="shade", params={"base_color": shade,
                                                                          "roughness": 0.6}),
    ]
    return ops, ["deterministic recipe 'lamp' (heuristic provider)"]


def build(name: str, root: str, sem: dict[str, Any], instructions: str):
    return {"tree": tree, "lamp": lamp}[name](root, sem, instructions)
