"""Workflow engine without Blender: layered 2D + code artifacts."""

from __future__ import annotations

import json

import pytest
from PIL import Image

from daedelus import engine as engine_mod
from daedelus import ingest
from daedelus.artifacts import create_artifact
from daedelus.engine import Engine, validate_workflow
from daedelus.models import (
    PlannedOperation,
    RunStatus,
    SourceBinding,
    TargetSelector,
    Workflow,
    WorkflowEdge,
    WorkflowNode,
)
from daedelus.providers.base import Plan, Provider

LAYERS = [
    {"id": "fg", "name": "Foreground", "fill": {"type": "solid", "colors": ["#335533"]},
     "shape": {"type": "rect", "box": [0, 0.6, 1, 0.4]}},
    {"id": "sky", "name": "Sky", "fill": {"type": "gradient", "colors": ["#88aadd", "#ddeeff"]}},
]


def _png(path, color):
    Image.new("RGB", (64, 48), color).save(path)
    return path


@pytest.fixture()
def setup(store, tmp_path):
    img = create_artifact(store, name="Picture", adapter="layered2d", template="layers",
                          params={"width": 64, "height": 48, "root_id": "picture",
                                  "layers": LAYERS})[0]
    code = create_artifact(store, name="Manifest", adapter="code", template="files", params={
        "files": {"m.json": "{}\n",
                  "test_m.py": "import json\n\ndef test_ok():\n    "
                               "assert isinstance(json.load(open('m.json')), dict)\n"}},
        metadata={"test_command": "python -m pytest -q"})[0]
    warm = ingest.register_file(store, _png(tmp_path / "warm.png", (230, 120, 60)))
    cold = ingest.register_file(store, _png(tmp_path / "cold.png", (40, 90, 200)))
    guide = ingest.register_text(store, "guide", "tint: #ffcc88\nsaturation: -0.2\nindent: 4\n")
    b_sky = store.save_binding(SourceBinding(
        source_id=warm.id, role="reference", aspects=["palette"],
        target=TargetSelector(scope="component", artifact_id=img.id, component_id="sky")))
    b_guide = store.save_binding(SourceBinding(
        source_id=guide.id, role="guideline", aspects=["style", "color", "code_style"],
        target=TargetSelector(scope="project")))
    nodes = [
        WorkflowNode(id="src", type="sources"),
        WorkflowNode(id="img", type="artifact", config={"artifact_id": img.id}),
        WorkflowNode(id="code", type="artifact", config={"artifact_id": code.id}),
        WorkflowNode(id="paint", type="agent", config={"fan_out": True}),
        WorkflowNode(id="record", type="agent", config={
            "fan_out": False, "allowed_ops": ["update_json"],
            "instructions": "manifest: /items\nmanifest_file: m.json",
            "validation": ["file_reopens", "json_valid"]}),
        WorkflowNode(id="check", type="validate", config={
            "checks": ["file_reopens", "tests", "json_valid", "component_preservation"]}),
    ]
    edges = [
        WorkflowEdge(id="a", source="img", source_port="artifact", target="paint",
                     target_port="artifact"),
        WorkflowEdge(id="b", source="src", source_port="sources", target="paint",
                     target_port="sources"),
        WorkflowEdge(id="c", source="code", source_port="artifact", target="record",
                     target_port="artifact"),
        WorkflowEdge(id="d", source="paint", source_port="revision", target="record",
                     target_port="after"),
        WorkflowEdge(id="e", source="paint", source_port="revision", target="check",
                     target_port="revision"),
        WorkflowEdge(id="f", source="record", source_port="revision", target="check",
                     target_port="revision"),
    ]
    wf = store.save_workflow(Workflow(name="t", nodes=nodes, edges=edges))
    return {"img": img, "code": code, "warm": warm, "cold": cold, "guide": guide,
            "b_sky": b_sky, "b_guide": b_guide, "wf": wf}


def _units(ex, node):
    return {u.unit.split("#")[-1]: u.status.value for u in ex.run(node).units}


