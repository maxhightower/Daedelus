"""V2.1 connector behaviour against synthetic fixtures (mock transport; NO live account).

Covers the Google Drive publication race (detected after the write, never reported as a clean
success), backup pinning, Graph preconditions and limits, permission and rate-limit failures,
and the discovery/creation/deletion/revision calls the opt-in live harness uses. These are
hand-written response shapes from the providers' documentation, not recordings.
"""

from __future__ import annotations

import io

import httpx
import pytest

from daedelus import connectors as cn
from daedelus.store import Workspace


def _xlsx(v=42) -> bytes:
    import openpyxl
    wb = openpyxl.Workbook()
    wb.active["A1"] = v
    b = io.BytesIO()
    wb.save(b)
    return b.getvalue()


class Drive:
    """Drive v3 with revision history. ``race`` injects a foreign write between our version
    check and our upload (the window Drive cannot close)."""

    def __init__(self, race=False, status=None):
        self.version, self.revs, self.pinned = 7, ["r1", "r2"], set()
        self.content, self.race, self.status = _xlsx(), race, status
        self.calls: list[httpx.Request] = []

    def meta(self):
        return {"id": "XL1", "name": "Data.xlsx", "mimeType": cn.OOXML["xlsx"],
                "version": str(self.version), "size": "4096", "headRevisionId": self.revs[-1]}

    def __call__(self, req):
        self.calls.append(req)
        if self.status:
            return httpx.Response(self.status, json={"error": {"code": self.status}})
        p, m = req.url.path, req.method
        if p == "/drive/v3/files/XL1" and m == "GET" and req.url.params.get("alt") == "media":
            return httpx.Response(200, content=self.content)
        if p == "/drive/v3/files/XL1" and m == "GET":
            return httpx.Response(200, json=self.meta())
        if p.startswith("/drive/v3/files/XL1/revisions/") and m == "PATCH":
            self.pinned.add(p.rsplit("/", 1)[1])
            return httpx.Response(200, json={"id": p.rsplit("/", 1)[1], "keepForever": True})
        if p == "/drive/v3/files/XL1/revisions" and m == "GET":
            return httpx.Response(200, json={"revisions": [{"id": r} for r in self.revs]})
        if p == "/upload/drive/v3/files/XL1" and m == "PATCH":
            self.keep = req.url.params.get("keepRevisionForever")
            if self.race:  # someone else's upload lands first
                self.revs.append("r_foreign")
                self.version += 1
            self.revs.append(f"r_ours{len(self.revs)}")
            self.version += 1
            self.content = req.content
            return httpx.Response(200, json=self.meta())
        if p == "/drive/v3/files" and m == "GET":
            return httpx.Response(200, json={"files": [self.meta()]})
        if p == "/drive/v3/files" and m == "POST":
            return httpx.Response(200, json={**self.meta(), "id": "XL1"})
        if p == "/drive/v3/files/XL1" and m == "DELETE":
            return httpx.Response(204)
        return httpx.Response(404)


@pytest.fixture()
def st(tmp_path):
    ws = Workspace(tmp_path / "ws")
    _, store = ws.create_project("conn", "")
    yield store
    ws.close()


def test_drive_publish_pins_backup_and_verifies_no_concurrent_write(st):
    d = Drive()
    gd = cn.GoogleDriveConnector(httpx.MockTransport(d), token="T")
    art, _ = cn.import_remote(st, gd, "XL1")
    new = cn.publish_remote(st, art.id, gd)
    assert new.metadata["verification"].startswith("verified")
    assert new.metadata["backup_revision"] == "r2" and "r2" in d.pinned
    assert d.keep == "true"  # our revision is kept as well
    assert "conflict" not in st.get_artifact(art.id).metadata["remote"]


