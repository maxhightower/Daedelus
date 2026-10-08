"""Deterministic local semantic layer (no AI model).

What it does:
- images: pixel *measurements* (palette, lighting, silhouette, composition) of the whole
  image or of a bound region - reported with ``basis="measured"``;
- text / documents: rule-based extraction of explicit requirements, instructions,
  numeric limits (e.g. "no more than 5,000 polygons") and section structure, each quoted
  with its line/page/section location;
- video: frame measurements only.

What it never does: say what an image depicts or what happens in a video. Those
limitations are written into every analysis it returns (status ``partial`` or
``unavailable``), so a measured palette is never mistaken for understanding.
"""

from __future__ import annotations

import re
import tempfile
from pathlib import Path
from typing import Any

from .. import features
from .models import (DerivedConstraint, Evaluation, Finding, Observation, SourceAnalysis,
                     SourceLocation)

NO_RECOGNITION = ("No object recognition: the local analyser measures pixels and cannot tell "
                  "what the image depicts.")

MODAL_RE = re.compile(r"\b(must|shall|should|required?|never|do not|don't|avoid|preserve|keep|"
                      r"maximum|minimum|at most|at least|no more than|no less than|limit|"
                      r"only|ensure|make sure)\b", re.I)
IMPERATIVE_RE = re.compile(r"^\s*(?:[-*•]|\d+[.)])?\s*(use|add|make|give|keep|preserve|"
                           r"avoid|apply|set|create|implement|return|write|include|remove|"
                           r"ensure|model|paint|place)\b", re.I)
NUM = r"(\d[\d,]*(?:\.\d+)?)\s*(k)?"
LIMITS = [
    # (regex, property, unit-normaliser)
    (re.compile(r"(max(?:imum)?|at most|no more than|up to|under|below|<=?|limit(?:ed)? to)\s*"
                r"(?:of\s*)?" + NUM + r"\s*(polygons?|polys?|faces?|triangles?|tris)", re.I),
     "poly_count", None),
    (re.compile(NUM + r"\s*(polygons?|polys?|faces?|triangles?|tris)\s*(max(?:imum)?|or (?:less|"
                r"fewer)|at most|limit)", re.I), "poly_count", "suffix"),
    (re.compile(r"(max(?:imum)?|at most|no more than|up to|under|below)\s*(?:of\s*)?" + NUM +
                r"\s*(vertices|verts)", re.I), "vertex_count", None),
]
DIM_RE = re.compile(r"\b(height|width|depth)\b[^.\n]{0,24}?\b(max(?:imum)?|at most|no more than|"
                    r"min(?:imum)?|at least|exactly|of)?\s*(\d+(?:\.\d+)?)\s*(m|cm|mm)\b", re.I)
DIM_PRE_RE = re.compile(r"\b(max(?:imum)?|min(?:imum)?|at most|at least|no more than)\s+"
                        r"(height|width|depth)\b[^.\n0-9]{0,12}(\d+(?:\.\d+)?)\s*(m|cm|mm)\b",
                        re.I)
INJECTION_RE = re.compile(r"\b(ignore (?:all |any )?(?:previous|prior|above|the) |disregard |"
                          r"you are (?:now |an? )|system prompt|as an ai\b|new instructions?:)",
                          re.I)
PRESERVE_RE = re.compile(r"\b(?:preserve|keep|do not (?:change|modify|alter))\s+(?:the\s+)?"
                         r"(?:existing\s+)?([a-z][a-z \-]{2,40}?)(?:\s+(?:on|of|for)\s+(?:the\s+)?"
                         r"([a-z][a-z \-]{1,30}))?(?:[.,;]|$)", re.I)


def _num(raw: str, k: str | None) -> float:
    v = float(raw.replace(",", ""))
    return v * 1000 if k else v