def _head(store, aid):
    a = store.get_artifact(aid)
    return store.get_revision(a.head_revision_id)


def test_validation_catches_graph_errors(store, setup):
    wf = setup["wf"]
    assert [i for i in validate_workflow(wf, store) if i["level"] == "error"] == []
    bad = wf.model_copy(deep=True)
    bad.edges.append(WorkflowEdge(id="cyc", source="record", source_port="revision",
                                  target="paint", target_port="after"))
    assert any("cycle" in i["message"] for i in validate_workflow(bad, store))
    bad2 = wf.model_copy(deep=True)
    bad2.edges.append(WorkflowEdge(id="type", source="src", source_port="sources",
                                   target="check", target_port="revision"))
    assert any("cannot connect" in i["message"] for i in validate_workflow(bad2, store))
    bad3 = wf.model_copy(deep=True)
    bad3.edges = [e for e in bad3.edges if e.target != "paint" or e.target_port != "artifact"]
    assert any("required input 'artifact'" in i["message"] for i in validate_workflow(bad3, store))
    bad4 = wf.model_copy(deep=True)
    bad4.nodes[3].config["execution"] = "cloud"
    bad4.nodes[3].config["allowed_ops"] = ["teleport"]
    msgs = " ".join(i["message"] for i in validate_workflow(bad4, store))
    assert "cloud" in msgs and "teleport" in msgs
    bad5 = wf.model_copy(deep=True)
    bad5.nodes.append(WorkflowNode(id="x", type="mystery"))
    assert any("unknown node type" in i["message"] for i in validate_workflow(bad5, store))


def test_invalid_workflow_does_not_execute(store, setup):
    wf = setup["wf"].model_copy(deep=True)
    wf.nodes[1].config["artifact_id"] = "art_missing"
    wf = store.save_workflow(wf)
    ex = Engine(store).execute(wf.id)
    assert ex.status == RunStatus.failed and "does not exist" in ex.error


def test_execution_incremental_and_scoped(store, setup):
    eng, wf = Engine(store), setup["wf"]
    img = setup["img"]
    ex = eng.execute(wf.id)
    assert ex.status == RunStatus.succeeded, ex.error
    assert _units(ex, "paint") == {"picture": "succeeded", "sky": "succeeded"}
    rev = _head(store, img.id)
    assert rev.number == 2 and rev.validation.passed
    # attribution records every presented binding with its declared use
    att = {(a.unit.split("#")[-1], a.binding_id): a for a in rev.attribution}
    assert att[("sky", setup["b_sky"].id)].applied
    assert att[("picture", setup["b_guide"].id)].applied
    # the code agent wrote a manifest entry with a diff and indent from the guideline
    crev = _head(store, setup["code"].id)
    assert crev.diff and '"items"' in crev.diff and '    "picture"' in crev.diff
    report = ex.run("check").outputs["report"]
    tests = [c for r in report["artifacts"] for c in r["report"]["checks"] if c["name"] == "tests"]
    assert tests and all(t["passed"] for t in tests)

    # re-running with unchanged inputs does nothing
    ex2 = eng.execute(wf.id)
    assert set(_units(ex2, "paint").values()) == {"skipped"}
    assert set(_units(ex2, "record").values()) == {"skipped"}
    assert _head(store, img.id).number == 2

    # changing the sky binding re-runs only the sky unit and only changes the sky layer
    before = _head(store, img.id).component_states
    store.save_binding(setup["b_sky"].model_copy(update={"source_id": setup["cold"].id}))
    imp = eng.impact(wf.id)
    paint = next(n for n in imp["nodes"] if n["node_id"] == "paint")
    assert {u["unit"].split("#")[-1]: u["stale"] for u in paint["units"]} == \
        {"picture": False, "sky": True}
    ex3 = eng.execute(wf.id)
    assert _units(ex3, "paint") == {"picture": "skipped", "sky": "succeeded"}
    after = _head(store, img.id).component_states
    assert [k for k in after if after[k] != before[k]] == ["sky"]
    assert _units(ex3, "record") == {"repo": "succeeded"}  # upstream revision changed

    # replay reproduces the recorded results
    rep = eng.replay(ex3.id)
    assert rep["reproducible"], rep