def test_drive_race_window_is_detected_and_reported_not_hidden(st):
    """Failure injection 11 (stale external version, the race variant): a foreign write lands
    between our check and our upload. Drive cannot refuse it; Daedelus detects it afterwards,
    keeps the foreign revision and reports a conflict instead of success."""
    d = Drive()
    gd = cn.GoogleDriveConnector(httpx.MockTransport(d), token="T")
    art, _ = cn.import_remote(st, gd, "XL1")
    d.race = True
    with pytest.raises(cn.ConnectorConflict) as ei:
        cn.publish_remote(st, art.id, gd)
    assert ei.value.written and ei.value.details["concurrent"] == ["r_foreign"]
    assert {"r2", "r_foreign"} <= d.pinned  # nothing lost: both kept forever
    link = st.get_artifact(art.id).metadata["remote"]
    assert link["conflict"]["concurrent"] == ["r_foreign"]  # recorded for the user


def test_drive_stale_version_is_refused_before_any_write(st):
    d = Drive()
    gd = cn.GoogleDriveConnector(httpx.MockTransport(d), token="T")
    art, _ = cn.import_remote(st, gd, "XL1")
    d.version = 99  # edited remotely after import
    with pytest.raises(cn.ConnectorConflict) as ei:
        cn.publish_remote(st, art.id, gd)
    assert not ei.value.written
    assert not any(r.method == "PATCH" for r in d.calls)


@pytest.mark.parametrize("status,exc", [(401, cn.ConnectorBlocked), (403, cn.ConnectorBlocked),
                                        (429, cn.ConnectorError)])
def test_permission_and_rate_limit_failures(status, exc, tmp_path):
    gd = cn.GoogleDriveConnector(httpx.MockTransport(Drive(status=status)), token="T")
    with pytest.raises(exc):
        gd.list("FOLDER1")


def test_drive_discovery_creation_deletion_and_revisions(tmp_path):
    d = Drive()
    gd = cn.GoogleDriveConnector(httpx.MockTransport(d), token="T")
    files = gd.list("FOLDER1")
    q = next(r for r in d.calls if r.url.path == "/drive/v3/files").url.params["q"]
    assert q == "'FOLDER1' in parents and trashed = false" and files[0].remote_id == "XL1"
    p = tmp_path / "t.xlsx"
    p.write_bytes(_xlsx(1))
    assert gd.create("FOLDER1", "daedelus-live-test.xlsx", p).remote_id == "XL1"
    assert [r["id"] for r in gd.revisions("XL1")][-1].startswith("r")
    gd.delete("XL1")
    for bad in ("x'; drop", "a/b", "..", " lead"):
        with pytest.raises(cn.ConnectorError):
            gd.create("FOLDER1", bad, p) if "'" not in bad else gd.list(bad)


class Graph:
    def __init__(self):
        self.etag = '"{A}",1'
        self.calls = []

    def __call__(self, req):
        self.calls.append(req)
        u = str(req.url)
        if req.method == "PUT" and ":/" in u and u.endswith(":/content?%40microsoft.graph."
                                                            "conflictBehavior=fail"):
            return httpx.Response(409, json={"error": {"code": "nameAlreadyExists"}})
        if req.method == "GET" and u.endswith("/children?%24select=id%2Cname%2CeTag%2Csize%2C"
                                              "file%2ClastModifiedDateTime%2CwebUrl"):
            return httpx.Response(200, json={"value": [
                {"id": "I1", "name": "a.xlsx", "eTag": self.etag, "file": {"mimeType": "x"}},
                {"id": "F2", "name": "sub", "folder": {}}]})
        if req.method == "GET" and u.endswith("/versions"):
            return httpx.Response(200, json={"value": [{"id": "1.0"}, {"id": "2.0"}]})
        if req.method == "DELETE":
            return httpx.Response(204)
        return httpx.Response(404)