# ---------------------------------------------------------------------------- analysis
def analyze(source, *, file_path: str | None, text: str | None, frames: list[dict[str, Any]],
            segment, extracted: dict[str, Any]) -> SourceAnalysis:
    mt = source.media_type.value if hasattr(source.media_type, "value") else str(source.media_type)
    base = dict(source_id=source.id, source_hash=source.content_hash, media_type=mt,
                provider="heuristic", model="heuristic-semantic-v1",
                segment=segment.model_dump() if segment else None)
    if mt == "image":
        return _image(base, file_path, segment, extracted)
    if mt in ("text", "document", "pdf", "spreadsheet", "presentation"):
        return _text(base, text if text is not None else extracted.get("text", ""), extracted)
    if mt == "video":
        return _video(base, frames, segment)
    if mt == "video_url":
        return SourceAnalysis(**base, pathway="none", status="unavailable",
                              summary="video URL: content not analysed locally",
                              limitations=["the local analyser cannot download or understand "
                                           "video; a live multimodal provider is required"])
    obs = [Observation(kind="structure", text=f"{k}: {str(v)[:160]}", basis="measured",
                       value={"key": k}) for k, v in list(extracted.items())[:12]
           if not isinstance(v, (dict, list)) or k in ("components", "files")]
    return SourceAnalysis(**base, pathway="measured", status="partial", observations=obs,
                          summary=f"{mt}: extracted properties only",
                          limitations=[f"no semantic analysis for {mt} sources in the local "
                                       "analyser"])


def _image(base, file_path, segment, extracted) -> SourceAnalysis:
    feat = extracted
    region = None
    if segment is not None and segment.kind == "region" and segment.region and file_path:
        from PIL import Image

        x, y, w, h = segment.region
        with Image.open(file_path) as im:
            im = im.convert("RGB")
            W, H = im.size
            crop = im.crop((int(x * W), int(y * H), int((x + w) * W), int((y + h) * H)))
            with tempfile.TemporaryDirectory() as td:
                p = Path(td) / "region.png"
                crop.save(p)
                feat = features.image_features(p)
        region = [x, y, w, h]
    elif not feat.get("palette") and file_path:
        feat = features.image_features(Path(file_path))
    loc = SourceLocation(region=region) if region else None
    obs: list[Observation] = []
    for p in (feat.get("palette") or [])[:5]:
        obs.append(Observation(kind="color", text=f"colour {p['hex']} covers "
                               f"{round(p['weight'] * 100)}% of the {'region' if region else 'image'}",
                               value={"hex": p["hex"], "weight": p["weight"]}, basis="measured",
                               confidence=1.0, aspects=["color", "palette"], location=loc))
    if "brightness" in feat:
        warm = feat.get("warmth", 0)
        obs.append(Observation(
            kind="lighting", basis="measured", confidence=1.0, aspects=["lighting", "mood"],
            text=f"brightness {feat['brightness']}, contrast {feat.get('contrast')}, "
                 f"{'warm' if warm > 0.04 else 'cool' if warm < -0.04 else 'neutral'} cast",
            value={k: feat.get(k) for k in ("brightness", "contrast", "saturation", "warmth")},
            location=loc))
    if "orientation" in feat:
        o = feat["orientation"]
        obs.append(Observation(
            kind="composition", basis="measured", confidence=1.0, aspects=["composition"],
            text=f"aspect {feat.get('aspect')}; edge energy is "
                 f"{'mostly horizontal' if o > 0.58 else 'mostly vertical' if o < 0.42 else 'balanced'}"
                 f" (orientation {o}); edge density {feat.get('edge_density')}",
            value={k: feat.get(k) for k in ("aspect", "orientation", "edge_density")},
            location=loc))
    sil = feat.get("silhouette") or {}
    if sil.get("found"):
        narrows = sil.get("taper", 0)
        obs.append(Observation(
            kind="proportion", basis="measured", confidence=0.6,
            aspects=["geometry", "proportions", "shape"],
            text=f"foreground silhouette {sil['aspect']}:1 (width:height), covers "
                 f"{round(sil['coverage'] * 100)}%; "
                 + ("wider at the top than the bottom" if narrows > 0.1 else
                    "wider at the bottom than the top" if narrows < -0.1 else
                    "similar width top and bottom"),
            value={k: sil.get(k) for k in ("aspect", "coverage", "taper", "top_width",
                                           "bottom_width", "mid_fill", "bbox")},
            location=SourceLocation(region=sil.get("bbox"))))
        for p in (sil.get("foreground_palette") or [])[:3]:
            obs.append(Observation(kind="color", basis="measured", confidence=0.6,
                                   aspects=["color", "material"],
                                   text=f"foreground colour {p['hex']} ({round(p['weight'] * 100)}%"
                                        " of the silhouette)",
                                   value={"hex": p["hex"], "weight": p["weight"],
                                          "foreground": True},
                                   location=SourceLocation(region=sil.get("bbox"))))
    obs.append(Observation(kind="uncertainty", basis="inferred", text=NO_RECOGNITION))
    return SourceAnalysis(**base, pathway="measured", status="partial", observations=obs,
                          summary=f"measured {len(obs) - 1} visual properties"
                                  + (f" of region {region}" if region else ""),
                          limitations=[NO_RECOGNITION,
                                       "silhouette assumes a background distinct from the "
                                       "subject; proportions are 2D image ratios, not 3D sizes"])


