"""Layered 2D adapter using OpenRaster (.ora).

OpenRaster is an open layered raster format (a zip with ``stack.xml`` and one
PNG per layer) that Krita, GIMP and MyPaint can open and edit. Each layer is a
component with a stable ``daedelus-id`` attribute.

Content operations (gradient fill, reference painting, shapes) write a layer's
*base* pixels; ``color_grade`` is stored as non-destructive parameters and
re-rendered from the base, so repeated execution converges instead of
compounding. The rendered layer PNGs are what other editors see.
"""

from __future__ import annotations

import hashlib
import io
import json
import zipfile
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET

import numpy as np
from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageOps

from .. import features
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

ENTRY = "image.ora"
MIMETYPE = b"image/openraster"
DEFAULT_GRADE = {"tint": None, "tint_strength": 0.0, "brightness": 1.0, "saturation": 1.0,
                 "contrast": 1.0}


class Layer:
    def __init__(self, id: str, name: str, base: Image.Image, *, opacity: float = 1.0,
                 visible: bool = True, composite: str = "svg:src-over", x: int = 0, y: int = 0,
                 grade: dict[str, Any] | None = None, base_kind: str = "raster"):
        self.id, self.name, self.base = id, name, base.convert("RGBA")
        self.opacity, self.visible, self.composite = opacity, visible, composite
        self.x, self.y = x, y
        self.grade = {**DEFAULT_GRADE, **(grade or {})}
        self.base_kind = base_kind

    def rendered(self) -> Image.Image:
        return apply_grade(self.base, self.grade)


def apply_grade(img: Image.Image, g: dict[str, Any]) -> Image.Image:
    alpha = img.getchannel("A")
    rgb = img.convert("RGB")
    if abs(g.get("brightness", 1.0) - 1.0) > 1e-6:
        rgb = ImageEnhance.Brightness(rgb).enhance(g["brightness"])
    if abs(g.get("contrast", 1.0) - 1.0) > 1e-6:
        rgb = ImageEnhance.Contrast(rgb).enhance(g["contrast"])
    if abs(g.get("saturation", 1.0) - 1.0) > 1e-6:
        rgb = ImageEnhance.Color(rgb).enhance(g["saturation"])
    if g.get("tint") and g.get("tint_strength", 0) > 0:
        tint = Image.new("RGB", rgb.size, tuple(int(c * 255) for c in
                                                features.hex_to_rgb(g["tint"])))
        # multiply-style tint preserves luminance structure
        mult = Image.fromarray((np.asarray(rgb, dtype=np.float32) *
                                np.asarray(tint, dtype=np.float32) / 255.0).astype(np.uint8))
        rgb = Image.blend(rgb, mult, float(g["tint_strength"]))
    out = rgb.convert("RGBA")
    out.putalpha(alpha)
    return out


def gradient(size: tuple[int, int], colors: list[str], direction: str) -> Image.Image:
    w, h = size
    stops = np.array([features.hex_to_rgb(c) for c in colors], dtype=np.float32)
    if direction == "radial":
        yy, xx = np.mgrid[0:h, 0:w]
        t = np.sqrt(((xx - w / 2) / (w / 2)) ** 2 + ((yy - h / 2) / (h / 2)) ** 2) / np.sqrt(2)
    elif direction == "horizontal":
        t = np.tile(np.linspace(0, 1, w, dtype=np.float32), (h, 1))
    else:
        t = np.tile(np.linspace(0, 1, h, dtype=np.float32)[:, None], (1, w))
    t = np.clip(t, 0, 1) * (len(stops) - 1)
    i0 = np.clip(np.floor(t).astype(int), 0, len(stops) - 1)
    i1 = np.clip(i0 + 1, 0, len(stops) - 1)
    f = (t - i0)[..., None]
    rgb = stops[i0] * (1 - f) + stops[i1] * f
    a = np.ones((h, w, 1), dtype=np.float32)
    return Image.fromarray((np.concatenate([rgb, a], axis=2) * 255).round().astype(np.uint8),
                           "RGBA")


