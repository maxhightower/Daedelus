"""V2.1: the workflow budget's remote-job limits reach the job request and the job process."""
import os
import subprocess
import sys
import threading

import pytest

from daedelus.adapters import registry
from daedelus.budget import ExecutionBudget, use_budget
from daedelus.distributed import remote
from daedelus.distributed.cas import BlobStore
from daedelus.distributed.sandbox import Sandbox


class _Stop(Exception):
    pass


class _FakeCluster:
    def __init__(self, root):
        self.blobs = BlobStore(root)
        self.submitted = []

    def submit(self, req):
        self.submitted.append(req)
        raise _Stop()


def _remote(tmp_path, monkeypatch):
    cl = _FakeCluster(tmp_path / "blobs")
    monkeypatch.setattr(remote, "get_cluster", lambda: cl)
    tc = remote.TargetContext(target="cloud_cpu", project_id="p1")
    return cl, remote.RemoteAdapter(registry()["spreadsheet"], tc, "cpu")


def test_budget_job_limits_reach_the_request(tmp_path, monkeypatch):
    cl, ad = _remote(tmp_path, monkeypatch)
    bud = ExecutionBudget.from_config({"job_timeout_s": 42, "job_memory_mb": 512})
    with use_budget(bud), pytest.raises(_Stop):
        ad._run("inspect", None, "", {})
    req = cl.submitted[0]
    assert req.timeout_s == 42 and req.memory_mb == 512


def test_without_budget_the_defaults_apply(tmp_path, monkeypatch):
    cl, ad = _remote(tmp_path, monkeypatch)
    with pytest.raises(_Stop):
        ad._run("inspect", None, "", {})
    assert cl.submitted[0].timeout_s == 900 and cl.submitted[0].memory_mb is None


def test_max_concurrent_jobs_bounds_jobs_in_flight(tmp_path, monkeypatch):
    cl, ad = _remote(tmp_path, monkeypatch)
    bud = ExecutionBudget.from_config({"max_concurrent_jobs": 2})
    inflight, peak, lock, gate = [0], [0], threading.Lock(), threading.Event()

    def slow(cl_, req, *a):
        with lock:
            inflight[0] += 1
            peak[0] = max(peak[0], inflight[0])
        gate.wait(2)
        with lock:
            inflight[0] -= 1
        raise _Stop()

    monkeypatch.setattr(remote.RemoteAdapter, "_submit_and_wait", lambda self, *a: slow(*a))

    def one():
        with use_budget(bud):
            try:
                ad._run("inspect", None, "", {})
            except _Stop:
                pass

    ts = [threading.Thread(target=one) for _ in range(5)]
    for t in ts:
        t.start()
    threading.Timer(0.5, gate.set).start()
    for t in ts:
        t.join(5)
    assert peak[0] == 2


@pytest.mark.skipif(os.name == "nt", reason="POSIX rlimits")
def test_job_memory_cap_only_lowers_the_worker_limit(tmp_path):
    sb = Sandbox(tmp_path, "process")
    code = "import resource; print(resource.getrlimit(resource.RLIMIT_AS)[0])"

    def limit(mb):
        jd = tmp_path / f"j{mb}"
        jd.mkdir()
        p = sb.popen([sys.executable, "-I", "-c", code], jd, memory_mb=mb, stdout=subprocess.PIPE)
        return int(p.communicate(timeout=30)[0].decode().strip())

    own = sb.limits.memory_mb * 1024 * 1024
    assert limit(None) == own
    assert limit(1024) == 1024 * 1024 * 1024
    assert limit(sb.limits.memory_mb * 4) == own  # a job cannot raise the worker's limit
