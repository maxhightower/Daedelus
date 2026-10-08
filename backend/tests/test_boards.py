"""V1 spatial boards: persistence, migration, derived connections, in-place edits, office formats."""

from __future__ import annotations

import json
import zipfile

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from daedelus import boards as B
from daedelus import ingest
from daedelus.api import create_app
from daedelus.artifacts import create_artifact
from daedelus.editing import EditRejected, apply_manual_edit, ensure_glb_with_ids
from daedelus.models import (
    Constraint,
    MediaType,
    PlannedOperation,
    SourceBinding,
    TargetSelector,
    Workflow,
    WorkflowEdge,
    WorkflowNode,
)

LAYERS = [{"id": "fg", "name": "FG", "fill": {"type": "solid", "colors": ["#335533"]},
           "shape": {"type": "rect", "box": [0, 0.6, 1, 0.4]}},
          {"id": "sky", "name": "Sky", "fill": {"type": "solid", "colors": ["#88aadd"]}}]


@pytest.fixture()
def proj(store, tmp_path):
    img = create_artifact(store, name="Pic", adapter="layered2d", template="layers",
                          params={"width": 32, "height": 24, "root_id": "pic", "layers": LAYERS})[0]
    code = create_artifact(store, name="Repo", adapter="code", template="files",
                           params={"files": {"a.py": "X = 1\n"}})[0]
    p = tmp_path / "ref.png"
    Image.new("RGB", (16, 16), (200, 40, 40)).save(p)
    src = ingest.register_file(store, p)
    bind = store.save_binding(SourceBinding(
        source_id=src.id, role="reference", aspects=["palette"],
        target=TargetSelector(scope="component", artifact_id=img.id, component_id="sky")))
    wf = store.save_workflow(Workflow(name="W", nodes=[
        WorkflowNode(id="a", type="artifact", config={"artifact_id": img.id},
                     position={"x": 0, "y": 0}),
        WorkflowNode(id="g", type="agent", position={"x": 300, "y": 0})],
        edges=[WorkflowEdge(id="e1", source="a", source_port="artifact", target="g",
                            target_port="artifact")]))
    return {"img": img, "code": code, "src": src, "bind": bind, "wf": wf}


def test_migration_creates_one_default_board_idempotently(store, proj):
    boards = B.ensure_boards(store)
    assert len(boards) == 1
    b = boards[0]
    kinds = sorted(i.resource_ref.kind for i in b.items)
    assert kinds.count("artifact") == 2 and kinds.count("source") == 1
    assert kinds.count("workflow_node") == 2 and kinds.count("workflow") == 1
    assert {i.presentation_state.get("title") for i in b.items if i.item_type == "frame"} >= \
        {"Artifacts", "References"}
    # every item of a framed resource sits inside its frame
    frames = {i.id for i in b.items if i.item_type in ("frame", "workflow")}
    assert all(i.group_id in frames for i in b.items if i.item_type not in ("frame", "workflow"))
    # repeated migration does not duplicate boards, items or native data
    n_rev = len(store.list_revisions(proj["img"].id))
    for _ in range(3):
        assert [x.id for x in B.ensure_boards(store)] == [b.id]
    assert len(B.get_board(store, b.id).items) == len(b.items)
    assert len(store.list_revisions(proj["img"].id)) == n_rev
    assert len(store.list_bindings()) == 1 and len(store.list_sources()) == 1


def test_migration_does_not_recreate_deleted_boards_contents(store, proj):
    b = B.ensure_boards(store)[0]
    b.items = []
    B.save_board(store, b)
    assert B.ensure_boards(store)[0].items == []  # migration ran once; user edits are kept