def shape_mask(size: tuple[int, int], shape: dict[str, Any]) -> Image.Image:
    w, h = size
    m = Image.new("L", size, 0)
    d = ImageDraw.Draw(m)
    kind = shape.get("type", "rect")
    if kind == "polygon":
        pts = [(float(x) * w, float(y) * h) for x, y in shape["points"]]
        d.polygon(pts, fill=255)
    elif kind == "ellipse":
        x, y, ew, eh = shape["box"]
        d.ellipse([x * w, y * h, (x + ew) * w, (y + eh) * h], fill=255)
    else:
        x, y, rw, rh = shape.get("box", [0, 0, 1, 1])
        d.rectangle([x * w, y * h, (x + rw) * w, (y + rh) * h], fill=255)
    return m


def build_layer_content(size: tuple[int, int], spec: dict[str, Any]) -> tuple[Image.Image, str]:
    fill = spec.get("fill", {"type": "solid", "colors": ["#ffffff"]})
    colors = fill.get("colors") or ["#ffffff"]
    if fill.get("type") == "gradient" and len(colors) > 1:
        img = gradient(size, colors, fill.get("direction", "vertical"))
        kind = "gradient"
    else:
        img = Image.new("RGBA", size, tuple(int(c * 255) for c in
                                            features.hex_to_rgb(colors[0])) + (255,))
        kind = "solid"
    if spec.get("shape"):
        img.putalpha(shape_mask(size, spec["shape"]))
        kind += "+shape"
    return img, kind


class OraDocument:
    def __init__(self, width: int, height: int, root_id: str = "image", root_name: str = "Image"):
        self.width, self.height = width, height
        self.root_id, self.root_name = root_id, root_name
        self.layers: list[Layer] = []  # top-most first (OpenRaster stack order)

    def layer(self, lid: str) -> Layer:
        for l in self.layers:
            if l.id == lid:
                return l
        raise AdapterError(f"layer not found: {lid}")

    # -- io ----------------------------------------------------------------
    @classmethod
    def load(cls, path: Path) -> "OraDocument":
        with zipfile.ZipFile(path) as z:
            if z.read("mimetype").strip() != MIMETYPE:
                raise AdapterError("not an OpenRaster file")
            root = ET.fromstring(z.read("stack.xml"))
            stack = root.find("stack")
            doc = cls(int(root.get("w")), int(root.get("h")),
                      stack.get("daedelus-id", "image") if stack is not None else "image",
                      stack.get("name", "Image") if stack is not None else "Image")
            meta = json.loads(z.read("daedelus/meta.json")) if "daedelus/meta.json" in z.namelist() \
                else {}
            for el in (stack if stack is not None else []):
                if el.tag != "layer":
                    continue
                lid = el.get("daedelus-id") or Path(el.get("src")).stem
                lm = meta.get("layers", {}).get(lid, {})
                base_name = f"daedelus/base/{lid}.png"
                src = base_name if base_name in z.namelist() else el.get("src")
                base = Image.open(io.BytesIO(z.read(src))).convert("RGBA")
                doc.layers.append(Layer(
                    lid, el.get("name", lid), base, opacity=float(el.get("opacity", 1.0)),
                    visible=el.get("visibility", "visible") == "visible",
                    composite=el.get("composite-op", "svg:src-over"),
                    x=int(el.get("x", 0)), y=int(el.get("y", 0)),
                    grade=lm.get("grade"), base_kind=lm.get("base_kind", "raster")))
            return doc

    def save(self, path: Path) -> None:
        image = ET.Element("image", {"version": "0.0.5", "w": str(self.width),
                                     "h": str(self.height)})
        stack = ET.SubElement(image, "stack", {"name": self.root_name,
                                               "daedelus-id": self.root_id})
        meta = {"generator": "daedelus.layered2d/1", "layers": {}}
        tmp = path.with_suffix(".tmp")
        with zipfile.ZipFile(tmp, "w") as z:
            z.writestr(zipfile.ZipInfo("mimetype"), MIMETYPE, compress_type=zipfile.ZIP_STORED)
            for l in self.layers:
                src = f"data/{l.id}.png"
                ET.SubElement(stack, "layer", {
                    "name": l.name, "src": src, "x": str(l.x), "y": str(l.y),
                    "opacity": f"{l.opacity:.4f}",
                    "visibility": "visible" if l.visible else "hidden",
                    "composite-op": l.composite, "daedelus-id": l.id})
                z.writestr(_zinfo(src), _png(l.rendered()), compress_type=zipfile.ZIP_DEFLATED)
                z.writestr(_zinfo(f"daedelus/base/{l.id}.png"), _png(l.base),
                           compress_type=zipfile.ZIP_DEFLATED)
                meta["layers"][l.id] = {"grade": l.grade, "base_kind": l.base_kind}
            z.writestr(_zinfo("stack.xml"), ET.tostring(image, xml_declaration=True,
                                                        encoding="UTF-8"),
                       compress_type=zipfile.ZIP_DEFLATED)
            merged = self.composite()
            z.writestr(_zinfo("mergedimage.png"), _png(merged), compress_type=zipfile.ZIP_DEFLATED)
            thumb = merged.copy()
            thumb.thumbnail((256, 256))
            z.writestr(_zinfo("Thumbnails/thumbnail.png"), _png(thumb),
                       compress_type=zipfile.ZIP_DEFLATED)
            z.writestr(_zinfo("daedelus/meta.json"), json.dumps(meta, sort_keys=True, indent=1),
                       compress_type=zipfile.ZIP_DEFLATED)
        tmp.replace(path)

    def composite(self) -> Image.Image:
        canvas = Image.new("RGBA", (self.width, self.height), (0, 0, 0, 0))
        for l in reversed(self.layers):  # bottom-most first
            if not l.visible:
                continue
            img = l.rendered()
            if l.opacity < 1.0:
                a = img.getchannel("A").point(lambda v, o=l.opacity: int(v * o))
                img.putalpha(a)
            layer_canvas = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
            layer_canvas.paste(img, (l.x, l.y))
            canvas = Image.alpha_composite(canvas, layer_canvas)
        return canvas


