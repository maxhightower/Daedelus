"""V2.1.1 benchmark additions: versioned checks, held-out specification cases, GLB evidence,
automatic failure classification and the labelled model assessment."""

from __future__ import annotations

import json
import struct

from daedelus import bench_v21 as b


def _glb(meshes):
    doc = json.dumps({"asset": {"version": "2.0"}, "meshes": meshes}).encode()
    doc += b" " * (-len(doc) % 4)
    head = struct.pack("<4sII", b"glTF", 2, 12 + 8 + len(doc))
    return head + struct.pack("<II", len(doc), 0x4E4F534A) + doc


def test_glb_mesh_count(tmp_path):
    p = tmp_path / "m.glb"
    p.write_bytes(_glb([{"primitives": [{"attributes": {"POSITION": 0}}]}]))
    assert b._glb_meshes(p) == 1
    p.write_bytes(_glb([{"primitives": []}]))
    assert b._glb_meshes(p) == 0  # an empty mesh is not geometry
    p.write_bytes(b"not a glb")
    assert b._glb_meshes(p) == 0


def test_held_out_cases_separate_correct_code_from_a_test_gaming_patch(tmp_path):
    gaming = tmp_path / "gaming"
    gaming.mkdir()
    (gaming / "textutil.py").write_text(
        "def slugify(text):\n"
        "    return {'Hello, World!': 'hello-world',\n"
        "            '  Crème brûlée  recipes ': 'creme-brulee-recipes'}.get(text, text)\n")
    res = b._held_out_spec(gaming)
    assert res["passed"] is False and "0/8" in res["detail"] or not res["passed"]
    good = tmp_path / "good"
    good.mkdir()
    (good / "textutil.py").write_text(
        "import re, unicodedata\n\n"
        "def slugify(text):\n"
        "    t = unicodedata.normalize('NFKD', text.lower())\n"
        "    t = ''.join(c for c in t if not unicodedata.combining(c))\n"
        "    return re.sub(r'[^a-z0-9]+', '-', t).strip('-')\n")
    assert b._held_out_spec(good) == {"passed": True, "detail": "8/8 cases; wrong: []"}


def test_versioned_checks_keep_the_v21_verdict_comparable():
    r = b.Run("A", "single", "heuristic")
    r.check("old check", True)
    r.check("new check", False, since="2.1.1")
    d = r.dump()
    assert d["bench_version"] == "2.1.1"
    assert d["passed"] is False and d["passed_v21_checks"] is True


def _run(**kw):
    d = {"task": "B", "mode": "single", "passed": False, "checks": [], "plans": [],
         "validation_decisions": [], "live_calls": 2, "model_observation_count": 3,
         "observations": []}
    d.update(kw)
    return d


def test_failure_classification_names_the_earliest_stage():
    c = b.classify_failure
    assert c(_run(passed=True)) is None
    assert c(_run(blocked_by="no key")) is None
    assert c(_run(error="ProviderOverBudget: model call budget exhausted"))["category"] == \
        "budget_exhaustion"
    assert c(_run(error="model declined (refusal, category=bio)"))["category"] == \
        "provider_refusal"
    assert c(_run(error="Anthropic API rate limit (429)"))["category"] == \
        "api_timeout_rate_limit"
    assert c(_run(model_observation_count=0))["category"] == "source_understanding_failure"
    assert c(_run(validation_decisions=[{"error": "invalid plan for x: unknown operation"}]))[
        "category"] == "invalid_operation_schema"
    preserve = _run(checks=[{"name": "unrelated components preserved exactly", "ok": False}])
    assert c(preserve)["category"] == "wrong_component_selected"
    geo = _run(task="A", checks=[{"name": "a native Blender object with geometry was created",
                                  "ok": False}])
    assert c(geo)["category"] == "inappropriate_operation_choice"  # no plan recorded at all
    geo["plans"] = [{"operations": [], "notes": ["cannot model a handle"]}]
    assert c(geo)["category"] == "no_operations_planned"
    assert "cannot model a handle" in c(geo)["evidence"]
    geo["plans"] = [{"operations": [{"op": "set_taper"}, {"op": "set_material"}]}]
    assert c(geo)["category"] == "inappropriate_operation_choice"  # nothing creates geometry
    geo["plans"] = [{"operations": [{"op": "add_primitive"}]}]
    assert c(geo)["category"] == "incorrect_parameter_selection"
    assert "heuristic" in c(geo)["basis"]
