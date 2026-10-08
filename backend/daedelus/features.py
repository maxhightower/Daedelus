"""Deterministic, dependency-light feature extraction shared by ingestion and validation.

These are *measurements*, not interpretations: they describe what is in a
piece of media (palette, silhouette, proportions, directives in text). Roles
and scopes decide later how - and whether - a measurement is used.
"""

from __future__ import annotations

import colorsys
import re
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image

NAMED_COLORS = {
    "black": "#000000", "white": "#ffffff", "red": "#c0392b", "green": "#27ae60",
    "blue": "#2e6fd8", "yellow": "#f1c40f", "orange": "#e67e22", "purple": "#8e44ad",
    "pink": "#e88aa8", "brown": "#7b4a2a", "grey": "#808080", "gray": "#808080",
    "oak": "#b8874f", "walnut": "#5d3a1a", "teal": "#1f8a8a", "navy": "#1b2a4a",
    "cream": "#efe3c8", "sand": "#d8c49a", "terracotta": "#c46a43", "olive": "#6b7a2e",
    "charcoal": "#36393f", "ivory": "#f4efe1", "sky": "#87b5e5", "crimson": "#a3162b",
    "gold": "#c9a227", "silver": "#b8bcc2", "mint": "#9fd8b8", "lavender": "#b6a6d8",
}

HEX_RE = re.compile(r"#(?:[0-9a-fA-F]{6}|[0-9a-fA-F]{3})\b")
DIRECTIVE_RE = re.compile(r"^\s*[-*]?\s*([A-Za-z][\w .\-/]{0,40}?)\s*[:=]\s*(.+?)\s*$")
NUMBER_RE = re.compile(r"[-+]?\d*\.?\d+")


# ---------------------------------------------------------------------------
# colour helpers
# ---------------------------------------------------------------------------

def hex_to_rgb(h: str) -> tuple[float, float, float]:
    h = h.strip().lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    return tuple(int(h[i:i + 2], 16) / 255.0 for i in (0, 2, 4))  # type: ignore[return-value]


def rgb_to_hex(rgb: tuple[float, float, float] | list[float]) -> str:
    r, g, b = (max(0, min(255, int(round(c * 255)))) for c in rgb[:3])
    return f"#{r:02x}{g:02x}{b:02x}"


def mix_hex(a: str, b: str, t: float) -> str:
    """Linear blend from ``a`` (t=0) to ``b`` (t=1)."""
    t = max(0.0, min(1.0, t))
    ra, rb = hex_to_rgb(a), hex_to_rgb(b)
    return rgb_to_hex([ra[i] + (rb[i] - ra[i]) * t for i in range(3)])


def color_distance(a: str, b: str) -> float:
    ra, rb = np.array(hex_to_rgb(a)), np.array(hex_to_rgb(b))
    return float(np.linalg.norm(ra - rb) / np.sqrt(3))


def parse_color(value: str) -> str | None:
    value = value.strip().lower()
    m = HEX_RE.search(value)
    if m:
        return rgb_to_hex(hex_to_rgb(m.group(0)))
    for name, hx in NAMED_COLORS.items():
        if re.search(rf"\b{name}\b", value):
            return hx
    return None


def parse_colors(value: str) -> list[str]:
    found = [rgb_to_hex(hex_to_rgb(m.group(0))) for m in HEX_RE.finditer(value)]
    if found:
        return found
    low = value.lower()
    named = []
    for name, hx in NAMED_COLORS.items():
        m = re.search(rf"\b{name}\b", low)
        if m:
            named.append((m.start(), hx))
    return [hx for _, hx in sorted(named)]


# ---------------------------------------------------------------------------
# images
# ---------------------------------------------------------------------------

def _load_rgb(path: Path, max_side: int = 512) -> np.ndarray:
    with Image.open(path) as im:
        im = im.convert("RGBA")
        bg = Image.new("RGBA", im.size, (255, 255, 255, 255))
        im = Image.alpha_composite(bg, im).convert("RGB")
        im.thumbnail((max_side, max_side), Image.Resampling.LANCZOS)
        return np.asarray(im, dtype=np.float32) / 255.0


def palette(arr: np.ndarray, k: int = 5) -> list[dict[str, Any]]:
    img = Image.fromarray((arr * 255).astype(np.uint8))
    q = img.quantize(colors=k, method=Image.Quantize.MEDIANCUT, dither=Image.Dither.NONE)
    pal = q.getpalette() or []
    counts = sorted(q.getcolors() or [], key=lambda c: (-c[0], c[1]))
    total = sum(c for c, _ in counts) or 1
    out = []
    for count, idx in counts:
        rgb = [pal[idx * 3 + i] / 255.0 for i in range(3)]
        out.append({"hex": rgb_to_hex(rgb), "weight": round(count / total, 4)})
    return out


