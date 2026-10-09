"""V2.1.1 Phase A: the live-verification entry point classifies every outcome honestly.

Every test drives ``daedelus live-run`` through its real entry (``live_runner.main``: input
validation -> gate orchestration -> classification -> manifest -> secret scrubbing -> exit code)
with fake *live* providers injected into the provider registry, so no real API is called and
no money is spent. The fakes bill their usage exactly as the real providers do.
"""

from __future__ import annotations

import json
import socket
from pathlib import Path

import pytest
import yaml

from daedelus import live_runner as lr
from daedelus import providers as provmod
from daedelus.providers import llm_common as lc
from daedelus.providers.base import Plan, ProviderError, ProviderRateLimited
from daedelus.providers.heuristic import HeuristicProvider
from daedelus.semantic.models import Observation, Usage

ROOT = Path(__file__).resolve().parents[2]
SECRET = "sk-ant-api03-THISISAFAKESECRETVALUE0123456789"


def _usage(model="claude-opus-5-5"):
    from daedelus.semantic.service import make_usage
    import time
    u = make_usage(model, 1200, 300, time.perf_counter())
    u.request_id = "req_fake_1"
    return u


class FakeLive(HeuristicProvider):
    """A live provider double: heuristic machinery underneath, live semantics on top."""
    live = True
    pathways = ("image", "text", "video_url")

    def __init__(self, name, mode="ok", model="claude-opus-5-5", available=True):
        self.name, self.mode, self.model, self._avail = name, mode, model, available

    def available(self):
        return (True, "fake credentials") if self._avail else (False, "no key (fake)")

    def analyze(self, req):
        if self.mode == "rate_limit":
            raise ProviderRateLimited("rate limit (429): slow down", retry_after=5)
        if self.mode == "secret_error":
            raise ProviderError(f"upstream said: invalid x-api-key {SECRET}")
        u = _usage(self.model)
        lc.bill(u)  # billed before the response is used, as the real providers do
        if self.mode == "malformed":
            raise ProviderError("malformed JSON from model: Expecting value: line 1 column 1")
        ana = super().analyze(req)
        text = "a white ceramic coffee cup on a saucer" if self.mode != "wrong_object" \
            else "a red bicycle leaning on a wall"
        return ana.model_copy(update={
            "provider": self.name, "model": self.model, "pathway": "image",
            "status": "complete", "usage": u,
            "observations": [Observation(kind="object", text=text, basis="observed")]})

    def plan(self, req):
        u = _usage(self.model)
        lc.bill(u)
        p = super().plan(req)
        ops = p.operations
        if self.mode == "invalid_op":
            ops = [op.model_copy(update={"op": "delete_everything"}) for op in ops] or ops
        return Plan(provider=self.name, model=self.model, operations=ops,
                    interpretations=p.interpretations, usage=u.model_dump())


@pytest.fixture
def fakes(monkeypatch):
    reg = {}
    real = provmod.providers

    def registry():
        d = real()
        d.update(reg)
        return d
    monkeypatch.setattr(provmod, "providers", registry)

    def set_(name, **kw):
        reg[name] = FakeLive(name, **kw)
        return reg[name]
    return set_


def _env(**kw):
    env = {"LIVE_PROVIDERS": "claude", "LIVE_STAGES": "smoke"}
    env.update({k: v for k, v in kw.items()})
    return env


def _run(tmp_path, env, ex=None, resolve=None):
    out = tmp_path / "evidence-live"
    code = lr.main(["--from-env", "--out", str(out)], env=env, ex=ex, resolve=resolve)
    man = json.loads((out / "live_results.json").read_text())
    return code, man, out


def _gate(man, gid):
    return next(g for g in man["gates"] if g["id"] == gid)


class Forbidden(lr.Executors):
    """Executors that must never be reached (input rejected first)."""

    def __init__(self):
        def boom(*a, **k):
            raise AssertionError("a gate ran although the input should have been rejected")
        super().__init__(smoke=boom, bench=boom, connector=boom)


# ---------------------------------------------------------------- 1. requested test passes
def test_1_requested_live_test_succeeds_is_pass(tmp_path, fakes):
    fakes("anthropic")
    code, man, out = _run(tmp_path, _env())
    g = _gate(man, "claude.smoke")
    assert code == 0 and g["outcome"] == "PASS", g
    assert man["summary"]["live_evaluation"] == "PASS"
    assert man["campaign"]["used"]["model_calls"] >= 2  # analysis + plan were accounted
    assert (out / "live_results.md").is_file()