def _zinfo(name: str) -> zipfile.ZipInfo:
    zi = zipfile.ZipInfo(name, date_time=(2020, 1, 1, 0, 0, 0))  # deterministic archives
    zi.external_attr = 0o644 << 16
    return zi


def _png(img: Image.Image) -> bytes:
    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=False)
    return buf.getvalue()


def _layer_hash(l: Layer) -> str:
    h = hashlib.sha256()
    img = l.rendered()
    h.update(f"{img.size}{l.opacity:.4f}{l.visible}{l.composite}{l.x},{l.y}".encode())
    h.update(img.tobytes())
    return h.hexdigest()


class LayeredImageAdapter(Adapter):
    name = "layered2d"
    version = "1"

    def info(self) -> AdapterInfo:
        layer_kinds = ["layer"]
        any_kind = ["layer", "image"]
        excl = {"exclude": {"type": "array", "items": {"type": "string"}}}
        return AdapterInfo(
            name=self.name, version=self.version,
            description="Layered raster images stored as OpenRaster (.ora); layers are components.",
            artifact_types=["image2d"], native_formats=[".ora"],
            preview_formats=["png (composite)", "png (per layer)"],
            export_formats=["png", "ora"],
            scopes=["artifact", "image", "layer"],
            measurements=["width", "height", "layer_count", "coverage", "brightness"],
            validation=["component_preservation", "constraints", "op_results", "file_reopens"],
            environment={"requires": "Pillow (bundled)"},
            templates=["layers", "blank"],
            operations=[
                OperationSpec(
                    name="fill_gradient", family="palette_fill", aspects=["color", "palette",
                                                                          "lighting", "mood"],
                    target_kinds=any_kind, subtree=True,
                    description="Replace layer base pixels with a gradient through the given colours "
                                "(keeps the layer's alpha mask).",
                    params_schema={"type": "object", "additionalProperties": False,
                                   "required": ["colors"], "properties": {
                                       "colors": {"type": "array", "items": HEX_COLOR,
                                                  "minItems": 1, "maxItems": 8},
                                       "direction": {"type": "string",
                                                     "enum": ["vertical", "horizontal", "radial"]},
                                       "preserve_alpha": {"type": "boolean"}, **excl}}),
                OperationSpec(
                    name="color_grade", family="color_grade", aspects=["color", "style", "mood",
                                                                       "palette", "lighting"],
                    target_kinds=any_kind, subtree=True,
                    description="Non-destructive grade (tint, brightness, saturation, contrast).",
                    params_schema={"type": "object", "additionalProperties": False, "properties": {
                        "tint": HEX_COLOR,
                        "tint_strength": {"type": "number", "minimum": 0, "maximum": 1},
                        "brightness": {"type": "number", "minimum": 0.2, "maximum": 2.0},
                        "saturation": {"type": "number", "minimum": 0, "maximum": 2.5},
                        "contrast": {"type": "number", "minimum": 0.2, "maximum": 2.5}, **excl}}),
                OperationSpec(
                    name="paint_reference", family="reference_paint",
                    aspects=["composition", "texture", "content"],
                    target_kinds=layer_kinds,
                    description="Paint a source image into the layer (fit, blur, opacity).",
                    params_schema={"type": "object", "additionalProperties": False,
                                   "required": ["source_id"], "properties": {
                                       "source_id": {"type": "string"},
                                       "fit": {"type": "string", "enum": ["cover", "contain"]},
                                       "blur": {"type": "number", "minimum": 0, "maximum": 50},
                                       "mix": {"type": "number", "minimum": 0, "maximum": 1},
                                       "preserve_alpha": {"type": "boolean"}}}),
                OperationSpec(
                    name="set_layer_props", family="layer_props", aspects=["composition"],
                    target_kinds=layer_kinds, description="Opacity / visibility / name.",
                    params_schema={"type": "object", "additionalProperties": False, "properties": {
                        "opacity": {"type": "number", "minimum": 0, "maximum": 1},
                        "visible": {"type": "boolean"}, "name": {"type": "string"}}}),
                OperationSpec(
                    name="add_layer", family="create", aspects=["composition"],
                    target_kinds=["new"], description="Add a layer (idempotent by id).",
                    params_schema={"type": "object", "required": ["id"], "properties": {
                        "id": {"type": "string", "pattern": "[A-Za-z0-9_.-]+"},
                        "name": {"type": "string"}, "index": {"type": "integer", "minimum": 0},
                        "fill": {"type": "object"}, "shape": {"type": "object"}}}),
            ],
        )

    # ------------------------------------------------------------------
    def create(self, native_dir: Path, template: str, params: dict[str, Any]) -> str:
        native_dir.mkdir(parents=True, exist_ok=True)
        w, h = int(params.get("width", 640)), int(params.get("height", 400))
        doc = OraDocument(w, h, params.get("root_id", "image"), params.get("name", "Image"))
        specs = params.get("layers", []) if template == "layers" else [
            {"id": "base", "name": "Base", "fill": {"type": "solid", "colors": ["#ffffff"]}}]
        if template not in ("layers", "blank"):
            raise AdapterError(f"unknown layered2d template: {template}")
        for spec in specs:  # specs are listed top-most first
            img, kind = build_layer_content((w, h), spec)
            doc.layers.append(Layer(spec["id"], spec.get("name", spec["id"]), img,
                                    opacity=float(spec.get("opacity", 1.0)), base_kind=kind))
        doc.save(native_dir / ENTRY)
        return ENTRY

    def inspect(self, native_dir: Path, entry: str) -> InspectResult:
        doc = OraDocument.load(native_dir / entry)
        comps = [Component(id=doc.root_id, name=doc.root_name, kind="image", native_ref="stack.xml",
                           metadata={"width": doc.width, "height": doc.height})]
        states: dict[str, str] = {}
        meas: dict[str, dict[str, Any]] = {}
        props: dict[str, dict[str, Any]] = {}
        root_h = hashlib.sha256(f"{doc.width}x{doc.height}".encode())
        for idx, l in enumerate(doc.layers):
            comps.append(Component(id=l.id, name=l.name, kind="layer", parent_id=doc.root_id,
                                   native_ref=f"data/{l.id}.png",
                                   metadata={"stack_index": idx}))
            states[l.id] = _layer_hash(l)
            root_h.update(l.id.encode())
            arr = np.asarray(l.rendered(), dtype=np.float32) / 255.0
            alpha = arr[..., 3]
            cov = float((alpha > 0.01).mean())
            visible_rgb = arr[..., :3][alpha > 0.01]
            mean = visible_rgb.mean(axis=0) if len(visible_rgb) else np.zeros(3)
            meas[l.id] = {"coverage": round(cov, 4), "mean_color": features.rgb_to_hex(mean),
                          "brightness": round(float(mean @ np.array([0.2126, 0.7152, 0.0722])), 4),
                          "opacity": l.opacity}
            props[l.id] = {"grade": l.grade, "base_kind": l.base_kind, "opacity": l.opacity,
                           "visible": l.visible, "mean_color": meas[l.id]["mean_color"]}
        states[doc.root_id] = root_h.hexdigest()
        comp = np.asarray(doc.composite().convert("RGB"), dtype=np.float32) / 255.0
        meas[doc.root_id] = {"width": doc.width, "height": doc.height,
                             "layer_count": len(doc.layers),
                             "brightness": round(float((comp @ np.array(
                                 [0.2126, 0.7152, 0.0722])).mean()), 4)}
        props[doc.root_id] = {"size": [doc.width, doc.height]}
        return InspectResult(components=comps, states=states, measurements=meas, properties=props)

    def apply(self, native_dir: Path, entry: str, operations: list[PlannedOperation],
              context: dict[str, Any]) -> ApplyResult:
        path = native_dir / entry
        doc = OraDocument.load(path)
        results = []
        source_paths: dict[str, str] = context.get("source_paths", {})
        for i, op in enumerate(operations):
            try:
                detail = self._apply_one(doc, op, source_paths)
                results.append({"index": i, "op": op.op, "component_id": op.component_id,
                                "status": "applied", "detail": detail})
            except Exception as exc:
                results.append({"index": i, "op": op.op, "component_id": op.component_id,
                                "status": "failed", "detail": f"{type(exc).__name__}: {exc}"})
                return ApplyResult(ok=False, results=results, error=results[-1]["detail"])
        doc.save(path)
        return ApplyResult(ok=True, results=results)

    def _targets(self, doc: OraDocument, cid: str | None, exclude: list[str]) -> list[Layer]:
        if cid in (None, doc.root_id):
            return [l for l in doc.layers if l.id not in exclude]
        return [doc.layer(cid)] if cid not in exclude else []

    def _apply_one(self, doc: OraDocument, op: PlannedOperation,
                   source_paths: dict[str, str]) -> str:
        p = op.params
        if op.op == "fill_gradient":
            layers = self._targets(doc, op.component_id, p.get("exclude", []))
            for l in layers:
                img = gradient((l.base.width, l.base.height), p["colors"],
                               p.get("direction", "vertical"))
                if p.get("preserve_alpha", True):
                    img.putalpha(l.base.getchannel("A"))
                l.base = img
                l.base_kind = "gradient"
            return f"gradient {p['colors']} on {[l.id for l in layers]}"
        if op.op == "color_grade":
            layers = self._targets(doc, op.component_id, p.get("exclude", []))
            for l in layers:
                l.grade = {**DEFAULT_GRADE, **{k: v for k, v in p.items() if k != "exclude"}}
            return f"grade on {[l.id for l in layers]}"
        if op.op == "paint_reference":
            l = doc.layer(op.component_id or "")
            sp = source_paths.get(p["source_id"])
            if not sp:
                raise AdapterError(f"source image not available: {p['source_id']}")
            with Image.open(sp) as im:
                ref = im.convert("RGBA")
            size = (l.base.width, l.base.height)
            ref = (ImageOps.fit(ref, size, Image.Resampling.LANCZOS) if p.get("fit", "cover")
                   == "cover" else ImageOps.pad(ref, size, Image.Resampling.LANCZOS))
            if p.get("blur", 0) > 0:
                ref = ref.filter(ImageFilter.GaussianBlur(float(p["blur"])))
            mix = float(p.get("mix", 1.0))
            base = Image.blend(l.base, ref, mix) if mix < 1.0 else ref
            if p.get("preserve_alpha", True):
                base.putalpha(l.base.getchannel("A"))
            l.base = base
            l.base_kind = "reference"
            return f"painted source {p['source_id']} into {l.id} (mix {mix})"
        if op.op == "set_layer_props":
            l = doc.layer(op.component_id or "")
            if "opacity" in p:
                l.opacity = float(p["opacity"])
            if "visible" in p:
                l.visible = bool(p["visible"])
            if "name" in p:
                l.name = str(p["name"])
            return f"props {p} on {l.id}"
        if op.op == "add_layer":
            if any(l.id == p["id"] for l in doc.layers):
                return f"layer {p['id']} already exists (idempotent no-op)"
            img, kind = build_layer_content((doc.width, doc.height), p)
            idx = min(int(p.get("index", 0)), len(doc.layers))
            doc.layers.insert(idx, Layer(p["id"], p.get("name", p["id"]), img, base_kind=kind))
            return f"added layer {p['id']}"
        raise AdapterError(f"unknown operation {op.op}")

    def preview(self, native_dir: Path, entry: str, out_dir: Path) -> dict[str, Path]:
        out_dir.mkdir(parents=True, exist_ok=True)
        doc = OraDocument.load(native_dir / entry)
        out = {}
        comp = out_dir / "composite.png"
        doc.composite().save(comp)
        out["composite"] = comp
        for l in doc.layers:
            lp = out_dir / f"layer_{l.id}.png"
            l.rendered().save(lp)
            out[f"layer:{l.id}"] = lp
        return out

    def export(self, native_dir: Path, entry: str, fmt: str, out_dir: Path) -> Path:
        out_dir.mkdir(parents=True, exist_ok=True)
        if fmt == "png":
            p = out_dir / "composite.png"
            OraDocument.load(native_dir / entry).composite().save(p)
            return p
        if fmt == "ora":
            p = out_dir / ENTRY
            p.write_bytes((native_dir / entry).read_bytes())
            return p
        raise AdapterError(f"unsupported export format {fmt}")

    def validate(self, native_dir: Path, entry: str, checks: list[str],
                 context: dict[str, Any]) -> ValidationReport:
        rep = ValidationReport()
        if "file_reopens" in checks:
            path = native_dir / entry
            try:
                with zipfile.ZipFile(path) as z:
                    first = z.infolist()[0]
                    ok = first.filename == "mimetype" and first.compress_type == zipfile.ZIP_STORED
                    names = set(z.namelist())
                    doc = OraDocument.load(path)
                    missing = [l.id for l in doc.layers if f"data/{l.id}.png" not in names]
                    ok = ok and not missing and "mergedimage.png" in names
                rep.add("file_reopens", ok,
                        f"OpenRaster valid: mimetype first/stored, {len(doc.layers)} layer PNGs, "
                        f"mergedimage present" if ok else f"invalid OpenRaster (missing {missing})")
            except Exception as exc:
                rep.add("file_reopens", False, f"{type(exc).__name__}: {exc}")
        return rep