def test_role_change_reinterprets_same_media(store, setup):
    eng, wf = Engine(store), setup["wf"]
    eng.execute(wf.id)
    sky1 = _head(store, setup["img"].id).component_states["sky"]
    store.save_binding(setup["b_sky"].model_copy(update={"role": "inspiration"}))
    ex = eng.execute(wf.id)
    assert _units(ex, "paint")["sky"] == "succeeded"
    plan = next(u.plan for u in ex.run("paint").units if u.unit.endswith("#sky"))
    assert "inspiration (blend" in plan["interpretations"][setup["b_sky"].id]
    assert _head(store, setup["img"].id).component_states["sky"] != sky1


def test_manual_edit_marks_units_stale(store, setup):
    from daedelus.artifacts import restore_revision

    eng, wf = Engine(store), setup["wf"]
    eng.execute(wf.id)
    img = store.get_artifact(setup["img"].id)
    first = store.list_revisions(img.id)[0]
    restore_revision(store, img.id, first.id)
    imp = eng.impact(wf.id)
    reasons = [r for n in imp["nodes"] for u in n["units"] for r in u["reasons"]]
    assert any("changed outside" in r for r in reasons)


def test_approval_checkpoint(store, setup):
    eng, wf = Engine(store), setup["wf"]
    wf2 = wf.model_copy(deep=True)
    wf2.nodes[3].config["require_approval"] = True
    wf2 = store.save_workflow(wf2)
    ex = eng.execute(wf2.id)
    assert ex.status == RunStatus.waiting_approval
    assert ex.run("paint").approval["pending"]["ops"]
    assert _head(store, setup["img"].id).number == 1  # nothing applied yet
    ex = eng.decide(ex.id, "paint", approve=True)
    assert ex.status == RunStatus.succeeded, ex.error
    assert _head(store, setup["img"].id).number == 2


def test_rejection_fails_and_blocks_downstream(store, setup):
    eng = Engine(store)
    wf2 = setup["wf"].model_copy(deep=True)
    wf2.nodes[3].config["require_approval"] = True
    wf2 = store.save_workflow(wf2)
    ex = eng.execute(wf2.id)
    ex = eng.decide(ex.id, "paint", approve=False, note="colours wrong")
    assert ex.status == RunStatus.failed
    assert "rejected" in ex.run("paint").error
    assert ex.run("record").status == RunStatus.blocked


class _BadProvider(Provider):
    name = "bad"

    def __init__(self, ops):
        self.ops = ops
        self.calls = 0

    def plan(self, req):
        self.calls += 1
        return Plan(provider="bad", operations=[PlannedOperation(**o) for o in self.ops])


def test_retry_then_rollback_on_adapter_failure(store, setup, monkeypatch):
    prov = _BadProvider([
        {"op": "color_grade", "component_id": "sky", "params": {"brightness": 1.3}},
        {"op": "paint_reference", "component_id": "sky", "params": {"source_id": "src_nope"}}])
    monkeypatch.setattr(engine_mod, "get_provider", lambda name: prov)
    wf = setup["wf"].model_copy(deep=True)
    wf.nodes[3].config["retry"] = {"max_attempts": 3, "backoff_seconds": 0}
    wf.nodes[3].config["target_component"] = "sky"
    wf.nodes[3].config["fan_out"] = False
    wf = store.save_workflow(wf)
    before = _head(store, setup["img"].id)
    native = store.abs(store.get_artifact(setup["img"].id).native_dir) / "image.ora"
    raw = native.read_bytes()
    ex = Engine(store).execute(wf.id)
    nr = ex.run("paint")
    assert nr.status == RunStatus.failed and nr.attempts == 3
    assert "source image not available" in nr.error
    assert native.read_bytes() == raw  # rolled back to the checkpoint
    assert _head(store, setup["img"].id).id == before.id
    assert ex.run("record").status == RunStatus.blocked


