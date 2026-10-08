"""Deterministic local planner (no AI model).

Maps *measurements* of bound sources onto an adapter's *operation families*
according to each binding's role profile and aspects. It has no knowledge of
particular objects (tables, legs, backgrounds): it only knows generic
families such as ``proportions``, ``material``, ``shape``, ``palette_fill``,
``color_grade``, ``reference_paint`` and ``structured_update``.

Because it is deterministic and blends from the artifact's *baseline* state,
the same inputs always produce the same plan - which is what makes
incremental re-execution and reproduction checks meaningful. It is honest
about its limits: it cannot read video content or understand images
semantically, and its interpretations say exactly which measurements it used.
"""

from __future__ import annotations

import re
from typing import Any

from .. import features
from ..models import PlannedOperation, ResolvedEntry
from ..semantic import heuristic as sem_heuristic
from ..semantic.models import Evaluation, SourceAnalysis
from . import recipes
from .base import (AnalyzeRequest, EvaluateRequest, Plan, PlanRequest, Provider,
                   ReviseRequest)

APPEARANCE = {"color", "material", "palette", "style", "general"}
GRADE_ASPECTS = {"color", "palette", "mood", "lighting", "style", "general"}
GEOMETRY = {"geometry", "proportions"}
SHAPE = {"shape"}
COMPOSITION = {"composition", "texture", "content"}
CODE_STYLE = {"convention", "code_style", "general", "code"}
FINISH_WORDS = {"matte": 0.85, "satin": 0.5, "eggshell": 0.6, "glossy": 0.2, "polished": 0.12,
                "rough": 0.9, "lacquered": 0.25}


def _aspects(e: ResolvedEntry) -> set[str]:
    return set(e.cascading_aspects) or {"general"}


def _num(v: Any) -> float | None:
    if isinstance(v, (int, float)):
        return float(v)
    return features.parse_number(str(v)) if v is not None else None


def _clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", s.lower()).strip("_") or "item"


class _Interp:
    def __init__(self) -> None:
        self.lines: dict[str, list[str]] = {}

    def add(self, e: ResolvedEntry | str, text: str) -> None:
        bid = e if isinstance(e, str) else e.binding.id
        self.lines.setdefault(bid, []).append(text)


