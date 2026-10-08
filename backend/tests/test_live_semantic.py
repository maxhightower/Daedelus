"""Opt-in LIVE semantic tests (real model calls on real media).

Run with DAEDELUS_LIVE_SEMANTIC=1 and provider credentials. Skipped otherwise, with the reason;
a skip means live semantic verification is BLOCKED, not passed. Set DAEDELUS_RECORD_DIR to
capture the responses as replay fixtures (origin "recorded").
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from daedelus import ingest
from daedelus.providers.anthropic_provider import AnthropicProvider
from daedelus.providers.gemini_provider import GeminiProvider
from daedelus.semantic import service as semsvc

ASSETS = Path(__file__).resolve().parents[1] / "daedelus" / "demo_assets" / "v11"
LIVE = os.environ.get("DAEDELUS_LIVE_SEMANTIC") == "1"


def _gate(provider):
    ok, why = provider.available()
    if not LIVE:
        pytest.skip("live semantic tests are opt-in: set DAEDELUS_LIVE_SEMANTIC=1")
    if not ok:
        pytest.skip(f"live semantic verification blocked: {why}")


def _photo():
    cands = sorted(ASSETS.glob("tree_trunk_photo.*"))
    if not cands:
        pytest.skip("reference photograph not present in demo_assets/v11")
    return cands[0]


@pytest.mark.parametrize("prov", [AnthropicProvider(), GeminiProvider()],
                         ids=["anthropic", "gemini"])
def test_live_image_analysis_describes_the_photo(store, prov):
    _gate(prov)
    src = ingest.register_file(store, _photo(), name="trunk photo")
    ana = semsvc.analyze_source(store, src, prov.name)
    assert ana.status in ("complete", "partial") and ana.observations
    text = " ".join(o.text.lower() for o in ana.observations)
    assert any(w in text for w in ("tree", "trunk", "bark", "wood"))
    assert not ana.recorded


def test_live_gemini_video_url_has_timestamped_steps(store):
    prov = GeminiProvider()
    _gate(prov)
    url = os.environ.get("DAEDELUS_LIVE_VIDEO_URL")
    if not url:
        pytest.skip("set DAEDELUS_LIVE_VIDEO_URL to a public tutorial video")
    src = ingest.register_url(store, url, name="tutorial")
    ana = semsvc.analyze_source(store, src, "gemini")
    steps = [o for o in ana.observations if o.kind in ("step", "operation")]
    assert steps and any(o.location and o.location.start_seconds is not None for o in steps)
