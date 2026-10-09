"""V2 distributed execution: durable queue, workers, publication, failure injection, security.

Integration tests run a real control plane (uvicorn on a loopback port) and real worker
processes (``daedelus worker``) - the same code paths the containers use. Faults are injected
through DAEDELUS_WORKER_FAULT and by killing processes.
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

import httpx
import pytest

from daedelus.distributed import cas
from daedelus.distributed.models import JobRequest, JobResult, Manifest, WorkerInfo
from daedelus.distributed.queue import JobQueue, LeaseLost
from daedelus.distributed.worker import kill_tree, new_group_kwargs

WT = "worker-token-" + "x" * 24


# ------------------------------------------------------------------ queue unit tests
def _req(**kw):
    return JobRequest(project_id="prj_t", adapter="spreadsheet", method="inspect", **kw)


def _w(**kw):
    return WorkerInfo(id="w1", name="w1", capabilities=["cpu"], adapters=["spreadsheet"], **kw)


def test_lease_heartbeat_complete_and_idempotency(tmp_path):
    q = JobQueue(tmp_path / "q.db", lease_s=5)
    q.register_worker(_w())
    a = q.enqueue(_req(idempotency_key="k1"))
    b = q.enqueue(_req(idempotency_key="k1"))
    assert a.id == b.id  # duplicate submission -> same job
    lease = q.lease(_w())
    assert lease and lease.attempt == 1 and q.lease(_w()) is None
    assert q.heartbeat(a.id, lease.lease_token, 0.5, "half")["cancel"] is False
    with pytest.raises(LeaseLost):
        q.heartbeat(a.id, "forged-token")
    res = JobResult(ok=True, value={"x": 1})
    s1 = q.complete(a.id, lease.lease_token, res)
    s2 = q.complete(a.id, lease.lease_token, res)  # duplicate completion
    assert s1.state == s2.state == "succeeded"
    kinds = [e["kind"] for e in q.events(job_id=a.id)]
    assert kinds == ["queued", "leased", "progress", "succeeded"]


def test_lost_lease_requeues_and_late_result_is_rejected(tmp_path):
    q = JobQueue(tmp_path / "q.db", lease_s=0.2)
    q.register_worker(_w())
    j = q.enqueue(_req(max_attempts=2))
    first = q.lease(_w())
    time.sleep(0.3)
    [s] = q.reap()
    assert s.state == "queued" and "lease expired" in s.error
    second = q.lease(_w())
    assert second.attempt == 2
    with pytest.raises(LeaseLost):  # the first worker comes back too late
        q.complete(j.id, first.lease_token, JobResult(ok=True))
    time.sleep(0.3)
    [s] = q.reap()
    assert s.state == "failed" and s.attempt == 2  # attempts exhausted


def test_cancel_and_timeout(tmp_path):
    q = JobQueue(tmp_path / "q.db", lease_s=5)
    q.register_worker(_w())
    a = q.enqueue(_req())
    assert q.cancel(a.id).state == "cancelled"
    b = q.enqueue(_req())
    lb = q.lease(_w())
    assert q.cancel(b.id).state == "leased"  # running: cancellation is requested
    assert q.heartbeat(b.id, lb.lease_token)["cancel"] is True
    assert q.fail(b.id, lb.lease_token, "stopped", retryable=False, cancelled=True).state == \
        "cancelled"
    c = q.enqueue(_req(timeout_s=0.1, max_attempts=1))
    q.lease(_w())
    time.sleep(0.2)
    [s] = q.reap()
    assert s.state == "timed_out"


def test_queue_survives_restart(tmp_path):
    q = JobQueue(tmp_path / "q.db", lease_s=0.2)
    q.register_worker(_w())
    j = q.enqueue(_req())
    q.lease(_w())
    q.close()  # control plane "crashes" while the job is leased
    time.sleep(0.3)
    q2 = JobQueue(tmp_path / "q.db", lease_s=5)
    [s] = q2.reap()  # startup recovery
    assert s.id == j.id and s.state == "queued"
    assert q2.lease(_w()).attempt == 2


def test_capability_matching(tmp_path):
    q = JobQueue(tmp_path / "q.db")
    q.enqueue(_req(requires="gpu"))
    assert q.lease(_w()) is None  # a CPU worker never takes a GPU job
    gpu = WorkerInfo(id="g", name="g", capabilities=["cpu", "gpu"], adapters=["spreadsheet"])
    assert q.lease(gpu) is not None


def test_cas_verifies_and_swaps_atomically(tmp_path):
    bs = cas.BlobStore(tmp_path / "blobs")
    with pytest.raises(cas.BlobError):
        bs.put_bytes(b"abc", expected="0" * 64)
    src = tmp_path / "src"
    (src / "d").mkdir(parents=True)
    (src / "a.txt").write_text("A")
    (src / "d" / "b.txt").write_text("B")
    m = cas.snapshot(src, bs)
    dst = tmp_path / "dst"
    dst.mkdir()
    (dst / "old.txt").write_text("old")
    cas.swap_in(m, bs, dst)
    assert sorted(p.name for p in dst.rglob("*") if p.is_file()) == ["a.txt", "b.txt"]
    bad = Manifest(files={"../escape": m.files["a.txt"]})
    with pytest.raises(cas.BlobError, match="unsafe"):
        cas.materialize(bad, bs, tmp_path / "x")
    bs.path(m.files["a.txt"].sha256).write_text("tampered")
    with pytest.raises(cas.BlobError, match="corrupt"):
        cas.materialize(m, bs, tmp_path / "y")


# ------------------------------------------------------------------ cluster harness
def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


class Cluster:
    def __init__(self, tmp: Path, monkeypatch, lease_s: str = "3"):
        import uvicorn

        from daedelus.api import create_app
        monkeypatch.setenv("DAEDELUS_WORKER_TOKENS", WT)
        monkeypatch.setenv("DAEDELUS_LEASE_S", lease_s)
        self.tmp = tmp
        self.app = create_app(tmp / "ws", studio_dist=tmp / "nodist")
        self.port = _free_port()
        self.url = f"http://127.0.0.1:{self.port}"
        self.server = uvicorn.Server(uvicorn.Config(self.app, host="127.0.0.1", port=self.port,
                                                    log_level="warning"))
        threading.Thread(target=self.server.run, daemon=True).start()
        while not self.server.started:
            time.sleep(0.05)
        self.c = httpx.Client(base_url=self.url, timeout=300)
        self.procs: list[subprocess.Popen] = []

    def worker(self, name: str, *, fault: str = "", adapters: str = "spreadsheet,document",
               caps: str = "cpu", max_jobs: int | None = None,
               env: dict | None = None) -> subprocess.Popen:
        env = {**os.environ, "DAEDELUS_WORKER_TOKEN": WT, "DAEDELUS_WORKER_FAULT": fault,
               **(env or {})}
        args = [sys.executable, "-m", "daedelus.cli", "worker", "--control", self.url, "--name",
                name, "--adapters", adapters, "--capabilities", caps,
                "--work-dir", str(self.tmp / f"wk_{name}")]
        if max_jobs:
            args += ["--max-jobs", str(max_jobs)]
        p = subprocess.Popen(args, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                             **new_group_kwargs())
        self.procs.append(p)
        t0 = time.time()
        while not any(w["name"] == name for w in self.c.get("/api/cluster/status").json()
                      ["workers"]):
            if p.poll() is not None or time.time() - t0 > 60:
                raise RuntimeError(f"worker {name} did not register: "
                                   f"{p.stdout.read().decode()[-2000:]}")
            time.sleep(0.2)
        return p

    def project(self, target: str = "cloud_cpu") -> str:
        pid = self.c.post("/api/projects", json={"name": "p"}).json()["id"]
        self.c.put(f"/api/projects/{pid}/execution", json={"target": target})
        return pid

    def close(self):
        for p in self.procs:
            kill_tree(p)
        self.server.should_exit = True
        from daedelus.distributed import service
        service.reset()


@pytest.fixture()
def cluster(tmp_path, monkeypatch):
    c = Cluster(tmp_path, monkeypatch)
    yield c
    c.close()


BOOK = {"name": "B", "adapter": "spreadsheet", "template": "data", "params": {
    "sheets": [{"id": "data", "title": "Data", "rows": [["a", "b"], [1, 2], [3, 4]]}]}}


def _workbook(cl: Cluster, pid: str) -> dict:
    r = cl.c.post(f"/api/projects/{pid}/artifacts", json=BOOK)
    assert r.status_code == 200, r.text
    return r.json()["artifact"]


def test_remote_edit_runs_on_worker_and_matches_local(cluster):
    cluster.worker("cpu-1")
    pid = cluster.project("cloud_cpu")
    art = _workbook(cluster, pid)
    r = cluster.c.post(f"/api/projects/{pid}/artifacts/{art['id']}/edit", json={
        "operations": [{"op": "set_cells", "component_id": "data",
                        "params": {"cells": {"C2": "=A2+B2"}}}], "message": "remote"})
    assert r.status_code == 200, r.text
    rev = r.json()
    jobs = cluster.c.get(f"/api/projects/{pid}/jobs").json()
    # V2.1: inspections of unchanged trees are reused (create/apply return their own), so
    # "inspect" jobs may legitimately be absent
    assert {j["method"] for j in jobs} >= {"create", "apply", "preview"}
    assert all(j["state"] == "succeeded" and j["worker_id"] for j in jobs)
    # same edit locally gives the same component states (deterministic adapter behaviour)
    lp = cluster.project("local")
    la = _workbook(cluster, lp)
    lrev = cluster.c.post(f"/api/projects/{lp}/artifacts/{la['id']}/edit", json={
        "operations": [{"op": "set_cells", "component_id": "data",
                        "params": {"cells": {"C2": "=A2+B2"}}}], "message": "local"}).json()
    assert rev["component_states"] == lrev["component_states"]
    assert not cluster.c.get(f"/api/projects/{lp}/jobs").json()  # local ran no jobs
    aud = [a["action"] for a in cluster.c.get("/api/cluster/audit").json()]
    assert "published" in aud and "worker_registered" in aud


def test_no_silent_local_fallback(cluster):
    pid = cluster.project("cloud_cpu")  # no worker connected
    r = cluster.c.post(f"/api/projects/{pid}/artifacts", json=BOOK)
    assert r.status_code == 400 and "not falling back" in r.text
    pid2 = cluster.project("cloud_gpu")
    cluster.worker("cpu-only")
    r = cluster.c.post(f"/api/projects/{pid2}/artifacts", json=BOOK)
    assert r.status_code == 400 and "GPU" in r.text
    pid3 = cluster.project("automatic")  # local tools exist here -> local, recorded
    assert cluster.c.post(f"/api/projects/{pid3}/artifacts", json=BOOK).status_code == 200
    assert not cluster.c.get(f"/api/projects/{pid3}/jobs").json()


def test_workflow_node_records_execution_decision(cluster):
    cluster.worker("cpu-1")
    pid = cluster.project("local")
    art = _workbook(cluster, pid)
    wf = cluster.c.post(f"/api/projects/{pid}/workflows", json={
        "name": "w", "nodes": [
            {"id": "a", "type": "artifact", "label": "A", "config": {"artifact_id": art["id"]}},
            {"id": "g", "type": "agent", "label": "G", "config": {
                "instructions": "Build the analysis workbook.", "execution": "cloud_cpu",
                "fan_out": False}}],
        "edges": [{"id": "e", "source": "a", "source_port": "artifact", "target": "g",
                   "target_port": "artifact"}]}).json()
    ex = cluster.c.post(f"/api/projects/{pid}/workflows/{wf['id']}/execute",
                        json={"mode": "incremental"}).json()
    for _ in range(600):
        ex = cluster.c.get(f"/api/projects/{pid}/executions/{ex['id']}").json()
        if ex["status"] not in ("pending", "running"):
            break
        time.sleep(0.2)
    nr = next(r for r in ex["node_runs"] if r["node_id"] == "g")
    exe = nr["outputs"]["execution"]
    assert exe["decisions"]["spreadsheet"]["resolved"] == "cloud_cpu"
    real = [j for j in exe["jobs"] if not j.get("cached")]  # V2.1: reused inspections
    assert real and all(j.get("worker_id") for j in real)


def test_worker_crash_mid_job_is_retried_by_another_worker(cluster):
    cluster.worker("crasher", fault="crash_after_lease")
    pid = cluster.project("cloud_cpu")
    result: dict = {}

    def create():
        result["r"] = cluster.c.post(f"/api/projects/{pid}/artifacts", json=BOOK)
    t = threading.Thread(target=create)
    t.start()
    time.sleep(2)  # the crasher leases the first job and dies
    cluster.worker("healthy")
    t.join(timeout=240)
    assert result["r"].status_code == 200, result["r"].text
    jobs = cluster.c.get(f"/api/projects/{pid}/jobs").json()
    first = [j for j in jobs if j["method"] == "create"][0]
    assert first["attempt"] == 2 and first["state"] == "succeeded"
    detail = cluster.c.get(f"/api/projects/{pid}/jobs/{first['id']}").json()
    kinds = [e["kind"] for e in detail["events"]]
    assert "retry" in kinds and "lease expired" in json.dumps(detail["events"])


def test_corrupted_upload_rejected_and_duplicate_completion_idempotent(cluster):
    cluster.worker("faulty", fault="corrupt_upload,duplicate_complete")
    pid = cluster.project("cloud_cpu")
    art = _workbook(cluster, pid)
    assert art["components"]
    aud = cluster.c.get("/api/cluster/audit").json()
    assert any(a["action"] == "blob_hash_mismatch" for a in aud)
    pubs = [a for a in aud if a["action"] == "published"]
    assert len({p["target"] for p in pubs}) == len(pubs)  # each job published once


def test_version_conflict_on_edit_keeps_the_newer_change(cluster):
    """V2.1 (found by the hosted harness, H9): a conflicting remote result must not be
    'rolled back' over the newer files either - the edit fails and the newer change stays."""
    pid = cluster.project("local")
    art = _workbook(cluster, pid)
    cluster.worker("slow", fault="slow:4")
    cluster.c.put(f"/api/projects/{pid}/execution", json={"target": "cloud_cpu"})
    st = cluster.app.state.workspace.open(pid)
    native = st.abs(st.get_artifact(art["id"]).native_dir)
    revs = len(st.list_revisions(art["id"]))
    res: dict = {}
    t = threading.Thread(target=lambda: res.update(r=cluster.c.post(
        f"/api/projects/{pid}/artifacts/{art['id']}/edit", json={
            "message": "slow remote edit", "operations": [
                {"op": "set_cells", "component_id": "data", "params": {"cells": {"D1": 9}}}]})))
    t.start()
    for _ in range(100):
        if any(j.method == "apply" and j.state in ("leased", "running")
               for j in service_jobs(pid)):
            break
        time.sleep(0.1)
    (native / "concurrent.txt").write_text("someone else edited the artifact")
    t.join(timeout=120)
    assert res["r"].status_code >= 400 and "conflict" in res["r"].text, res["r"].text
    assert (native / "concurrent.txt").exists()  # not erased by a rollback
    assert len(st.list_revisions(art["id"])) == revs  # nothing recorded
    assert not [j for j in service_jobs(pid) if j.method == "after_restore"]


def service_jobs(pid):
    from daedelus.distributed import service
    return service.get_cluster().queue.list(project_id=pid)


def test_version_conflict_is_not_published(cluster, monkeypatch):
    from daedelus.adapters import registry
    from daedelus.distributed import remote, service
    from daedelus.models import PlannedOperation
    pid = cluster.project("local")
    art = _workbook(cluster, pid)
    cluster.worker("slow", fault="slow:4")
    st = cluster.app.state.workspace.open(pid)
    a = st.get_artifact(art["id"])
    native = st.abs(a.native_dir)
    before = cas.snapshot(native).digest()
    res: dict = {}

    def run():
        with remote.use_target(pid, "cloud_cpu", origin={"artifact_id": a.id}):
            ad = remote.wrap(registry()["spreadsheet"])
            try:
                ad.apply(native, a.entry, [PlannedOperation(
                    op="set_cells", component_id="data", params={"cells": {"D1": 9}})], {})
            except Exception as exc:
                res["err"] = str(exc)
    t = threading.Thread(target=run)
    t.start()
    time.sleep(1.5)
    (native / "concurrent.txt").write_text("someone else edited the artifact")  # conflict
    t.join(timeout=120)
    assert "version conflict" in res.get("err", "")
    assert (native / "concurrent.txt").exists()  # nothing was overwritten
    assert cas.snapshot(native).digest() != before
    jobs = service.get_cluster().queue.list(project_id=pid)
    assert any(j.state == "conflict" for j in jobs)


def test_cancellation_of_running_job(cluster):
    cluster.worker("slow", fault="slow:30")
    pid = cluster.project("cloud_cpu")
    t = threading.Thread(target=lambda: cluster.c.post(f"/api/projects/{pid}/artifacts",
                                                       json=BOOK))
    t.start()
    job = None
    for _ in range(100):
        jobs = cluster.c.get(f"/api/projects/{pid}/jobs").json()
        job = next((j for j in jobs if j["state"] in ("leased", "running")), None)
        if job:
            break
        time.sleep(0.1)
    assert job
    cluster.c.post(f"/api/projects/{pid}/jobs/{job['id']}/cancel")
    t.join(timeout=60)
    st = cluster.c.get(f"/api/projects/{pid}/jobs/{job['id']}").json()["status"]
    assert st["state"] == "cancelled"
    assert not cluster.c.get(f"/api/projects/{pid}/artifacts").json()  # nothing created


def test_sse_stream_reports_job_events(cluster):
    cluster.worker("cpu-1")
    pid = cluster.project("cloud_cpu")
    got: list[str] = []

    def listen():
        with httpx.stream("GET", f"{cluster.url}/api/projects/{pid}/events?since=0",
                          timeout=60) as r:
            for line in r.iter_lines():
                if line.startswith("event: "):
                    got.append(line[7:])
                if "published" in got:
                    return
    t = threading.Thread(target=listen, daemon=True)
    t.start()
    time.sleep(0.5)
    _workbook(cluster, pid)
    t.join(timeout=60)
    assert got[0] == "hello" and {"queued", "leased", "succeeded", "published"} <= set(got)


def test_long_poll_events_for_proxies_without_sse(cluster):
    """V2.1: the long-poll fallback (Cloudflare quick tunnels buffer SSE entirely)."""
    cluster.worker("cpu-1")
    pid = cluster.project("cloud_cpu")
    start = cluster.c.get(f"/api/projects/{pid}/events/poll").json()
    assert start["events"] == []
    t0 = time.time()
    empty = cluster.c.get(f"/api/projects/{pid}/events/poll?since={start['seq']}&wait_s=0.5").json()
    assert empty == {"seq": start["seq"], "events": []} and time.time() - t0 >= 0.4
    res: dict = {}
    t = threading.Thread(target=lambda: res.update(r=cluster.c.get(
        f"/api/projects/{pid}/events/poll?since={start['seq']}&wait_s=20")))
    t.start()
    time.sleep(0.3)
    _workbook(cluster, pid)
    t.join(timeout=30)
    first = res["r"].json()
    assert first["events"] and first["seq"] == first["events"][-1]["seq"]  # woke on the first event
    kinds, seq = [e["kind"] for e in first["events"]], first["seq"]
    for _ in range(20):
        if "published" in kinds:
            break
        r = cluster.c.get(f"/api/projects/{pid}/events/poll?since={seq}&wait_s=5").json()
        kinds += [e["kind"] for e in r["events"]]
        seq = r["seq"]
    assert {"queued", "leased", "succeeded", "published"} <= set(kinds)


def test_worker_protocol_requires_tokens_and_scopes_blobs(cluster):
    c = cluster.c
    assert c.post("/api/cluster/workers", json={"name": "x"}).status_code == 401
    assert c.post("/api/cluster/workers", json={"name": "x"},
                  headers={"Authorization": "Bearer wrong-token-xxxxxxxxxxxx"}).status_code == 401
    w = c.post("/api/cluster/workers", json={"name": "probe", "adapters": ["spreadsheet"]},
               headers={"Authorization": f"Bearer {WT}"}).json()
    # V2.1: the join token only enrols; the worker then uses its own credential
    h = {"Authorization": f"Bearer {w['credential']}"}
    assert c.post("/api/cluster/lease", json={"worker_id": w["id"], "wait_s": 0},
                  headers={"Authorization": f"Bearer {WT}"}).status_code == 401
    # a lease token for one job cannot read other blobs
    cl = cluster.app.state.cluster
    secret = cl.blobs.put_bytes(b"other project's data")
    job = JobRequest(project_id="prj_a", adapter="spreadsheet", method="inspect")
    cl.submit(job)
    lease = c.post("/api/cluster/lease", json={"worker_id": w["id"], "wait_s": 1},
                   headers=h).json()
    r = c.get(f"/api/cluster/jobs/{lease['job']['id']}/blobs/{secret}",
              headers={**h, "X-Lease-Token": lease["lease_token"]})
    assert r.status_code == 403
    r = c.get(f"/api/cluster/jobs/{lease['job']['id']}/blobs/{secret}",
              headers={**h, "X-Lease-Token": "forged"})
    assert r.status_code == 409
    acts = [a["action"] for a in c.get("/api/cluster/audit").json()]
    assert {"worker_auth_failed", "blob_scope_denied", "lease_rejected"} <= set(acts)


# ------------------------------------------------------------------ API security
def test_api_tokens_project_scope_and_trusted_hosts(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from daedelus.api import create_app
    from daedelus.distributed import service
    admin, scoped = "admin-token-" + "a" * 20, "scoped-token-" + "b" * 20
    monkeypatch.setenv("DAEDELUS_API_TOKENS", admin)
    app = create_app(tmp_path / "ws", studio_dist=tmp_path / "nodist")
    c = TestClient(app)
    assert c.get("/api/health").status_code == 200  # health stays open
    assert c.get("/api/projects").status_code == 401
    A = {"Authorization": f"Bearer {admin}"}
    p1 = c.post("/api/projects", json={"name": "one"}, headers=A).json()["id"]
    p2 = c.post("/api/projects", json={"name": "two"}, headers=A).json()["id"]
    service.reset()
    monkeypatch.setenv("DAEDELUS_API_TOKENS", f"{admin},{scoped}:{p1}")
    c = TestClient(create_app(tmp_path / "ws", studio_dist=tmp_path / "nodist"))
    S = {"Authorization": f"Bearer {scoped}"}
    assert [p["id"] for p in c.get("/api/projects", headers=S).json()] == [p1]
    assert c.get(f"/api/projects/{p1}/artifacts", headers=S).status_code == 200
    assert c.get(f"/api/projects/{p2}/artifacts", headers=S).status_code == 403
    # V2.1: reusable tokens in query strings are refused
    assert c.get(f"/api/projects/{p1}/artifacts?token={scoped}").status_code == 401
    assert c.post("/api/projects", json={"name": "x"}, headers=S).status_code == 403
    assert c.get("/api/cluster/audit", headers=S).status_code == 403
    assert c.get("/api/health", headers={"Host": "evil.example"}).status_code == 400
    acts = [a["action"] for a in c.get("/api/cluster/audit", headers=A).json()]
    assert "api_scope_denied" in acts and "api_auth_failed" in acts
    service.reset()


def test_serve_refuses_public_bind_without_tokens(monkeypatch):
    from daedelus.cli import main
    monkeypatch.delenv("DAEDELUS_API_TOKENS", raising=False)
    assert main(["serve", "--host", "0.0.0.0", "--port", "1"]) == 2


def test_gpu_capability_requires_explicit_verification(monkeypatch):
    from daedelus.distributed.worker import main
    monkeypatch.setenv("DAEDELUS_WORKER_TOKEN", WT)
    monkeypatch.delenv("DAEDELUS_GPU_VERIFIED", raising=False)
    assert main(["--capabilities", "cpu,gpu", "--control", "http://127.0.0.1:1"]) == 2


@pytest.mark.parametrize("url", [
    "http://127.0.0.1/", "http://localhost:8765/api", "http://10.0.0.5/", "http://[::1]/",
    "http://169.254.169.254/latest/meta-data/", "http://[::ffff:127.0.0.1]/",
    "http://100.64.1.1/", "file:///etc/passwd", "gopher://x/", "http://user:pw@example.com/",
    "http://example.com:22/"])
def test_ssrf_guard_refuses_internal_targets(url):
    from daedelus.netsafe import FetchRefused, check_url
    with pytest.raises(FetchRefused):
        check_url(url, resolve=lambda *a, **k: [(0, 0, 0, "", ("127.0.0.1", 80))])


def test_ssrf_guard_checks_resolution_and_redirects(monkeypatch):
    from daedelus.netsafe import FetchRefused, check_url, safe_get
    public = lambda *a, **k: [(0, 0, 0, "", ("93.184.215.14", 443))]  # noqa: E731
    private = lambda *a, **k: [(0, 0, 0, "", ("192.168.1.10", 443))]  # noqa: E731
    assert check_url("https://example.com/x", resolve=public)
    with pytest.raises(FetchRefused, match="non-public"):
        check_url("https://intranet.example/", resolve=private)
    monkeypatch.setenv("DAEDELUS_FETCH_ALLOWLIST", "example.org")
    with pytest.raises(FetchRefused, match="allowlist"):
        check_url("https://example.com/", resolve=public)
    monkeypatch.delenv("DAEDELUS_FETCH_ALLOWLIST")

    def handler(req):  # a public page redirecting to the metadata service
        return httpx.Response(302, headers={"location": "http://169.254.169.254/latest"})
    import daedelus.netsafe as ns
    monkeypatch.setattr(ns.socket, "getaddrinfo", public)
    with httpx.Client(transport=httpx.MockTransport(handler)) as c:
        with pytest.raises(FetchRefused):
            safe_get(c, "https://example.com/start")
