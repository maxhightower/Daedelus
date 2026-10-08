"""Source registration and ingestion.

Ingestion only records *what a source is*: its media type, raw content,
metadata, measurements and previews. It never assigns a role or a target -
that is the job of ``SourceBinding``.

Every extractor is replaceable via ``EXTRACTORS``. Extractors must report
honestly: if content cannot be accessed or understood the source is marked
``partial``/``failed``/``unsupported`` with the reason, and nothing is invented.
"""

from __future__ import annotations

import ast
import hashlib
import json
import mimetypes
import os
import re
import shutil
import struct
import subprocess
from pathlib import Path
from typing import Any, Callable
from urllib.parse import parse_qs, urlparse

from . import features
from .models import (
    Locator,
    MediaSource,
    MediaType,
    Processing,
    ProcessingState,
    Provenance,
    now_iso,
)
from .store import ProjectStore

EXT_TYPES: dict[MediaType, set[str]] = {
    MediaType.image: {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".tif", ".tiff"},
    MediaType.video: {".mp4", ".mov", ".webm", ".mkv", ".avi", ".m4v"},
    MediaType.audio: {".mp3", ".wav", ".ogg", ".flac", ".m4a"},
    MediaType.document: {".pdf"},
    MediaType.text: {".txt", ".md", ".markdown", ".rst"},
    MediaType.model3d: {".glb", ".gltf", ".obj", ".stl", ".blend", ".ply", ".fbx"},
    MediaType.code: {".py", ".ts", ".tsx", ".js", ".jsx", ".rs", ".go", ".java", ".c", ".cc",
                     ".cpp", ".h", ".hpp", ".cs", ".rb", ".php", ".json", ".toml", ".yaml",
                     ".yml", ".css", ".html"},
}

VIDEO_HOSTS = ("youtube.com", "youtu.be", "vimeo.com")
SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv", "dist", "build", "target"}


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def sha256_tree(root: Path) -> str:
    h = hashlib.sha256()
    for p in sorted(root.rglob("*")):
        if any(part in SKIP_DIRS for part in p.relative_to(root).parts):
            continue
        if p.is_file():
            h.update(p.relative_to(root).as_posix().encode())
            h.update(sha256_file(p).encode())
    return h.hexdigest()


def detect_media_type(name: str) -> MediaType:
    ext = Path(name).suffix.lower()
    for mt, exts in EXT_TYPES.items():
        if ext in exts:
            return mt
    return MediaType.file


def _safe_name(name: str) -> str:
    base = re.sub(r"[^A-Za-z0-9._-]+", "_", Path(name).name).strip("._") or "file"
    return base[:120]


# ---------------------------------------------------------------------------
# registration
# ---------------------------------------------------------------------------

def register_bytes(store: ProjectStore, data: bytes, filename: str, *, name: str | None = None,
                   media_type: MediaType | None = None, origin: str = "upload",
                   tags: list[str] | None = None, ingest_now: bool = True) -> MediaSource:
    mt = media_type or detect_media_type(filename)
    src = MediaSource(
        name=name or filename, media_type=mt,
        mime_type=mimetypes.guess_type(filename)[0],
        locator=Locator(kind="file"),
        provenance=Provenance(origin=origin, original_name=filename),  # type: ignore[arg-type]
        tags=tags or [],
    )
    d = store.source_dir(src.id)
    dest = d / f"original{Path(filename).suffix.lower()}"
    dest.write_bytes(data)
    src.locator.path = store.rel(dest)
    src.content_hash = hashlib.sha256(data).hexdigest()
    src.size_bytes = len(data)
    store.save_source(src)
    return ingest(store, src) if ingest_now else src


def register_file(store: ProjectStore, path: Path | str, **kw: Any) -> MediaSource:
    path = Path(path)
    if path.is_dir():
        return register_path(store, path, **kw)
    return register_bytes(store, path.read_bytes(), path.name, origin=kw.pop("origin", "path"), **kw)


def register_text(store: ProjectStore, name: str, text: str, *, origin: str = "inline",
                  tags: list[str] | None = None) -> MediaSource:
    fname = name if Path(name).suffix else f"{name}.md"
    return register_bytes(store, text.encode("utf-8"), _safe_name(fname), name=name,
                          media_type=MediaType.text, origin=origin, tags=tags)


