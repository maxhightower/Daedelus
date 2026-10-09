"""Planner providers registry."""

from __future__ import annotations

from typing import Any

from .base import Plan, PlanRequest, Provider, ProviderError, ProviderOverBudget


def providers() -> dict[str, Provider]:
    from .anthropic_provider import AnthropicProvider
    from .gemini_provider import GeminiProvider
    from .heuristic import HeuristicProvider
    from .replay import ReplayProvider

    return {p.name: p for p in (HeuristicProvider(), AnthropicProvider(), GeminiProvider(),
                                ReplayProvider())}


class BudgetedProvider(Provider):
    """Applies the active execution budget (``budget.py``) to every model contract: the call
    is refused *before* it is made when a limit is reached, and its usage is charged after."""

    def __init__(self, inner: Provider, budget):
        self.inner, self.budget = inner, budget
        self.name, self.description = inner.name, inner.description
        self.contracts, self.pathways, self.live = inner.contracts, inner.pathways, inner.live

    def available(self):
        return self.inner.available()

    def __getattr__(self, item):  # non-contract helpers (e.g. generate_image) pass through
        return getattr(self.inner, item)

    def describe(self):
        return self.inner.describe()

    def ops_for(self, req):
        return self.inner.ops_for(req)

    def _call(self, contract: str, req: Any):
        from ..budget import BudgetExceeded
        if self.inner.live:
            try:
                self.budget.before_call(self.inner.name, contract)
            except BudgetExceeded as exc:
                raise ProviderOverBudget(str(exc)) from None
        out = getattr(self.inner, contract)(req)
        usage = getattr(out, "usage", None)
        if hasattr(usage, "model_dump"):
            usage = usage.model_dump()
        usage = dict(usage or {})
        if not usage.get("model"):
            usage["model"] = getattr(out, "model", None)
        if self.inner.live or usage.get("calls"):
            self.budget.charge(self.inner.name, usage, live=self.inner.live)
        return out

    def analyze(self, req):
        return self._call("analyze", req)

    def plan(self, req):
        return self._call("plan", req)

    def evaluate(self, req):
        return self._call("evaluate", req)

    def revise(self, req):
        return self._call("revise", req)


def get_provider(name: str) -> Provider:
    p = providers().get(name)
    if p is None:
        raise ProviderError(f"unknown provider: {name}")
    from ..budget import current
    b = current()
    return BudgetedProvider(p, b) if b is not None else p


__all__ = ["Plan", "PlanRequest", "Provider", "ProviderError", "providers", "get_provider"]