def test_derived_reference_and_execution_connections(store, proj):
    b = B.ensure_boards(store)[0]
    view = B.board_view(store, b)
    refs = [c for c in view["connections"] if c["connection_type"] == "reference"]
    assert len(refs) == 1
    r = refs[0]
    assert r["derived"] and r["domain_ref"] == {"kind": "binding", "id": proj["bind"].id,
                                                 "workflow_id": None}
    assert r["target_anchor"]["component_id"] == "sky"
    assert r["target_anchor"]["handle"] == "comp-in:sky"
    exe = [c for c in view["connections"] if c["connection_type"] == "execution"]
    assert len(exe) == 1 and exe[0]["domain_ref"]["id"] == "e1"
    # a second view of the same artifact gets its own reference edge
    B.place_resource(store, b, B.ResourceRef(kind="artifact", id=proj["img"].id), 2000, 0)
    B.save_board(store, b)
    view = B.board_view(store, B.get_board(store, b.id))
    assert len([c for c in view["connections"] if c["connection_type"] == "reference"]) == 2
    # deleting the binding removes the derived edge: the board never holds the truth
    store.delete_binding(proj["bind"].id)
    view = B.board_view(store, B.get_board(store, b.id))
    assert not [c for c in view["connections"] if c["connection_type"] == "reference"]


def test_workflow_node_item_mapping(store, proj):
    b = B.ensure_boards(store)[0]
    ops = {i.resource_ref.node_id: i for i in b.items if i.resource_ref.kind == "workflow_node"}
    assert set(ops) == {"a", "g"}
    assert all(i.resource_ref.workflow_id == proj["wf"].id for i in ops.values())
    # removing a workflow node from the workflow leaves a visibly missing item, not a ghost edge
    wf = proj["wf"].model_copy(deep=True)
    wf.nodes = [n for n in wf.nodes if n.id != "g"]
    wf.edges = []
    store.save_workflow(wf)
    view = B.board_view(store, b)
    assert ops["g"].id in view["missing_items"]
    assert not [c for c in view["connections"] if c["connection_type"] == "execution"]


def test_board_save_rules(store, proj):
    b = B.ensure_boards(store)[0]
    items = [i for i in b.items if i.item_type == "artifact_view"]
    b.connections.append(B.CanvasConnection(connection_type="dependency",
                                            source_item_id=items[1].id,
                                            target_item_id=items[0].id,
                                            presentation_state={"label": "imports"}))
    saved = B.save_board(store, b)
    rev = saved.revision
    assert [c.connection_type for c in saved.connections] == ["dependency"]
    with pytest.raises(ValueError):
        b2 = B.get_board(store, b.id)
        b2.connections.append(B.CanvasConnection(connection_type="reference",
                                                 source_item_id=items[0].id,
                                                 target_item_id=items[1].id))
        B.save_board(store, b2)
    with pytest.raises(B.BoardConflict):
        B.save_board(store, B.get_board(store, b.id), expected_revision=rev - 1)
    # removing an endpoint drops the dangling stored connection
    b3 = B.get_board(store, b.id)
    b3.items = [i for i in b3.items if i.id != items[1].id]
    assert B.save_board(store, b3).connections == []


def test_layout_survives_reopen(store, proj, workspace):
    b = B.ensure_boards(store)[0]
    item = b.items[3]
    item.position = B.Vec2(x=1234.5, y=-77)
    item.size = B.Size(width=500, height=410)
    item.presentation_state = {"camera": {"position": [1, 2, 3], "target": [0, 0, 0]}}
    b.viewport = B.Viewport(x=-50, y=20, zoom=0.42)
    b.saved_views.append(B.SavedView(name="overview", viewport=b.viewport))
    B.save_board(store, b)
    reopened = type(store)(store.root)
    b2 = B.get_board(reopened, b.id)
    it2 = next(i for i in b2.items if i.id == item.id)
    assert it2.position.x == 1234.5 and it2.size.height == 410
    assert it2.presentation_state["camera"]["position"] == [1, 2, 3]
    assert b2.viewport.zoom == 0.42 and b2.saved_views[0].name == "overview"


def test_manual_edit_2d_creates_scoped_revision(store, proj):
    img = proj["img"]
    head = store.get_artifact(img.id).head_revision_id
    rev = apply_manual_edit(store, img.id, [PlannedOperation(
        op="set_layer_props", component_id="sky", params={"opacity": 0.4})],
        message="Sky opacity", base_revision_id=head)
    assert rev.origin == "manual" and rev.number == 2
    assert rev.changed_components == ["sky"]
    assert rev.validation.passed
    # stale views are refused instead of overwriting a newer revision
    with pytest.raises(EditRejected, match="changed since"):
        apply_manual_edit(store, img.id, [PlannedOperation(
            op="set_layer_props", component_id="sky", params={"opacity": 0.9})],
            base_revision_id=head)