def silhouette(arr: np.ndarray) -> dict[str, Any]:
    """Foreground estimate: pixels that differ from the median border colour."""
    h, w, _ = arr.shape
    border = np.concatenate([arr[0, :], arr[-1, :], arr[:, 0], arr[:, -1]])
    bg = np.median(border, axis=0)
    diff = np.linalg.norm(arr - bg, axis=2)
    mask = diff > 0.18
    cover = float(mask.mean())
    if cover < 0.002 or cover > 0.98:
        return {"found": False, "coverage": round(cover, 4), "background": rgb_to_hex(bg)}
    ys, xs = np.nonzero(mask)
    y0, y1, x0, x1 = ys.min(), ys.max(), xs.min(), xs.max()
    bw, bh = (x1 - x0 + 1), (y1 - y0 + 1)
    sub = mask[y0:y1 + 1, x0:x1 + 1]
    band = max(1, bh // 5)
    top_w = float(sub[:band].sum(axis=1).mean())
    bot_w = float(sub[-band:].sum(axis=1).mean())
    fg = arr[mask]
    fg_pal = palette(fg.reshape(-1, 1, 3), k=4) if len(fg) > 16 else []
    # vertical "spread": how much of the silhouette's width is filled at mid height
    mid = sub[bh // 2]
    return {
        "found": True,
        "coverage": round(cover, 4),
        "bbox": [round(x0 / w, 4), round(y0 / h, 4), round(bw / w, 4), round(bh / h, 4)],
        "aspect": round(float(bw / bh), 4),  # width / height of the foreground
        "top_width": round(top_w / bw, 4),
        "bottom_width": round(bot_w / bw, 4),
        "taper": round(float((top_w - bot_w) / max(top_w, bot_w, 1.0)), 4),
        "mid_fill": round(float(mid.mean()), 4),
        "background": rgb_to_hex(bg),
        "foreground_palette": fg_pal,
    }


def image_features(path: Path) -> dict[str, Any]:
    arr = _load_rgb(path)
    h, w, _ = arr.shape
    lum = arr @ np.array([0.2126, 0.7152, 0.0722], dtype=np.float32)
    mean = arr.reshape(-1, 3).mean(axis=0)
    hsv = colorsys.rgb_to_hsv(*mean)
    gy, gx = np.gradient(lum)
    energy_v = float(np.abs(gy).mean())
    energy_h = float(np.abs(gx).mean())
    pal = palette(arr)
    # vertical colour profile: top / middle / bottom thirds (useful for gradients)
    thirds = [rgb_to_hex(arr[int(h * i / 3):int(h * (i + 1) / 3)].reshape(-1, 3).mean(axis=0))
              for i in range(3)]
    return {
        "analysis_size": [int(w), int(h)],
        "aspect": round(w / h, 4),
        "palette": pal,
        "dominant": pal[0]["hex"] if pal else None,
        "mean_color": rgb_to_hex(mean),
        "brightness": round(float(lum.mean()), 4),
        "contrast": round(float(lum.std()), 4),
        "saturation": round(float(hsv[1]), 4),
        "warmth": round(float(mean[0] - mean[2]), 4),
        "edge_density": round(float((np.hypot(gx, gy) > 0.08).mean()), 4),
        "orientation": round(energy_v / (energy_v + energy_h + 1e-9), 4),
        "vertical_profile": thirds,
        "silhouette": silhouette(arr),
    }


def make_thumbnail(src: Path, dst: Path, size: int = 320) -> Path:
    with Image.open(src) as im:
        im = im.convert("RGBA")
        im.thumbnail((size, size), Image.Resampling.LANCZOS)
        dst.parent.mkdir(parents=True, exist_ok=True)
        im.save(dst, format="PNG")
    return dst


# ---------------------------------------------------------------------------
# text
# ---------------------------------------------------------------------------

def parse_number(value: str) -> float | None:
    m = NUMBER_RE.search(value)
    return float(m.group(0)) if m else None


def text_features(text: str) -> dict[str, Any]:
    lines = text.splitlines()
    directives: dict[str, str] = {}
    headings = []
    for ln in lines:
        s = ln.strip()
        if s.startswith("#"):
            headings.append(s.lstrip("#").strip())
            continue
        m = DIRECTIVE_RE.match(ln)
        if m and not s.lower().startswith(("http:", "https:")):
            key = re.sub(r"[\s\-/.]+", "_", m.group(1).strip().lower())
            directives[key] = m.group(2).strip()
    words = re.findall(r"[A-Za-z][A-Za-z\-']+", text.lower())
    freq: dict[str, int] = {}
    for wd in words:
        if len(wd) > 3:
            freq[wd] = freq.get(wd, 0) + 1
    keywords = [w for w, _ in sorted(freq.items(), key=lambda kv: (-kv[1], kv[0]))[:25]]
    return {
        "characters": len(text),
        "words": len(words),
        "lines": len(lines),
        "headings": headings[:50],
        "directives": directives,
        "colors": parse_colors(text),
        "keywords": keywords,
        "excerpt": text[:1200],
    }