def _sections(lines: list[str]) -> list[str | None]:
    cur, out = None, []
    for ln in lines:
        s = ln.strip()
        if s.startswith("#"):
            cur = s.lstrip("#").strip()
        out.append(cur)
    return out


def _text(base, text: str, extracted: dict[str, Any]) -> SourceAnalysis:
    pages = extracted.get("pages")
    chunks: list[tuple[int | None, str]] = (
        [(p["page"], p.get("text", "")) for p in pages] if pages else [(None, text or "")])
    obs: list[Observation] = []
    derived: list[DerivedConstraint] = []
    for page, chunk in chunks:
        lines = chunk.splitlines()
        secs = _sections(lines)
        for i, ln in enumerate(lines):
            s = ln.strip()
            if not s:
                continue
            loc = SourceLocation(page=page, line=i + 1, section=secs[i])
            if s.startswith("#"):
                obs.append(Observation(kind="structure", basis="quoted", text=s.lstrip("#").strip(),
                                       value={"heading": True}, location=loc))
                continue
            for rx, prop, mode in LIMITS:
                for m in rx.finditer(s):
                    g = m.groups()
                    raw, k = (g[0], g[1]) if mode == "suffix" else (g[1], g[2])
                    derived.append(DerivedConstraint(property=prop, op="lte",
                                                     value=_num(raw, k), text=s, location=loc))
            dims = [m.groups() for m in DIM_RE.finditer(s)]
            dims += [(d, q, v, u) for q, d, v, u in (m.groups() for m in DIM_PRE_RE.finditer(s))]
            for dim, qual, val, unit in dims:
                v = float(val) * {"m": 1, "cm": 0.01, "mm": 0.001}[unit.lower()]
                q = (qual or "").lower()
                if q == "of" and any(x[0] == dim and x[1] and x[1].lower() != "of" for x in dims):
                    continue
                op = "lte" if q.startswith(("max", "at most", "no more")) else \
                    "gte" if q.startswith(("min", "at least")) else "eq"
                derived.append(DerivedConstraint(property=dim.lower(), op=op, value=round(v, 5),
                                                 text=s, location=loc))
            if INJECTION_RE.search(s):
                obs.append(Observation(
                    kind="quote", basis="quoted", text=s[:400], location=loc,
                    value={"possible_instruction_to_ai": True},
                    aspects=[]))
                continue
            kind = None
            if MODAL_RE.search(s):
                kind = "requirement"
            elif IMPERATIVE_RE.match(s):
                kind = "instruction"
            if kind:
                pv = PRESERVE_RE.search(s)
                val: dict[str, Any] = {}
                if pv:
                    val = {"preserve": pv.group(1).strip(), "of": (pv.group(2) or "").strip()}
                obs.append(Observation(kind=kind, basis="quoted", text=s[:400], value=val,
                                       location=loc))
    for k, v in (extracted.get("directives") or {}).items():
        obs.append(Observation(kind="instruction", basis="quoted", text=f"{k}: {v}",
                               value={"key": k, "value": v}))
    for c in (extracted.get("colors") or [])[:6]:
        obs.append(Observation(kind="color", basis="quoted", text=f"colour mentioned: {c}",
                               value={"hex": c}, aspects=["color", "palette"]))
    return SourceAnalysis(
        **base, pathway="text_rules", status="partial", observations=obs,
        derived_constraints=derived,
        summary=f"{sum(1 for o in obs if o.kind == 'requirement')} requirement(s), "
                f"{sum(1 for o in obs if o.kind == 'instruction')} instruction(s), "
                f"{len(derived)} measurable limit(s)",
        limitations=["rule-based extraction: only explicit sentences and numeric limits are "
                     "recognised; implied meaning is not understood"] + (
            ["the source contains text addressed to an AI; it is reported as quoted data and "
             "has no authority over the workflow"]
            if any(o.value.get("possible_instruction_to_ai") for o in obs) else []))


