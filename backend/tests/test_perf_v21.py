"""V2.1 remote-execution optimisations keep their safety properties.

* inspection results are reused only for byte-identical trees (content digest) and never after
  a change;
* mutating jobs return the inspection of their output (no separate inspect job);
* side-effect scopes of a multi-operation edit are computed in one job;
* results are identical to the unoptimised path.
"""

from __future__ import annotations

from daedelus.distributed import remote

from test_distributed import BOOK, Cluster  # noqa: F401  (fixture module)
from test_distributed import cluster  # noqa: F401


def _methods(cluster, pid):
    out: dict[str, int] = {}
    for j in cluster.c.get(f"/api/projects/{pid}/jobs?limit=500").json():
        out[j["method"]] = out.get(j["method"], 0) + 1
    return out


def test_fewer_jobs_same_result_and_cache_never_stale(cluster):
    remote.clear_inspect_cache()
    cluster.worker("w1")
    pid = cluster.project("cloud_cpu")
    art = cluster.c.post(f"/api/projects/{pid}/artifacts", json=BOOK).json()["artifact"]
    ops = {"operations": [
        {"op": "set_cells", "component_id": "data", "params": {"cells": {"C2": "=A2+B2"}}},
        {"op": "set_cells", "component_id": "data", "params": {"cells": {"D2": 7}}}],
        "message": "two ops"}
    rev = cluster.c.post(f"/api/projects/{pid}/artifacts/{art['id']}/edit", json=ops).json()
    m = _methods(cluster, pid)
    assert m.get("side_effect_scope") == 1  # both operations in one job
    # V2 ran create + inspect (record) + scope x2 + inspect (before) + apply + inspect (after)
    # + validate + inspect/preview/diff; V2.1 reuses inspections of identical trees
    assert m.get("inspect", 0) <= 1, m
    # identical outcome to local execution
    lp = cluster.project("local")
    la = cluster.c.post(f"/api/projects/{lp}/artifacts", json=BOOK).json()["artifact"]
    lrev = cluster.c.post(f"/api/projects/{lp}/artifacts/{la['id']}/edit", json=ops).json()
    assert rev["component_states"] == lrev["component_states"]
    # a changed tree is never served from the cache
    st = cluster.app.state.workspace.open(pid)
    a = st.get_artifact(art["id"])
    native = st.abs(a.native_dir)
    from daedelus.adapters import registry
    before_jobs = sum(_methods(cluster, pid).values())
    with remote.use_target(pid, "cloud_cpu", origin={}):
        ad = remote.wrap(registry()["spreadsheet"])
        ad.inspect(native, a.entry)  # unchanged tree: cache hit, no job
        assert sum(_methods(cluster, pid).values()) == before_jobs
        (native / "extra.txt").write_text("changed")
        ad.inspect(native, a.entry)  # changed tree: a real job
    assert sum(_methods(cluster, pid).values()) == before_jobs + 1
    assert remote.cache_stats["hits"] >= 1
