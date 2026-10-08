"""Planner providers: the Claude provider is exercised against a fake client (no network)."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from daedelus import ingest
from daedelus.adapters import get_adapter
from daedelus.bindings import resolve
from daedelus.models import Artifact, Component, SourceBinding, TargetSelector
from daedelus.providers.anthropic_provider import AnthropicProvider, build_prompt
from daedelus.providers.base import PlanRequest, ProviderError
from daedelus.providers.heuristic import HeuristicProvider


def _req(store):
    a = store.save_artifact(Artifact(
        name="Pic", artifact_type="image2d", adapter="layered2d", native_dir="x", entry="x",
        components=[Component(id="img", name="Img", kind="image"),
                    Component(id="sky", name="Sky", kind="layer", parent_id="img")]))
    s = ingest.register_text(store, "g", "tint: #ff0000\nsaturation: 0.2")
    store.save_binding(SourceBinding(source_id=s.id, role="guideline", aspects=["color"],
                                     target=TargetSelector(scope="project")))
    t = TargetSelector(scope="component", artifact_id=a.id, component_id="sky")
    ctx = resolve(store, t)
    return PlanRequest(unit=t.key(), artifact=a, component=a.component("sky"), context=ctx,
                       adapter=get_adapter("layered2d").info(),
                       properties={"sky": {"mean_color": "#8899aa"}},
                       measurements={"sky": {"brightness": 0.5}}), s


def test_heuristic_is_deterministic(store):
    req, _ = _req(store)
    p1, p2 = HeuristicProvider().plan(req), HeuristicProvider().plan(req)
    assert p1.deterministic and p1.model_dump() == p2.model_dump()
    op = p1.operations[0]
    assert op.op == "color_grade" and op.params["tint"] == "#ff0000"
    assert op.params["saturation"] == 1.2


def test_allowed_ops_restrict_heuristic(store):
    req, _ = _req(store)
    req.allowed_ops = ["set_layer_props"]
    assert HeuristicProvider().plan(req).operations == []


class _FakeMessages:
    def __init__(self, payload, stop="end_turn"):
        self.payload, self.stop, self.kwargs = payload, stop, None

    def create(self, **kwargs):
        self.kwargs = kwargs
        return SimpleNamespace(stop_reason=self.stop, model=kwargs["model"],
                               content=[SimpleNamespace(type="text",
                                                        text=json.dumps(self.payload))],
                               usage=SimpleNamespace(input_tokens=10, output_tokens=5))


def test_anthropic_provider_structured_plan(store):
    pytest.importorskip("anthropic")
    req, s = _req(store)
    bid = req.context.entries[0].binding.id
    fake = _FakeMessages({"operations": [{"op": "color_grade", "component_id": "sky",
                                          "params_json": json.dumps({"tint": "#ff0000"}),
                                          "derived_from": [bid], "rationale": "guide"}],
                          "interpretations": [{"binding_id": bid, "interpretation": "tint"}],
                          "notes": []})
    client = SimpleNamespace(beta=SimpleNamespace(messages=fake))
    plan = AnthropicProvider().plan(req, client=client)
    assert plan.operations[0].params == {"tint": "#ff0000"}
    assert plan.interpretations[bid] == "tint"
    kw = fake.kwargs
    assert kw["model"] == "claude-opus-5-5"
    assert kw["output_config"]["format"]["type"] == "json_schema"
    assert "tool_choice" not in kw
    enum = kw["output_config"]["format"]["schema"]["properties"]["operations"]["items"][
        "properties"]["op"]["enum"]
    assert "color_grade" in enum


def test_anthropic_provider_refusal_and_malformed(store):
    pytest.importorskip("anthropic")
    req, _ = _req(store)
    client = SimpleNamespace(beta=SimpleNamespace(messages=_FakeMessages({}, stop="refusal")))
    with pytest.raises(ProviderError, match="declined"):
        AnthropicProvider().plan(req, client=client)
    bad = _FakeMessages({"operations": [{"op": "x", "component_id": "", "params_json": "[1]",
                                         "derived_from": [], "rationale": ""}],
                         "interpretations": [], "notes": []})
    with pytest.raises(ProviderError, match="malformed"):
        AnthropicProvider().plan(req, client=SimpleNamespace(beta=SimpleNamespace(messages=bad)))


def test_prompt_contains_scope_and_sources(store):
    req, s = _req(store)
    messages, ops = build_prompt(req)
    text = messages[0]["content"][-1]["text"]
    assert '"role": "guideline"' in text and "color_grade" in ops


def test_anthropic_availability_without_credentials(monkeypatch):
    for v in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_PROFILE"):
        monkeypatch.delenv(v, raising=False)
    ok, why = AnthropicProvider().available()
    assert not ok and "credentials" in why