def test_manual_edit_respects_hard_constraints(store, proj, tmp_path):
    img = proj["img"]
    store.save_binding(SourceBinding(
        source_id=proj["src"].id, role="constraint", constraint="hard",
        target=TargetSelector(scope="component", artifact_id=img.id, component_id="fg"),
        constraints=[Constraint(property="brightness", op="preserve")]))
    native = store.abs(img.native_dir) / img.entry
    raw = native.read_bytes()
    with pytest.raises(EditRejected, match="brightness preserve"):
        apply_manual_edit(store, img.id, [PlannedOperation(
            op="color_grade", component_id="fg", params={"brightness": 1.8})])
    assert native.read_bytes() == raw
    assert len(store.list_revisions(img.id)) == 1


def test_manual_edit_code_diff_and_schema_check(store, proj):
    code = proj["code"]
    rev = apply_manual_edit(store, code.id, [PlannedOperation(
        op="write_file", component_id="file:a.py",
        params={"path": "a.py", "content": "X = 2\n"})], message="Edit a.py")
    assert "-X = 1" in rev.diff and "+X = 2" in rev.diff and rev.vcs_commit
    with pytest.raises(EditRejected, match="invalid operations"):
        apply_manual_edit(store, code.id, [PlannedOperation(op="explode", component_id="repo")])


def test_office_formats_ingest(store, tmp_path):
    csvp = tmp_path / "t.csv"
    csvp.write_text("name,width\ntable,1.2\nchair,0.5\n")
    s = ingest.register_file(store, csvp)
    assert s.media_type == MediaType.spreadsheet
    assert s.extracted["sheets"][0]["rows"][1] == ["table", "1.2"]

    xlsx = tmp_path / "w.xlsx"
    with zipfile.ZipFile(xlsx, "w") as z:
        z.writestr("xl/workbook.xml", '<workbook xmlns="http://schemas.openxmlformats.org/'
                   'spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/'
                   'officeDocument/2006/relationships"><sheets><sheet name="Dims" sheetId="1" '
                   'r:id="rId1"/></sheets></workbook>')
        z.writestr("xl/_rels/workbook.xml.rels", '<Relationships xmlns="http://schemas.'
                   'openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" '
                   'Target="worksheets/sheet1.xml" Type="x"/></Relationships>')
        z.writestr("xl/sharedStrings.xml", '<sst xmlns="http://schemas.openxmlformats.org/'
                   'spreadsheetml/2006/main"><si><t>height</t></si></sst>')
        z.writestr("xl/worksheets/sheet1.xml", '<worksheet xmlns="http://schemas.openxmlformats'
                   '.org/spreadsheetml/2006/main"><sheetData><row r="1"><c r="A1" t="s"><v>0</v>'
                   '</c><c r="B1"><v>0.75</v></c></row></sheetData></worksheet>')
    s = ingest.register_file(store, xlsx)
    assert s.extracted["sheet_names"] == ["Dims"]
    assert s.extracted["sheets"][0]["rows"] == [["height", "0.75"]]
    assert any("view-only" in w for w in s.processing.warnings)

    docx = tmp_path / "d.docx"
    with zipfile.ZipFile(docx, "w") as z:
        z.writestr("word/document.xml", '<w:document xmlns:w="http://schemas.openxmlformats.org/'
                   'wordprocessingml/2006/main"><w:body><w:p><w:pPr><w:pStyle w:val="Heading1"/>'
                   '</w:pPr><w:r><w:t>Spec</w:t></w:r></w:p><w:p><w:r><w:t>roughness: 0.3</w:t>'
                   '</w:r></w:p></w:body></w:document>')
    s = ingest.register_file(store, docx)
    assert s.media_type == MediaType.document
    assert s.extracted["headings"] == ["Spec"] and s.extracted["directives"]["roughness"] == "0.3"

    pptx = tmp_path / "p.pptx"
    with zipfile.ZipFile(pptx, "w") as z:
        for i, t in enumerate(["Intro", "Details"], start=1):
            z.writestr(f"ppt/slides/slide{i}.xml", '<p:sld xmlns:p="p" xmlns:a="http://schemas.'
                       f'openxmlformats.org/drawingml/2006/main"><a:t>{t}</a:t></p:sld>')
    s = ingest.register_file(store, pptx)
    assert s.media_type == MediaType.presentation
    assert [x["title"] for x in s.extracted["slides"]] == ["Intro", "Details"]