# ---------------------------------------------------------------- 2. assertion fails
def test_2_assertion_failure_is_fail_and_nonzero(tmp_path, fakes):
    fakes("anthropic", mode="wrong_object")
    code, man, _ = _run(tmp_path, _env())
    g = _gate(man, "claude.smoke")
    assert code == 1 and g["outcome"] == "FAIL"
    assert "identify the photographed object" in g["reason"]


# ---------------------------------------------------------------- 3. missing key
def test_3_missing_key_is_blocked_and_nonzero(tmp_path, monkeypatch):
    for k in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_PROFILE"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setattr("daedelus.providers.anthropic_provider.AnthropicProvider.available",
                        lambda self: (False, "no Anthropic credentials (test)"))
    code, man, _ = _run(tmp_path, _env(LIVE_STAGES="smoke,bench", LIVE_TASKS="AB"))
    assert code == 1
    for gid in ("claude.smoke", "claude.bench.A", "claude.bench.B"):
        g = _gate(man, gid)
        assert g["outcome"] == "BLOCKED" and g["failure_kind"] == "credentials", g
    assert man["summary"]["live_evaluation"] == "BLOCKED"
    assert man["campaign"]["used"]["model_calls"] == 0


# ---------------------------------------------------------------- 4. rate limit
def test_4_rate_limit_is_fail_retriable_not_pass(tmp_path, fakes):
    fakes("anthropic", mode="rate_limit")
    code, man, _ = _run(tmp_path, _env())
    g = _gate(man, "claude.smoke")
    assert code == 1 and g["outcome"] == "FAIL" and g["retriable"] is True
    assert g["failure_kind"] == "provider" and "429" in g["reason"]


# ---------------------------------------------------------------- 5. malformed JSON
def test_5_malformed_json_is_fail_and_still_billed(tmp_path, fakes):
    fakes("anthropic", mode="malformed")
    code, man, _ = _run(tmp_path, _env())
    g = _gate(man, "claude.smoke")
    assert code == 1 and g["outcome"] == "FAIL" and "malformed JSON" in g["reason"]
    used = man["campaign"]["used"]
    assert used["model_calls"] == 1 and used["failed_calls"] == 1  # the unusable reply cost
    assert used["known_cost_usd"] > 0


# ---------------------------------------------------------------- 6. invalid operation
def test_6_invalid_model_operation_fails_safely_without_modifying_the_artifact(tmp_path, fakes):
    fakes("anthropic", mode="invalid_op")
    code, man, _ = _run(tmp_path, _env())
    g = _gate(man, "claude.smoke")
    assert code == 1 and g["outcome"] == "FAIL"
    plan = g["details"]["plan"]
    assert plan["status"] == "failed" and plan["revisions"] == 1  # only the creation revision
    assert "catalogue" in g["reason"] or "executed" in g["reason"]


# ---------------------------------------------------------------- 7. unknown price
def test_7_unknown_price_is_blocked_unless_an_unpriced_cap_is_approved(tmp_path, fakes):
    fakes("gemini", model="gemini-3.8-flash")
    code, man, _ = _run(tmp_path, _env(LIVE_PROVIDERS="gemini"))
    g = _gate(man, "gemini.smoke")
    assert code == 1 and g["outcome"] == "BLOCKED" and g["failure_kind"] == "pricing"
    assert man["campaign"]["used"]["model_calls"] == 0  # nothing spent
    # approved: runs, bounded by the call/token caps, and the cost is visibly incomplete
    code, man, _ = _run(tmp_path, _env(LIVE_PROVIDERS="gemini", LIVE_ALLOW_UNPRICED="true",
                                       LIVE_MAX_MODEL_CALLS="10"))
    assert _gate(man, "gemini.smoke")["outcome"] == "PASS" and code == 0
    c = man["campaign"]
    assert c["cost_complete"] is False and c["used"]["unpriced_calls"] >= 2
    assert c["limits"]["max_model_calls"] == 10


# ---------------------------------------------------------------- 8. malicious task input
@pytest.mark.parametrize("tasks", ["A;rm -rf /", "A B", "A$(id)", "A\nB", "--help", "AA",
                                   "F", "a", "ABCDEA", "A,B", "`whoami`", ""])
def test_8_malicious_task_input_is_rejected(tmp_path, tasks):
    code, man, _ = _run(tmp_path, _env(LIVE_STAGES="bench", LIVE_TASKS=tasks or " "),
                        ex=Forbidden())
    assert code == 2 and man["summary"]["infrastructure"] == "input_rejected"
    assert "tasks" in man["error"]


