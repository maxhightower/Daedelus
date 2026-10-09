"""Replay provider: serves fixture responses for reproducible, offline CI.

A fixture holds the structured JSON a model-backed provider returned (or, for
``origin: "synthetic"``, a hand-written response in the same format). Replayed responses go
through exactly the parsing and validation code used for live responses, so the integration
is exercised - but every result is flagged ``recorded`` and carries its origin. A replayed
result proves the integration code handles that response; it does not prove a live model
would produce it.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from ..semantic.models import Evaluation, SourceAnalysis, Usage
from . import llm_common as lc
from .anthropic_provider import _to_plan
from .base import (AnalyzeRequest, EvaluateRequest, Plan, PlanRequest, Provider, ProviderError,
                   ReviseRequest)

DEFAULT_DIR = Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "providers"


def fixture_dirs() -> list[Path]:
    env = os.environ.get("DAEDELUS_FIXTURES")
    dirs = [Path(p) for p in env.split(os.pathsep)] if env else []
    return dirs + [DEFAULT_DIR]


def load_fixtures() -> list[dict[str, Any]]:
    out = []
    for d in fixture_dirs():
        if d.is_dir():
            for f in sorted(d.rglob("*.json")):
                try:
                    data = json.loads(f.read_text(encoding="utf-8"))
                except ValueError:
                    continue
                if isinstance(data, dict) and "contract" in data and "response" in data:
                    data["_file"] = str(f)
                    out.append(data)
    return out


def _matches(fix: dict[str, Any], key: dict[str, Any]) -> bool:
    m = fix.get("match", {})
    return all(key.get(k) == v for k, v in m.items())


class ReplayProvider(Provider):
    name = "replay"
    description = ("Replays recorded or synthetic provider responses (offline, reproducible). "
                   "Never a live model call.")
    contracts = ("analyze", "plan", "evaluate", "revise")
    pathways = ("any (from fixtures)",)

    def available(self) -> tuple[bool, str]:
        n = len(load_fixtures())
        return (n > 0, f"{n} fixture(s)") if n else (False, "no provider fixtures found")

    def _find(self, contract: str, req: Any) -> dict[str, Any]:
        key = lc.match_key(contract, req)
        for fx in load_fixtures():
            if fx["contract"] == contract and _matches(fx, key):
                return fx
        raise ProviderError(f"no {contract} fixture matches {key}")

    @staticmethod
    def _usage(fx: dict[str, Any]) -> Usage:
        u = fx.get("usage") or {}
        return Usage(**{k: u.get(k) for k in ("input_tokens", "output_tokens", "cost_usd")},
                     calls=0, latency_ms=0.0)

    def analyze(self, req: AnalyzeRequest) -> SourceAnalysis:
        fx = self._find("analyze", req)
        ana = lc.to_analysis(fx["response"], req, provider=f"replay:{fx.get('provider')}",
                             model=fx.get("model"), pathway=fx.get("pathway", "fixture"),
                             usage=self._usage(fx))
        ana.recorded, ana.fixture_origin = True, fx.get("origin", "synthetic")
        return ana

    def plan(self, req: PlanRequest) -> Plan:
        fx = self._find("plan", req)
        p = _to_plan(fx["response"], f"replay:{fx.get('provider')}", fx.get("model"),
                     self._usage(fx))
        p.recorded = True
        p.notes.append(f"replayed {fx.get('origin', 'synthetic')} fixture "
                       f"{Path(fx['_file']).name}")
        return p

    def evaluate(self, req: EvaluateRequest) -> Evaluation:
        fx = self._find("evaluate", req)
        ev = lc.to_evaluation(fx["response"], req, provider=f"replay:{fx.get('provider')}",
                              model=fx.get("model"), usage=self._usage(fx))
        ev.recorded = True
        return ev

    def revise(self, req: ReviseRequest) -> Plan:
        fx = self._find("revise", req)
        p = _to_plan(fx["response"], f"replay:{fx.get('provider')}", fx.get("model"),
                     self._usage(fx))
        p.recorded = True
        p.notes.append(f"replayed {fx.get('origin', 'synthetic')} revision fixture")
        return p
