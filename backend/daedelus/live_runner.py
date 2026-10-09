"""Authoritative live-verification runner (V2.1.1).

    daedelus live-run --from-env --out evidence-live

The GitHub workflow (``.github/workflows/live.yml``) passes every dispatch input through
environment variables and calls this entry point; nothing user-supplied is ever interpolated
into shell source. This module

1. validates the request (providers, stages, tasks, connectors, video URL, budgets) against
   fixed allowlists and refuses anything else before any credential is used or money is spent;
2. runs the requested gates in a safe order (smoke tests before the benchmark, which is skipped
   for a provider whose smoke test did not pass);
3. classifies every known gate as PASS, FAIL, BLOCKED or NOT RUN;
4. writes a machine-readable manifest (``live_results.json``) and a summary, scrubs secrets
   from everything it wrote, and
5. exits 0 only when every requested gate passed. A missing credential, an unavailable video, an
   exhausted budget or a failed assertion all exit nonzero: nothing that did not run can ever
   look green.

Outcomes:
* PASS: the live operation ran and met all its assertions;
* FAIL: it ran but violated an assertion, hit an unexpected error or produced invalid output
  (``failure_kind`` says which: ``evaluation``, ``provider`` or ``infrastructure``);
* BLOCKED: a required credential, account, video, price or budget authorisation was missing;
* NOT RUN: not requested.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import traceback
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlparse

SCHEMA = "daedelus.live-results/1"
PASS, FAIL, BLOCKED, NOT_RUN = "PASS", "FAIL", "BLOCKED", "NOT RUN"

PROVIDERS = ("claude", "gemini")
PROVIDER_IMPL = {"claude": "anthropic", "gemini": "gemini"}
CONNECTORS = ("google", "msgraph")
STAGES = ("smoke", "bench")
TASK_IDS = "ABCDE"
# hosts whose videos the providers may receive: YouTube (Gemini reads public YouTube URLs
# itself) and Wikimedia's upload host (freely licensed media); extend with
# DAEDELUS_LIVE_VIDEO_HOSTS (comma-separated) for other approved sources
DEFAULT_VIDEO_HOSTS = ("youtube.com", "youtu.be", "upload.wikimedia.org")
MAX_URL = 2048
# campaign limits accepted from a dispatch (hard upper bounds; the defaults are lower)
CAMPAIGN_BOUNDS = {"max_model_calls": (1, 200), "max_cost_usd": (0.01, 25.0),
                   "max_tokens": (1_000, 5_000_000), "max_seconds": (60, 5400)}
SMOKE_BUDGET = {"max_model_calls": 4, "max_cost_usd": 0.5, "max_tokens": 120_000,
                "max_seconds": 300, "max_iterations": 0, "call_timeout_s": 120,
                "max_retries": 1}


class InputError(ValueError):
    """A dispatch input that is not exactly one of the accepted forms."""


# ---------------------------------------------------------------------------- input validation
_TOKEN = re.compile(r"[a-z0-9]+")


def parse_choice_list(raw: str | None, allowed: tuple[str, ...], name: str) -> tuple[str, ...]:
    """``"a,b"`` -> ("a", "b"); ``""``/``"none"`` -> (); ``"all"`` -> allowed. Anything that is
    not a comma-separated list of known lower-case identifiers is refused."""
    raw = (raw or "").strip()
    if raw in ("", "none"):
        return ()
    if raw == "all":
        return allowed
    if len(raw) > 64:
        raise InputError(f"{name}: too long")
    out: list[str] = []
    for tok in raw.split(","):
        tok = tok.strip()
        if not _TOKEN.fullmatch(tok):
            raise InputError(f"{name}: invalid entry {tok!r} (allowed: {', '.join(allowed)})")
        if tok not in allowed:
            raise InputError(f"{name}: unknown entry {tok!r} (allowed: {', '.join(allowed)})")
        if tok in out:
            raise InputError(f"{name}: duplicate entry {tok!r}")
        out.append(tok)
    return tuple(t for t in allowed if t in out)  # canonical order


def parse_tasks(raw: str | None) -> str:
    """Benchmark task selection: one to five distinct letters from A-E, nothing else."""
    raw = (raw or "").strip()
    if not re.fullmatch(r"[A-E]{1,5}", raw):
        raise InputError("tasks: must be 1-5 of the letters A B C D E with no other "
                         f"characters (got {raw[:40]!r})")
    if len(set(raw)) != len(raw):
        raise InputError("tasks: duplicate task letters")
    return "".join(t for t in TASK_IDS if t in raw)


def parse_bool(raw: str | None, name: str) -> bool:
    raw = (raw or "false").strip().lower()
    if raw not in ("true", "false"):
        raise InputError(f"{name}: must be true or false")
    return raw == "true"


def parse_number(raw: str | None, name: str, default: float | int) -> float | int:
    if raw is None or raw.strip() == "":
        return default
    raw = raw.strip()
    if not re.fullmatch(r"\d{1,9}(\.\d{1,4})?", raw):
        raise InputError(f"{name}: not a plain non-negative number")
    v: float | int = float(raw) if "." in raw else int(raw)
    lo, hi = CAMPAIGN_BOUNDS[name]
    if not lo <= v <= hi:
        raise InputError(f"{name}: {v} outside the accepted range {lo}..{hi}")
    return v


def video_hosts(env: dict[str, str]) -> tuple[str, ...]:
    extra = [h.strip().lower() for h in (env.get("DAEDELUS_LIVE_VIDEO_HOSTS") or "").split(",")
             if h.strip()]
    for h in extra:
        if not re.fullmatch(r"[a-z0-9]([a-z0-9\-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9\-]*[a-z0-9])?)+",
                            h):
            raise InputError(f"DAEDELUS_LIVE_VIDEO_HOSTS: invalid host {h!r}")
    return DEFAULT_VIDEO_HOSTS + tuple(extra)


def validate_video_url(raw: str | None, env: dict[str, str], resolve=None) -> str | None:
    """A supported HTTPS video URL on an approved host, or None when none was given."""
    raw = (raw or "").strip()
    if not raw:
        return None
    if len(raw) > MAX_URL:
        raise InputError("video_url: too long")
    if any(ord(c) < 0x21 or ord(c) > 0x7E for c in raw):
        raise InputError("video_url: whitespace, control or non-ASCII characters are not allowed")
    if any(c in raw for c in "`$\\\"'<>|;{}"):
        raise InputError("video_url: shell or markup metacharacters are not allowed")
    u = urlparse(raw)
    if u.scheme != "https":
        raise InputError("video_url: only https:// URLs are accepted")
    if u.username or u.password or "@" in u.netloc:
        raise InputError("video_url: credentials in URLs are not allowed")
    host = (u.hostname or "").rstrip(".").lower()
    if not host:
        raise InputError("video_url: no host")
    if u.port not in (None, 443):
        raise InputError("video_url: only the default HTTPS port is accepted")
    allowed = video_hosts(env)
    if not any(host == h or host.endswith("." + h) for h in allowed):
        raise InputError(f"video_url: host {host!r} is not an approved video source "
                         f"({', '.join(allowed)})")
    if host.endswith(("youtube.com", "youtu.be")):
        from .ingest import _youtube_id
        if not _youtube_id(raw):
            raise InputError("video_url: not a YouTube video URL (no video id)")
    from . import netsafe
    try:  # the same SSRF policy as every other fetch (no private/metadata addresses)
        netsafe.check_url(raw, **({"resolve": resolve} if resolve else {}))
    except netsafe.FetchRefused as exc:
        raise InputError(f"video_url: refused by the network policy: {exc}") from None
    except OSError as exc:
        raise InputError(f"video_url: host does not resolve: {exc}") from None
    return raw


@dataclass
class Request:
    providers: tuple[str, ...] = ()
    stages: tuple[str, ...] = ()
    tasks: str = ""
    video_url: str | None = None
    connectors: tuple[str, ...] = ()
    allow_unpriced: bool = False
    campaign: dict[str, Any] = field(default_factory=dict)

    def gates(self) -> list[str]:
        g = []
        for p in self.providers:
            if "smoke" in self.stages:
                g.append(f"{p}.smoke")
            if "bench" in self.stages:
                g += [f"{p}.bench.{t}" for t in self.tasks]
        g += [f"connector.{c}" for c in self.connectors]
        return g


def request_from_env(env: dict[str, str], resolve=None) -> Request:
    from .budget import CAMPAIGN_DEFAULTS
    providers = parse_choice_list(env.get("LIVE_PROVIDERS"), PROVIDERS, "providers")
    stages = parse_choice_list(env.get("LIVE_STAGES") or "smoke", STAGES, "stages")
    tasks = parse_tasks(env.get("LIVE_TASKS") or "ABCD") if "bench" in stages else ""
    connectors = parse_choice_list(env.get("LIVE_CONNECTORS"), CONNECTORS, "connectors")
    video = validate_video_url(env.get("LIVE_VIDEO_URL"), env, resolve)
    if providers and not stages:
        raise InputError("stages: at least one of smoke, bench is required with providers")
    if not providers and not connectors:
        raise InputError("nothing requested: set providers and/or connectors")
    camp = {k: parse_number(env.get("LIVE_" + k.upper()), k, CAMPAIGN_DEFAULTS[k])
            for k in CAMPAIGN_DEFAULTS}
    return Request(providers=providers, stages=stages, tasks=tasks, video_url=video,
                   connectors=connectors,
                   allow_unpriced=parse_bool(env.get("LIVE_ALLOW_UNPRICED"), "allow_unpriced"),
                   campaign=camp)


def all_gates() -> list[str]:
    return Request(providers=PROVIDERS, stages=STAGES, tasks=TASK_IDS,
                   connectors=CONNECTORS).gates()


# ---------------------------------------------------------------------------- results
@dataclass
class Gate:
    id: str
    requested: bool
    outcome: str = NOT_RUN
    reason: str = ""
    failure_kind: str | None = None  # evaluation | provider | infrastructure
    retriable: bool = False
    seconds: float | None = None
    evidence: str | None = None
    details: dict[str, Any] = field(default_factory=dict)


def classify_exception(exc: BaseException) -> tuple[str, str, bool, str]:
    """(outcome, failure_kind, retriable, reason) for an exception raised by a live gate."""
    from .budget import BudgetExceeded
    from .providers.base import (NotSupported, ProviderError, ProviderOverBudget,
                                 ProviderRateLimited, ProviderTimeout)
    msg = f"{type(exc).__name__}: {exc}"
    if isinstance(exc, (ProviderOverBudget, BudgetExceeded)):
        return BLOCKED, "budget", False, msg
    if isinstance(exc, ProviderRateLimited):
        return FAIL, "provider", True, msg
    if isinstance(exc, ProviderTimeout):
        return FAIL, "provider", True, msg
    if isinstance(exc, NotSupported):
        return FAIL, "provider", False, msg
    if isinstance(exc, ProviderError):
        return FAIL, "provider", False, msg
    return FAIL, "infrastructure", False, msg


def _price_known(impl: str) -> tuple[str | None, bool]:
    from .semantic.service import PRICES
    model = None
    if impl == "anthropic":
        from .providers import anthropic_provider as m
        model = m.DEFAULT_MODEL
    elif impl == "gemini":
        from .providers import gemini_provider as m
        model = m.DEFAULT_MODEL
    return model, bool(model and model in PRICES)


def cost_bounds(req: Request) -> dict[str, Any]:
    """What the dollar cap does and does not guarantee, per requested provider."""
    from .budget import DEFAULTS
    from .semantic.service import PRICES_AS_OF, worst_case_call_usd
    out: dict[str, Any] = {"prices_as_of": PRICES_AS_OF}
    attempts = 1 + int(DEFAULTS["max_retries"])
    for p in req.providers:
        impl = PROVIDER_IMPL[p]
        model, priced = _price_known(impl)
        if impl == "anthropic":
            from .providers.anthropic_provider import MAX_TOKENS
        else:
            from .providers.gemini_provider import MAX_TOKENS
        per_call = worst_case_call_usd(model, MAX_TOKENS, ASSUMED_MAX_INPUT_TOKENS, attempts)
        cap = float(req.campaign.get("max_cost_usd") or 0)
        out[p] = {"model": model, "price_known": priced, "max_output_tokens": MAX_TOKENS,
                  "worst_case_one_call_usd": per_call,
                  "worst_case_campaign_usd": round(cap + per_call, 4) if per_call else None,
                  "note": ("known-price cap: spending stops before a call once the cap is "
                           "reached; the last call can overshoot by at most one call "
                           f"(<= ${per_call} with {attempts} attempts at {MAX_TOKENS} output "
                           f"and an assumed {ASSUMED_MAX_INPUT_TOKENS} input tokens)")
                  if priced else
                  ("price unknown: NO monetary guarantee. Cost is bounded only by the call cap "
                   f"({req.campaign.get('max_model_calls')}) and token cap "
                   f"({req.campaign.get('max_tokens')})")}
    return out


# a request carries at most 6 images of <= 1024 px plus text; generous upper estimate
ASSUMED_MAX_INPUT_TOKENS = 60_000


# ---------------------------------------------------------------------------- gates
def smoke_gate(provider: str, out: Path, campaign) -> tuple[str, str, dict[str, Any]]:
    """Smallest real calls: image understanding (structured analysis) and one schema-valid
    plan executed against a real native artifact."""
    from . import ingest
    from .adapters import get_adapter
    from .artifacts import create_artifact
    from .bench_v21 import ASSETS, POSTER
    from .budget import ExecutionBudget, use_budget
    from .engine import Engine
    from .models import SourceBinding, TargetSelector
    from .providers import get_provider
    from .scenarios_v11 import _wf
    from .semantic import service as semsvc
    from .store import Workspace

    impl = PROVIDER_IMPL[provider]
    checks: list[dict[str, Any]] = []

    def check(name, ok, detail=""):
        checks.append({"name": name, "ok": bool(ok), "detail": str(detail)[:400]})
        return bool(ok)

    det: dict[str, Any] = {"checks": checks, "provider": impl}
    ws = Workspace(out / "workspace")
    _, st = ws.create_project(f"smoke {provider}")
    budget = ExecutionBudget.from_config(campaign.run_limits(SMOKE_BUDGET))
    try:
        with use_budget(budget):
            prov = get_provider(impl)
            det["pathways"] = sorted(prov.pathways)
            src = ingest.register_file(st, ASSETS / "coffee_photo.png", name="Cafe photograph")
            ana = semsvc.analyze_source(st, src, impl)
            text = " ".join(o.text.lower() for o in ana.observations)
            u = ana.usage
            det["analysis"] = {"model": ana.model, "pathway": ana.pathway, "status": ana.status,
                               "observations": [o.text for o in ana.observations][:12],
                               "usage": u.model_dump()}
            check("image analysed by the live model (not recorded, not heuristic)",
                  ana.provider == impl and not getattr(ana, "recorded", False)
                  and ana.pathway == "image", (ana.provider, ana.pathway))
            check("structured observations returned", ana.status in ("complete", "partial")
                  and ana.observations, ana.status)
            check("the observations identify the photographed object",
                  any(w in text for w in ("cup", "mug", "coffee", "espresso", "saucer")),
                  text[:200])
            check("the serving model is reported", ana.model, ana.model)
            check("usage is reported (input and output tokens)", (u.input_tokens or 0) > 0
                  and (u.output_tokens or 0) > 0, u.model_dump())
            check("a request id is reported", u.request_id, u.request_id)
            art, _ = create_artifact(st, name="Smoke poster", adapter="layered2d",
                                     template="layers", params={"width": 160, "height": 200,
                                                                "root_id": "poster",
                                                                "layers": POSTER})
            note = ingest.register_text(st, "Art direction",
                                        "Make the subject layer a warm orange colour.")
            st.save_binding(SourceBinding(source_id=note.id, role="guideline",
                                          aspects=["color"],
                                          target=TargetSelector(scope="component",
                                                                artifact_id=art.id,
                                                                component_id="subject")))
            wf = _wf(st, "smoke plan", art.id, {
                "instructions": "Apply the art direction to the bound layer.",
                "provider": impl, "understand": False, "fan_out": True})
            # the execution runs under its own budget: cap it by what the smoke test has left
            left = {k: v for k, v in budget.limits.items()}
            left["max_model_calls"] = int(left["max_model_calls"]) - budget.calls
            left["max_cost_usd"] = float(left["max_cost_usd"]) - budget.cost_usd
            wf.parameters = {"budget": left}
            st.save_workflow(wf)
            ex = Engine(st).execute(wf.id)
            det["plan_budget"] = ex.budget
            nr = ex.run("agent")
            plans = [x.plan for x in nr.units if x.plan]
            ops = [o for p in plans for o in (p.get("operations") or [])]
            allowed = {o.name for o in get_adapter("layered2d").info().operations}
            det["plan"] = {"status": ex.status.value, "error": ex.error, "plans": plans,
                           "revisions": len(st.list_revisions(art.id))}
            check("a live plan was produced (not recorded, not deterministic)", plans and all(
                p.get("provider") == impl and not p.get("recorded") for p in plans),
                [(p.get("provider"), p.get("recorded")) for p in plans])
            check("every planned operation is in the adapter catalogue", ops and all(
                o.get("op") in allowed for o in ops), [o.get("op") for o in ops])
            check("operations target only the bound component", ops and all(
                o.get("component_id") in (None, "subject") for o in ops),
                [o.get("component_id") for o in ops])
            check("the plan executed against the native artifact", ex.status.value ==
                  "succeeded" and len(st.list_revisions(art.id)) >= 2, ex.error)
    finally:
        det["budget"] = budget.snapshot()
        campaign.absorb(f"{provider}.smoke analysis", det["budget"])
        if det.get("plan_budget"):
            campaign.absorb(f"{provider}.smoke plan", det["plan_budget"])
        ws.close() if hasattr(ws, "close") else None
    calls = det["budget"]["used"]["model_calls"] + \
        ((det.get("plan_budget") or {}).get("used") or {}).get("model_calls", 0)
    check("usage accounted in the budget (analysis + plan)", calls >= 2, calls)
    refused = det["budget"].get("refusals", []) + \
        ((det.get("plan_budget") or {}).get("refusals") or [])
    if not all(c["ok"] for c in checks):
        if refused:  # the approved budget stopped the test before it could finish
            return BLOCKED, "budget: " + refused[0], det
        return FAIL, "; ".join(c["name"] for c in checks if not c["ok"]), det
    return PASS, "all smoke assertions met", det


def bench_gates(provider: str, tasks: str, video: str | None, out: Path,
                campaign) -> dict[str, tuple[str, str, dict[str, Any], str | None]]:
    """Runs the creative benchmark once for all requested tasks; one gate per task."""
    from . import bench_v21
    impl = PROVIDER_IMPL[provider]
    rep = bench_v21.run(out, provider=impl, tasks=tasks, video=video, campaign=campaign)
    res: dict[str, tuple[str, str, dict[str, Any], str | None]] = {}
    if rep.get("status") == "blocked":
        for t in tasks:
            res[t] = (BLOCKED, rep.get("blocked_by", "provider unavailable"), {}, None)
        return res
    for t in tasks:
        runs = [r for r in rep["runs"] if r["task"] == t]
        det = {"runs": [{k: r.get(k) for k in ("mode", "verification", "passed", "blocked_by",
                                               "live_calls", "seconds", "error", "exception")}
                        | {"checks": r.get("checks")} for r in runs]}
        blocked = [r for r in runs if r.get("blocked_by")]
        if blocked:
            res[t] = (BLOCKED, blocked[0]["blocked_by"], det, None)
            continue
        crashed = [r for r in runs if r.get("exception")]
        if crashed:
            res[t] = (FAIL, crashed[0]["exception"], det, "infrastructure")
            continue
        not_live = [r for r in runs if not r.get("live_calls")]
        if not_live:  # a live gate that made no model call proves nothing
            res[t] = (FAIL, f"no live model call happened in {not_live[0]['mode']} mode",
                      det, "provider")
            continue
        recorded = [r for r in runs for p in r.get("plans") or [] if p.get("recorded")]
        if recorded:
            res[t] = (FAIL, "a recorded (replayed) response was used in a live gate", det,
                      "infrastructure")
            continue
        failed = [r for r in runs if not r.get("passed")]
        if failed:
            names = [c["name"] for r in failed for c in r["checks"] if not c["ok"]]
            prov_err = [r for r in failed if "Provider" in str(r.get("error") or "")]
            res[t] = (FAIL, "; ".join(dict.fromkeys(names)) or "checks failed", det,
                      "provider" if prov_err else "evaluation")
            continue
        res[t] = (PASS, "all checks met in single and iterative modes", det, None)
    return res


def connector_gate(connector: str, out: Path) -> tuple[str, str, dict[str, Any]]:
    from . import connectors_live
    os.environ["DAEDELUS_LIVE_CONNECTORS"] = "1"  # the dispatch requested this connector
    rep = connectors_live.run(connector, out)
    det = {k: rep.get(k) for k in ("status", "checks", "cleanup", "cleanup_errors",
                                   "blocked_by")}
    if rep.get("status") == "blocked":
        return BLOCKED, rep.get("blocked_by", "credentials missing"), det
    cleanup = rep.get("cleanup") or {}
    leftover = set(cleanup.get("created") or []) - set(cleanup.get("deleted_and_verified") or [])
    if rep.get("status") != "live":
        bad = [c["name"] for c in rep.get("checks", []) if not c["ok"]]
        return FAIL, "; ".join(bad) or "connector run failed", det
    if leftover:
        return FAIL, f"test files not verified deleted: {sorted(leftover)}", det
    return PASS, "all connector assertions met, created files deleted", det


# ---------------------------------------------------------------------------- orchestration
@dataclass
class Executors:
    smoke: Callable[..., tuple[str, str, dict[str, Any]]] = smoke_gate
    bench: Callable[..., dict[str, Any]] = bench_gates
    connector: Callable[..., tuple[str, str, dict[str, Any]]] = connector_gate


def run(req: Request, out: Path, ex: Executors | None = None) -> dict[str, Any]:
    from .budget import CampaignBudget
    from .providers import providers as registry
    ex = ex or Executors()
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    campaign = CampaignBudget.from_config(req.campaign)
    requested = set(req.gates())
    gates = {g: Gate(g, g in requested) for g in all_gates()}
    t_all = time.time()

    def finish(g: Gate, outcome, reason, details=None, kind=None, retriable=False, t0=None,
               evidence=None):
        g.outcome, g.reason, g.details = outcome, reason, details or {}
        g.failure_kind = kind if outcome == FAIL else (kind if outcome == BLOCKED else None)
        g.retriable = retriable
        g.seconds = round(time.time() - t0, 2) if t0 else None
        g.evidence = evidence

    for p in req.providers:
        impl = PROVIDER_IMPL[p]
        prov = registry()[impl]
        ok, why = prov.available()
        model, priced = _price_known(impl)
        pg = [g for g in requested if g.startswith(p + ".")]
        if not ok:
            for g in pg:
                finish(gates[g], BLOCKED, f"credentials unavailable: {why}", kind="credentials")
            continue
        if not priced and not req.allow_unpriced:
            for g in pg:
                finish(gates[g], BLOCKED,
                       f"no authoritative price for model {model!r}: the dollar cap cannot be "
                       "enforced. Approve an unpriced run (allow_unpriced=true) bounded by the "
                       "call and token caps, or configure a priced model.", kind="pricing")
            continue
        smoke_ok = True
        if f"{p}.smoke" in requested:
            g, t0 = gates[f"{p}.smoke"], time.time()
            reason = campaign.exhausted()
            if reason:
                finish(g, BLOCKED, reason, kind="budget")
            else:
                try:
                    o, r, d = ex.smoke(p, out / p / "smoke", campaign)
                    finish(g, o, r, d, kind={FAIL: "provider", BLOCKED: "budget"}.get(o),
                           t0=t0, evidence=f"{p}/smoke")
                except Exception as exc:
                    o, kind, retr, r = classify_exception(exc)
                    finish(g, o, r, {"traceback": traceback.format_exc()[-3000:]}, kind, retr,
                           t0)
            smoke_ok = g.outcome == PASS
        bench = [g for g in pg if ".bench." in g]
        if not bench:
            continue
        if not smoke_ok:
            for g in bench:
                finish(gates[g], BLOCKED, f"prerequisite {p}.smoke did not pass; the benchmark "
                                          "was not started", kind="prerequisite")
            continue
        tasks = "".join(g.rsplit(".", 1)[1] for g in sorted(bench))
        t0 = time.time()
        try:
            res = ex.bench(p, tasks, req.video_url, out / p / "bench", campaign)
            for t in tasks:
                o, r, d, kind = res.get(t, (FAIL, "no result recorded", {}, "infrastructure"))
                finish(gates[f"{p}.bench.{t}"], o, r, d, kind, t0=t0, evidence=f"{p}/bench")
        except Exception as exc:
            o, kind, retr, r = classify_exception(exc)
            for t in tasks:
                finish(gates[f"{p}.bench.{t}"], o, r,
                       {"traceback": traceback.format_exc()[-3000:]}, kind, retr, t0)

    for c in req.connectors:
        g, t0 = gates[f"connector.{c}"], time.time()
        try:
            o, r, d = ex.connector(c, out / "connectors" / c)
            finish(g, o, r, d, kind="evaluation" if o == FAIL else None, t0=t0,
                   evidence=f"connectors/{c}")
        except Exception as exc:
            o, kind, retr, r = classify_exception(exc)
            finish(g, o, r, {"traceback": traceback.format_exc()[-3000:]}, kind, retr, t0)

    req_gates = [gates[g] for g in req.gates()]
    counts = {k: sum(1 for g in req_gates if g.outcome == k) for k in (PASS, FAIL, BLOCKED)}
    live_eval = (PASS if req_gates and counts[PASS] == len(req_gates) else
                 FAIL if counts[FAIL] else BLOCKED if counts[BLOCKED] else NOT_RUN)
    manifest = {
        "schema": SCHEMA,
        "run": {"commit": os.environ.get("GITHUB_SHA"), "ref": os.environ.get("GITHUB_REF"),
                "workflow_run": (f"{os.environ.get('GITHUB_SERVER_URL', 'https://github.com')}/"
                                 f"{os.environ['GITHUB_REPOSITORY']}/actions/runs/"
                                 f"{os.environ['GITHUB_RUN_ID']}")
                if os.environ.get("GITHUB_RUN_ID") and os.environ.get("GITHUB_REPOSITORY")
                else None,
                "seconds": round(time.time() - t_all, 1)},
        "request": asdict(req),
        "gates": [asdict(gates[g]) for g in all_gates()],
        "campaign": campaign.snapshot(),
        "cost_bounds": cost_bounds(req),
        "summary": {
            # the runner itself completed and wrote this manifest; says nothing about the
            # live results below
            "infrastructure": "completed",
            "live_evaluation": live_eval,
            "requested": len(req_gates), **{k.lower().replace(" ", "_"): v
                                             for k, v in counts.items()},
            "exit_code": 0 if live_eval == PASS else 1}}
    _write(out, manifest)
    return manifest


def _write(out: Path, manifest: dict[str, Any]) -> None:
    from .redact import scrub_text, scrub_tree
    (out / "live_results.json").write_text(json.dumps(manifest, indent=1, default=str))
    s = manifest["summary"]
    lines = ["# Live verification results", "",
             f"* Infrastructure: **{s['infrastructure']}** (the runner completed; this says "
             "nothing about model quality)",
             f"* Live evaluation: **{s['live_evaluation']}** ({s['requested']} requested: "
             f"{s.get('pass', 0)} pass, {s.get('fail', 0)} fail, {s.get('blocked', 0)} "
             "blocked)",
             f"* Campaign usage: {manifest['campaign']['used']}", "",
             "| Gate | Outcome | Kind | Reason |", "|---|---|---|---|"]
    for g in manifest["gates"]:
        if g["requested"]:
            lines.append(f"| {g['id']} | {g['outcome']} | {g['failure_kind'] or ''} | "
                         f"{str(g['reason']).replace('|', '/')[:200]} |")
    not_run = [g["id"] for g in manifest["gates"] if not g["requested"]]
    if not_run:
        lines += ["", "NOT RUN (not requested): " + ", ".join(not_run)]
    (out / "live_results.md").write_text("\n".join(lines) + "\n")
    scrub_tree(out)  # nothing secret leaves the run, whichever file it got into
    step = os.environ.get("GITHUB_STEP_SUMMARY")
    if step:
        with open(step, "a", encoding="utf-8") as f:
            f.write(scrub_text((out / "live_results.md").read_text()) + "\n")


def main(argv: list[str] | None = None, env: dict[str, str] | None = None,
         ex: Executors | None = None, resolve=None) -> int:
    ap = argparse.ArgumentParser(prog="daedelus live-run")
    ap.add_argument("--from-env", action="store_true", required=True,
                    help="read the request from LIVE_* environment variables (the only mode)")
    ap.add_argument("--out", required=True)
    ap.add_argument("--validate-only", action="store_true",
                    help="check the request and print it; no credentials, no calls")
    a = ap.parse_args(argv)
    env = dict(os.environ if env is None else env)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    try:
        req = request_from_env(env, resolve)
    except InputError as exc:
        # refused before any credential was touched or any call was made
        from .redact import scrub_text
        rej = {"schema": SCHEMA, "summary": {"infrastructure": "input_rejected",
                                             "live_evaluation": NOT_RUN, "exit_code": 2},
               "error": scrub_text(str(exc))}
        (out / "live_results.json").write_text(json.dumps(rej, indent=1))
        print(f"input rejected: {rej['error']}", file=sys.stderr)
        return 2
    if a.validate_only:
        (out / "live_request.json").write_text(json.dumps(asdict(req), indent=1))
        print(json.dumps({"validated": asdict(req), "gates": req.gates()}))
        return 0
    manifest = run(req, out, ex)
    print(json.dumps(manifest["summary"]))
    return int(manifest["summary"]["exit_code"])


if __name__ == "__main__":
    sys.exit(main())
