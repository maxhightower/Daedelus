"""Heterogeneous source registration and honest extraction."""

from __future__ import annotations

import json
import shutil
import subprocess

import pytest
from PIL import Image, ImageDraw

from daedelus import ingest
from daedelus.models import MediaType, ProcessingState


def _img(path, size=(200, 300), poly=((80, 20), (120, 20), (110, 280), (90, 280))):
    im = Image.new("RGB", size, (240, 240, 240))
    ImageDraw.Draw(im).polygon(poly, fill=(40, 30, 20))
    im.save(path)
    return path


def test_text_directives(store):
    s = ingest.register_text(store, "guide", "# Guide\npalette: #112233, #445566\nroughness: 0.4\n")
    assert s.media_type == MediaType.text
    assert s.processing.state == ProcessingState.ready
    assert s.extracted["directives"]["roughness"] == "0.4"
    assert s.extracted["colors"] == ["#112233", "#445566"]
    assert s.content_hash and s.locator.path


def test_image_measurements_and_preview(store, tmp_path):
    s = ingest.register_file(store, _img(tmp_path / "leg.png"))
    assert s.media_type == MediaType.image
    sil = s.extracted["silhouette"]
    assert sil["found"] and sil["taper"] > 0.3  # wider at the top than at the bottom
    assert sil["aspect"] < 1
    assert store.abs(s.preview_path).exists()
    assert s.extracted["understanding"]["kind"] == "measured"


def test_ingestion_assigns_no_role(store, tmp_path):
    s = ingest.register_file(store, _img(tmp_path / "a.png"))
    assert store.list_bindings(s.id) == []
    assert "role" not in s.model_dump()


def test_pdf_text_and_pages(store, tmp_path):
    reportlab = pytest.importorskip("reportlab.pdfgen.canvas")
    pdf = tmp_path / "spec.pdf"
    c = reportlab.Canvas(str(pdf))
    c.drawString(72, 720, "width: 1.5")
    c.showPage()
    c.drawString(72, 720, "height: 0.9")
    c.save()
    s = ingest.register_file(store, pdf)
    assert s.media_type == MediaType.document
    assert s.metadata["pages"] == 2
    assert s.extracted["directives"]["width"] == "1.5"


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg not installed")
def test_video_frames(store, tmp_path):
    vid = tmp_path / "clip.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
                    "color=c=red:s=64x48:d=2", "-pix_fmt", "yuv420p", str(vid)], check=True)
    s = ingest.register_file(store, vid)
    assert s.media_type == MediaType.video
    assert s.extracted["frames"]
    assert s.extracted["dominant"].startswith("#f") or s.extracted["dominant"].startswith("#e")
    # no transcript backend: must say so instead of inventing one
    assert s.processing.state == ProcessingState.partial
    assert any("transcript" in w for w in s.processing.warnings)


def test_video_url_unreachable_is_reported_not_invented(store, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("network down")
    monkeypatch.setattr(ingest, "http_client", boom)
    s = ingest.register_url(store, "https://www.youtube.com/watch?v=abcdefghijk")
    assert s.media_type == MediaType.video_url
    assert s.metadata["video_id"] == "abcdefghijk"
    assert s.processing.state == ProcessingState.partial
    assert "title" not in s.extracted
    assert s.extracted["understanding"]["kind"] == "metadata_only"
    assert any("network down" in w for w in s.processing.warnings)


def test_network_disabled_by_project_setting(store):
    p = store.get_project()
    p.settings.allow_network_fetch = False
    store.save_project(p)
    s = ingest.register_url(store, "https://youtu.be/abcdefghijk")
    assert s.processing.state == ProcessingState.partial
    assert any("disabled" in w for w in s.processing.warnings)


def test_obj_and_gltf_models(store, tmp_path):
    obj = tmp_path / "box.obj"
    obj.write_text("o box\nv 0 0 0\nv 2 0 0\nv 2 1 0\nv 0 1 1\nf 1 2 3\n")
    s = ingest.register_file(store, obj)
    assert s.media_type == MediaType.model3d
    assert s.extracted["bbox"]["size"] == [2.0, 1.0, 1.0]
    assert s.extracted["proportions"]["width_over_height"] == 2.0
    gltf = tmp_path / "m.gltf"
    gltf.write_text(json.dumps({"nodes": [{"name": "a", "mesh": 0}], "meshes": [{}],
                                "accessors": [{"type": "VEC3", "min": [0, 0, 0],
                                               "max": [1, 2, 3]}]}))
    s2 = ingest.register_file(store, gltf)
    assert s2.extracted["hierarchy"][0]["name"] == "a"
    assert s2.extracted["bbox"]["size"] == [1, 2, 3]


def test_code_repository_snapshot(store, tmp_path):
    repo = tmp_path / "repo"
    (repo / "pkg").mkdir(parents=True)
    (repo / "pkg" / "mod.py").write_text("class A:\n    pass\n\ndef f():\n    return 1\n")
    (repo / "README.md").write_text("# Repo\nindent: 4\n")
    s = ingest.register_path(store, repo)
    assert s.media_type == MediaType.code
    assert {x["name"] for x in s.extracted["symbols"]["pkg/mod.py"]} == {"A", "f"}
    assert s.extracted["directives"]["indent"] == "4"


def test_unknown_file_is_stored_but_flagged(store):
    s = ingest.register_bytes(store, b"\x00\x01", "blob.xyz")
    assert s.media_type == MediaType.file
    assert s.processing.state == ProcessingState.partial


def test_extractor_crash_reports_failure(store, tmp_path):
    bad = tmp_path / "broken.png"
    bad.write_bytes(b"not an image")
    s = ingest.register_file(store, bad)
    assert s.processing.state == ProcessingState.failed
    assert s.processing.error
    assert s.extracted == {}
