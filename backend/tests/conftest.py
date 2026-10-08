from __future__ import annotations

import pytest

from daedelus.adapters.blender import find_blender
from daedelus.store import Workspace


def pytest_collection_modifyitems(config, items):
    if find_blender():
        return
    skip = pytest.mark.skip(reason="Blender executable not available (set DAEDELUS_BLENDER)")
    for item in items:
        if "blender" in item.keywords:
            item.add_marker(skip)


@pytest.fixture()
def workspace(tmp_path):
    ws = Workspace(tmp_path / "ws")
    yield ws
    ws.close()


@pytest.fixture()
def store(workspace):
    project, st = workspace.create_project("test project")
    return st