def test_graph_listing_skips_folders_and_create_refuses_overwrite(tmp_path):
    g = cn.GraphConnector(httpx.MockTransport(Graph()), token="T")
    assert [f.remote_id for f in g.list("ROOT")] == ["I1"]
    p = tmp_path / "x.xlsx"
    p.write_bytes(_xlsx())
    with pytest.raises(cn.ConnectorConflict, match="already exists"):
        g.create("ROOT", "x.xlsx", p)
    assert [v["id"] for v in g.revisions("I1")] == ["1.0", "2.0"]
    g.delete("I1")


def test_graph_refuses_files_beyond_simple_upload_limit(tmp_path, monkeypatch):
    g = cn.GraphConnector(httpx.MockTransport(Graph()), token="T")
    p = tmp_path / "big.xlsx"
    p.write_bytes(b"x")
    monkeypatch.setattr(cn, "GRAPH_SIMPLE_UPLOAD_MAX", 0)
    with pytest.raises(cn.ConnectorError, match="250 MB"):
        g.upload("I1", p, '"{A}",1')


class DriveFS:
    """Stateful in-memory Drive for exercising the live harness logic offline."""

    def __init__(self):
        self.files: dict[str, dict] = {}
        self.n = 0

    def _meta(self, fid):
        f = self.files[fid]
        return {"id": fid, "name": f["name"], "mimeType": cn.OOXML["xlsx"],
                "version": str(f["version"]), "headRevisionId": f["revs"][-1]}

    def __call__(self, req):
        import re
        p, m = req.url.path, req.method
        if p == "/drive/v3/files" and m == "GET":
            return httpx.Response(200, json={"files": [self._meta(i) for i in self.files]})
        if p == "/drive/v3/files" and m == "POST":
            import json as _j
            self.n += 1
            fid = f"F{self.n}"
            self.files[fid] = {"name": _j.loads(req.content)["name"], "version": 1,
                               "revs": ["r0"], "content": b""}
            return httpx.Response(200, json=self._meta(fid))
        mm = re.match(r"^/(upload/)?drive/v3/files/([^/]+)(/revisions(/[^/]+)?)?$", p)
        if not mm or mm.group(2) not in self.files:
            return httpx.Response(404)
        fid, f = mm.group(2), self.files[mm.group(2)]
        if mm.group(1) and m == "PATCH":
            f["content"], f["version"] = req.content, f["version"] + 1
            f["revs"].append(f"r{len(f['revs'])}")
            return httpx.Response(200, json=self._meta(fid))
        if mm.group(3) and m == "PATCH":
            return httpx.Response(200, json={"keepForever": True})
        if mm.group(3):
            return httpx.Response(200, json={"revisions": [{"id": r} for r in f["revs"]]})
        if m == "DELETE":
            del self.files[fid]
            return httpx.Response(204)
        if req.url.params.get("alt") == "media":
            return httpx.Response(200, content=f["content"])
        return httpx.Response(200, json=self._meta(fid))


def test_live_harness_logic_against_a_stateful_fake(tmp_path, monkeypatch):
    """The opt-in live harness itself (not a live result): every step and the cleanup."""
    from daedelus import connectors_live
    fs = DriveFS()
    monkeypatch.setattr(cn, "registry", lambda transport=None: {
        "google": cn.GoogleDriveConnector(httpx.MockTransport(fs), token="T")})
    monkeypatch.setenv("DAEDELUS_LIVE_CONNECTORS", "1")
    monkeypatch.setenv("DAEDELUS_GOOGLE_TOKEN", "T")
    monkeypatch.setenv("DAEDELUS_LIVE_GOOGLE_FOLDER", "FOLDER1")
    rep = connectors_live.run("google", tmp_path / "out")
    assert rep["status"] == "live", rep["checks"]
    assert rep["cleanup"]["deleted_and_verified"] == rep["created"] and not fs.files
    monkeypatch.delenv("DAEDELUS_LIVE_CONNECTORS")
    assert connectors_live.run("google", tmp_path / "o2")["status"] == "blocked"