def test_board_api_round_trip(tmp_path):
    c = TestClient(create_app(tmp_path / "ws", studio_dist=tmp_path / "nodist"))
    pid = c.post("/api/projects", json={"name": "P"}).json()["id"]
    boards = c.get(f"/api/projects/{pid}/boards").json()
    assert len(boards) == 1
    bid = boards[0]["id"]
    b = c.get(f"/api/projects/{pid}/boards/{bid}").json()
    note = {"item_type": "note", "resource_ref": {"kind": "note"},
            "presentation_state": {"text": "Research"}, "position": {"x": 5, "y": 6}}
    b["items"].append(note)
    saved = c.put(f"/api/projects/{pid}/boards/{bid}",
                  json={"board": b, "expected_revision": b["revision"]}).json()
    assert saved["items"][-1]["presentation_state"]["text"] == "Research"
    stale = c.put(f"/api/projects/{pid}/boards/{bid}",
                  json={"board": b, "expected_revision": b["revision"]})
    assert stale.status_code == 409
    nb = c.post(f"/api/projects/{pid}/boards", json={"name": "Second"}).json()
    assert c.patch(f"/api/projects/{pid}/boards/{nb['id']}", json={"name": "Renamed"}).json()[
        "name"] == "Renamed"
    assert len(c.get(f"/api/projects/{pid}/boards").json()) == 2
    assert c.delete(f"/api/projects/{pid}/boards/{nb['id']}").status_code == 200
    assert c.delete(f"/api/projects/{pid}/boards/{bid}").status_code == 400
    src = c.post(f"/api/projects/{pid}/sources/text", json={"name": "n", "text": "x"}).json()
    placed = c.post(f"/api/projects/{pid}/boards/{bid}/place",
                    json={"resource_ref": {"kind": "source", "id": src["id"]}, "x": 10,
                          "y": 20}).json()
    assert placed["item"]["item_type"] == "source"
    assert c.post(f"/api/projects/{pid}/boards/{bid}/place",
                  json={"resource_ref": {"kind": "source", "id": "src_nope"}}).status_code == 404


@pytest.mark.blender
def test_blender_manual_edit_and_component_ids(store, tmp_path):
    comps = [{"id": "t", "primitive": "empty"},
             {"id": "top", "primitive": "cube", "size": [1, 1, 0.1], "location": [0, 0, 1],
              "parent": "t"},
             {"id": "leg", "primitive": "cube", "size": [0.1, 0.1, 1], "location": [0, 0, 0.5],
              "parent": "t"}]
    art, rev1 = create_artifact(store, name="T", adapter="blender", template="components",
                                params={"components": comps})
    assert "glb_ids" in rev1.previews
    glb = store.abs(ensure_glb_with_ids(store, rev1.id)).read_bytes()
    n = int.from_bytes(glb[12:16], "little")
    nodes = json.loads(glb[20:20 + n])["nodes"]
    assert {nd["extras"]["daedelus_id"] for nd in nodes} == {"t", "top", "leg"}
    rev = apply_manual_edit(store, art.id, [PlannedOperation(
        op="set_material", component_id="leg", params={"base_color": "#aa2200"})])
    assert rev.changed_components == ["leg"] and rev.origin == "manual"
    # legacy revision without id-carrying GLB is re-exported on demand, snapshot untouched
    rev1 = store.get_revision(rev1.id)
    rev1.previews.pop("glb_ids")
    store.save_revision(rev1)
    snap = store.abs(rev1.snapshot_dir) / "model.blend"
    before = snap.read_bytes()
    path = ensure_glb_with_ids(store, rev1.id)
    assert path.endswith("model_ids.glb") and snap.read_bytes() == before
