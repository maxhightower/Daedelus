"""Source-binding resolution.

Given a target (project, artifact or component), determine which bindings
apply, through which relation (own / inherited / descendant), what each one is
meant to influence, which constraints are mandatory, and where hard
constraints conflict. The result (``ResolvedContext``) is a plain, inspectable
document; it is produced before any execution and stored with each run.
"""

from __future__ import annotations

from typing import Any, Iterable

from . import features
from .models import (
    CASCADING_ASPECTS,
    Artifact,
    Conflict,
    MediaSource,
    Project,
    ProcessingState,
    ResolvedContext,
    ResolvedEntry,
    SourceBinding,
    TargetSelector,
)
from .store import ProjectStore


def target_path(target: TargetSelector, artifact: Artifact | None) -> list[str]:
    """Scope keys from the most general to the most specific."""
    path = ["project"]
    if target.scope == "project":
        return path
    path.append(f"artifact:{target.artifact_id}")
    if target.scope == "component" and artifact is not None and target.component_id:
        for anc in artifact.ancestors(target.component_id):
            path.append(f"artifact:{target.artifact_id}#{anc}")
        path.append(f"artifact:{target.artifact_id}#{target.component_id}")
    return path


def _depth(key: str, path: list[str]) -> int:
    return path.index(key) if key in path else len(path)