def register_path(store: ProjectStore, path: Path | str, *, name: str | None = None,
                  media_type: MediaType | None = None, tags: list[str] | None = None,
                  ingest_now: bool = True, **_: Any) -> MediaSource:
    """Register a directory (e.g. a code repository) by copying a snapshot of it."""
    path = Path(path).resolve()
    if not path.is_dir():
        raise ValueError(f"not a directory: {path}")
    src = MediaSource(
        name=name or path.name, media_type=media_type or MediaType.code,
        locator=Locator(kind="repo", original_path=str(path)),
        provenance=Provenance(origin="path", original_name=path.name),
        tags=tags or [],
    )
    dest = store.source_dir(src.id) / "snapshot"
    shutil.copytree(path, dest, ignore=shutil.ignore_patterns(*SKIP_DIRS))
    src.locator.path = store.rel(dest)
    src.content_hash = sha256_tree(dest)
    store.save_source(src)
    return ingest(store, src) if ingest_now else src


def register_url(store: ProjectStore, url: str, *, name: str | None = None,
                 media_type: MediaType | None = None, tags: list[str] | None = None,
                 ingest_now: bool = True) -> MediaSource:
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise ValueError("only http(s) URLs are supported")
    host = parsed.netloc.lower()
    if media_type is None:
        if any(host.endswith(h) for h in VIDEO_HOSTS):
            media_type = MediaType.video_url
        elif url.endswith(".git") or host.endswith("github.com") and parsed.path.count("/") == 2:
            media_type = MediaType.code
        else:
            guess = detect_media_type(parsed.path)
            media_type = guess if guess != MediaType.file else MediaType.url
    src = MediaSource(
        name=name or url, media_type=media_type,
        locator=Locator(kind="url", url=url),
        provenance=Provenance(origin="url", url=url),
        content_hash=hashlib.sha256(url.encode()).hexdigest(),
        tags=tags or [],
    )
    store.save_source(src)
    return ingest(store, src) if ingest_now else src


# ---------------------------------------------------------------------------
# ingestion dispatch
# ---------------------------------------------------------------------------

class ExtractionResult:
    def __init__(self) -> None:
        self.extracted: dict[str, Any] = {}
        self.metadata: dict[str, Any] = {}
        self.preview: Path | None = None
        self.state = ProcessingState.ready
        self.warnings: list[str] = []
        self.error: str | None = None

    def partial(self, msg: str) -> None:
        self.warnings.append(msg)
        if self.state == ProcessingState.ready:
            self.state = ProcessingState.partial


Extractor = Callable[[ProjectStore, MediaSource], ExtractionResult]
EXTRACTORS: dict[MediaType, tuple[str, str, Extractor]] = {}


def extractor(media_type: MediaType, name: str, version: str = "1"):
    def deco(fn: Extractor) -> Extractor:
        EXTRACTORS[media_type] = (name, version, fn)
        return fn
    return deco


def ingest(store: ProjectStore, src: MediaSource) -> MediaSource:
    entry = EXTRACTORS.get(src.media_type)
    if entry is None:
        src.processing = Processing(state=ProcessingState.unsupported, extractor=None,
                                    error=f"no extractor registered for {src.media_type.value}")
        return store.save_source(src)
    name, version, fn = entry
    src.processing = Processing(state=ProcessingState.processing, extractor=name,
                                extractor_version=version)
    store.save_source(src)
    try:
        res = fn(store, src)
    except Exception as exc:  # extractor bugs must surface as failures, never as fake data
        src.processing = Processing(state=ProcessingState.failed, extractor=name,
                                    extractor_version=version, error=f"{type(exc).__name__}: {exc}")
        return store.save_source(src)
    src.extracted = res.extracted
    src.metadata.update(res.metadata)
    if res.preview is not None:
        src.preview_path = store.rel(res.preview)
    src.processing = Processing(state=res.state, extractor=name, extractor_version=version,
                                warnings=res.warnings, error=res.error, updated_at=now_iso())
    return store.save_source(src)


def source_file(store: ProjectStore, src: MediaSource) -> Path:
    if not src.locator.path:
        raise FileNotFoundError(f"source {src.id} has no stored file")
    return store.abs(src.locator.path)


# ---------------------------------------------------------------------------
# extractors
# ---------------------------------------------------------------------------