@pytest.mark.parametrize("field,value", [
    ("LIVE_PROVIDERS", "claude;curl evil"), ("LIVE_PROVIDERS", "openai"),
    ("LIVE_PROVIDERS", "claude,claude"), ("LIVE_STAGES", "smoke && rm"),
    ("LIVE_CONNECTORS", "google $(id)"), ("LIVE_ALLOW_UNPRICED", "yes"),
    ("LIVE_MAX_MODEL_CALLS", "1e9"), ("LIVE_MAX_COST_USD", "1000"),
    ("LIVE_MAX_MODEL_CALLS", "-5")])
def test_8b_other_malformed_inputs_are_rejected(tmp_path, field, value):
    code, man, _ = _run(tmp_path, _env(**{field: value}), ex=Forbidden())
    assert code == 2 and man["summary"]["live_evaluation"] == "NOT RUN"


# ---------------------------------------------------------------- 9. malicious video input
def _resolver(ip):
    def resolve(host, port, *a, **k):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, port))]
    return resolve


@pytest.mark.parametrize("url", [
    "http://www.youtube.com/watch?v=dQw4w9WgXcQ",                 # not https
    "https://evil.example/video.mp4",                              # not an approved host
    "https://www.youtube.com.evil.example/watch?v=x",              # look-alike host
    "https://user:pw@www.youtube.com/watch?v=dQw4w9WgXcQ",          # credentials
    "https://www.youtube.com:8443/watch?v=dQw4w9WgXcQ",            # port
    "https://www.youtube.com/watch?v=x; rm -rf /",                 # whitespace / shell
    "https://www.youtube.com/watch?v=$(id)",                       # shell metacharacters
    "https://www.youtube.com/",                                     # no video id
    "file:///etc/passwd", "javascript:alert(1)",
    "https://upload.wikimedia.org/a.webm\nLIVE_PROVIDERS=gemini",  # header/env smuggling
])
def test_9_malicious_video_input_is_rejected(tmp_path, url):
    code, man, _ = _run(tmp_path, _env(LIVE_VIDEO_URL=url), ex=Forbidden(),
                        resolve=_resolver("93.184.216.34"))
    assert code == 2 and "video_url" in man["error"], man


def test_9b_video_host_resolving_to_a_private_address_is_refused(tmp_path):
    code, man, _ = _run(tmp_path, _env(LIVE_VIDEO_URL="https://upload.wikimedia.org/a.webm"),
                        ex=Forbidden(), resolve=_resolver("169.254.169.254"))
    assert code == 2 and "network policy" in man["error"]


def test_9c_a_valid_video_url_is_accepted(tmp_path):
    env = _env(LIVE_VIDEO_URL="https://www.youtube.com/watch?v=dQw4w9WgXcQ")
    req = lr.request_from_env(env, resolve=_resolver("142.250.80.46"))
    assert req.video_url == "https://www.youtube.com/watch?v=dQw4w9WgXcQ"