def summarize_source(src: MediaSource, binding: SourceBinding | None = None) -> dict[str, Any]:
    """Compact, planner-facing view of a source's extracted content.

    Honours the binding's segment (time range / page) where the source supports it.
    """
    ex = src.extracted or {}
    seg = binding.segment if binding else None
    mt = src.media_type.value
    out: dict[str, Any] = {"media_type": mt, "state": src.processing.state.value}
    if mt == "image":
        sil = ex.get("silhouette", {})
        out.update({
            "dominant": ex.get("dominant"), "palette": [p["hex"] for p in ex.get("palette", [])[:5]],
            "brightness": ex.get("brightness"), "saturation": ex.get("saturation"),
            "contrast": ex.get("contrast"), "warmth": ex.get("warmth"),
            "vertical_profile": ex.get("vertical_profile"),
            "silhouette": {k: sil.get(k) for k in ("found", "aspect", "taper", "bbox",
                                                    "top_width", "bottom_width")},
            "foreground_palette": [p["hex"] for p in sil.get("foreground_palette", [])[:3]],
        })
        if seg and seg.kind == "region" and seg.region:
            out["region"] = seg.region
            out["notes"] = ["region recorded; measurements are for the whole image"]
    elif mt in ("text", "document", "url", "code"):
        directives = dict(ex.get("directives", {}))
        colors = list(ex.get("colors", []))
        keywords = list(ex.get("keywords", []))[:12]
        if mt == "document" and seg and seg.kind == "page" and seg.page:
            page = next((p for p in ex.get("pages", []) if p["page"] == seg.page), None)
            if page:
                pf = features.text_features(page["text"])
                directives, colors, keywords = pf["directives"], pf["colors"], pf["keywords"][:12]
                out["page"] = seg.page
        out.update({"directives": directives, "colors": colors, "keywords": keywords,
                    "excerpt": (ex.get("excerpt") or "")[:400]})
        if mt == "code":
            out["files"] = len(ex.get("files", []))
    elif mt == "video":
        frames = ex.get("frames", [])
        if seg and seg.kind == "time":
            lo, hi = seg.start or 0.0, seg.end if seg.end is not None else float("inf")
            frames = [f for f in frames if lo <= f["time"] <= hi]
            out["segment"] = [seg.start, seg.end]
        out.update({"frames": len(frames),
                    "dominant": frames[len(frames) // 2]["dominant"] if frames else None,
                    "palette": [p["hex"] for f in frames for p in f["palette"][:1]][:5],
                    "brightness": (sum(f["brightness"] for f in frames) / len(frames))
                    if frames else None})
    elif mt == "video_url":
        tf = ex.get("title_features", {})
        out.update({"title": ex.get("title"), "keywords": tf.get("keywords", []),
                    "directives": tf.get("directives", {}),
                    "understanding": ex.get("understanding")})
        if seg and seg.kind == "time":
            out["segment"] = [seg.start, seg.end]
            out.setdefault("notes", []).append(
                "time segment recorded; video content itself is not analysed")
    elif mt == "model3d":
        out.update({"bbox": ex.get("bbox"), "proportions": ex.get("proportions"),
                    "hierarchy": [h.get("name") for h in ex.get("hierarchy", [])][:20]})
    if binding and binding.instructions:
        out["binding_directives"] = features.text_features(binding.instructions)["directives"]
        out["binding_colors"] = features.parse_colors(binding.instructions)
    return out


def _aspects_for(b: SourceBinding, project: Project) -> list[str]:
    return list(b.aspects) or list(project.role(b.role).default_aspects)


def resolve(store: ProjectStore, target: TargetSelector, *,
            source_ids: Iterable[str] | None = None,
            bindings: list[SourceBinding] | None = None,
            sources: dict[str, MediaSource] | None = None,
            project: Project | None = None,
            artifact: Artifact | None = None) -> ResolvedContext:
    project = project or store.get_project()
    if artifact is None and target.artifact_id:
        artifact = store.get_artifact(target.artifact_id)
    if target.scope == "component" and artifact is not None:
        if artifact.component(target.component_id or "") is None:
            raise LookupError(f"component {target.component_id} not in artifact {artifact.id}")
    bindings = bindings if bindings is not None else store.list_bindings()
    sources = sources if sources is not None else {s.id: s for s in store.list_sources()}
    allowed = set(source_ids) if source_ids is not None else None

    path = target_path(target, artifact)
    ctx = ResolvedContext(target=target, target_path=path)
    target_key = path[-1]
    unit_component = target.component_id if target.scope == "component" else (
        artifact.root_id() if artifact else None)
    unit_lineage = set()
    if artifact is not None and unit_component:
        unit_lineage = set(artifact.ancestors(unit_component)) | {unit_component}

    for b in bindings:
        if not b.enabled:
            continue
        if allowed is not None and b.source_id not in allowed:
            continue
        anchor = b.target.key()
        # an artifact-scoped binding is "own" for the artifact's root component
        root_self = (artifact is not None and b.target.scope == "artifact"
                     and unit_component is not None and unit_component == artifact.root_id())
        if anchor in path:
            relation = "self" if (anchor == target_key or root_self) else "inherited"
        elif (artifact is not None and b.target.scope == "component"
              and b.target.artifact_id == artifact.id and b.target.component_id
              and (target.scope == "artifact" or
                   b.target.component_id in artifact.descendants(target.component_id or ""))):
            relation = "descendant"
        else:
            continue
        src = sources.get(b.source_id)
        if src is None:
            ctx.warnings.append(f"binding {b.id} references missing source {b.source_id}")
            continue
        role = project.role(b.role)
        aspects = _aspects_for(b, project)
        cascading = [a for a in aspects if a in CASCADING_ASPECTS] if aspects else ["general"]
        notes: list[str] = []
        applies = True
        if relation == "descendant":
            applies = False
            notes.append("anchored below this unit; applied at its own component unit")
        elif relation == "inherited" and not cascading and not b.constraints:
            applies = False
            notes.append(f"inherited, but aspects {aspects} do not cascade to sub-components")
        excluded = unit_lineage & set(b.exclusions)
        if excluded and relation != "descendant":
            applies = False
            notes.append(f"unit lies inside excluded component(s): {sorted(excluded)}")
        if src.processing.state in (ProcessingState.failed, ProcessingState.unsupported):
            applies = False
            notes.append(f"source not usable: {src.processing.state.value} "
                         f"({src.processing.error or 'no extractor'})")
            ctx.warnings.append(f"source '{src.name}' is {src.processing.state.value}")
        elif src.processing.state == ProcessingState.partial:
            notes.append("source only partially understood: " + "; ".join(src.processing.warnings))
        if role.use in ("context", "evaluate"):
            notes.append(f"role '{role.name}' is '{role.use}': presented, not applied by planner")
        if role.use == "context" and not role.builtin and role.description.startswith("(no profile"):
            ctx.warnings.append(f"role '{b.role}' has no profile; treated as context only")
        weight = round(b.strength * role.weight, 4) if role.use in ("drive", "blend", "method",
                                                                     "constrain") else 0.0
        ctx.entries.append(ResolvedEntry(
            binding=b, source_name=src.name, media_type=src.media_type.value,
            source_hash=src.content_hash, processing_state=src.processing.state.value,
            anchor=anchor, relation=relation,  # type: ignore[arg-type]
            cascading_aspects=cascading if relation == "inherited" else aspects,
            applies=applies, role_use=role.use, effective_weight=weight, notes=notes,
            summary=summarize_source(src, b),
        ))

    _apply_overrides(ctx, path)
    _collect_constraints(ctx, path)
    _detect_directive_conflicts(ctx, path)
    return ctx


def _active_aspects(e: ResolvedEntry) -> list[str]:
    return e.cascading_aspects if e.relation == "inherited" else (e.binding.aspects or ["general"])


def _can_override(general: ResolvedEntry, specific: ResolvedEntry) -> bool:
    if general.binding.constraint == "soft":
        return specific.binding.priority >= general.binding.priority
    return general.binding.allow_override and specific.binding.priority > general.binding.priority


def _apply_overrides(ctx: ResolvedContext, path: list[str]) -> None:
    """More specific bindings override inherited ones per aspect, when permitted."""
    applying = [e for e in ctx.entries if e.applies and e.role_use in ("drive", "blend")]
    for e in applying:
        if e.relation != "inherited":
            continue
        keep = []
        for aspect in _active_aspects(e):
            rivals = [o for o in applying if o is not e
                      and _depth(o.anchor, path) > _depth(e.anchor, path)
                      and aspect in _active_aspects(o)]
            winners = [o for o in rivals if _can_override(e, o)]
            if winners:
                e.overridden_by.extend(o.binding.id for o in winners
                                       if o.binding.id not in e.overridden_by)
                e.notes.append(f"aspect '{aspect}' overridden by more specific binding(s) "
                               f"{[o.binding.id for o in winners]}")
            else:
                keep.append(aspect)
                if rivals and e.binding.constraint == "hard":
                    for o in rivals:
                        if o.binding.constraint == "hard" and o.binding.source_id != e.binding.source_id:
                            ctx.conflicts.append(Conflict(
                                property=f"aspect:{aspect}", bindings=[e.binding.id, o.binding.id],
                                detail=(f"hard binding '{e.source_name}' ({e.anchor}) and hard "
                                        f"binding '{o.source_name}' ({o.anchor}) both require "
                                        f"control of '{aspect}', and the inherited one does not "
                                        f"permit override")))
        e.cascading_aspects = keep
        if not keep and not e.binding.constraints:
            e.applies = False
    # same-level hard drivers competing for the same aspect
    selfs = [e for e in applying if e.relation == "self" and e.binding.constraint == "hard"]
    for i, a in enumerate(selfs):
        for b in selfs[i + 1:]:
            shared = set(_active_aspects(a)) & set(_active_aspects(b))
            if shared and a.binding.source_id != b.binding.source_id:
                if a.binding.priority != b.binding.priority:
                    hi, lo = (a, b) if a.binding.priority > b.binding.priority else (b, a)
                    ctx.conflicts.append(Conflict(
                        property=f"aspect:{','.join(sorted(shared))}",
                        bindings=[hi.binding.id, lo.binding.id], resolved=True,
                        detail="two hard bindings on the same target share aspects",
                        resolution=f"higher priority binding '{hi.source_name}' takes precedence"))
                    lo.cascading_aspects = [x for x in _active_aspects(lo) if x not in shared]
                    lo.overridden_by.append(hi.binding.id)
                else:
                    ctx.conflicts.append(Conflict(
                        property=f"aspect:{','.join(sorted(shared))}",
                        bindings=[a.binding.id, b.binding.id],
                        detail=(f"hard bindings '{a.source_name}' and '{b.source_name}' on the same "
                                f"target both require {sorted(shared)} with equal priority")))


def _constraint_compatible(a: dict[str, Any], b: dict[str, Any]) -> bool:
    ca, cb = a["constraint"], b["constraint"]
    ops = {ca["op"], cb["op"]}
    tol = max(ca.get("tolerance", 1e-3), cb.get("tolerance", 1e-3))
    if ops <= {"preserve", "forbid_change"}:
        return True
    if "preserve" in ops or "forbid_change" in ops:
        other = cb if ca["op"] in ("preserve", "forbid_change") else ca
        return other["op"] not in ("eq", "range")
    try:
        if ca["op"] == "eq" and cb["op"] == "eq":
            return abs(float(ca["value"]) - float(cb["value"])) <= tol
        lo, hi = float("-inf"), float("inf")
        for c in (ca, cb):
            if c["op"] == "gte":
                lo = max(lo, float(c["value"]))
            elif c["op"] == "lte":
                hi = min(hi, float(c["value"]))
            elif c["op"] == "eq":
                lo, hi = max(lo, float(c["value"])), min(hi, float(c["value"]))
            elif c["op"] == "range":
                lo, hi = max(lo, float(c["value"][0])), min(hi, float(c["value"][1]))
        return lo <= hi + tol
    except (TypeError, ValueError, IndexError):
        return ca == cb


def _collect_constraints(ctx: ResolvedContext, path: list[str]) -> None:
    items = []
    for e in ctx.entries:
        if e.relation == "descendant" or not e.binding.enabled:
            continue
        if any("excluded" in n for n in e.notes):
            continue
        for c in e.binding.constraints:
            items.append({"binding_id": e.binding.id, "source": e.source_name, "anchor": e.anchor,
                          "hard": e.binding.constraint == "hard", "depth": _depth(e.anchor, path),
                          "priority": e.binding.priority,
                          "allow_override": e.binding.allow_override,
                          "constraint": c.model_dump(), "active": True})
    for i, a in enumerate(items):
        for b in items[i + 1:]:
            if a["constraint"]["property"] != b["constraint"]["property"]:
                continue
            if _constraint_compatible(a, b):
                continue
            prop = a["constraint"]["property"]
            if a["hard"] and b["hard"]:
                gen, spec = (a, b) if a["depth"] <= b["depth"] else (b, a)
                if (spec["depth"] > gen["depth"] and gen["allow_override"]
                        and spec["priority"] > gen["priority"]):
                    gen["active"] = False
                    ctx.conflicts.append(Conflict(
                        property=prop, bindings=[gen["binding_id"], spec["binding_id"]],
                        resolved=True, detail="incompatible hard constraints",
                        resolution=(f"more specific binding from '{spec['source']}' overrides "
                                    f"'{gen['source']}' (higher priority, override permitted)")))
                else:
                    ctx.conflicts.append(Conflict(
                        property=prop, bindings=[a["binding_id"], b["binding_id"]],
                        detail=(f"incompatible hard constraints on '{prop}': "
                                f"{a['constraint']['op']} {a['constraint'].get('value')} "
                                f"('{a['source']}') vs {b['constraint']['op']} "
                                f"{b['constraint'].get('value')} ('{b['source']}'); neither "
                                f"declaration permits override")))
            else:
                soft = a if not a["hard"] else b
                hard = b if soft is a else a
                if soft["hard"] is False and hard["hard"] is False:
                    gen, spec = (a, b) if a["depth"] <= b["depth"] else (b, a)
                    gen["active"] = False
                    resolution = f"more specific soft constraint from '{spec['source']}' wins"
                else:
                    soft["active"] = False
                    resolution = f"hard constraint from '{hard['source']}' wins over soft"
                ctx.conflicts.append(Conflict(property=prop,
                                              bindings=[a["binding_id"], b["binding_id"]],
                                              resolved=True, detail="incompatible constraints",
                                              resolution=resolution))
    ctx.constraints = items


def _detect_directive_conflicts(ctx: ResolvedContext, path: list[str]) -> None:
    """Hard guideline bindings that set the same directive to different values."""
    seen: dict[str, tuple[ResolvedEntry, str]] = {}
    for e in sorted(ctx.entries, key=lambda x: _depth(x.anchor, path)):
        if not e.applies or e.binding.constraint != "hard" or e.role_use != "constrain":
            continue
        directives = {**e.summary.get("directives", {}), **e.summary.get("binding_directives", {})}
        for k, v in directives.items():
            if k in seen and seen[k][1].strip().lower() != str(v).strip().lower():
                prev, pv = seen[k]
                if prev.binding.source_id == e.binding.source_id:
                    continue
                if (_depth(e.anchor, path) > _depth(prev.anchor, path)
                        and _can_override(prev, e)):
                    ctx.conflicts.append(Conflict(
                        property=f"directive:{k}", bindings=[prev.binding.id, e.binding.id],
                        resolved=True, detail=f"'{k}': '{pv}' vs '{v}'",
                        resolution=f"more specific '{e.source_name}' overrides (permitted)"))
                    seen[k] = (e, str(v))
                else:
                    ctx.conflicts.append(Conflict(
                        property=f"directive:{k}", bindings=[prev.binding.id, e.binding.id],
                        detail=(f"hard guideline '{prev.source_name}' sets {k}='{pv}' but hard "
                                f"guideline '{e.source_name}' sets {k}='{v}'")))
            else:
                seen[k] = (e, str(v))