@extractor(MediaType.text, "text.directives")
def _text(store: ProjectStore, src: MediaSource) -> ExtractionResult:
    r = ExtractionResult()
    raw = source_file(store, src).read_bytes()
    text = raw.decode("utf-8", errors="replace")
    r.extracted = {"text": text[:200_000], **features.text_features(text)}
    r.metadata = {"encoding": "utf-8"}
    return r


@extractor(MediaType.image, "image.measure")
def _image(store: ProjectStore, src: MediaSource) -> ExtractionResult:
    from PIL import Image

    r = ExtractionResult()
    path = source_file(store, src)
    with Image.open(path) as im:
        r.metadata = {"width": im.width, "height": im.height, "mode": im.mode,
                      "format": im.format}
    r.extracted = features.image_features(path)
    r.preview = features.make_thumbnail(path, path.parent / "preview.png")
    r.extracted["understanding"] = {
        "kind": "measured",
        "note": "palette, silhouette and proportions are pixel measurements; no semantic "
                "recognition is performed by the local extractor",
    }
    return r


def _ffprobe(path: Path) -> dict[str, Any] | None:
    if not shutil.which("ffprobe"):
        return None
    out = subprocess.run(["ffprobe", "-v", "error", "-print_format", "json", "-show_format",
                          "-show_streams", str(path)], capture_output=True, text=True, timeout=60)
    if out.returncode != 0:
        raise RuntimeError(f"ffprobe failed: {out.stderr.strip()[:400]}")
    return json.loads(out.stdout)