def test_plan_outside_unit_scope_rejected(store, setup, monkeypatch):
    prov = _BadProvider([{"op": "color_grade", "component_id": "fg",
                          "params": {"brightness": 0.5}}])
    monkeypatch.setattr(engine_mod, "get_provider", lambda name: prov)
    wf = setup["wf"].model_copy(deep=True)
    wf.nodes[3].config.update({"target_component": "sky", "fan_out": False})
    wf = store.save_workflow(wf)
    ex = Engine(store).execute(wf.id)
    assert ex.run("paint").status == RunStatus.failed
    assert "outside unit" in ex.run("paint").error


def test_invalid_params_rejected_before_execution(store, setup, monkeypatch):
    prov = _BadProvider([{"op": "color_grade", "component_id": "picture",
                          "params": {"brightness": 99}}])
    monkeypatch.setattr(engine_mod, "get_provider", lambda name: prov)
    ex = Engine(store).execute(setup["wf"].id)
    assert "maximum" in ex.run("paint").error


def test_conflict_blocks_execution(store, setup):
    from daedelus.models import Constraint

    s = setup["guide"]
    t = TargetSelector(scope="component", artifact_id=setup["img"].id, component_id="sky")
    store.save_binding(SourceBinding(source_id=s.id, role="constraint", constraint="hard",
                                     target=t, constraints=[
                                         Constraint(property="coverage", op="eq", value=1.0)]))
    store.save_binding(SourceBinding(source_id=s.id, role="constraint", constraint="hard",
                                     target=t, constraints=[
                                         Constraint(property="coverage", op="eq", value=0.5)]))
    ex = Engine(store).execute(setup["wf"].id)
    assert ex.run("paint").status == RunStatus.failed
    assert "conflict" in ex.run("paint").error
    assert _head(store, setup["img"].id).number == 1


def test_constraint_violation_rolls_back(store, setup, monkeypatch):
    from daedelus.models import Constraint

    t = TargetSelector(scope="component", artifact_id=setup["img"].id, component_id="fg")
    store.save_binding(SourceBinding(source_id=setup["guide"].id, role="constraint",
                                     constraint="hard", target=t,
                                     constraints=[Constraint(property="brightness",
                                                             op="preserve")]))
    prov = _BadProvider([{"op": "color_grade", "component_id": "fg",
                          "params": {"brightness": 1.8}}])
    monkeypatch.setattr(engine_mod, "get_provider", lambda name: prov)
    wf = setup["wf"].model_copy(deep=True)
    wf.nodes[3].config.update({"target_component": "fg", "fan_out": False})
    wf = store.save_workflow(wf)
    ex = Engine(store).execute(wf.id)
    assert ex.run("paint").status == RunStatus.failed
    assert "brightness preserve" in ex.run("paint").error
    assert _head(store, setup["img"].id).number == 1


def test_workflow_versions_persist(store, setup, workspace):
    wf = setup["wf"]
    v2 = store.save_workflow(wf.model_copy(update={"description": "changed"}))
    assert v2.version == wf.version + 1
    store2 = type(store)(store.root)
    assert store2.get_workflow(wf.id).description == "changed"
    assert store2.get_workflow(wf.id, wf.version).description == ""
    assert [w.version for w in store2.list_workflow_versions(wf.id)] == [1, 2]
    exported = json.loads(v2.model_dump_json())
    assert Workflow.model_validate(exported) == v2


def test_cloud_execution_reports_unavailable(store, setup):
    wf = setup["wf"].model_copy(deep=True)
    wf.nodes[3].config["execution"] = "cloud"
    wf = store.save_workflow(wf)
    ex = Engine(store).execute(wf.id)
    assert ex.status == RunStatus.failed and "cloud" in ex.error
