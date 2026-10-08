"""Adapters are independently testable: native formats, idempotency, validation."""

from __future__ import annotations

import zipfile

import pytest

from daedelus.adapters import get_adapter, registry
from daedelus.adapters.base import check_schema
from daedelus.adapters.layered2d import OraDocument
from daedelus.models import PlannedOperation as Op

LAYERS = [{"id": "top", "name": "Top", "fill": {"type": "solid", "colors": ["#ff0000"]},
           "shape": {"type": "ellipse", "box": [0.25, 0.25, 0.5, 0.5]}},
          {"id": "base", "name": "Base", "fill": {"type": "gradient",
                                                  "colors": ["#000000", "#ffffff"]}}]


def test_registry_declares_capabilities():
    for a in registry().values():
        info = a.info()
        assert info.operations and info.artifact_types and info.native_formats
        assert info.validation and info.measurements
        for op in info.operations:
            assert op.params_schema.get("type") == "object"


def test_schema_checker():
    s = {"type": "object", "required": ["a"], "additionalProperties": False,
         "properties": {"a": {"type": "number", "maximum": 1}, "c": {"type": "string",
                                                                    "enum": ["x"]}}}
    assert check_schema({"a": 0.5}, s) == []
    assert len(check_schema({"a": 2, "b": 1, "c": "y"}, s)) == 3
    assert check_schema({}, s) == ["params.a: required"]


def test_layered2d_openraster_roundtrip(tmp_path):
    a = get_adapter("layered2d")
    entry = a.create(tmp_path, "layers", {"width": 40, "height": 30, "layers": LAYERS})
    with zipfile.ZipFile(tmp_path / entry) as z:
        first = z.infolist()[0]
        assert first.filename == "mimetype" and first.compress_type == zipfile.ZIP_STORED
        assert {"stack.xml", "mergedimage.png", "data/top.png", "data/base.png"} <= \
            set(z.namelist())
    insp = a.inspect(tmp_path, entry)
    assert [c.id for c in insp.components] == ["image", "top", "base"]
    assert a.validate(tmp_path, entry, ["file_reopens"], {}).passed


def test_layered2d_ops_are_scoped_and_idempotent(tmp_path):
    a = get_adapter("layered2d")
    entry = a.create(tmp_path, "layers", {"width": 40, "height": 30, "layers": LAYERS})
    s0 = a.inspect(tmp_path, entry).states
    ops = [Op(op="color_grade", component_id="top", params={"tint": "#0000ff",
                                                            "tint_strength": 0.5,
                                                            "brightness": 1.2})]
    assert a.apply(tmp_path, entry, ops, {}).ok
    s1 = a.inspect(tmp_path, entry).states
    assert s1["top"] != s0["top"] and s1["base"] == s0["base"]
    assert a.apply(tmp_path, entry, ops, {}).ok  # declarative: re-applying converges
    assert a.inspect(tmp_path, entry).states == s1
    # alpha mask of shaped layers survives a gradient fill
    assert a.apply(tmp_path, entry, [Op(op="fill_gradient", component_id="top",
                                        params={"colors": ["#00ff00", "#008800"]})], {}).ok
    doc = OraDocument.load(tmp_path / entry)
    top = doc.layer("top").base
    assert top.getpixel((0, 0))[3] == 0 and top.getpixel((20, 15))[3] == 255


def test_layered2d_failure_reports_op(tmp_path):
    a = get_adapter("layered2d")
    entry = a.create(tmp_path, "blank", {"width": 8, "height": 8})
    res = a.apply(tmp_path, entry, [Op(op="set_layer_props", component_id="nope",
                                       params={"opacity": 0.5})], {})
    assert not res.ok and "layer not found" in res.error


def test_code_adapter_commits_diffs_and_symbols(tmp_path):
    a = get_adapter("code")
    root = tmp_path / "repo"
    a.create(root, "files", {"files": {"pkg/m.py": "def f():\n    return 1\n",
                                       "data.json": "{\"a\": 1}\n"}})
    insp = a.inspect(root, ".")
    ids = {c.id for c in insp.components}
    assert {"repo", "file:pkg/m.py", "file:pkg/m.py::f", "file:data.json"} <= ids
    before = tmp_path / "before"
    import shutil
    shutil.copytree(root, before, ignore=shutil.ignore_patterns(".git"))
    res = a.apply(root, ".", [Op(op="replace_text", component_id="file:pkg/m.py",
                                 params={"old": "return 1", "new": "return 2"}),
                              Op(op="update_json", component_id="file:data.json",
                                 params={"pointer": "/b/c", "value": [1, 2]})],
                  {"message": "change"})
    assert res.ok and "commit" in res.logs
    after = a.inspect(root, ".")
    assert after.states["file:pkg/m.py::f"] != insp.states["file:pkg/m.py::f"]
    diff = a.diff(before, root, ".")
    assert "-    return 1" in diff and "+    return 2" in diff and '"c"' in diff
    assert a.validate(root, ".", ["json_valid", "file_reopens"], {}).passed