# ---------------------------------------------------------------- 10. secrets redacted
def test_10_secrets_in_error_output_are_redacted(tmp_path, fakes, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", SECRET)
    fakes("anthropic", mode="secret_error")
    code, man, out = _run(tmp_path, _env())
    assert code == 1 and _gate(man, "claude.smoke")["outcome"] == "FAIL"
    for p in out.rglob("*"):
        if p.is_file() and p.suffix in (".json", ".md", ".txt", ".log"):
            assert SECRET not in p.read_text(), p
    assert "[REDACTED]" in (out / "live_results.json").read_text()


# ---------------------------------------------------------------- 11. unrequested -> NOT RUN
def test_11_unrequested_provider_and_tasks_are_not_run(tmp_path, fakes):
    fakes("anthropic")
    code, man, _ = _run(tmp_path, _env())
    for g in man["gates"]:
        if g["id"].startswith(("gemini.", "connector.")) or ".bench." in g["id"]:
            assert g["outcome"] == "NOT RUN" and g["requested"] is False, g
    assert code == 0


# ---------------------------------------------------------------- 12. mixed -> FAIL
def test_12_one_provider_passes_another_fails_aggregate_fail(tmp_path, fakes):
    fakes("anthropic")
    fakes("gemini", mode="rate_limit", model="gemini-3.8-flash")
    code, man, _ = _run(tmp_path, _env(LIVE_PROVIDERS="claude,gemini",
                                       LIVE_ALLOW_UNPRICED="true"))
    assert _gate(man, "claude.smoke")["outcome"] == "PASS"
    assert _gate(man, "gemini.smoke")["outcome"] == "FAIL"
    assert code == 1 and man["summary"]["live_evaluation"] == "FAIL"


# ---------------------------------------------------------------- orchestration rules
def test_benchmark_is_not_started_after_a_failed_smoke_test(tmp_path, fakes):
    fakes("anthropic", mode="wrong_object")
    calls = []

    class Ex(lr.Executors):
        def __init__(self):
            super().__init__(bench=lambda *a, **k: calls.append(a) or {})
    code, man, _ = _run(tmp_path, _env(LIVE_STAGES="smoke,bench", LIVE_TASKS="C"), ex=Ex())
    assert calls == [] and code == 1
    g = _gate(man, "claude.bench.C")
    assert g["outcome"] == "BLOCKED" and g["failure_kind"] == "prerequisite"


def test_bench_gate_classification_through_the_real_benchmark(tmp_path, fakes):
    """Task C end to end with the live double: every run must make a live call and pass its
    checks for PASS; the gate reports the per-mode checks."""
    fakes("anthropic")
    code, man, out = _run(tmp_path, _env(LIVE_STAGES="smoke,bench", LIVE_TASKS="C"))
    g = _gate(man, "claude.bench.C")
    assert g["outcome"] in ("PASS", "FAIL"), g
    modes = {r["mode"]: r for r in g["details"]["runs"]}
    assert set(modes) == {"single", "iterative"}
    assert all(r["live_calls"] > 0 for r in modes.values())
    assert code == (0 if g["outcome"] == "PASS" else 1)
    rep = json.loads((out / "claude" / "bench" / "bench_report.json").read_text())
    for r in rep["runs"]:  # V2.1.1: every live run gets a labelled, non-authoritative assessment
        ma = r["model_assessment"]
        assert ma["authoritative"] is False and ma["assessor"] == "anthropic"
        assert "same provider" in ma["independence"] and ma["status"] in ("complete", "failed")
        assert "failure_analysis" in r and r["bench_version"] == "2.1.1"
    # the assessment calls were counted in the campaign
    assert any("assessment" in x["run"] for x in man["campaign"]["runs"])


def test_campaign_budget_exhaustion_blocks_remaining_work(tmp_path, fakes):
    fakes("anthropic")
    code, man, _ = _run(tmp_path, _env(LIVE_STAGES="smoke,bench", LIVE_TASKS="CD",
                                       LIVE_MAX_MODEL_CALLS="2"))
    assert code == 1
    assert man["campaign"]["used"]["model_calls"] <= 2 + 1  # at most one call of overshoot
    gates = [_gate(man, g) for g in ("claude.smoke", "claude.bench.C", "claude.bench.D")]
    assert all(g["outcome"] != "PASS" for g in gates[1:]), gates  # never started or blocked
    assert _gate(man, "claude.smoke")["outcome"] == "BLOCKED"
    assert _gate(man, "claude.smoke")["failure_kind"] == "budget"
    assert "budget" in _gate(man, "claude.smoke")["reason"]


def test_validate_only_touches_nothing(tmp_path):
    out = tmp_path / "req"
    code = lr.main(["--from-env", "--validate-only", "--out", str(out)],
                   env=_env(LIVE_PROVIDERS="claude,gemini", LIVE_STAGES="smoke,bench",
                            LIVE_TASKS="DCBA"), ex=Forbidden())
    req = json.loads((out / "live_request.json").read_text())
    assert code == 0 and req["tasks"] == "ABCD" and req["providers"] == ["claude", "gemini"]


# ---------------------------------------------------------------- the workflow file itself
def test_live_workflow_has_no_failure_masking_or_input_interpolation():
    text = (ROOT / ".github" / "workflows" / "live.yml").read_text()
    wf = yaml.safe_load(text)
    on = wf.get("on", wf.get(True))
    assert set(on) == {"workflow_dispatch"}, on  # manual only: no push / pull_request(_target)
    assert wf["permissions"] == {"contents": "read"}
    assert wf["concurrency"]["cancel-in-progress"] is False
    assert "|| true" not in text and "continue-on-error" not in text
    for name, job in wf["jobs"].items():
        assert job.get("timeout-minutes"), name
        for step in job["steps"]:
            run = step.get("run", "")
            assert "${{" not in run, (name, step)  # inputs only via env, never shell source
            env = step.get("env") or {}
            if any("secrets." in str(v) for v in env.values()):
                assert "live-run --from-env --out" in run, step  # secrets only for the runner
    assert "secrets." not in json.dumps(wf["jobs"]["validate"])
    assert wf["jobs"]["live"]["environment"] == "daedelus-live"
    assert wf["jobs"]["live"]["needs"] == "validate"
    up = [s for s in wf["jobs"]["live"]["steps"] if "upload-artifact" in str(s.get("uses"))]
    assert up and up[0]["if"] == "always()" and up[0]["with"]["retention-days"] <= 14
