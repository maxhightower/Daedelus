"""V2.1 security suite: worker identities, browser sessions, tickets, manifests, SSRF, isolation.

Every test asserts the *safe* outcome: a refused request or a rejected operation is a pass.
Network-level tests use real uvicorn servers and real worker processes where the behaviour
depends on them (revocation during execution, stolen lease tokens).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from daedelus.distributed import cas
from daedelus.distributed.identity import CredentialStore, parse_credential
from daedelus.distributed.models import FileEntry, JobRequest, JobResult, Manifest, WorkerInfo
from daedelus.distributed.queue import JobQueue, LeaseLost, LeaseOwnerMismatch

from test_distributed import BOOK, WT, Cluster, _workbook

ADMIN = "admin-token-" + "a" * 20
SCOPED = "scoped-token-" + "b" * 20


# =============================================================== worker identity (unit)
def _job(**kw):
    return JobRequest(project_id="prj_t", adapter="spreadsheet", method="inspect", **kw)


def _info(cid):
    return WorkerInfo(id=cid, name=cid, capabilities=["cpu"], adapters=["spreadsheet"])


def test_credentials_are_hashed_distinct_and_revocable(tmp_path):
    q = JobQueue(tmp_path / "q.db", lease_s=30)
    cs = CredentialStore(q)
    a, wa = cs.create("a", adapters=["spreadsheet"])
    b, wb = cs.create("b")
    assert a["id"] != b["id"] and wa != wb
    raw = (tmp_path / "q.db").read_bytes()
    assert parse_credential(wa)[1].encode() not in raw  # only the hash is stored
    assert cs.authenticate(wa).id == a["id"] and cs.authenticate(wb).id == b["id"]
    assert cs.authenticate(wa[:-2] + "xx") is None  # wrong secret
    assert cs.authenticate(f"ddw1.{b['id']}.{parse_credential(wa)[1]}") is None  # A's secret, B's id
    assert cs.authenticate("not-a-credential") is None
    # adapter/capability restrictions
    ident = cs.authenticate(wa)
    assert ident.allows(["spreadsheet"], ["cpu"]) is None
    assert "adapters" in ident.allows(["spreadsheet", "code"], ["cpu"])
    assert "capabilities" in ident.allows(["spreadsheet"], ["cpu", "gpu"])
    cs.revoke(a["id"], "test")
    assert cs.authenticate(wa) is None and cs.get(a["id"])["state"] == "revoked"


def test_rotation_with_and_without_grace(tmp_path):
    q = JobQueue(tmp_path / "q.db")
    cs = CredentialStore(q)
    rec, old = cs.create("w")
    new = cs.rotate(rec["id"])  # no grace: the old secret dies at once
    assert cs.authenticate(old) is None and cs.authenticate(new).id == rec["id"]
    newer = cs.rotate(rec["id"], grace_s=0.5)
    assert cs.authenticate(new) is not None  # within grace
    assert cs.authenticate(newer) is not None
    time.sleep(0.6)
    assert cs.authenticate(new) is None and cs.authenticate(newer) is not None


def test_lease_is_bound_to_the_worker_that_took_it(tmp_path):
    q = JobQueue(tmp_path / "q.db", lease_s=30)
    cs = CredentialStore(q)
    a, _ = cs.create("a")
    b, _ = cs.create("b")
    q.register_worker(_info(a["id"]))
    q.register_worker(_info(b["id"]))
    j = q.enqueue(_job())
    lease = q.lease(_info(a["id"]))
    # B holds A's (stolen) lease token: refused everywhere, A keeps its lease
    with pytest.raises(LeaseOwnerMismatch):
        q.check_lease(j.id, lease.lease_token, b["id"])
    with pytest.raises(LeaseOwnerMismatch):
        q.heartbeat(j.id, lease.lease_token, worker_id=b["id"])
    with pytest.raises(LeaseOwnerMismatch):
        q.complete(j.id, lease.lease_token, JobResult(ok=True), worker_id=b["id"])
    with pytest.raises(LeaseOwnerMismatch):
        q.fail(j.id, lease.lease_token, "x", retryable=False, worker_id=b["id"])
    assert q.status(j.id).state == "leased"
    assert q.complete(j.id, lease.lease_token, JobResult(ok=True),
                      worker_id=a["id"]).state == "succeeded"


def test_revocation_revokes_held_leases(tmp_path):
    q = JobQueue(tmp_path / "q.db", lease_s=30)
    cs = CredentialStore(q)
    a, _ = cs.create("a")
    q.register_worker(_info(a["id"]))
    j = q.enqueue(_job(max_attempts=3))
    lease = q.lease(_info(a["id"]))
    assert cs.revoke(a["id"], "compromised") == [j.id]
    st = q.status(j.id)
    assert st.state == "queued" and "revoked" in st.error  # another worker may take it
    with pytest.raises(LeaseLost):
        q.complete(j.id, lease.lease_token, JobResult(ok=True), worker_id=a["id"])
    assert not [w for w in q.workers() if w.id == a["id"]]


# =============================================================== worker protocol (HTTP)
@pytest.fixture()
def cluster(tmp_path, monkeypatch):
    c = Cluster(tmp_path, monkeypatch)
    yield c
    c.close()


def _register(c, token, name="w", adapters=("spreadsheet",), caps=("cpu",)):
    return c.post("/api/cluster/workers", json={"name": name, "adapters": list(adapters),
                                                "capabilities": list(caps)},
                  headers={"Authorization": f"Bearer {token}"})


def test_unauthorized_workers_cannot_register_or_lease(cluster):
    c = cluster.c
    assert _register(c, "nope-" + "x" * 30).status_code == 401
    assert _register(c, "ddw1.wkr_fake.%s" % ("y" * 43)).status_code == 401
    assert c.post("/api/cluster/lease", json={"worker_id": "x", "wait_s": 0}).status_code == 401
    # the join token cannot be used for anything but enrolment
    assert c.post("/api/cluster/lease", json={"worker_id": "x", "wait_s": 0},
                  headers={"Authorization": f"Bearer {WT}"}).status_code == 401
    acts = [a["action"] for a in c.get("/api/cluster/audit").json()]
    assert acts.count("worker_auth_failed") >= 3


def test_registration_with_wrong_identity_and_restricted_credential(cluster):
    c = cluster.c
    cl = cluster.app.state.cluster
    rec, cred = cl.creds.create("office-only", adapters=["spreadsheet"])
    # a credential cannot advertise adapters or GPU beyond what the operator allowed
    r = _register(c, cred, adapters=("spreadsheet", "code"))
    assert r.status_code == 403 and "adapters" in r.text
    r = _register(c, cred, caps=("cpu", "gpu"))
    assert r.status_code == 403 and "capabilities" in r.text
    w = _register(c, cred).json()
    assert w["id"] == rec["id"] and w["credential_kind"] == "provisioned"
    assert "credential" not in w  # a provisioned secret is never echoed
    other = _register(c, WT, name="enrolled").json()
    # worker A presenting worker B's id is refused
    r = c.post("/api/cluster/lease", json={"worker_id": other["id"], "wait_s": 0},
               headers={"Authorization": f"Bearer {cred}"})
    assert r.status_code == 403
    acts = [a["action"] for a in c.get("/api/cluster/audit").json()]
    assert {"worker_registration_refused", "worker_identity_mismatch",
            "worker_enrolled"} <= set(acts)


def test_stolen_lease_token_is_rejected_over_http(cluster):
    c = cluster.c
    cl = cluster.app.state.cluster
    a = _register(c, WT, name="a").json()
    b = _register(c, WT, name="b").json()
    HA = {"Authorization": f"Bearer {a['credential']}"}
    HB = {"Authorization": f"Bearer {b['credential']}"}
    blob = cl.blobs.put_bytes(b"input")
    job = JobRequest(project_id="prj_x", adapter="spreadsheet", method="inspect",
                     files={"source:s": blob})
    cl.submit(job)
    lease = c.post("/api/cluster/lease", json={"worker_id": a["id"], "wait_s": 2},
                   headers=HA).json()
    tok = {"X-Lease-Token": lease["lease_token"]}
    jid = lease["job"]["id"]
    assert c.get(f"/api/cluster/jobs/{jid}/blobs/{blob}", headers={**HB, **tok}).status_code == 403
    assert c.post(f"/api/cluster/jobs/{jid}/heartbeat", json={},
                  headers={**HB, **tok}).status_code == 403
    res = JobResult(ok=True, worker_id=a["id"]).model_dump_json()
    assert c.post(f"/api/cluster/jobs/{jid}/complete", content=res,
                  headers={**HB, **tok, "Content-Type": "application/json"}).status_code == 403
    assert cl.queue.status(jid).state == "leased"  # the real owner keeps the job
    # the owner completes; a self-reported worker id is replaced by the authenticated one
    spoof = JobResult(ok=True, worker_id=b["id"]).model_dump_json()
    r = c.post(f"/api/cluster/jobs/{jid}/complete", content=spoof,
               headers={**HA, **tok, "Content-Type": "application/json"})
    assert r.status_code == 200
    assert cl.queue.result(jid).worker_id == a["id"]
    acts = [x["action"] for x in c.get("/api/cluster/audit").json()]
    assert acts.count("lease_owner_mismatch") == 3


def test_revoked_worker_cannot_complete_or_retrieve(cluster):
    c = cluster.c
    cl = cluster.app.state.cluster
    a = _register(c, WT, name="a").json()
    HA = {"Authorization": f"Bearer {a['credential']}"}
    blob = cl.blobs.put_bytes(b"secret input")
    cl.submit(JobRequest(project_id="prj_x", adapter="spreadsheet", method="inspect",
                         files={"source:s": blob}))
    lease = c.post("/api/cluster/lease", json={"worker_id": a["id"], "wait_s": 2},
                   headers=HA).json()
    tok = {"X-Lease-Token": lease["lease_token"]}
    jid = lease["job"]["id"]
    r = c.post(f"/api/cluster/credentials/{a['id']}/revoke", json={"reason": "test"})
    assert r.status_code == 200 and r.json()["leases_revoked"] == [jid]
    assert c.get(f"/api/cluster/jobs/{jid}/blobs/{blob}", headers={**HA, **tok}).status_code == 401
    res = JobResult(ok=True).model_dump_json()
    assert c.post(f"/api/cluster/jobs/{jid}/complete", content=res,
                  headers={**HA, **tok, "Content-Type": "application/json"}).status_code == 401
    assert _register(c, a["credential"], name="a").status_code == 401  # cannot come back
    assert cl.queue.status(jid).state == "queued"  # available to a legitimate worker


def test_worker_credential_revoked_during_execution(cluster):
    """Failure injection 1: revoke a worker while it runs a job. Its result is not accepted,
    the worker stops, and another worker completes the job."""
    rec, cred = cluster.app.state.cluster.creds.create("victim",
                                                       adapters=["spreadsheet", "document"])
    env_cred = {"DAEDELUS_WORKER_CREDENTIAL": cred}
    p = cluster.worker("victim", fault="slow:6", env=env_cred)
    pid = cluster.project("cloud_cpu")
    out: dict = {}
    t = threading.Thread(target=lambda: out.update(
        r=cluster.c.post(f"/api/projects/{pid}/artifacts", json=BOOK)))
    t.start()
    for _ in range(100):
        jobs = cluster.c.get(f"/api/projects/{pid}/jobs").json()
        if any(j["state"] in ("leased", "running") and j["worker_id"] == rec["id"]
               for j in jobs):
            break
        time.sleep(0.1)
    r = cluster.c.post(f"/api/cluster/credentials/{rec['id']}/revoke",
                       json={"reason": "failure injection"})
    assert r.json()["leases_revoked"]
    assert p.wait(timeout=60) == 3  # the revoked worker stops
    cluster.worker("healthy")
    t.join(timeout=240)
    assert out["r"].status_code == 200, out["r"].text
    jobs = cluster.c.get(f"/api/projects/{pid}/jobs").json()
    assert all(j["state"] == "succeeded" and j["worker_id"] != rec["id"] for j in jobs)
    acts = [a["action"] for a in cluster.c.get("/api/cluster/audit").json()]
    assert "worker_revoked" in acts


def test_enrolment_is_off_in_hosted_profile(tmp_path, monkeypatch):
    monkeypatch.setenv("DAEDELUS_PROFILE", "hosted")
    monkeypatch.setenv("DAEDELUS_API_TOKENS", ADMIN)
    c = Cluster(tmp_path, monkeypatch)
    try:
        assert c.app.state.cluster.enrollment_allowed is False
        r = c.c.post("/api/cluster/workers", json={"name": "w"},
                     headers={"Authorization": f"Bearer {WT}"})
        assert r.status_code in (401, 403)
        monkeypatch.setenv("DAEDELUS_ALLOW_WORKER_ENROLLMENT", "1")
        assert c.app.state.cluster.enrollment_allowed is True
    finally:
        c.close()


def test_hosted_worker_refuses_join_token_and_plain_http(monkeypatch):
    from daedelus.distributed.worker import control_client_kwargs, main
    monkeypatch.setenv("DAEDELUS_PROFILE", "hosted")
    monkeypatch.setenv("DAEDELUS_WORKER_TOKEN", WT)
    monkeypatch.delenv("DAEDELUS_WORKER_CREDENTIAL", raising=False)
    assert main(["--control", "https://control.example"]) == 2
    with pytest.raises(ValueError, match="plain-HTTP"):
        control_client_kwargs("http://control.example:8765")
    monkeypatch.setenv("DAEDELUS_ALLOW_INSECURE_CONTROL", "1")
    with pytest.raises(ValueError):  # the override is not honoured in the hosted profile
        control_client_kwargs("http://control.example:8765")
    monkeypatch.delenv("DAEDELUS_PROFILE")
    assert control_client_kwargs("http://control:8765") == {}
    assert control_client_kwargs("http://127.0.0.1:8765") == {}
    assert "verify" in control_client_kwargs("https://control.example")


def test_hosted_serve_refuses_public_plaintext(monkeypatch):
    from daedelus.cli import main
    monkeypatch.setenv("DAEDELUS_PROFILE", "hosted")
    monkeypatch.setenv("DAEDELUS_API_TOKENS", ADMIN)
    assert main(["serve", "--host", "0.0.0.0", "--port", "1"]) == 2
    monkeypatch.delenv("DAEDELUS_API_TOKENS")
    assert main(["serve", "--host", "127.0.0.1", "--port", "1"]) == 2  # tokens required


# =============================================================== browser sessions / tickets
@pytest.fixture()
def api(tmp_path, monkeypatch):
    from daedelus.api import create_app
    from daedelus.distributed import service
    monkeypatch.setenv("DAEDELUS_API_TOKENS", ADMIN)
    app = create_app(tmp_path / "ws", studio_dist=tmp_path / "nodist")
    A = {"Authorization": f"Bearer {ADMIN}"}
    c = TestClient(app)
    p1 = c.post("/api/projects", json={"name": "one"}, headers=A).json()["id"]
    p2 = c.post("/api/projects", json={"name": "two"}, headers=A).json()["id"]
    service.reset()
    monkeypatch.setenv("DAEDELUS_API_TOKENS", f"{ADMIN},{SCOPED}:{p1}")
    app = create_app(tmp_path / "ws", studio_dist=tmp_path / "nodist")
    yield app, p1, p2
    service.reset()


def test_session_cookie_attributes_and_csrf(api):
    app, p1, _ = api
    c = TestClient(app)
    assert c.get("/api/auth/config").json()["auth_required"] is True
    assert c.post("/api/auth/session").status_code == 401  # needs the token once
    r = c.post("/api/auth/session", headers={"Authorization": f"Bearer {SCOPED}"})
    assert r.status_code == 200
    sc = r.headers["set-cookie"]
    assert "HttpOnly" in sc and "SameSite=strict" in sc.replace("Strict", "strict")
    assert SCOPED not in sc  # the cookie holds a session id, not the token
    csrf = r.json()["csrf"]
    assert r.json()["projects"] == [p1]
    assert c.get(f"/api/projects/{p1}/artifacts").status_code == 200  # cookie works for reads
    # state change without / with a wrong CSRF token, and from a foreign origin: refused
    body = {"name": "x", "text": "hello"}
    assert c.post(f"/api/projects/{p1}/sources/text", json=body).status_code == 403
    assert c.post(f"/api/projects/{p1}/sources/text", json=body,
                  headers={"X-CSRF-Token": "forged"}).status_code == 403
    assert c.post(f"/api/projects/{p1}/sources/text", json=body,
                  headers={"X-CSRF-Token": csrf, "Origin": "https://evil.example"}
                  ).status_code == 403
    assert c.post(f"/api/projects/{p1}/sources/text", json=body,
                  headers={"X-CSRF-Token": csrf}).status_code == 200
    # the session carries the token's scope
    assert c.get(f"/api/projects/{api[2]}/artifacts").status_code == 403
    assert c.delete("/api/auth/session", headers={"X-CSRF-Token": csrf}).status_code == 200
    assert c.get(f"/api/projects/{p1}/artifacts").status_code == 401
    acts = [a["action"] for a in TestClient(app).get(
        "/api/cluster/audit", headers={"Authorization": f"Bearer {ADMIN}"}).json()]
    assert "csrf_rejected" in acts and "session_created" in acts


def test_session_secure_flag_in_hosted_profile(api, monkeypatch):
    app, _, _ = api
    monkeypatch.setenv("DAEDELUS_PROFILE", "hosted")
    r = TestClient(app).post("/api/auth/session", headers={"Authorization": f"Bearer {ADMIN}"})
    assert "Secure" in r.headers["set-cookie"]


def test_session_dies_when_its_token_is_withdrawn(tmp_path, monkeypatch, api):
    from daedelus.api import create_app
    from daedelus.distributed import service
    app, p1, _ = api
    c = TestClient(app)
    c.post("/api/auth/session", headers={"Authorization": f"Bearer {SCOPED}"})
    assert c.get(f"/api/projects/{p1}/artifacts").status_code == 200
    service.reset()
    monkeypatch.setenv("DAEDELUS_API_TOKENS", ADMIN)  # the scoped token is removed
    app2 = create_app(tmp_path / "ws", studio_dist=tmp_path / "nodist")
    c2 = TestClient(app2)
    c2.cookies = c.cookies
    assert c2.get(f"/api/projects/{p1}/artifacts").status_code == 401


def test_tickets_are_short_lived_scoped_and_single_purpose(api):
    app, p1, p2 = api
    c = TestClient(app)
    S = {"Authorization": f"Bearer {SCOPED}"}
    assert c.post("/api/auth/ticket", json={"project_id": p2, "purpose": "files"},
                  headers=S).status_code == 403  # not this token's project
    t = c.post("/api/auth/ticket", json={"project_id": p1, "purpose": "files"},
               headers=S).json()["ticket"]
    app.state.workspace.project_root(p1).joinpath("hello.txt").write_text("hi")
    nc = TestClient(app)  # no cookie, no header
    assert nc.get(f"/api/projects/{p1}/files/hello.txt?ticket={t}").status_code == 200
    assert nc.get(f"/api/projects/{p1}/artifacts?ticket={t}").status_code == 401  # wrong path
    assert nc.get(f"/api/projects/{p2}/files/hello.txt?ticket={t}").status_code == 401
    assert nc.post(f"/api/projects/{p1}/sources/text?ticket={t}",
                   json={"name": "x", "text": "y"}).status_code == 401  # GET only
    ev = c.post("/api/auth/ticket", json={"project_id": p1, "purpose": "events"},
                headers=S).json()["ticket"]
    st = app.state.sessions
    assert st.redeem(ev, "GET", f"/api/projects/{p1}/events") is not None
    assert st.redeem(ev, "GET", f"/api/projects/{p1}/events") is None  # single use
    assert nc.get(f"/api/projects/{p1}/artifacts?token={SCOPED}").status_code == 401


# =============================================================== manifests / staging
def _fe(bs, data=b"x", mode=0o644):
    return FileEntry(sha256=bs.put_bytes(data), size=len(data), mode=mode)


@pytest.mark.parametrize("rel", ["../escape", "/abs", "a/../../b", "a\\b", "C:/win", "a/./b",
                                 "a//b", "nul\x00byte", "", "x" * 2000])
def test_invalid_manifest_paths_never_leave_staging(tmp_path, rel):
    bs = cas.BlobStore(tmp_path / "blobs")
    m = Manifest(files={rel: _fe(bs)})
    with pytest.raises(cas.BlobError):
        cas.materialize(m, bs, tmp_path / "out")
    assert not (tmp_path / "out").exists()
    assert not [p for p in tmp_path.iterdir() if p.name.startswith(".mat_")]


def test_manifest_structure_checks_and_modes(tmp_path):
    bs = cas.BlobStore(tmp_path / "blobs")
    fe = _fe(bs)
    for files in ({"A.txt": fe, "a.txt": fe}, {"a": fe, "a/b": fe},
                  {"ok": FileEntry(sha256="z" * 64, size=1)}):
        with pytest.raises(cas.BlobError):
            cas.materialize(Manifest(files=files), bs, tmp_path / "o1")
    m = Manifest(files={"suid": _fe(bs, b"s", 0o4777), "exe": _fe(bs, b"e", 0o775)})
    cas.materialize(m, bs, tmp_path / "o2")
    if os.name == "nt":
        return  # POSIX permission bits do not exist on Windows
    assert (tmp_path / "o2" / "suid").stat().st_mode & 0o7777 == 0o755
    assert (tmp_path / "o2" / "exe").stat().st_mode & 0o7777 == 0o755
    m = Manifest(files={"plain": _fe(bs, b"p", 0o666)})
    cas.materialize(m, bs, tmp_path / "o3")
    assert (tmp_path / "o3" / "plain").stat().st_mode & 0o7777 == 0o644


def test_failed_and_cancelled_jobs_publish_nothing(cluster):
    pid = cluster.project("local")
    art = _workbook(cluster, pid)
    st = cluster.app.state.workspace.open(pid)
    a = st.get_artifact(art["id"])
    before = cas.snapshot(st.abs(a.native_dir)).digest()
    cluster.c.put(f"/api/projects/{pid}/execution", json={"target": "cloud_cpu"})
    cluster.worker("w1")
    # an adapter error on the worker (unknown component) changes nothing
    r = cluster.c.post(f"/api/projects/{pid}/artifacts/{art['id']}/edit", json={
        "operations": [{"op": "set_cells", "component_id": "no_such_sheet",
                        "params": {"cells": {"A1": 1}}}], "message": "bad"})
    assert r.status_code >= 400
    assert cas.snapshot(st.abs(a.native_dir)).digest() == before
    native = st.abs(a.native_dir)
    assert not [p for p in native.parent.iterdir() if p.name.endswith((".new", ".old"))]


# =============================================================== path sources / symlinks
def test_path_sources_refused_in_hosted_profile_and_outside_roots(api, tmp_path, monkeypatch):
    app, p1, _ = api
    c = TestClient(app)
    A = {"Authorization": f"Bearer {ADMIN}"}
    (tmp_path / "allowed").mkdir()
    (tmp_path / "allowed" / "x.txt").write_text("x")
    monkeypatch.setenv("DAEDELUS_PROFILE", "hosted")
    r = c.post(f"/api/projects/{p1}/sources/path", json={"path": "/etc/passwd"}, headers=A)
    assert r.status_code == 403
    monkeypatch.setenv("DAEDELUS_PATH_SOURCE_ROOTS", str(tmp_path / "allowed"))
    assert c.post(f"/api/projects/{p1}/sources/path", json={"path": "/etc/passwd"},
                  headers=A).status_code == 403
    assert c.post(f"/api/projects/{p1}/sources/path",
                  json={"path": str(tmp_path / "allowed" / "x.txt")}, headers=A).status_code == 200


def test_repository_symlinks_cannot_pull_in_outside_files(tmp_path):
    from daedelus.ingest import register_path
    from daedelus.store import Workspace
    secret = tmp_path / "secret.txt"
    secret.write_text("TOP SECRET")
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "main.py").write_text("print(1)\n")
    (repo / "inner.txt").write_text("inside")
    try:
        os.symlink(secret, repo / "leak.txt")
    except OSError as exc:  # Windows without Developer Mode or elevation (WinError 1314)
        pytest.skip(f"cannot create symlinks here: {exc}")
    os.symlink(repo / "inner.txt", repo / "alias.txt")
    _, st = Workspace(tmp_path / "ws").create_project("p")
    src = register_path(st, repo, ingest_now=False)
    snap = st.abs(src.locator.path)
    assert not (snap / "leak.txt").exists()
    assert (snap / "alias.txt").read_text() == "inside"
    assert src.metadata["skipped_symlinks"] == ["leak.txt"]
    assert "TOP SECRET" not in "".join(p.read_text() for p in snap.rglob("*") if p.is_file())


# =============================================================== SSRF
@pytest.mark.parametrize("host", ["2130706433", "0x7f.1", "0177.0.0.1", "127.1", "0x7f000001",
                                  "[::ffff:7f00:1]", "[64:ff9b::7f00:1]", "[2002:7f00:1::]",
                                  "[fd00::1]", "[fe80::1]", "169.254.169.254", "0.0.0.0",
                                  "[::]", "198.18.0.1", "100.100.100.200"])
def test_ssrf_alternate_numeric_and_ipv6_forms(host):
    from daedelus.netsafe import FetchRefused, check_url
    with pytest.raises(FetchRefused):
        check_url(f"http://{host}/", resolve=lambda *a, **k: [(0, 0, 0, "", ("8.8.8.8", 80))])


def test_ssrf_metadata_hostnames_and_rebinding_are_refused(monkeypatch):
    from daedelus import netsafe
    for v in ("HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy", "ALL_PROXY",
              "all_proxy"):
        monkeypatch.delenv(v, raising=False)
    meta = lambda *a, **k: [(0, 0, 0, "", ("169.254.169.254", 80))]  # noqa: E731
    with pytest.raises(netsafe.FetchRefused):
        netsafe.check_url("http://metadata.google.internal/", resolve=meta)
    calls: list[str] = []

    def rebinding(host, port, proto=0):  # public for the check, loopback for the connect
        calls.append(host)
        return [(0, 0, 0, "", ("93.184.215.14" if len(calls) == 1 else "127.0.0.1", port))]
    c = netsafe.pinned_client(resolve=rebinding)
    with pytest.raises(netsafe.FetchRefused, match="non-public"):
        netsafe.safe_get(c, "http://rebind.example/", resolve=rebinding)
    assert len(calls) == 2  # the connect re-resolved and re-checked: nothing reached 127.0.0.1


def test_ssrf_pinned_client_connects_only_to_the_validated_address(monkeypatch):
    """A real local HTTP server: the pinned client refuses it because 127.0.0.1 is private,
    even when the URL names a public-looking host."""
    import http.server
    import socketserver
    from daedelus import netsafe
    for v in ("HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy", "ALL_PROXY",
              "all_proxy"):
        monkeypatch.delenv(v, raising=False)
    hits: list[str] = []

    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            hits.append(self.path)
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"internal")

        def log_message(self, *a):
            pass
    srv = socketserver.TCPServer(("127.0.0.1", 0), H)
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    monkeypatch.setenv("DAEDELUS_FETCH_ALLOW_PORTS", str(port))
    try:
        local = lambda *a, **k: [(0, 0, 0, "", ("127.0.0.1", port))]  # noqa: E731
        c = netsafe.pinned_client(resolve=local)
        with pytest.raises(netsafe.FetchRefused):
            netsafe.safe_get(c, f"http://internal.example:{port}/x", resolve=local)
        assert hits == []
    finally:
        srv.shutdown()


def test_ssrf_oversized_body_is_cut_off():
    from daedelus.netsafe import FetchRefused, safe_get
    big = b"x" * 5000

    def handler(req):
        return httpx.Response(200, content=big)
    import daedelus.netsafe as ns
    with httpx.Client(transport=httpx.MockTransport(handler)) as c:
        with pytest.raises(FetchRefused, match="larger"):
            safe_get(c, "https://example.com/", max_bytes=1000,
                     resolve=lambda *a, **k: [(0, 0, 0, "", ("93.184.215.14", 443))])
    assert ns  # module imported


# =============================================================== job isolation
def test_process_sandbox_strips_secrets_and_limits_resources(tmp_path, monkeypatch):
    from daedelus.distributed.sandbox import Sandbox
    monkeypatch.setenv("DAEDELUS_WORKER_TOKEN", "should-not-leak-" + "z" * 20)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-should-not-leak")
    sb = Sandbox(tmp_path, "process")
    rep = sb.probe()
    assert rep["verified"]
    c = rep["controls"]
    assert c["no_secrets_in_env"]
    if os.name != "nt":  # Windows has no POSIX rlimits: the probe reports them as absent
        assert c["rlimit_memory"] and c["no_core_dumps"]
    else:
        assert not c["rlimit_memory"]
    # the process profile does not claim what it cannot do
    assert rep["profile"] == "process"


@pytest.mark.skipif(os.name == "nt", reason="POSIX rlimits; Windows workers have no per-job "
                    "memory limit (documented in SECURITY_MODEL.md)")
def test_process_sandbox_enforces_memory_limit(tmp_path):
    from daedelus.distributed.sandbox import Limits, Sandbox
    sb = Sandbox(tmp_path, "process", limits=Limits(memory_mb=256))
    jd = tmp_path / "job"
    jd.mkdir()
    p = sb.popen([sys.executable, "-c", "b = bytearray(1024*1024*1024); print('allocated')"],
                 jd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    out, err = p.communicate(timeout=60)
    assert p.returncode != 0 and b"allocated" not in out and b"MemoryError" in err


@pytest.mark.skipif(not __import__("shutil").which("bwrap"), reason="bubblewrap not installed")
def test_bwrap_sandbox_isolates_network_filesystem_and_processes(tmp_path):
    from daedelus.distributed.sandbox import Sandbox
    sb = Sandbox(tmp_path, "bwrap")
    rep = sb.probe()
    if not rep.get("verified"):
        pytest.skip(f"bubblewrap cannot create namespaces here: {rep.get('error')}")
    c = rep["controls"]
    assert c["network_isolated"] and c["private_job_dir"] and c["read_only_root"]
    assert c["pid_namespace"] and c["no_secrets_in_env"] and c["no_new_privs"]


def test_code_adapter_not_offered_without_verified_sandbox(tmp_path, monkeypatch):
    from daedelus.distributed.sandbox import Sandbox
    from daedelus.distributed.worker import Worker
    monkeypatch.setenv("DAEDELUS_REQUIRE_SANDBOX", "code")
    sb = Sandbox(tmp_path / "w", "process")
    (tmp_path / "w").mkdir()
    sb.probe()
    w = Worker("http://127.0.0.1:1", WT, adapters=["code", "spreadsheet"], work_dir=tmp_path / "w",
               sandbox=sb)
    assert w.adapters == ["spreadsheet"] and w.refused_adapters == ["code"]


# =============================================================== secrets in logs/artifacts
def test_credentials_absent_from_logs_audit_and_artifacts(cluster):
    rec, cred = cluster.app.state.cluster.creds.create("w", adapters=["spreadsheet", "document"])
    cluster.worker("w", env={"DAEDELUS_WORKER_CREDENTIAL": cred})
    pid = cluster.project("cloud_cpu")
    art = _workbook(cluster, pid)
    cluster.c.post(f"/api/projects/{pid}/artifacts/{art['id']}/edit", json={
        "operations": [{"op": "set_cells", "component_id": "data",
                        "params": {"cells": {"C2": "=A2+B2"}}}], "message": "m"})
    secrets_ = [parse_credential(cred)[1], WT]
    root = Path(cluster.tmp)
    leaks = []
    for p in root.rglob("*"):
        if p.is_file() and p.stat().st_size < 50_000_000:
            try:
                data = p.read_bytes()
            except PermissionError:  # Windows locks SQLite's shared-memory index while the
                if p.name.endswith("-shm"):  # database is open; it holds no row data
                    continue
                raise
            leaks += [str(p) for s in secrets_ if s.encode() in data]
    jobs = cluster.c.get(f"/api/projects/{pid}/jobs").json()
    for j in jobs:
        d = json.dumps(cluster.c.get(f"/api/projects/{pid}/jobs/{j['id']}").json())
        leaks += [j["id"] for s in secrets_ if s in d]
    assert leaks == []


def test_hosted_profile_refuses_requests_forwarded_over_plain_http(tmp_path, monkeypatch):
    from daedelus.api import create_app
    from daedelus.distributed import service
    monkeypatch.setenv("DAEDELUS_PROFILE", "hosted")
    monkeypatch.setenv("DAEDELUS_API_TOKENS", ADMIN)
    c = TestClient(create_app(tmp_path / "ws", studio_dist=tmp_path / "nodist"))
    A = {"Authorization": f"Bearer {ADMIN}"}
    assert c.get("/api/projects", headers={**A, "X-Forwarded-Proto": "http"}).status_code == 400
    assert c.get("/api/projects", headers={**A, "X-Forwarded-Proto": "https"}).status_code == 200
    assert c.get("/api/health").status_code == 200  # direct loopback health check
    service.reset()