@extractor(MediaType.video, "video.frames")
def _video(store: ProjectStore, src: MediaSource) -> ExtractionResult:
    r = ExtractionResult()
    path = source_file(store, src)
    probe = _ffprobe(path)
    if probe is None:
        r.state = ProcessingState.partial
        r.warnings.append("ffprobe/ffmpeg not available: video stored but not analysed")
        r.extracted = {"understanding": {"kind": "none", "reason": "ffmpeg unavailable"}}
        return r
    fmt = probe.get("format", {})
    duration = float(fmt.get("duration") or 0)
    vstreams = [s for s in probe.get("streams", []) if s.get("codec_type") == "video"]
    r.metadata = {"duration": duration, "format": fmt.get("format_name"),
                  "width": vstreams[0].get("width") if vstreams else None,
                  "height": vstreams[0].get("height") if vstreams else None,
                  "streams": len(probe.get("streams", []))}
    frames_dir = path.parent / "frames"
    frames_dir.mkdir(exist_ok=True)
    frames = []
    if vstreams and duration > 0:
        n = 6
        for i in range(n):
            t = duration * (i + 0.5) / n
            fp = frames_dir / f"frame_{i:02d}.png"
            subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", f"{t:.3f}", "-i", str(path),
                            "-frames:v", "1", "-vf", "scale=320:-2", str(fp)],
                           capture_output=True, timeout=60)
            if fp.exists():
                feats = features.image_features(fp)
                frames.append({"time": round(t, 3), "path": store.rel(fp),
                               "dominant": feats["dominant"], "brightness": feats["brightness"],
                               "palette": feats["palette"][:3]})
    if not frames:
        r.partial("no frames could be decoded from the video")
    else:
        r.preview = features.make_thumbnail(store.abs(frames[len(frames) // 2]["path"]),
                                            path.parent / "preview.png")
    # aggregate palette across frames
    weights: dict[str, float] = {}
    for fr in frames:
        for p in fr["palette"]:
            weights[p["hex"]] = weights.get(p["hex"], 0.0) + p["weight"] / max(1, len(frames))
    agg = [{"hex": h, "weight": round(w, 4)} for h, w in
           sorted(weights.items(), key=lambda kv: -kv[1])[:6]]
    r.extracted = {
        "frames": frames,
        "palette": agg,
        "dominant": agg[0]["hex"] if agg else None,
        "temporal": {"duration": duration, "sampled_frames": len(frames)},
        "understanding": {"kind": "sampled_frames",
                          "note": "frames sampled and measured; no speech/caption or action "
                                  "recognition is performed by the local extractor"},
    }
    if not vstreams:
        r.partial("no video stream present")
    r.partial("captions/transcript not extracted (no speech recognition backend configured)")
    return r


def _youtube_id(url: str) -> str | None:
    p = urlparse(url)
    if p.netloc.endswith("youtu.be"):
        return p.path.lstrip("/") or None
    if "youtube.com" in p.netloc:
        if p.path == "/watch":
            return (parse_qs(p.query).get("v") or [None])[0]
        m = re.match(r"^/(?:shorts|embed|live)/([\w-]+)", p.path)
        if m:
            return m.group(1)
    return None


def http_client(timeout: float = 15.0):
    import httpx

    verify: Any = True
    for var in ("SSL_CERT_FILE", "REQUESTS_CA_BUNDLE", "CURL_CA_BUNDLE"):
        if os.environ.get(var) and Path(os.environ[var]).exists():
            verify = os.environ[var]
            break
    return httpx.Client(timeout=timeout, follow_redirects=True, verify=verify,
                        headers={"User-Agent": "Daedelus/0.1 (+source-ingestion)"})


@extractor(MediaType.video_url, "video_url.metadata")
def _video_url(store: ProjectStore, src: MediaSource) -> ExtractionResult:
    r = ExtractionResult()
    url = src.locator.url or ""
    host = urlparse(url).netloc.lower()
    yid = _youtube_id(url)
    r.metadata = {"host": host, "video_id": yid}
    r.extracted = {"understanding": {
        "kind": "metadata_only",
        "note": "only public metadata (title/author/thumbnail) is retrieved; the video's "
                "visual and audio content has NOT been analysed",
    }}
    if not store.get_project().settings.allow_network_fetch:
        r.partial("network fetch disabled in project settings; URL recorded only")
        return r
    oembed = None
    if "youtu" in host:
        oembed = f"https://www.youtube.com/oembed?url={url}&format=json"
    elif "vimeo.com" in host:
        oembed = f"https://vimeo.com/api/oembed.json?url={url}"
    if oembed is None:
        r.partial("no metadata endpoint known for this host; URL recorded only")
        return r
    try:
        with http_client() as c:
            resp = c.get(oembed)
            if resp.status_code != 200:
                raise RuntimeError(f"HTTP {resp.status_code}")
            meta = resp.json()
            r.metadata.update({k: meta.get(k) for k in
                               ("title", "author_name", "provider_name", "thumbnail_url")})
            title = meta.get("title") or ""
            r.extracted["title"] = title
            r.extracted.update({"title_features": features.text_features(title)})
            if meta.get("thumbnail_url"):
                t = c.get(meta["thumbnail_url"])
                if t.status_code == 200:
                    tp = store.source_dir(src.id) / "thumbnail.jpg"
                    tp.write_bytes(t.content)
                    r.preview = features.make_thumbnail(tp, tp.parent / "preview.png")
                    r.extracted["thumbnail_features"] = features.image_features(tp)
    except Exception as exc:
        r.partial(f"metadata fetch failed ({type(exc).__name__}: {exc}); URL recorded only")
    r.partial("video content not analysed (no video download/understanding backend configured)")
    return r


@extractor(MediaType.url, "url.metadata")
def _url(store: ProjectStore, src: MediaSource) -> ExtractionResult:
    r = ExtractionResult()
    url = src.locator.url or ""
    if not store.get_project().settings.allow_network_fetch:
        r.partial("network fetch disabled in project settings; URL recorded only")
        return r
    try:
        with http_client() as c:
            resp = c.get(url)
            r.metadata = {"status": resp.status_code,
                          "content_type": resp.headers.get("content-type")}
            ctype = resp.headers.get("content-type", "")
            if resp.status_code == 200 and ctype.startswith("text/"):
                text = resp.text
                title = re.search(r"<title[^>]*>(.*?)</title>", text, re.S | re.I)
                body = re.sub(r"<script.*?</script>|<style.*?</style>", " ", text, flags=re.S | re.I)
                body = re.sub(r"<[^>]+>", " ", body)
                body = re.sub(r"\s+", " ", body).strip()
                r.extracted = {"title": title.group(1).strip() if title else None,
                               **features.text_features(body)}
            else:
                r.partial(f"unsupported content type or status: {ctype} / {resp.status_code}")
    except Exception as exc:
        r.state = ProcessingState.failed
        r.error = f"fetch failed: {type(exc).__name__}: {exc}"
    return r


@extractor(MediaType.document, "document.pdf")
def _pdf(store: ProjectStore, src: MediaSource) -> ExtractionResult:
    from pypdf import PdfReader

    r = ExtractionResult()
    path = source_file(store, src)
    reader = PdfReader(str(path))
    pages = []
    for i, page in enumerate(reader.pages):
        try:
            txt = page.extract_text() or ""
        except Exception as exc:  # pragma: no cover - depends on PDF
            txt = ""
            r.partial(f"page {i + 1}: text extraction failed ({exc})")
        n_images = 0
        try:
            n_images = len(page.images)
        except Exception:
            pass
        pages.append({"page": i + 1, "text": txt[:20_000], "chars": len(txt), "images": n_images})
    full = "\n".join(p["text"] for p in pages)
    info = reader.metadata or {}
    r.metadata = {"pages": len(pages), "title": getattr(info, "title", None),
                  "author": getattr(info, "author", None)}
    r.extracted = {"pages": pages, **features.text_features(full)}
    if not full.strip():
        r.partial("no extractable text (scanned PDF?) - OCR is not configured")
    try:
        import pypdfium2 as pdfium

        doc = pdfium.PdfDocument(str(path))
        img = doc[0].render(scale=1.0).to_pil()
        pp = path.parent / "page1.png"
        img.save(pp)
        r.preview = features.make_thumbnail(pp, path.parent / "preview.png")
    except Exception as exc:
        r.partial(f"page preview unavailable ({type(exc).__name__})")
    return r


def _bbox(verts: list[tuple[float, float, float]]) -> dict[str, Any] | None:
    if not verts:
        return None
    xs, ys, zs = zip(*verts)
    size = [max(xs) - min(xs), max(ys) - min(ys), max(zs) - min(zs)]
    return {"min": [min(xs), min(ys), min(zs)], "max": [max(xs), max(ys), max(zs)],
            "size": [round(s, 6) for s in size]}


@extractor(MediaType.model3d, "model3d.inspect")
def _model3d(store: ProjectStore, src: MediaSource) -> ExtractionResult:
    r = ExtractionResult()
    path = source_file(store, src)
    ext = path.suffix.lower()
    if ext == ".obj":
        verts, groups, faces = [], [], 0
        for line in path.read_text(errors="replace").splitlines():
            if line.startswith("v "):
                parts = line.split()
                verts.append(tuple(float(x) for x in parts[1:4]))
            elif line.startswith(("o ", "g ")):
                groups.append(line[2:].strip())
            elif line.startswith("f "):
                faces += 1
        r.extracted = {"format": "obj", "vertices": len(verts), "faces": faces,
                       "hierarchy": [{"name": g} for g in groups], "bbox": _bbox(verts)}
    elif ext in (".gltf", ".glb"):
        data = path.read_bytes()
        if ext == ".glb":
            magic, _ver, _len = struct.unpack_from("<4sII", data, 0)
            if magic != b"glTF":
                raise ValueError("not a GLB file")
            clen, _ctype = struct.unpack_from("<II", data, 12)
            gltf = json.loads(data[20:20 + clen])
        else:
            gltf = json.loads(data)
        nodes = gltf.get("nodes", [])
        mins, maxs = [], []
        for acc in gltf.get("accessors", []):
            if acc.get("type") == "VEC3" and "min" in acc and "max" in acc:
                mins.append(acc["min"])
                maxs.append(acc["max"])
        bbox = None
        if mins:
            mn = [min(m[i] for m in mins) for i in range(3)]
            mx = [max(m[i] for m in maxs) for i in range(3)]
            bbox = {"min": mn, "max": mx, "size": [round(mx[i] - mn[i], 6) for i in range(3)]}
        r.extracted = {
            "format": ext[1:],
            "hierarchy": [{"name": n.get("name"), "children": n.get("children", []),
                           "mesh": n.get("mesh")} for n in nodes],
            "meshes": len(gltf.get("meshes", [])),
            "materials": [m.get("name") for m in gltf.get("materials", [])],
            "bbox": bbox,
            "note": "bbox approximated from accessor bounds (node transforms not applied)",
        }
    elif ext == ".stl":
        data = path.read_bytes()
        verts = []
        if data[:5].lower() == b"solid" and b"facet" in data[:400]:
            for line in data.decode(errors="replace").splitlines():
                s = line.strip()
                if s.startswith("vertex"):
                    verts.append(tuple(float(x) for x in s.split()[1:4]))
        else:
            n = struct.unpack_from("<I", data, 80)[0]
            for i in range(n):
                off = 84 + i * 50 + 12
                for j in range(3):
                    verts.append(struct.unpack_from("<3f", data, off + j * 12))
        r.extracted = {"format": "stl", "triangles": len(verts) // 3, "bbox": _bbox(verts)}
    elif ext == ".blend":
        from .adapters import registry

        adapter = registry().get("blender")
        ok, reason = adapter.check_environment() if adapter else (False, "no blender adapter")
        if not ok:
            r.state = ProcessingState.partial
            r.warnings.append(f".blend stored but not inspected: {reason}")
            return r
        info = adapter.inspect_file(path)  # type: ignore[union-attr]
        r.extracted = {"format": "blend", "hierarchy": info.get("components", []),
                       "measurements": info.get("measurements", {})}
    else:
        r.state = ProcessingState.partial
        r.warnings.append(f"{ext} stored but no parser available")
    bbox = r.extracted.get("bbox")
    if bbox and bbox["size"][2]:
        s = bbox["size"]
        r.extracted["proportions"] = {"width_over_height": round(s[0] / s[2], 4) if s[2] else None,
                                      "depth_over_width": round(s[1] / s[0], 4) if s[0] else None}
    return r


def _python_symbols(text: str) -> list[dict[str, Any]]:
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return []
    out = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            out.append({"name": node.name, "kind": "class" if isinstance(node, ast.ClassDef)
                        else "function", "line": node.lineno})
    return out


@extractor(MediaType.code, "code.inspect")
def _code(store: ProjectStore, src: MediaSource) -> ExtractionResult:
    r = ExtractionResult()
    if src.locator.kind == "url" and not src.locator.path:
        url = src.locator.url or ""
        if not store.get_project().settings.allow_network_fetch:
            r.partial("network fetch disabled; repository URL recorded only")
            return r
        dest = store.source_dir(src.id) / "snapshot"
        proc = subprocess.run(["git", "clone", "--depth", "1", url, str(dest)],
                              capture_output=True, text=True, timeout=300)
        if proc.returncode != 0:
            r.state = ProcessingState.failed
            r.error = f"git clone failed: {proc.stderr.strip()[:400]}"
            return r
        src.locator.path = store.rel(dest)
    root = source_file(store, src)
    files = [root] if root.is_file() else [
        p for p in sorted(root.rglob("*"))
        if p.is_file() and not any(part in SKIP_DIRS for part in p.relative_to(root).parts)]
    langs: dict[str, int] = {}
    symbols: dict[str, list[dict[str, Any]]] = {}
    docs_text = []
    for p in files[:5000]:
        ext = p.suffix.lower() or p.name
        langs[ext] = langs.get(ext, 0) + 1
        rel = p.name if root.is_file() else p.relative_to(root).as_posix()
        if p.suffix == ".py" and p.stat().st_size < 500_000:
            symbols[rel] = _python_symbols(p.read_text(errors="replace"))
        if p.name.lower().startswith(("readme", "contributing", "architecture", "style")):
            docs_text.append(p.read_text(errors="replace")[:50_000])
    r.metadata = {"files": len(files), "languages": langs}
    r.extracted = {
        "files": [p.name if root.is_file() else p.relative_to(root).as_posix()
                  for p in files[:2000]],
        "symbols": symbols,
        **features.text_features("\n".join(docs_text) if docs_text else
                                 (root.read_text(errors="replace") if root.is_file() else "")),
    }
    return r


@extractor(MediaType.audio, "audio.probe")
def _audio(store: ProjectStore, src: MediaSource) -> ExtractionResult:
    r = ExtractionResult()
    probe = _ffprobe(source_file(store, src))
    if probe:
        fmt = probe.get("format", {})
        r.metadata = {"duration": float(fmt.get("duration") or 0),
                      "format": fmt.get("format_name")}
    r.partial("audio content not analysed (no speech/music understanding backend configured)")
    return r


@extractor(MediaType.file, "file.basic")
def _file(store: ProjectStore, src: MediaSource) -> ExtractionResult:
    r = ExtractionResult()
    r.partial("generic file stored; no content extractor for this type")
    return r
