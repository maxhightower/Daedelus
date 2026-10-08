"""Opt-in live smoke test against the real Claude API.

Runs only with DAEDELUS_LIVE_CLAUDE=1 *and* Anthropic credentials in the environment. It is
reported as skipped (with the reason) otherwise; a skip here means live verification is
blocked, not passed.
"""

from __future__ import annotations

import os

import pytest

from daedelus.adapters import get_adapter
from daedelus.providers.anthropic_provider import AnthropicProvider

from test_providers import _req

LIVE = os.environ.get("DAEDELUS_LIVE_CLAUDE") == "1"
OK, WHY = AnthropicProvider().available()

pytestmark = pytest.mark.skipif(
    not (LIVE and OK),
    reason=("live Claude smoke test is opt-in: set DAEDELUS_LIVE_CLAUDE=1"
            if not LIVE else f"live Claude smoke test blocked: {WHY}"))


def test_live_plan_is_valid_for_the_adapter(store):
    req, _ = _req(store)
    plan = AnthropicProvider().plan(req)
    assert plan.provider == "anthropic" and not plan.deterministic
    allowed = {o.name for o in get_adapter("layered2d").info().operations}
    assert plan.operations, plan.notes
    for op in plan.operations:
        assert op.op in allowed
        assert op.component_id in (None, "sky", "img")