def test_code_adapter_rejects_path_escape_and_missing_text(tmp_path):
    a = get_adapter("code")
    a.create(tmp_path, "files", {"files": {"x.txt": "hi\n"}})
    res = a.apply(tmp_path, ".", [Op(op="write_file", component_id=None,
                                     params={"path": "../evil.txt", "content": "x"})], {})
    assert not res.ok and "escapes" in res.error
    res = a.apply(tmp_path, ".", [Op(op="replace_text", component_id="file:x.txt",
                                     params={"old": "absent", "new": "y"})], {})
    assert not res.ok and "not found" in res.error


def test_code_tests_need_permission(tmp_path):
    a = get_adapter("code")
    a.create(tmp_path, "files", {"files": {"test_a.py": "def test_a():\n    assert True\n"}})
    ctx = {"test_command": "python -m pytest -q", "allowed_commands": []}
    rep = a.validate(tmp_path, ".", ["tests"], ctx)
    assert not rep.passed and "not in the project's allowed_commands" in rep.checks[0].detail
    rep = a.validate(tmp_path, ".", ["tests"], {**ctx, "allowed_commands": ["python -m pytest"]})
    assert rep.passed, rep.checks[0].data.get("output")


@pytest.mark.blender
def test_blender_components_and_modifiers(tmp_path):
    a = get_adapter("blender")
    comps = [{"id": "root", "primitive": "empty"},
             {"id": "box", "primitive": "cube", "size": [1, 1, 2], "location": [0, 0, 1],
              "parent": "root"},
             {"id": "other", "primitive": "cube", "size": [0.5, 0.5, 0.5],
              "location": [2, 0, 0.25], "parent": "root"}]
    entry = a.create(tmp_path, "components", {"components": comps})
    s0 = a.inspect(tmp_path, entry)
    assert {c.id for c in s0.components} == {"root", "box", "other"}
    assert s0.measurements["box"]["height"] == pytest.approx(2.0)
    ops = [Op(op="set_taper", component_id="box", params={"factor": 0.5}),
           Op(op="set_material", component_id="box", params={"base_color": "#336699",
                                                             "roughness": 0.3})]
    assert a.apply(tmp_path, entry, ops, {}).ok
    s1 = a.inspect(tmp_path, entry)
    assert s1.states["other"] == s0.states["other"]
    assert s1.states["box"] != s0.states["box"]
    assert s1.measurements["box"]["height"] == pytest.approx(2.0)
    assert s1.properties["box"]["material"]["base_color"] == "#336699"
    assert s1.properties["box"]["taper"] == pytest.approx(0.5)
    assert a.apply(tmp_path, entry, ops, {}).ok
    assert a.inspect(tmp_path, entry).states == s1.states  # idempotent
    res = a.apply(tmp_path, entry, [Op(op="set_dimensions", component_id="root",
                                       params={"width": 4.0})], {})
    assert res.ok and a.inspect(tmp_path, entry).measurements["root"]["width"] == \
        pytest.approx(4.0, rel=1e-3)
    prev = a.preview(tmp_path, entry, tmp_path / "prev")
    assert prev["render"].stat().st_size > 1000 and prev["glb"].read_bytes()[:4] == b"glTF"
    out = a.export(tmp_path, entry, "obj", tmp_path / "exp")
    assert out.exists()


@pytest.mark.blender
def test_blender_failure_does_not_save(tmp_path):
    a = get_adapter("blender")
    entry = a.create(tmp_path, "components", {"components": [{"id": "c", "primitive": "cube"}]})
    raw = (tmp_path / entry).read_bytes()
    res = a.apply(tmp_path, entry, [Op(op="set_taper", component_id="c",
                                       params={"factor": 0.2}),
                                    Op(op="set_taper", component_id="missing",
                                       params={"factor": 0.2})], {})
    assert not res.ok and "component not found" in res.error
    assert (tmp_path / entry).read_bytes() == raw
