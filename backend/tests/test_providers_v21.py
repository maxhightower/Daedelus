"""V2.1 provider compatibility, error classes, usage/cost reporting and execution budgets.

No network: the Anthropic and Gemini SDK clients are replaced by fakes that record the request
and return (or raise) what the real APIs document. These tests prove request shape and error
handling; they do not prove live model behaviour (that is the opt-in live gate).
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from daedelus.budget import ExecutionBudget, use_budget
from daedelus.providers import BudgetedProvider, get_provider
from daedelus.providers.base import (Plan, Provider, ProviderOverBudget, ProviderRateLimited,
                                     ProviderTimeout)
from daedelus.semantic.models import Usage

from test_providers import _FakeMessages, _req


def _plan_payload(bid):
    return {"operations": [{"op": "color_grade", "component_id": "sky",
                            "params_json": json.dumps({"tint": "#ff0000"}),
                            "derived_from": [bid], "rationale": "guide"}],
            "interpretations": [{"binding_id": bid, "interpretation": "tint"}], "notes": []}


# ------------------------------------------------------------------ Anthropic request shape
def test_anthropic_request_matches_current_api(store):
    pytest.importorskip("anthropic")
    from daedelus.providers.anthropic_provider import AnthropicProvider
    req, _ = _req(store)
    fake = _FakeMessages(_plan_payload(req.context.entries[0].binding.id))
    AnthropicProvider().plan(req, client=SimpleNamespace(beta=SimpleNamespace(messages=fake)))
    kw = fake.kwargs
    assert kw["model"] == "claude-opus-5-5"
    assert kw["thinking"] == {"type": "adaptive"}  # budget_tokens is rejected on Opus 5.5
    assert kw["output_config"]["effort"] == "medium"
    assert kw["output_config"]["format"]["type"] == "json_schema"
    assert kw["betas"] == ["server-side-fallback-2026-07-01"] and kw["fallbacks"] == "default"
    assert "temperature" not in kw and "tool_choice" not in kw  # 400 on current models
    assert kw["max_tokens"] <= 16000  # non-streaming request stays under SDK timeouts


def _fake_usage(**kw):
    base = dict(input_tokens=1000, output_tokens=200, cache_read_input_tokens=0,
                cache_creation_input_tokens=0)
    return SimpleNamespace(**{**base, **kw})


class _Msgs:
    def __init__(self, payload, model="claude-opus-5-5", blocks=(), usage=None, exc=None):
        self.payload, self.model, self.blocks, self.usage, self.exc = payload, model, blocks, \
            usage, exc

    def create(self, **kw):
        if self.exc:
            raise self.exc
        return SimpleNamespace(stop_reason="end_turn", model=self.model, _request_id="req_1",
                               content=[*self.blocks, SimpleNamespace(
                                   type="text", text=json.dumps(self.payload))],
                               usage=self.usage or _fake_usage())


def test_anthropic_usage_cost_fallback_and_request_id(store):
    pytest.importorskip("anthropic")
    from daedelus.providers.anthropic_provider import AnthropicProvider
    req, _ = _req(store)
    pl = _plan_payload(req.context.entries[0].binding.id)
    c = SimpleNamespace(beta=SimpleNamespace(messages=_Msgs(pl, usage=_fake_usage(
        cache_read_input_tokens=1000))))
    p = AnthropicProvider().plan(req, client=c)
    u = p.usage
    assert u["model"] == "claude-opus-5-5" and u["request_id"] == "req_1"
    # 1000 in @ $4 + 1000 cache-read @ $0.4 + 200 out @ $20 per million tokens
    assert u["cost_usd"] == pytest.approx(0.004 + 0.0004 + 0.004)
    assert u["input_tokens"] == 2000 and u["cache_read_tokens"] == 1000
    # a refusal fallback served by another model is reported, and an unknown price stays
    # unknown rather than becoming zero
    c = SimpleNamespace(beta=SimpleNamespace(messages=_Msgs(
        pl, model="claude-mystery-9", blocks=[SimpleNamespace(type="fallback")])))
    p = AnthropicProvider().plan(req, client=c)
    assert p.usage["fallback_from"] == "claude-opus-5-5" and p.usage["cost_usd"] is None
    assert p.model == "claude-mystery-9"


def _httpx2_response(status, headers=None):
    import httpx2
    return httpx2.Response(status, headers=headers or {},
                           request=httpx2.Request("POST", "https://api.anthropic.com/v1/messages"))


def test_anthropic_rate_limit_timeout_and_overload_are_typed(store):
    anthropic = pytest.importorskip("anthropic")
    import httpx2

    from daedelus.providers.anthropic_provider import AnthropicProvider
    req, _ = _req(store)
    rl = anthropic.RateLimitError("rate limited", response=_httpx2_response(
        429, {"retry-after": "7"}), body=None)
    c = SimpleNamespace(beta=SimpleNamespace(messages=_Msgs({}, exc=rl)))
    with pytest.raises(ProviderRateLimited) as ei:
        AnthropicProvider().plan(req, client=c)
    assert ei.value.retry_after == 7.0
    to = anthropic.APITimeoutError(request=httpx2.Request("POST", "https://api.anthropic.com"))
    c = SimpleNamespace(beta=SimpleNamespace(messages=_Msgs({}, exc=to)))
    with pytest.raises(ProviderTimeout):
        AnthropicProvider().plan(req, client=c)
    ov = anthropic.APIStatusError("overloaded", response=_httpx2_response(529), body=None)
    c = SimpleNamespace(beta=SimpleNamespace(messages=_Msgs({}, exc=ov)))
    with pytest.raises(ProviderRateLimited):
        AnthropicProvider().plan(req, client=c)


def test_anthropic_client_uses_budget_timeouts(store, monkeypatch):
    anthropic = pytest.importorskip("anthropic")
    from daedelus.providers.anthropic_provider import AnthropicProvider
    req, _ = _req(store)
    seen = {}
    pl = _plan_payload(req.context.entries[0].binding.id)

    class FakeClient:
        def __init__(self, **kw):
            seen.update(kw)
            self.beta = SimpleNamespace(messages=_Msgs(pl))
    monkeypatch.setattr(anthropic, "Anthropic", FakeClient)
    with use_budget(ExecutionBudget.from_config({"call_timeout_s": 42, "max_retries": 1})):
        AnthropicProvider().plan(req)
    assert seen == {"timeout": 42.0, "max_retries": 1}


def test_large_pdf_is_refused_before_upload(store, tmp_path):
    pytest.importorskip("anthropic")
    from daedelus import ingest
    from daedelus.providers.anthropic_provider import PDF_MAX_BYTES, AnthropicProvider
    from daedelus.providers.base import AnalyzeRequest, NotSupported
    big = tmp_path / "big.pdf"
    with open(big, "wb") as f:
        f.write(b"%PDF-1.4\n")
        f.seek(PDF_MAX_BYTES + 10)
        f.write(b"\n%%EOF")
    src = ingest.register_text(store, "x", "x")
    src.media_type = "pdf"
    with pytest.raises(NotSupported, match="larger"):
        AnthropicProvider().analyze(AnalyzeRequest(source=src, file_path=str(big)),
                                    client=SimpleNamespace())


# ------------------------------------------------------------------ Gemini
class _GemModels:
    def __init__(self, exc=None):
        self.exc, self.calls = exc, []

    def generate_content(self, **kw):
        self.calls.append(kw)
        if self.exc:
            raise self.exc
        return SimpleNamespace(text=json.dumps({"summary": "s", "status": "complete",
                                                "observations": [], "derived_constraints": [],
                                                "limitations": []}),
                               model_version="gemini-3.8-flash-001", response_id="r1",
                               usage_metadata=SimpleNamespace(
                                   prompt_token_count=100, candidates_token_count=10,
                                   thoughts_token_count=5, cached_content_token_count=0))


class _GemFiles:
    def __init__(self):
        self.uploaded, self.deleted, self.polls = [], [], 0

    def upload(self, file, config):
        self.uploaded.append((file, config))
        return SimpleNamespace(name="files/abc", uri="https://generativelanguage/files/abc",
                               state="PROCESSING")

    def get(self, name):
        self.polls += 1
        return SimpleNamespace(name=name, uri="https://generativelanguage/files/abc",
                               state="ACTIVE")

    def delete(self, name):
        self.deleted.append(name)


def _video_source(store, tmp_path, size):
    from daedelus import ingest
    p = tmp_path / "clip.mp4"
    with open(p, "wb") as f:
        f.seek(size)
        f.write(b"\0")
    src = ingest.register_text(store, "clip", "x")
    src.media_type = "video"
    src.mime_type = "video/mp4"
    return src, p


def test_gemini_default_model_files_api_and_cleanup(store, tmp_path, monkeypatch):
    pytest.importorskip("google.genai")
    import daedelus.providers.gemini_provider as gp
    from daedelus.providers.base import AnalyzeRequest
    assert gp.DEFAULT_MODEL == "gemini-3.8-flash"  # gemini-2.5-flash shuts down 2026-10-16
    monkeypatch.setattr(gp.time, "sleep", lambda s: None)
    src, p = _video_source(store, tmp_path, gp.INLINE_LIMIT + 1)
    client = SimpleNamespace(models=_GemModels(), files=_GemFiles())
    ana = gp.GeminiProvider().analyze(AnalyzeRequest(source=src, file_path=str(p)),
                                      client=client)
    assert ana.pathway == "video_file (Files API)"
    assert client.files.uploaded and client.files.polls >= 1
    assert client.files.deleted == ["files/abc"]  # user media removed after the call
    part = client.models.calls[0]["contents"][0]
    assert part.file_data.file_uri.endswith("files/abc")
    assert ana.usage.model == "gemini-3.8-flash-001" and ana.usage.cost_usd is None
    (tmp_path / "s").mkdir()
    small, sp = _video_source(store, tmp_path / "s", 1000)
    client2 = SimpleNamespace(models=_GemModels(), files=_GemFiles())
    ana2 = gp.GeminiProvider().analyze(AnalyzeRequest(source=small, file_path=str(sp)),
                                       client=client2)
    assert ana2.pathway == "video_file" and not client2.files.uploaded


def test_gemini_errors_are_typed(store, tmp_path):
    pytest.importorskip("google.genai")
    from google.genai import errors

    import daedelus.providers.gemini_provider as gp
    from daedelus import ingest
    from daedelus.providers.base import AnalyzeRequest
    src = ingest.register_text(store, "t", "hello")
    for code, status, cls in ((429, "RESOURCE_EXHAUSTED", ProviderRateLimited),
                              (504, "DEADLINE_EXCEEDED", ProviderTimeout)):
        exc = errors.ClientError(code, {"error": {"code": code, "status": status,
                                                  "message": "x"}}) if code < 500 else \
            errors.ServerError(code, {"error": {"code": code, "status": status, "message": "x"}})
        client = SimpleNamespace(models=_GemModels(exc=exc), files=_GemFiles())
        with pytest.raises(cls):
            gp.GeminiProvider().analyze(AnalyzeRequest(source=src, text="hello"), client=client)


# ------------------------------------------------------------------ budgets
class _Live(Provider):
    name, live, contracts = "fakelive", True, ("plan",)

    def __init__(self, cost=0.01):
        self.cost, self.n = cost, 0

    def plan(self, req):
        self.n += 1
        return Plan(provider=self.name, model="fake-model", usage=Usage(
            calls=1, input_tokens=100, output_tokens=10, cost_usd=self.cost).model_dump())


def test_budget_refuses_calls_before_they_are_made():
    inner = _Live()
    b = ExecutionBudget.from_config({"max_model_calls": 2})
    p = BudgetedProvider(inner, b)
    p.plan(None)
    p.plan(None)
    with pytest.raises(ProviderOverBudget, match="call budget"):
        p.plan(None)
    assert inner.n == 2  # the third call never reached the provider
    snap = b.snapshot()
    assert snap["used"]["model_calls"] == 2 and snap["used"]["cost_usd"] == pytest.approx(0.02)


def test_budget_cost_limit_and_unknown_prices():
    b = ExecutionBudget.from_config({"max_cost_usd": 0.015})
    p = BudgetedProvider(_Live(cost=0.01), b)
    p.plan(None)
    p.plan(None)  # 0.02 now: over
    with pytest.raises(ProviderOverBudget, match="cost budget"):
        p.plan(None)
    b2 = ExecutionBudget.from_config({})
    BudgetedProvider(_Live(cost=None), b2).plan(None)
    s = b2.snapshot()["used"]
    assert s["cost"] == "unknown" and s["unpriced_calls"] == 1  # never reported as $0
    total = Usage(calls=1, cost_usd=0.5).add(Usage(calls=2, cost_usd=None))
    assert total.unpriced_calls == 2 and "unknown price" in total.cost_label


def test_get_provider_is_budgeted_only_inside_an_execution():
    assert not isinstance(get_provider("heuristic"), BudgetedProvider)
    with use_budget(ExecutionBudget.from_config({})):
        p = get_provider("heuristic")
        assert isinstance(p, BudgetedProvider) and p.name == "heuristic"


def test_budget_caps_loop_iterations():
    b = ExecutionBudget.from_config({"max_iterations": 1})
    assert b.iterations_cap(3) == 1 and b.iterations_cap(0) == 0
