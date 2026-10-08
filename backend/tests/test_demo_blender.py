"""Section 8 demonstration end to end (requires Blender)."""

from __future__ import annotations

import pytest

from daedelus.scenarios import run_scenarios


@pytest.mark.blender
def test_multimodal_demo_scenarios(tmp_path):
    report = run_scenarios(tmp_path / "ws", tmp_path / "evidence", include_video=False)
    failed = [c for c in report["checks"] if not c["passed"]]
    assert not failed, failed
    assert (tmp_path / "evidence" / "demo_report.md").exists()
