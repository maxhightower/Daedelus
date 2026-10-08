"""HTTP API round trips (the studio uses exactly these endpoints)."""

from __future__ import annotations

import io
import time

from fastapi.testclient import TestClient
from PIL import Image

from daedelus.api import create_app


def _client(tmp_path):
    return TestClient(create_app(tmp_path / "ws", studio_dist=tmp_path / "nodist"))


def _png_bytes(color=(200, 100, 50)):
    buf = io.BytesIO()
    Image.new("RGB", (32, 32), color).save(buf, format="PNG")
    return buf.getvalue()


def test_full_api_flow(tmp_path):
    c = _client(tmp_path)
    h = c.get("/api/health").json()
    assert h["ok"] and {a["name"] for a in h["adapters"]} == {"blender", "layered2d", "code",
        "spreadsheet", "document", "presentation"}
    assert any(p["name"] == "heuristic" and p["available"] for p in h["providers"])
    assert {n["type"] for n in c.get("/api/node-types").json()} >= {"agent", "sources",
                                                                    "validate"}
    pid = c.post("/api/projects", json={"name": "P"}).json()["id"]
    assert [p["id"] for p in c.get("/api/projects").json()] == [pid]

    up = c.post(f"/api/projects/{pid}/sources/upload",
                files=[("files", ("a.png", _png_bytes(), "image/png"))]).json()
    img = up[0]
    assert img["media_type"] == "image" and img["processing"]["state"] == "ready"
    txt = c.post(f"/api/projects/{pid}/sources/text",
                 json={"name": "guide", "text": "tint: #ff8800"}).json()
    assert c.get(f"/api/projects/{pid}/files/{img['preview_path']}").status_code == 200
    assert c.get(f"/api/projects/{pid}/files/../../etc/passwd").status_code in (400, 404)

    art = c.post(f"/api/projects/{pid}/artifacts", json={
        "name": "Pic", "adapter": "layered2d", "template": "layers",
        "params": {"width": 32, "height": 32, "layers": [
            {"id": "bg", "name": "BG", "fill": {"type": "solid", "colors": ["#ffffff"]}}]}}
    ).json()["artifact"]
    assert [x["id"] for x in art["components"]] == ["image", "bg"]

    b1 = c.post(f"/api/projects/{pid}/bindings", json={
        "source_id": img["id"], "role": "reference", "aspects": ["palette"],
        "target": {"scope": "component", "artifact_id": art["id"], "component_id": "bg"}}).json()
    b2 = c.post(f"/api/projects/{pid}/bindings", json={
        "source_id": img["id"], "role": "evaluation", "aspects": ["color"],
        "target": {"scope": "project"}}).json()
    assert len(c.get(f"/api/projects/{pid}/bindings?source_id={img['id']}").json()) == 2
    bad = c.post(f"/api/projects/{pid}/bindings", json={
        "source_id": img["id"], "role": "reference",
        "target": {"scope": "component", "artifact_id": art["id"], "component_id": "zzz"}})
    assert bad.status_code == 400
    patched = c.patch(f"/api/projects/{pid}/bindings/{b1['id']}",
                      json={"role": "inspiration", "strength": 0.9}).json()
    assert patched["role"] == "inspiration" and patched["strength"] == 0.9
    scope = c.get(f"/api/projects/{pid}/bindings/{b2['id']}/scope").json()
    assert scope["affected"][0]["artifact_id"] == art["id"]

    ctx = c.post(f"/api/projects/{pid}/resolve", json={"target": {
        "scope": "component", "artifact_id": art["id"], "component_id": "bg"}}).json()
    assert {e["binding"]["id"] for e in ctx["entries"]} == {b1["id"], b2["id"]}

    wf = {"name": "W", "nodes": [
        {"id": "a", "type": "artifact", "config": {"artifact_id": art["id"]}},
        {"id": "g", "type": "agent", "config": {}}],
        "edges": [{"id": "e", "source": "a", "source_port": "artifact", "target": "g",
                   "target_port": "artifact"}]}
    assert c.post(f"/api/projects/{pid}/workflows/validate", json=wf).json() == []
    w = c.post(f"/api/projects/{pid}/workflows", json=wf).json()
    assert w["version"] == 1
    saved = c.put(f"/api/projects/{pid}/workflows/{w['id']}",
                  json={**w, "description": "v2"}).json()
    assert saved["workflow"]["version"] == 2 and saved["issues"] == []
    exp = c.get(f"/api/projects/{pid}/workflows/{w['id']}/export").json()
    imp = c.post(f"/api/projects/{pid}/workflows/import", json=exp).json()
    assert imp["workflow"]["id"] != w["id"] and imp["workflow"]["nodes"] == exp["workflow"]["nodes"]
    assert [v["version"] for v in c.get(f"/api/projects/{pid}/workflows/{w['id']}/versions")
            .json()] == [1, 2]

    pv = c.post(f"/api/projects/{pid}/workflows/{w['id']}/preview", json={"node_id": "g"}).json()
    assert any(u.get("plan", {}).get("operations") for u in pv["units"])

    ex = c.post(f"/api/projects/{pid}/workflows/{w['id']}/execute", json={"wait": True}).json()
    for _ in range(100):
        ex = c.get(f"/api/projects/{pid}/executions/{ex['id']}").json()
        if ex["status"] not in ("pending", "running"):
            break
        time.sleep(0.05)
    assert ex["status"] == "succeeded", ex
    revs = c.get(f"/api/projects/{pid}/artifacts/{art['id']}/revisions").json()
    assert [r["number"] for r in revs] == [1, 2]
    files = c.get(f"/api/projects/{pid}/revisions/{revs[1]['id']}/files").json()
    assert "image.ora" in files
    rp = c.post(f"/api/projects/{pid}/executions/{ex['id']}/replay").json()
    assert rp["reproducible"], rp
    restored = c.post(f"/api/projects/{pid}/artifacts/{art['id']}/restore",
                      json={"revision_id": revs[0]["id"]}).json()
    assert restored["number"] == 3 and restored["component_states"] == revs[0]["component_states"]

    # editing a text source changes its hash and re-ingests it
    upd = c.put(f"/api/projects/{pid}/sources/{txt['id']}/text", json={"text": "tint: #0000ff"})
    assert upd.json()["content_hash"] != txt["content_hash"]
    assert upd.json()["extracted"]["directives"]["tint"] == "#0000ff"

    msg = c.post(f"/api/projects/{pid}/agent/messages", json={
        "text": "saturation: 0.5", "target": {"scope": "artifact", "artifact_id": art["id"]},
        "aspects": ["color"], "workflow_id": w["id"]}).json()
    assert msg["binding"]["role"] == "instruction"
    assert "saturation=0.5" in msg["reply"]["text"]
    assert len(c.get(f"/api/projects/{pid}/agent/messages").json()) == 2


def test_unknown_project_404(tmp_path):
    c = _client(tmp_path)
    assert c.get("/api/projects/prj_nope/sources").status_code == 404
