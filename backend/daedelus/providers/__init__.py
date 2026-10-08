"""Planner providers registry."""

from __future__ import annotations

from .base import Plan, PlanRequest, Provider, ProviderError


def providers() -> dict[str, Provider]:
    from .anthropic_provider import AnthropicProvider
    from .gemini_provider import GeminiProvider
    from .heuristic import HeuristicProvider
    from .replay import ReplayProvider

    return {p.name: p for p in (HeuristicProvider(), AnthropicProvider(), GeminiProvider(),
                                ReplayProvider())}


def get_provider(name: str) -> Provider:
    p = providers().get(name)
    if p is None:
        raise ProviderError(f"unknown provider: {name}")
    return p


__all__ = ["Plan", "PlanRequest", "Provider", "ProviderError", "providers", "get_provider"]