class HeuristicProvider(Provider):
    name = "heuristic"
    description = ("Deterministic local planner: maps measured source features to operation "
                   "families by role and aspect. No AI model; no semantic understanding.")
    contracts = ("analyze", "plan", "evaluate", "revise")
    pathways = ("image (measured)", "image region (measured)", "text (rules)", "pdf (rules)",
                "video (frame measurements only)")

    # -------------------------------------------------------------- semantic contracts
    def analyze(self, req: AnalyzeRequest) -> SourceAnalysis:
        return sem_heuristic.analyze(req.source, file_path=req.file_path, text=req.text,
                                     frames=req.frames, segment=req.segment,
                                     extracted=req.source.extracted or {})

    def evaluate(self, req: EvaluateRequest) -> Evaluation:
        return sem_heuristic.heuristic_evaluate(req)

    def revise(self, req: ReviseRequest) -> Plan:
        pr = req.plan_request
        avail = set(self.ops_for(pr))
        plan = Plan(provider=self.name, model="heuristic-v1", deterministic=True)
        root = pr.component.id if pr.component else pr.artifact.root_id()
        done: set[tuple[str, str]] = set()

        def emit(op: str, cid: str, params: dict[str, Any], why: str) -> None:
            if op in avail and (op, cid) not in done and len(plan.operations) < req.max_operations:
                done.add((op, cid))
                plan.operations.append(PlannedOperation(op=op, component_id=cid, params=params,
                                                        rationale=why))

        for f in req.evaluation.findings:
            if f.status == "pass":
                continue
            cid = f.component_id or root
            ev = f.evidence or {}
            prop, limit, actual = ev.get("property"), ev.get("value"), ev.get("actual")
            if f.kind in ("constraint", "deterministic") and prop == "poly_count" and actual \
                    and limit:
                self._reduce_polys(pr, cid, float(actual), float(limit), emit)
            elif f.kind == "constraint" and prop in ("height", "width", "depth") and limit:
                op = ev.get("op")
                tgt = float(limit) * (0.98 if op == "lte" else 1.02 if op == "gte" else 1.0)
                emit("set_dimensions", cid, {prop: round(tgt, 4), "keep_ground": True},
                     f"{prop} {actual} violates {op} {limit}")
            elif f.kind == "deterministic" and ev.get("reference"):
                rough = ((pr.properties.get(cid, {}) or {}).get("material") or {}).get("roughness")
                params: dict[str, Any] = {"base_color": ev["reference"]}
                if rough is not None:
                    params["roughness"] = rough
                emit("set_material", cid, params, f.detail)
        if not plan.operations:
            plan.notes.append("no deterministic correction available for the failed findings")
        plan.notes.append(f"heuristic revision, iteration {req.iteration}")
        return plan

    @staticmethod
    def _reduce_polys(pr: PlanRequest, cid: str, actual: float, limit: float, emit) -> None:
        """Lower detail on the largest polygon contributors (leaves of the unit) until the
        estimated total fits the budget: curve tessellation, then subdivision, then a bounded
        decimate. Estimates only - the next evaluation measures the real result."""
        leaves = [c for c in [cid] + pr.artifact.descendants(cid)
                  if not pr.properties.get(c, {}).get("children")]
        contrib = sorted(((pr.properties.get(c, {}).get("own_poly_count") or 0, c)
                          for c in leaves), reverse=True)
        excess = actual - limit
        for polys, c in contrib:
            if excess <= 0 or polys <= 0:
                break
            props = pr.properties.get(c, {})
            curve = props.get("curve")
            if curve:
                res = max(1, int(curve["resolution"]) // 2)
                bev = max(0, int(curve["bevel_resolution"]) - 1)
                if (res, bev) == (curve["resolution"], curve["bevel_resolution"]):
                    continue
                emit("set_curve_detail", c, {"resolution": res, "bevel_resolution": bev},
                     f"poly_count {actual:.0f} > {limit:.0f}: '{c}' ({polys} polys) - curve "
                     f"resolution {curve['resolution']}->{res}, profile "
                     f"{curve['bevel_resolution']}->{bev}")
                excess -= polys * 0.5
            elif props.get("subdivision"):
                sub = int(props["subdivision"])
                emit("set_subdivision", c, {"levels": sub - 1},
                     f"poly_count {actual:.0f} > {limit:.0f}: '{c}' subdivision {sub}->{sub - 1}")
                excess -= polys * 0.75
            elif polys > 24:
                ratio = round(_clamp((polys - excess) / polys, 0.5, 0.95), 3)
                emit("set_modifier", c, {"type": "decimate", "ratio": ratio},
                     f"poly_count {actual:.0f} > {limit:.0f}: decimate '{c}' to {ratio}")
                excess -= polys * (1 - ratio)

    def plan(self, req: PlanRequest) -> Plan:
        plan = Plan(provider=self.name, model="heuristic-v1", deterministic=True)
        ops_avail = self.ops_for(req)
        recipe = self._recipe(req, ops_avail)
        if recipe is not None:
            return recipe
        families = {}
        for spec in ops_avail.values():
            families.setdefault(spec.family, spec.name)
        cid = req.component.id if req.component else req.artifact.root_id()
        kind = req.component.kind if req.component else "artifact"
        path = req.context.target_path
        entries = sorted(
            [e for e in req.context.entries if e.applies],
            key=lambda e: (path.index(e.anchor) if e.anchor in path else 99, e.binding.priority))
        interp = _Interp()
        used: set[str] = set()
        directives, dsrc = self._directives(req, entries)
        props = req.properties.get(cid or "", {})
        base = {**props, **req.baseline.get(cid or "", {})}
        meas = req.measurements.get(cid or "", {})
        ctx = dict(req=req, cid=cid, kind=kind, entries=entries, interp=interp, used=used,
                   directives=directives, dsrc=dsrc, base=base, props=props, meas=meas, plan=plan)

        if "proportions" in families:
            self._proportions(families["proportions"], **ctx)
        if "shape" in families:
            self._shape(ops_avail, **ctx)
        if "material" in families:
            self._material(families["material"], **ctx)
        if "reference_paint" in families:
            self._reference_paint(families["reference_paint"], **ctx)
        if "palette_fill" in families:
            self._palette_fill(families["palette_fill"], **ctx)
        if "color_grade" in families:
            self._color_grade(families["color_grade"], **ctx)
        if "structured_update" in families:
            self._structured_update(families["structured_update"], **ctx)

        for e in req.context.entries:
            if e.relation == "descendant":
                continue
            bid = e.binding.id
            if bid in interp.lines:
                plan.interpretations[bid] = "; ".join(interp.lines[bid])
            elif not e.applies:
                plan.interpretations[bid] = "presented, not applied: " + "; ".join(e.notes)
            elif e.role_use in ("context", "evaluate"):
                plan.interpretations[bid] = (f"presented as {e.role_use}; not used for planning"
                                             + ("" if e.role_use == "context" else
                                                " (available to validation)"))
            else:
                plan.interpretations[bid] = (
                    f"presented; no allowed operation family of adapter "
                    f"'{req.adapter.name}' expresses aspects {sorted(_aspects(e))} for "
                    f"{e.media_type} measurements at this unit")
        if req.context.blocking_conflicts:
            plan.warnings.append("unresolved conflicts present; engine will not apply this plan")
        return plan

    # ------------------------------------------------------------------ helpers
    def _recipe(self, req: PlanRequest, ops_avail) -> Plan | None:
        name = recipes.match(req.instructions)
        if name is None or "add_primitive" not in ops_avail:
            return None
        cid = req.component.id if req.component else req.artifact.root_id()
        sub = [c for c in req.artifact.components if c.parent_id == cid]
        if sub:  # only builds into an empty target; edits of existing objects use families
            return None
        ops, notes = recipes.build(name, cid, req.semantic or {}, req.instructions)
        ops = [o for o in ops if o.op in ops_avail]
        used = [e["binding_id"] for e in (req.semantic or {}).get("entries", [])
                if e.get("applies") and e["category"] in ("reference", "inspiration",
                                                         "constraint", "guideline")]
        for o in ops:
            o.derived_from = used
        plan = Plan(provider=self.name, model="heuristic-v1", deterministic=True,
                    operations=ops, notes=notes)
        for e in (req.semantic or {}).get("entries", []):
            plan.interpretations[e["binding_id"]] = (
                f"{e['category']}: used measured observations "
                f"({len(e['observations'])}) in recipe '{name}'" if e["binding_id"] in used else
                f"{e['category']}: presented, not used by the recipe")
        return plan

    def _directives(self, req: PlanRequest, entries: list[ResolvedEntry]):
        """Merge directives general -> specific; node instructions are most specific."""
        out: dict[str, str] = {}
        src: dict[str, str] = {}
        for e in entries:
            if e.role_use in ("context", "evaluate"):
                continue
            d: dict[str, str] = {}
            if e.role_use in ("constrain", "method") or e.media_type in ("text", "document", "url",
                                                                           "code"):
                d.update(e.summary.get("directives", {}))
            d.update(e.summary.get("binding_directives", {}))
            for k, v in d.items():
                out[k] = v
                src[k] = e.binding.id
        node_d = features.text_features(req.instructions)["directives"] if req.instructions else {}
        for k, v in node_d.items():
            out[k] = v
            src[k] = "node-instructions"
        return out, src

    @staticmethod
    def _object_color(e: ResolvedEntry) -> tuple[str | None, str]:
        s = e.summary
        if s.get("binding_colors"):
            return s["binding_colors"][0], "color named in binding instructions"
        mt = e.media_type
        if mt == "image":
            sil = s.get("silhouette") or {}
            if sil.get("found") and s.get("foreground_palette"):
                return s["foreground_palette"][0], "dominant foreground colour (measured)"
            return s.get("dominant"), "dominant image colour (measured)"
        if mt == "video":
            return s.get("dominant"), "dominant colour of sampled frames (measured)"
        if mt == "video_url":
            return None, ""
        d = s.get("directives", {})
        for key in ("base_color", "color", "material_color", "primary_color", "palette"):
            if key in d:
                c = features.parse_color(d[key])
                if c:
                    return c, f"directive '{key}: {d[key]}'"
        if s.get("colors"):
            return s["colors"][0], "first colour mentioned in text"
        return None, ""

    def _emit(self, plan: Plan, op: str, cid: str | None, params: dict[str, Any],
              contributors: list[str], why: str) -> int:
        plan.operations.append(PlannedOperation(op=op, component_id=cid, params=params,
                                                derived_from=sorted(set(contributors)),
                                                rationale=why))
        return len(plan.operations) - 1

    def _preserved(self, req: PlanRequest) -> tuple[set[str], dict[str, float]]:
        keep, fixed = set(), {}
        for c in req.context.constraints:
            if not c.get("active", True):
                continue
            con = c["constraint"]
            if con["op"] in ("preserve", "forbid_change"):
                keep.add(con["property"])
            elif con["op"] == "eq" and _num(con.get("value")) is not None:
                fixed[con["property"]] = float(_num(con["value"]))  # type: ignore[arg-type]
        return keep, fixed

    # ------------------------------------------------------------------ families
    def _proportions(self, op, *, req, cid, entries, interp, used, directives, dsrc, base, meas,
                     plan, **_):
        dims0 = base.get("dimensions") or {k: meas.get(k) for k in ("width", "depth", "height")}
        if not dims0 or not all(dims0.get(k) for k in ("width", "depth", "height")):
            return
        bw, bd, bh = (float(dims0[k]) for k in ("width", "depth", "height"))
        keep, fixed = self._preserved(req)
        drivers = []
        for e in entries:
            if e.relation != "self" or e.role_use not in ("drive", "blend"):
                continue
            if not (set(e.binding.aspects) & GEOMETRY):
                continue
            s = e.summary
            ratio = None
            if e.media_type == "image" and (s.get("silhouette") or {}).get("found"):
                ratio = s["silhouette"]["aspect"]
                how = f"foreground silhouette width/height = {ratio}"
            elif e.media_type == "model3d" and (s.get("proportions") or {}).get("width_over_height"):
                ratio = s["proportions"]["width_over_height"]
                how = f"model bbox width/height = {ratio}"
            if ratio:
                drivers.append((e, float(ratio), how))
        target: dict[str, float] = {}
        contributors: list[str] = []
        why = []
        if drivers:
            wsum = sum(e.effective_weight for e, _, _ in drivers) or 1.0
            ratio = sum(e.effective_weight * r for e, r, _ in drivers) / wsum
            pull = _clamp(max(e.effective_weight for e, _, _ in drivers), 0, 1)
            if "width" in keep and "height" not in keep:
                h = bh + pull * (bw / ratio - bh)
                target["height"] = h
            else:
                w = bw + pull * (bh * ratio - bw)
                target["width"] = w
                if "depth" not in keep:
                    target["depth"] = bd * (w / bw)
            for e, r, how in drivers:
                contributors.append(e.binding.id)
                used.add(e.binding.id)
                interp.add(e, f"{e.binding.role} ({e.role_use}, weight {e.effective_weight}): "
                              f"{how} -> proportions pulled {pull:.2f} toward ratio {ratio:.3f}")
            why.append(f"match reference proportions (ratio {ratio:.3f}, pull {pull:.2f})")
        for axis in ("width", "depth", "height"):
            if axis in directives and _num(directives[axis]) is not None:
                target[axis] = float(_num(directives[axis]))  # type: ignore[arg-type]
                contributors.append(dsrc[axis])
                interp.add(dsrc[axis], f"directive {axis}={directives[axis]}")
                why.append(f"directive {axis}")
        for axis, val in fixed.items():
            if axis in ("width", "depth", "height"):
                target[axis] = val
                why.append(f"hard constraint {axis}={val}")
        for axis in keep:
            if axis in target:
                plan.warnings.append(f"{axis} is preserved by a constraint; requested change dropped")
                target.pop(axis)
        if "height" in keep:
            why.append("height preserved by constraint")
        cur = {k: meas.get(k) for k in ("width", "depth", "height")}
        params = {k: round(v, 4) for k, v in target.items()
                  if cur.get(k) is None or abs(v - float(cur[k])) > 1e-4}
        if params and contributors:
            self._emit(plan, op, cid, params, contributors, "; ".join(why))
        elif target and contributors:
            plan.notes.append("proportions already match the planned values")

    def _shape(self, ops, *, req, cid, entries, interp, used, directives, dsrc, base, meas, plan,
               **_):
        fam = {o.name: o for o in ops.values() if o.family == "shape"}
        if "set_taper" in fam:
            cur = float(base.get("taper") or 0.0)
            f, contributors, why = cur, [], []
            for e in entries:
                if e.relation != "self" or e.role_use not in ("drive", "blend"):
                    continue
                if not (set(e.binding.aspects) & SHAPE):
                    continue
                sil = e.summary.get("silhouette") or {}
                if e.media_type != "image" or not sil.get("found"):
                    continue
                top, bot = float(sil.get("top_width") or 0), float(sil.get("bottom_width") or 0)
                if top <= 0 or bot <= 0:
                    continue
                r = top / bot  # >1: wider at the top, narrower at the bottom
                # Blender's taper scales by (1 + factor * z / extent): top/bottom = (1+f/2)/(1-f/2)
                ft = _clamp(2 * (r - 1) / (r + 1), -1.5, 1.5)
                w = _clamp(e.effective_weight, 0, 1)
                f = f + w * (ft - f)
                contributors.append(e.binding.id)
                used.add(e.binding.id)
                interp.add(e, f"{e.binding.role} ({e.role_use}, weight {w}): silhouette top/bottom "
                              f"width {top:.2f}/{bot:.2f} -> taper target {ft:.3f}, applied {f:.3f}")
                why.append(f"silhouette taper ratio {r:.2f}")
            if "taper" in directives and _num(directives["taper"]) is not None:
                f = _clamp(float(_num(directives["taper"])), -1.5, 1.5)  # type: ignore[arg-type]
                contributors.append(dsrc["taper"])
                interp.add(dsrc["taper"], f"directive taper={directives['taper']}")
                why.append("taper directive")
            if contributors:
                self._emit(plan, "set_taper", cid, {"factor": round(f, 4), "axis": "Z"},
                           contributors, "; ".join(why))
        if "set_bevel" in fam:
            width, segs, contributors, why = None, 2, [], []
            for key in ("bevel", "bevel_width", "edge_bevel"):
                if key in directives and _num(directives[key]) is not None:
                    width = float(_num(directives[key]))  # type: ignore[arg-type]
                    contributors.append(dsrc[key])
                    interp.add(dsrc[key], f"directive {key}={directives[key]} -> bevel width")
                    why.append(f"directive {key}")
            if "bevel_segments" in directives and _num(directives["bevel_segments"]):
                segs = int(_num(directives["bevel_segments"]))  # type: ignore[arg-type]
            for e in entries:
                if e.role_use != "method":
                    continue
                words = set(e.summary.get("keywords", [])) | set(
                    re.findall(r"[a-z]+", (e.summary.get("title") or "").lower()))
                if any(w.startswith("bevel") for w in words):
                    if width is None:
                        width = 0.01
                    contributors.append(e.binding.id)
                    used.add(e.binding.id)
                    basis = ("video title metadata only - the video content was not analysed"
                             if e.media_type == "video_url" else "keywords in the source text")
                    interp.add(e, f"technique: 'bevel' found in {basis} -> bevel modifier")
                    why.append("technique source mentions bevelling")
            if width is not None and contributors:
                self._emit(plan, "set_bevel", cid, {"width": round(_clamp(width, 0, 0.5), 4),
                                                    "segments": int(_clamp(segs, 1, 8))},
                           contributors, "; ".join(why))

    def _material(self, op, *, req, cid, entries, interp, used, directives, dsrc, base, plan, **_):
        base_mat = (base.get("material") or {})
        color = base_mat.get("base_color") or "#bfbfbf"
        contributors, why = [], []
        for e in entries:
            if e.role_use not in ("constrain", "drive", "blend"):
                continue
            if not (_aspects(e) & APPEARANCE):
                continue
            c, how = self._object_color(e)
            if not c:
                continue
            t = 1.0 if e.role_use == "constrain" else _clamp(e.effective_weight, 0, 1)
            new = features.mix_hex(color, c, t)
            interp.add(e, f"{e.binding.role} ({e.role_use}, weight {t:.2f}): {how} {c} -> "
                          f"base colour {color} -> {new}"
                       + (f" (inherited from {e.anchor})" if e.relation == "inherited" else ""))
            color = new
            contributors.append(e.binding.id)
            used.add(e.binding.id)
            why.append(f"colour from '{e.source_name}'")
        params: dict[str, Any] = {"base_color": color}
        for key in ("roughness", "metallic"):
            if key in directives and _num(directives[key]) is not None:
                params[key] = round(_clamp(float(_num(directives[key])), 0, 1), 4)  # type: ignore
                contributors.append(dsrc[key])
                interp.add(dsrc[key], f"directive {key}={directives[key]}")
        if "roughness" not in params:
            for key in ("finish", "surface", "material"):
                v = str(directives.get(key, "")).lower()
                for word, r in FINISH_WORDS.items():
                    if word in v:
                        params["roughness"] = r
                        contributors.append(dsrc[key])
                        interp.add(dsrc[key], f"directive {key}='{directives[key]}' -> "
                                              f"roughness {r}")
                        break
                if "roughness" in params:
                    break
        if contributors:
            self._emit(plan, op, cid, params, contributors,
                       "; ".join(why) or "material directives")

    def _reference_paint(self, op, *, req, cid, kind, entries, interp, used, directives, dsrc,
                         plan, **_):
        if kind != "layer":
            return
        for e in entries:
            if e.relation != "self" or e.role_use != "drive" or e.media_type != "image":
                continue
            if not (set(e.binding.aspects) & COMPOSITION):
                continue
            mix = round(_clamp(e.effective_weight, 0, 1), 4)
            blur = _num(directives.get("blur", 0)) or 0.0
            self._emit(plan, op, cid, {"source_id": e.binding.source_id, "fit": "cover",
                                       "mix": mix, "blur": round(_clamp(blur, 0, 50), 2)},
                       [e.binding.id], f"paint reference '{e.source_name}' into layer")
            used.add(e.binding.id)
            interp.add(e, f"reference pixels painted into layer at mix {mix}")

    def _palette_fill(self, op, *, req, cid, kind, entries, interp, used, directives, dsrc, base,
                      meas, plan, **_):
        if kind != "layer":
            return
        start = base.get("mean_color") or meas.get("mean_color") or "#808080"
        stops: list[str] | None = None
        contributors = []
        for e in entries:
            if e.relation != "self" or e.role_use not in ("drive", "blend", "constrain"):
                continue
            if not (set(e.binding.aspects) & {"palette", "color", "lighting"}):
                continue
            if e.binding.id in used:
                continue
            prof = e.summary.get("vertical_profile")
            if e.media_type == "text":
                cols = e.summary.get("binding_colors") or features.parse_colors(
                    e.summary.get("directives", {}).get("palette", "")) or e.summary.get("colors")
                prof = cols[:4] if cols and len(cols) > 1 else None
            if not prof:
                continue
            w = 1.0 if e.role_use == "constrain" else _clamp(e.effective_weight, 0, 1)
            cur = stops or [start] * len(prof)
            if len(cur) != len(prof):
                cur = [start] * len(prof)
            stops = [features.mix_hex(a, b, w) for a, b in zip(cur, prof)]
            contributors.append(e.binding.id)
            used.add(e.binding.id)
            interp.add(e, f"{e.binding.role} ({e.role_use}, weight {w:.2f}): vertical colour "
                          f"profile {prof} -> gradient stops {stops}")
        if stops and contributors:
            self._emit(plan, op, cid, {"colors": stops, "direction": "vertical",
                                       "preserve_alpha": True}, contributors,
                       "layer palette from bound sources")

    def _color_grade(self, op, *, req, cid, kind, entries, interp, used, directives, dsrc, base,
                     meas, plan, **_):
        tint, wsum, bright, contributors, why = None, 0.0, 1.0, [], []
        base_b = base.get("brightness") or meas.get("brightness")
        sat, con = 1.0, 1.0
        for e in entries:
            if e.binding.id in used or e.role_use not in ("drive", "blend", "constrain"):
                continue
            if not (_aspects(e) & GRADE_ASPECTS):
                continue
            w = 1.0 if e.role_use == "constrain" else _clamp(e.effective_weight, 0, 1)
            s = e.summary
            c = None
            before = (sat, con, bright)
            if e.media_type in ("image", "video"):
                c = s.get("dominant")
                if s.get("brightness") and base_b:
                    ratio = float(s["brightness"]) / max(float(base_b), 0.05)
                    bright += w * 0.5 * (ratio - 1.0)
                if c:
                    interp.add(e, f"{e.binding.role} ({e.role_use}, weight {w:.2f}): measured "
                                  f"dominant {c}, brightness {s.get('brightness')} -> grade tint/"
                                  f"brightness" + (f" (inherited from {e.anchor})"
                                                   if e.relation == "inherited" else ""))
            else:
                d = {**s.get("directives", {}), **s.get("binding_directives", {})}
                for key in ("tint", "palette", "color", "mood_color"):
                    if key in d and features.parse_color(d[key]):
                        c = features.parse_color(d[key])
                        break
                if c is None and s.get("colors"):
                    c = s["colors"][0]
                if c:
                    interp.add(e, f"{e.binding.role} ({e.role_use}): text colour {c} -> grade tint"
                               + (f" (inherited from {e.anchor})" if e.relation == "inherited"
                                  else ""))
                for key, word_map in (("saturation", {"muted": 0.75, "desaturated": 0.6,
                                                      "vivid": 1.25, "saturated": 1.3}),):
                    v = d.get(key)
                    if v is not None:
                        n = _num(v)
                        if n is not None:
                            sat = 1.0 + n if -1.0 <= n <= 1.0 and n != 1.0 else n
                        else:
                            for wd, f in word_map.items():
                                if wd in str(v).lower():
                                    sat = f
                        interp.add(e, f"directive saturation={v} -> {sat:.2f}")
                    for wd, f in word_map.items():
                        if wd in " ".join(s.get("keywords", [])) and v is None:
                            sat = f
                            interp.add(e, f"keyword '{wd}' -> saturation {f}")
                            break
                if d.get("contrast") and _num(d["contrast"]) is not None:
                    con = float(_num(d["contrast"]))  # type: ignore[arg-type]
                    interp.add(e, f"directive contrast={d['contrast']}")
            if c is None and (sat, con, bright) == before:
                continue
            if c:
                tint = c if tint is None else features.mix_hex(tint, c, w / (wsum + w))
                wsum += w
            contributors.append(e.binding.id)
            used.add(e.binding.id)
            why.append(f"grade from '{e.source_name}'")
        if not contributors:
            return
        params: dict[str, Any] = {"brightness": round(_clamp(bright, 0.6, 1.5), 4),
                                  "saturation": round(_clamp(sat, 0, 2.5), 4),
                                  "contrast": round(_clamp(con, 0.2, 2.5), 4)}
        if tint:
            params["tint"] = tint
            params["tint_strength"] = round(_clamp(wsum * 0.25, 0.05, 0.5), 4)
        self._emit(plan, op, cid, params, contributors, "; ".join(why))

    def _structured_update(self, op, *, req, cid, entries, interp, used, directives, dsrc, plan,
                           **_):
        indent, sort_keys = 2, True
        style_src = []
        for e in entries:
            if not (_aspects(e) & CODE_STYLE) or e.role_use not in ("constrain", "drive", "blend"):
                continue
            d = {**e.summary.get("directives", {}), **e.summary.get("binding_directives", {})}
            if "indent" in d and _num(d["indent"]) is not None:
                indent = int(_clamp(float(_num(d["indent"])), 0, 8))  # type: ignore[arg-type]
                style_src.append(e.binding.id)
                interp.add(e, f"directive indent={d['indent']} -> JSON indent {indent}")
            if "sort_keys" in d:
                sort_keys = str(d["sort_keys"]).strip().lower() in ("true", "yes", "1", "on")
                style_src.append(e.binding.id)
                interp.add(e, f"directive sort_keys={d['sort_keys']}")
            used.add(e.binding.id)
        manifest = directives.get("manifest")
        if not manifest or not req.upstream:
            if manifest and not req.upstream:
                plan.notes.append("manifest directive present but no upstream revisions connected")
            return
        target_cid = cid
        if req.component is None or req.component.kind != "file":
            mf = directives.get("manifest_file")
            if not mf:
                plan.warnings.append("manifest directive needs a file unit or 'manifest_file'")
                return
            target_cid = f"file:{mf}"
        pointer = manifest if manifest.startswith("/") else "/" + manifest
        for up in req.upstream:
            value = {
                "artifact_id": up["artifact_id"], "name": up["artifact_name"],
                "type": up["artifact_type"], "revision": up["revision_number"],
                "revision_id": up["revision_id"], "native": up["native"],
                "previews": up.get("previews", {}), "measurements": up.get("measurements", {}),
            }
            self._emit(plan, op, target_cid,
                       {"pointer": f"{pointer.rstrip('/')}/{_slug(up['artifact_name'])}",
                        "value": value, "indent": indent, "sort_keys": sort_keys},
                       [dsrc.get("manifest", "node-instructions")] + style_src,
                       f"record upstream revision {up['revision_number']} of "
                       f"'{up['artifact_name']}' in manifest")