def _video(base, frames, segment) -> SourceAnalysis:
    obs = []
    for fr in frames:
        t = fr.get("time")
        if segment is not None and segment.kind == "time":
            if (segment.start is not None and t < segment.start) or \
                    (segment.end is not None and t > segment.end):
                continue
        pal = fr.get("palette") or []
        if pal:
            obs.append(Observation(kind="color", basis="measured", confidence=1.0,
                                   text=f"frame at {t}s: dominant {pal[0]['hex']}",
                                   value={"palette": pal[:3]},
                                   location=SourceLocation(start_seconds=t, frame=fr.get("path"))))
    return SourceAnalysis(
        **base, pathway="video_frames_measured", status="unavailable" if not obs else "partial",
        observations=obs, summary=f"{len(obs)} sampled frame(s) measured; content not understood",
        limitations=["no step, action, narration or technique understanding: the local analyser "
                     "only measures sampled frames; a live multimodal provider is required"])


# ---------------------------------------------------------------------------- evaluation
def check(prop: str, op: str, value: Any, actual: Any, tol: float = 1e-3) -> bool | None:
    if actual is None:
        return None
    a = float(actual)
    if op == "lte":
        return a <= float(value) + tol
    if op == "gte":
        return a >= float(value) - tol
    if op == "eq":
        return abs(a - float(value)) <= max(tol, abs(float(value)) * 0.02)
    if op == "range":
        lo, hi = value
        return float(lo) - tol <= a <= float(hi) + tol
    return None


def heuristic_semantic_findings(req) -> list[Finding]:
    """Measured comparisons a reviewer would otherwise eyeball: component colour vs the colour
    the bound references measured. Kind stays 'deterministic' - these are measurements."""
    out: list[Finding] = []
    sem = req.plan_request.semantic or {}
    for unit in sem.get("entries", []):
        if unit.get("category") not in ("reference", "inspiration") or not unit.get("applies"):
            continue
        if not {"color", "material", "palette"} & set(unit.get("aspects", [])):
            continue
        ref = next((o["value"]["hex"] for o in unit.get("observations", [])
                    if o["kind"] == "color" and o["value"].get("foreground")), None) or \
            next((o["value"]["hex"] for o in unit.get("observations", [])
                  if o["kind"] == "color" and o["value"].get("hex")), None)
        cid = unit.get("anchor_component")
        mat = (req.plan_request.properties.get(cid or "", {}) or {}).get("material") or {}
        cur = mat.get("base_color")
        if not ref or not cur:
            continue
        d = features.color_distance(ref, cur)
        limit = 0.25 if unit["category"] == "reference" else 0.45
        out.append(Finding(criterion=f"material colour close to {unit['source']} ({ref})",
                           status="pass" if d <= limit else "fail", kind="deterministic",
                           detail=f"{cur} vs {ref}: distance {d:.3f} (limit {limit})",
                           component_id=cid, binding_id=unit.get("binding_id"),
                           evidence={"distance": round(d, 4), "limit": limit, "current": cur,
                                     "reference": ref},
                           suggestion=f"set base colour of {cid} toward {ref}"))
    return out


def heuristic_evaluate(req) -> Evaluation:
    return Evaluation(provider="heuristic", model="heuristic-semantic-v1",
                      findings=list(req.deterministic) + heuristic_semantic_findings(req),
                      summary="measured checks only; no visual judgement (no AI model)")
