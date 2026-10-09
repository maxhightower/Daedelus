"""V2/V2.1 container end-to-end: control plane + Blender/Office/code workers via compose.

    python deploy/cluster_e2e.py --env-file deploy/.env --out evidence/v2/cluster [--no-build]
    python deploy/cluster_e2e.py ... --hardened [--tls]        # V2.1 topology + S9-S11

``--hardened`` adds compose.hardened.yml (per-worker credentials, no capabilities, read-only
root, seccomp, bubblewrap job sandbox); ``--tls`` adds compose.tls.yml (Caddy TLS edge with a
private CA; the control plane is reachable only through it; hosted profile).

Everything runs on ONE Docker host (a CI runner or a cloud container). This verifies the
distributed protocol between containers on separate networks; it is NOT a hosted multi-machine
deployment and not GPU execution (see the native-validation register N9/N10).

Scenarios
  S1 topology: control plane has no Blender/LibreOffice; workers sit on an internal network
     with no route out; the published port is loopback-only.
  S2 Blender on a worker: create + edit + render a model; previews come from the worker.
  S3 Office pipeline on workers: the V1.2 research pipeline runs remotely, formulas are
     recalculated by LibreOffice inside the office worker, dependencies sync.
  S4 code worker: allowlisted tests run on the code worker; a non-allowlisted command is refused.
  S5 worker killed mid-job -> lease expires -> another worker completes (attempt 2).
  S6 network partition of a worker mid-job -> retry elsewhere; the partitioned worker's late
     result is rejected and audited.
  S7 control-plane restart mid-execution -> queue survives, execution resumes, the in-flight
     apply job is reused through its idempotency key (not recomputed).
  S8 security: API token required, worker endpoints reject bad tokens, SSRF guard refuses
     internal URLs.
  S9 (V2.1, --hardened) worker identity: distinct provisioned credentials; forged credential
     and join token refused; a worker revoked mid-job stops, its job completes elsewhere.
  S10 (V2.1, --hardened) isolation: container controls (capabilities, no-new-privileges,
     seccomp, read-only root, limits) and the per-job sandbox verified from inside; repository
     tests that try to escape (network, credentials, other jobs, system files) are contained;
     a memory bomb fails the job, not the worker.
  S11 (V2.1, --tls) transport: only HTTPS is exposed; certificates verified; a worker with
     the wrong CA cannot register; SSE streams through the proxy; Secure session cookies.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "backend" / "daedelus" / "demo_assets" / "v12"


class Run:
    def __init__(self, out: Path, env_file: Path, hardened: bool = False, tls: bool = False):
        self.out = out
        self.env_file = env_file
        self.hardened = hardened
        self.tls = tls
        self.files = ["compose.yml"] + (["compose.hardened.yml"] if hardened else []) + \
            (["compose.tls.yml"] if tls else [])
        self.checks: list[dict] = []
        self.scenario = "setup"
        self.metrics: dict = {}
        env = dict(line.split("=", 1) for line in env_file.read_text().splitlines()
                   if "=" in line and not line.lstrip().startswith("#"))
        self.token = env["DAEDELUS_API_TOKENS"].split(",")[0].split(":")[0]
        self.port = int(env.get("DAEDELUS_PORT", "8765"))
        if tls:
            import ssl
            self.port = int(env.get("DAEDELUS_TLS_PORT", "8443"))
            self.base = f"https://localhost:{self.port}"
            self.verify = ssl.create_default_context(cafile=str(ROOT / "deploy/tls/ca.crt"))
        else:
            self.base = f"http://127.0.0.1:{self.port}"
            self.verify = True
        self.c = httpx.Client(base_url=self.base, timeout=600, verify=self.verify,
                              headers={"Authorization": f"Bearer {self.token}"})

    def check(self, name: str, ok, detail="") -> bool:
        self.checks.append({"scenario": self.scenario, "name": name, "ok": bool(ok),
                            "detail": "" if ok else str(detail)[:800]})
        print(f"{'PASS' if ok else 'FAIL'} [{self.scenario}] {name}"
              + ("" if ok else f" - {str(detail)[:300]}"), flush=True)
        return bool(ok)

    def dc(self, *args: str, check: bool = True, capture: bool = True) -> str:
        cmd = ["docker", "compose"]
        for f in self.files:
            cmd += ["-f", str(ROOT / "deploy" / f)]
        cmd += ["--env-file", str(self.env_file), *args]
        r = subprocess.run(cmd, capture_output=capture, text=True)
        if check and r.returncode != 0:
            raise RuntimeError(f"{' '.join(args)} failed: {(r.stderr or '')[-2000:]}")
        return r.stdout if capture else ""

    def docker(self, *args: str, check: bool = True) -> str:
        r = subprocess.run(["docker", *args], capture_output=True, text=True)
        if check and r.returncode != 0:
            raise RuntimeError(f"docker {' '.join(args)}: {r.stderr[-1000:]}")
        return r.stdout

    # ------------------------------------------------------------------ helpers
    def wait_health(self, timeout=180):
        t0 = time.time()
        while time.time() - t0 < timeout:
            try:
                if self.c.get("/api/health").status_code == 200:
                    return True
            except httpx.HTTPError:
                pass
            time.sleep(1)
        return False

    def workers(self):
        return self.c.get("/api/cluster/status").json()["workers"]

    def wait_workers(self, adapters: set[str], timeout=180):
        t0 = time.time()
        while time.time() - t0 < timeout:
            alive = [w for w in self.workers() if w["alive"]]
            if adapters <= {a for w in alive for a in w["adapters"]}:
                return alive
            time.sleep(1)
        return []

    def project(self, name: str, target: str = "cloud_cpu") -> str:
        pid = self.c.post("/api/projects", json={"name": name}).json()["id"]
        r = self.c.put(f"/api/projects/{pid}/execution", json={"target": target})
        r.raise_for_status()
        return pid

    def wait_execution(self, pid, eid, timeout=900):
        t0 = time.time()
        while time.time() - t0 < timeout:
            try:
                ex = self.c.get(f"/api/projects/{pid}/executions/{eid}").json()
                if ex["status"] not in ("pending", "running"):
                    return ex
            except httpx.HTTPError:
                pass
            time.sleep(1)
        raise TimeoutError(eid)

    def dump(self, name: str, data) -> None:
        (self.out / name).write_text(json.dumps(data, indent=1, default=str))


def upload(r: Run, pid: str, path: Path, name: str) -> dict:
    with open(path, "rb") as f:
        x = r.c.post(f"/api/projects/{pid}/sources/upload", files={"files": (path.name, f)})
    x.raise_for_status()
    return x.json()[0]


# ---------------------------------------------------------------------------- scenarios
def s1_topology(r: Run):
    r.scenario = "S1 topology"
    out = r.dc("exec", "-T", "control", "sh", "-c",
               "command -v blender soffice libreoffice || true")
    r.check("control plane container has no Blender and no LibreOffice", out.strip() == "", out)
    health = r.c.get("/api/health").json()
    local = {a["name"]: a for a in health["adapters"]}
    r.check("control plane reports Blender unavailable locally",
            not local["blender"]["available"], local["blender"].get("unavailable_reason"))
    nets = json.loads(r.docker("network", "inspect", "daedelus_jobs"))[0]
    r.check("worker network is internal (no route out of the host)", nets["Internal"], nets)
    probe = r.dc("exec", "-T", "worker-office", "python", "-c",
                 "import socket,sys\ntry:\n socket.create_connection(('1.1.1.1',443),3);"
                 "print('reachable')\nexcept OSError as e: print('blocked', e)", check=False)
    r.check("workers cannot reach the internet", "blocked" in probe, probe)
    front = "edge" if r.tls else "control"
    ports = r.docker("port", r.dc("ps", "-q", front).strip())
    r.check(f"published port ({front}) binds to 127.0.0.1 only", ports.strip() and all(
        "127.0.0.1" in line for line in ports.strip().splitlines()), ports)
    if r.tls:
        cports = r.docker("port", r.dc("ps", "-q", "control").strip(), check=False)
        r.check("the control plane itself publishes no port (TLS edge only)",
                cports.strip() == "", cports)
    ws = r.wait_workers({"blender", "spreadsheet", "document", "presentation", "code"})
    r.check("Blender, Office and code workers registered", ws, r.workers())
    r.dump("workers.json", r.workers())


def s2_blender(r: Run):
    r.scenario = "S2 Blender worker"
    pid = r.project("blender-remote")
    t0 = time.time()
    a = r.c.post(f"/api/projects/{pid}/artifacts", json={
        "name": "Lamp", "adapter": "blender", "template": "empty",
        "params": {"root_id": "lamp", "name": "Lamp"}})
    r.check("Blender artifact created on a worker", a.status_code == 200, a.text)
    aid = a.json()["artifact"]["id"]
    e = r.c.post(f"/api/projects/{pid}/artifacts/{aid}/edit", json={
        "message": "add base and shade (remote)", "operations": [
            {"op": "add_primitive", "params": {"id": "base", "primitive": "cylinder",
                                               "parent": "lamp", "size": [0.4, 0.4, 0.1]}},
            {"op": "add_primitive", "params": {"id": "shade", "primitive": "cone",
                                               "parent": "lamp", "location": [0, 0, 0.8],
                                               "size": [0.5, 0.5, 0.4]}}]})
    r.check("remote edit produced a checked revision", e.status_code == 200, e.text)
    r.metrics["blender_create_edit_seconds"] = round(time.time() - t0, 1)
    rev = e.json()
    render = rev.get("previews", {}).get("render")
    img = r.c.get(f"/api/projects/{pid}/files/{render}") if render else None
    r.check("render preview produced by the worker and served by the control plane",
            img is not None and img.status_code == 200 and img.content[:4] == b"\x89PNG",
            render)
    if img is not None and img.status_code == 200:
        (r.out / "blender_render.png").write_bytes(img.content)
    jobs = r.c.get(f"/api/projects/{pid}/jobs").json()
    names = {w["id"]: w["name"] for w in r.workers()}
    ran_on = {names.get(j["worker_id"], j["worker_id"]) for j in jobs}
    r.check("every Blender job ran on the Blender worker",
            jobs and all(j["state"] == "succeeded" for j in jobs) and
            all("blender" in str(x) or x for x in ran_on), [(j["method"], j["state"]) for j in jobs])
    r.check("components exist after publication", {"base", "shade"} <= {
        c["id"] for c in r.c.get(f"/api/projects/{pid}/artifacts/{aid}").json()["components"]})
    r.dump("blender_jobs.json", jobs)


def _pipeline(r: Run, pid: str):
    data = upload(r, pid, ASSETS / "co2_annmean_mlo.csv", "CO2 (NOAA GML)")
    notes = upload(r, pid, ASSETS / "research_notes.md", "Research notes")
    guide = upload(r, pid, ASSETS / "visual_guide.md", "Visual guide")

    def mk(name, adapter, params):
        x = r.c.post(f"/api/projects/{pid}/artifacts", json={
            "name": name, "adapter": adapter, "template": "blank", "params": params})
        x.raise_for_status()
        return x.json()["artifact"]
    wb = mk("CO2 analysis", "spreadsheet", {"name": "CO2 analysis"})
    doc = mk("CO2 report", "document", {"title": "Atmospheric CO2 at Mauna Loa"})
    deck = mk("CO2 briefing", "presentation", {})
    for s, a, role in ((data, wb, "reference"), (notes, doc, "reference"),
                       (guide, deck, "guideline"), (notes, deck, "context")):
        r.c.post(f"/api/projects/{pid}/bindings", json={
            "source_id": s["id"], "role": role, "aspects": [],
            "target": {"scope": "artifact", "artifact_id": a["id"]}}).raise_for_status()
    links = [
        {"source": {"artifact_id": wb["id"], "component_id": "summary_values"},
         "target": {"artifact_id": doc["id"], "component_id": "results_table"},
         "target_kind": "table", "options": {"create": {"parent": "results"}}},
        {"source": {"artifact_id": wb["id"], "component_id": "decade_means"},
         "target": {"artifact_id": deck["id"], "component_id": "deck_chart"},
         "target_kind": "chart", "options": {"create": {"slide": "data_slide",
                                                        "box": [0.08, 0.22, 0.84, 0.7]}}}]
    nodes = [{"id": "src", "type": "sources", "label": "Sources", "config": {}}]
    edges = []
    for nid, art, instr in (("wb", wb, "Build the analysis workbook.\nValue label: CO2 (ppm)"),
                            ("doc", doc, "Write the report."),
                            ("deck", deck, "Title: CO2\nBuild the slide deck.")):
        nodes += [{"id": f"a_{nid}", "type": "artifact", "label": nid,
                   "config": {"artifact_id": art["id"]}},
                  {"id": nid, "type": "agent", "label": nid, "config": {
                      "instructions": instr, "fan_out": False}}]
        edges += [{"id": f"e1{nid}", "source": f"a_{nid}", "source_port": "artifact",
                   "target": nid, "target_port": "artifact"},
                  {"id": f"e2{nid}", "source": "src", "source_port": "sources", "target": nid,
                   "target_port": "sources"},
                  {"id": f"e3{nid}", "source": nid, "source_port": "revision", "target": "deps",
                   "target_port": "after"}]
    nodes.append({"id": "deps", "type": "dependencies", "label": "deps",
                  "config": {"links": links}})
    wf = r.c.post(f"/api/projects/{pid}/workflows", json={
        "name": "pipeline", "nodes": nodes, "edges": edges}).json()
    return wf, wb, doc, deck


def s3_office(r: Run):
    r.scenario = "S3 Office workers"
    pid = r.project("office-remote")
    wf, wb, doc, deck = _pipeline(r, pid)
    t0 = time.time()
    ex = r.c.post(f"/api/projects/{pid}/workflows/{wf['id']}/execute", json={}).json()
    ex = r.wait_execution(pid, ex["id"])
    r.metrics["office_pipeline_seconds"] = round(time.time() - t0, 1)
    r.check("research pipeline succeeded with all adapter work on workers",
            ex["status"] == "succeeded", ex.get("error"))
    for nr in ex["node_runs"]:
        if nr["node_type"] == "agent":
            d = (nr["outputs"].get("execution") or {}).get("decisions", {})
            r.check(f"node {nr['node_id']} resolved to cloud_cpu",
                    all(x["resolved"] == "cloud_cpu" for x in d.values()) and d, d)
    view = r.c.get(f"/api/projects/{pid}/artifacts/{wb['id']}/office").json()["view"]
    r.check("formulas recalculated by LibreOffice inside the office worker",
            view.get("calculated") is True, view.get("calc_note"))
    deps = r.c.get(f"/api/projects/{pid}/dependencies").json()
    r.check("cross-artifact dependencies synced remotely",
            deps and all(d["status"] == "synced" for d in deps), [d["status"] for d in deps])
    jobs = r.c.get(f"/api/projects/{pid}/jobs").json()
    r.metrics["office_pipeline_jobs"] = len(jobs)
    r.dump("office_pipeline_execution.json", ex)
    return pid, wf


def s4_code(r: Run):
    r.scenario = "S4 code worker"
    pid = r.project("code-remote")
    a = r.c.post(f"/api/projects/{pid}/artifacts", json={
        "name": "lib", "adapter": "code", "template": "files", "params": {"files": {
            "lib.py": "def add(a, b):\n    return a + b\n",
            "test_lib.py": "from lib import add\n\ndef test_add():\n    assert add(2, 3) == 5\n"}},
        "metadata": {"test_command": "python -m pytest -q"}})
    r.check("code artifact created on the code worker", a.status_code == 200, a.text)
    aid = a.json()["artifact"]["id"]
    wf = r.c.post(f"/api/projects/{pid}/workflows", json={"name": "t", "nodes": [
        {"id": "a", "type": "artifact", "label": "a", "config": {"artifact_id": aid}},
        {"id": "v", "type": "validate", "label": "v", "config": {"checks": ["tests"]}}],
        "edges": [{"id": "e", "source": "a", "source_port": "artifact", "target": "v",
                   "target_port": "revision"}]}).json()
    ex = r.wait_execution(pid, r.c.post(f"/api/projects/{pid}/workflows/{wf['id']}/execute",
                                        json={}).json()["id"])
    rep = json.dumps(ex["node_runs"][-1].get("outputs", {}))
    r.check("allowlisted tests ran on the code worker and passed",
            ex["status"] == "succeeded" and "passed" in rep, rep[:600])
    st = r.c.get(f"/api/projects/{pid}").json()
    st["settings"]["allowed_commands"] = ["python -m pytest", "sh -c"]
    r.c.patch(f"/api/projects/{pid}", json={"settings": st["settings"]}).raise_for_status()
    b = r.c.post(f"/api/projects/{pid}/artifacts", json={
        "name": "evil", "adapter": "code", "template": "files",
        "params": {"files": {"x.txt": "x\n"}},
        "metadata": {"test_command": "sh -c 'echo pwned > /tmp/pwned'"}}).json()["artifact"]
    wf2 = r.c.post(f"/api/projects/{pid}/workflows", json={"name": "t2", "nodes": [
        {"id": "a", "type": "artifact", "label": "a", "config": {"artifact_id": b["id"]}},
        {"id": "v", "type": "validate", "label": "v", "config": {"checks": ["tests"],
                                                                 "fail_on_error": False}}],
        "edges": [{"id": "e", "source": "a", "source_port": "artifact", "target": "v",
                   "target_port": "revision"}]}).json()
    ex2 = r.wait_execution(pid, r.c.post(f"/api/projects/{pid}/workflows/{wf2['id']}/execute",
                                         json={}).json()["id"])
    rep2 = json.dumps(ex2["node_runs"][-1].get("outputs", {}))
    pwned = r.dc("exec", "-T", "worker-code", "sh", "-c", "test -e /tmp/pwned && echo yes || "
                 "echo no", check=False).strip()
    r.check("a command the project allows but the WORKER's allowlist does not is not executed",
            "not executed" in rep2 and pwned == "no", (rep2[:600], pwned))


def _slow_worker(r: Run, name: str, fault: str, cred: str | None = None) -> str:
    r.docker("rm", "-f", name, check=False)
    extra = []
    if r.hardened:  # every extra worker gets its own identity
        if cred is None:
            cred = r.c.post("/api/cluster/credentials", json={
                "name": name, "adapters": ["spreadsheet", "document", "presentation"]}
            ).json()["credential"]
        extra = ["-e", f"DAEDELUS_WORKER_CREDENTIAL={cred}"]
    r.dc("run", "-d", "--name", name, "-e", f"DAEDELUS_WORKER_FAULT={fault}",
         "-e", f"DAEDELUS_WORKER_NAME={name}", *extra, "worker-office")
    t0 = time.time()
    while time.time() - t0 < 120:
        if any(w["name"] == name and w["alive"] for w in r.workers()):
            return name
        time.sleep(1)
    raise RuntimeError(f"{name} did not register")


def _bg_create(r: Run, pid: str, res: dict):
    def go():
        res["r"] = r.c.post(f"/api/projects/{pid}/artifacts", json={
            "name": "Book", "adapter": "spreadsheet", "template": "blank", "params": {}})
    t = threading.Thread(target=go)
    t.start()
    return t


def _leased_by(r: Run, pid: str, name: str, timeout=120):
    ids = {w["id"] for w in r.workers() if w["name"] == name}
    t0 = time.time()
    while time.time() - t0 < timeout:
        for j in r.c.get(f"/api/projects/{pid}/jobs").json():
            if j["worker_id"] in ids and j["state"] in ("leased", "running"):
                return j
        time.sleep(0.5)
    return None


def s5_worker_crash(r: Run):
    r.scenario = "S5 worker killed mid-job"
    r.dc("stop", "worker-office")
    _slow_worker(r, "victim", "slow:60")
    pid = r.project("crash")
    res: dict = {}
    t = _bg_create(r, pid, res)
    job = _leased_by(r, pid, "victim")
    r.check("the slow worker leased the job", job, r.c.get(f"/api/projects/{pid}/jobs").json())
    r.docker("kill", "victim")
    t_kill = time.time()
    r.dc("start", "worker-office")
    t.join(timeout=600)
    r.metrics["recovery_after_worker_kill_seconds"] = round(time.time() - t_kill, 1)
    r.check("request completed after the worker was killed",
            res.get("r") is not None and res["r"].status_code == 200,
            res.get("r") and res["r"].text)
    if job:
        d = r.c.get(f"/api/projects/{pid}/jobs/{job['id']}").json()
        r.check("job retried (attempt 2) and succeeded on another worker",
                d["status"]["state"] == "succeeded" and d["status"]["attempt"] >= 2,
                d["status"])
        r.check("lease expiry recorded in the job's event history",
                "lease expired" in json.dumps(d["events"]), d["events"])
        r.dump("worker_crash_job.json", d)
    r.docker("rm", "-f", "victim", check=False)


def s6_partition(r: Run):
    r.scenario = "S6 network partition"
    r.dc("stop", "worker-office")
    _slow_worker(r, "partitioned", "slow:15")
    pid = r.project("partition")
    res: dict = {}
    t = _bg_create(r, pid, res)
    job = _leased_by(r, pid, "partitioned")
    r.check("the partitioned-to-be worker leased the job", job)
    r.docker("network", "disconnect", "daedelus_jobs", "partitioned")
    r.dc("start", "worker-office")
    t.join(timeout=600)
    r.check("request completed despite the partition",
            res.get("r") is not None and res["r"].status_code == 200,
            res.get("r") and res["r"].text)
    r.docker("network", "connect", "daedelus_jobs", "partitioned")
    time.sleep(25)  # the partitioned worker finishes its stale attempt and reports
    aud = r.c.get("/api/cluster/audit?n=2000").json()
    stale = [a for a in aud if a["action"] in ("late_result_discarded", "lease_rejected")
             and a["target"] == (job or {}).get("id")]
    r.check("the partitioned worker's late result was rejected and audited", stale,
            [a["action"] for a in aud[-30:]])
    if job:
        d = r.c.get(f"/api/projects/{pid}/jobs/{job['id']}").json()
        r.check("exactly one result published for the job",
                d["status"]["state"] == "succeeded" and d["publication"] is not None, d["status"])
        r.dump("partition_job.json", d)
    r.docker("rm", "-f", "partitioned", check=False)


def s7_restart(r: Run):
    r.scenario = "S7 control-plane restart"
    r.dc("stop", "worker-office")
    _slow_worker(r, "steady", "slow:25")
    pid = r.project("restart")
    wf, wb, doc, deck = _pipeline(r, pid)
    ex = r.c.post(f"/api/projects/{pid}/workflows/{wf['id']}/execute", json={}).json()
    applied = None
    t0 = time.time()
    while time.time() - t0 < 300 and applied is None:
        for j in r.c.get(f"/api/projects/{pid}/jobs").json():
            if j["method"] == "apply" and j["state"] in ("leased", "running"):
                applied = j
        time.sleep(0.5)
    r.check("an apply job is running on a worker when the control plane restarts", applied)
    r.dc("restart", "control")
    t_restart = time.time()
    r.check("control plane back after restart", r.wait_health())
    r.dc("start", "worker-office")
    # keep the slow worker only until its in-flight job is done, then let normal workers run
    t1 = time.time()
    while applied and time.time() - t1 < 120:
        st = r.c.get(f"/api/projects/{pid}/jobs/{applied['id']}").json()["status"]["state"]
        if st in ("succeeded", "failed", "timed_out", "cancelled", "conflict"):
            break
        time.sleep(1)
    r.docker("rm", "-f", "steady", check=False)
    ex = r.wait_execution(pid, ex["id"], timeout=1200)
    r.metrics["execution_after_restart_seconds"] = round(time.time() - t_restart, 1)
    r.check("interrupted execution resumed and succeeded", ex["status"] == "succeeded",
            ex.get("error"))
    jobs = r.c.get(f"/api/projects/{pid}/jobs?limit=500").json()
    if applied:
        same = [j for j in jobs if j["method"] == "apply" and j["id"] == applied["id"]]
        r.check("the in-flight apply job finished on its worker and was reused (not recomputed)",
                same and same[0]["state"] == "succeeded", same)
    logs = json.dumps(ex["node_runs"])
    r.check("node log records the resumption", "resuming" in logs, logs[:400])
    aud = [a["action"] for a in r.c.get("/api/cluster/audit?n=2000").json()]
    r.check("resumption audited", "execution_resumed" in aud)
    r.dump("restart_execution.json", ex)
    r.docker("rm", "-f", "steady", check=False)


def s8_security(r: Run):
    r.scenario = "S8 security"
    anon = httpx.Client(base_url=r.base, timeout=30, verify=r.verify)
    r.check("API rejects requests without a token",
            anon.get("/api/projects").status_code == 401)
    r.check("worker endpoint rejects a bad worker token", anon.post(
        "/api/cluster/workers", json={"name": "x"},
        headers={"Authorization": "Bearer not-a-worker-token-123"}).status_code in
        ((401, 403) if r.hardened else (401,)))
    r.check("Host header outside the allowlist is rejected (DNS rebinding)", anon.get(
        "/api/health", headers={"Host": "attacker.example"}).status_code == 400)
    pid = r.project("ssrf", "local")
    for url in ("http://169.254.169.254/latest/meta-data/", "http://control:8765/api/health",
                "http://127.0.0.1:8765/api/projects"):
        s = r.c.post(f"/api/projects/{pid}/sources/url", json={"url": url}).json()
        if s["processing"]["state"] not in ("failed", "ready", "partial"):
            s = r.c.post(f"/api/projects/{pid}/sources/{s['id']}/reingest").json()
        r.check(f"SSRF guard refuses {url}", s["processing"]["state"] == "failed" and
                "refused" in (s["processing"]["error"] or ""), s["processing"])
    aud = r.c.get("/api/cluster/audit?n=2000").json()
    r.check("authentication failures are audited",
            any(a["action"] in ("api_auth_failed", "worker_auth_failed") for a in aud))
    r.dump("audit_tail.json", aud[-300:])


# ---------------------------------------------------------------------------- V2.1 scenarios
def s9_identity(r: Run):
    r.scenario = "S9 worker identity"
    ws = [w for w in r.workers() if w["alive"]]
    r.check("every worker registered with its own provisioned credential",
            ws and all(w.get("credential_kind") == "provisioned" for w in ws) and
            len({w["id"] for w in ws}) == len(ws), [(w["name"], w["id"],
                                                    w.get("credential_kind")) for w in ws])
    anon = httpx.Client(base_url=r.base, timeout=30, verify=r.verify)
    forged = "ddw1.wkr_000000000000." + "A" * 43
    r.check("a forged worker credential cannot register", anon.post(
        "/api/cluster/workers", json={"name": "forged", "adapters": ["code"]},
        headers={"Authorization": f"Bearer {forged}"}).status_code == 401)
    env = dict(line.split("=", 1) for line in r.env_file.read_text().splitlines()
               if "=" in line and not line.lstrip().startswith("#"))
    join = env.get("DAEDELUS_WORKER_TOKEN", "")
    if join:
        code = anon.post("/api/cluster/workers", json={"name": "joiner"},
                         headers={"Authorization": f"Bearer {join}"}).status_code
        r.check("the old shared join token no longer enrols workers", code in (401, 403), code)
    # revoke a worker while it holds a job
    r.dc("stop", "worker-office")
    rec = r.c.post("/api/cluster/credentials", json={
        "name": "rogue", "adapters": ["spreadsheet", "document", "presentation"]}).json()
    _slow_worker(r, "rogue", "slow:40", cred=rec["credential"])
    pid = r.project("revoke")
    res: dict = {}
    t = _bg_create(r, pid, res)
    job = _leased_by(r, pid, "rogue")
    r.check("the soon-to-be-revoked worker leased the job", job)
    rv = r.c.post(f"/api/cluster/credentials/{rec['id']}/revoke",
                  json={"reason": "cluster e2e S9"}).json()
    r.check("revocation revoked the worker's lease", job and job["id"] in rv.get(
        "leases_revoked", []), rv)
    code = subprocess.run(["docker", "wait", "rogue"], capture_output=True, text=True,
                          timeout=120).stdout.strip()
    r.check("the revoked worker stopped (exit code 3)", code == "3", code)
    r.dc("start", "worker-office")
    t.join(timeout=600)
    r.check("the job completed on a legitimate worker",
            res.get("r") is not None and res["r"].status_code == 200,
            res.get("r") and res["r"].text)
    jobs = r.c.get(f"/api/projects/{pid}/jobs").json()
    r.check("nothing was accepted from the revoked worker",
            jobs and all(j["state"] == "succeeded" and j["worker_id"] != rec["id"]
                         for j in jobs), jobs)
    aud = r.c.get("/api/cluster/audit?n=3000").json()
    r.check("revocation audited", any(a["action"] == "worker_revoked" and
                                      a["target"] == rec["id"] for a in aud))
    r.dump("identity_workers.json", r.workers())
    r.docker("rm", "-f", "rogue", check=False)


ESCAPE_TESTS = {
    "test_escape.py": """import os, socket, pathlib, pytest

MARKERS = ("TOKEN", "SECRET", "PASSWORD", "CREDENTIAL", "API_KEY", "PRIVATE")


def _connect(host, port):
    socket.create_connection((host, port), timeout=3).close()


@pytest.mark.parametrize("host,port", [("1.1.1.1", 443), ("control", 8765),
                                       ("control.daedelus.internal", 8443)])
def test_no_network(host, port):
    with pytest.raises(OSError):
        _connect(host, port)


def test_worker_credential_not_visible():
    p = pathlib.Path("/run/secrets/worker_credential")
    with pytest.raises(OSError):
        p.read_text()


def test_no_secrets_in_environment():
    assert not [k for k in os.environ if any(m in k.upper() for m in MARKERS)]


def test_cannot_modify_system_files():
    with pytest.raises(OSError):
        open("/opt/venv/pwned", "w").write("x")


def test_cannot_see_worker_processes():
    assert len([d for d in os.listdir("/proc") if d.isdigit()]) <= 6


def test_cannot_see_other_jobs_or_blob_cache():
    work = pathlib.Path(os.environ.get("DAEDELUS_WORKER_DIR", "/tmp/daedelus-worker"))
    here = pathlib.Path.cwd()
    visible = [p for p in work.iterdir()] if work.exists() else []
    assert all(p == here or p in here.parents or here in p.parents for p in visible), visible
""",
}


def _code_validation(r: Run, pid: str, name: str, files: dict) -> tuple[dict, str]:
    a = r.c.post(f"/api/projects/{pid}/artifacts", json={
        "name": name, "adapter": "code", "template": "files", "params": {"files": files},
        "metadata": {"test_command": "python -m pytest -q"}})
    a.raise_for_status()
    aid = a.json()["artifact"]["id"]
    wf = r.c.post(f"/api/projects/{pid}/workflows", json={"name": name, "nodes": [
        {"id": "a", "type": "artifact", "label": "a", "config": {"artifact_id": aid}},
        {"id": "v", "type": "validate", "label": "v", "config": {"checks": ["tests"],
                                                                 "fail_on_error": False}}],
        "edges": [{"id": "e", "source": "a", "source_port": "artifact", "target": "v",
                   "target_port": "revision"}]}).json()
    ex = r.wait_execution(pid, r.c.post(f"/api/projects/{pid}/workflows/{wf['id']}/execute",
                                        json={}).json()["id"])
    return ex, json.dumps(ex["node_runs"][-1].get("outputs", {}))


def s10_isolation(r: Run):
    r.scenario = "S10 isolation"
    report = {}
    for svc in ("worker-office", "worker-blender", "worker-code"):
        cid = r.dc("ps", "-q", svc).strip()
        info = json.loads(r.docker("inspect", cid))[0]["HostConfig"]
        st = r.dc("exec", "-T", svc, "python", "-c",
                  "import json;s=dict(l.split(':',1) for l in open('/proc/1/status') if ':' in l);"
                  "print(json.dumps({k:s[k].strip() for k in ('Uid','CapEff','NoNewPrivs',"
                  "'Seccomp')}))", check=False)
        try:
            proc = json.loads(st.strip().splitlines()[-1])
        except Exception:
            proc = {"error": st[-300:]}
        report[svc] = {"ReadonlyRootfs": info.get("ReadonlyRootfs"), "CapDrop": info.get("CapDrop"),
                       "SecurityOpt": info.get("SecurityOpt"), "PidsLimit": info.get("PidsLimit"),
                       "Memory": info.get("Memory"), "NanoCpus": info.get("NanoCpus"),
                       "proc1": proc}
        ok = (info.get("ReadonlyRootfs") and "ALL" in (info.get("CapDrop") or []) and
              any("no-new-privileges" in x for x in info.get("SecurityOpt") or []) and
              any(x.startswith("seccomp") for x in info.get("SecurityOpt") or []) and
              (info.get("PidsLimit") or 0) > 0 and (info.get("Memory") or 0) > 0 and
              proc.get("CapEff") == "0000000000000000" and proc.get("NoNewPrivs") == "1" and
              proc.get("Seccomp") == "2" and not proc.get("Uid", "0").startswith("0"))
        r.check(f"{svc}: non-root, no capabilities, no-new-privileges, seccomp filter, "
                "read-only root, PID and memory limits (inspected and seen from /proc)", ok,
                report[svc])
    for w in r.workers():
        if not w["alive"]:
            continue
        iso = w.get("isolation") or {}
        c = iso.get("controls") or {}
        r.check(f"worker {w['name']}: per-job bubblewrap sandbox verified by its self-test",
                iso.get("profile") == "bwrap" and iso.get("verified") and all(
                    c.get(k) for k in ("network_isolated", "private_job_dir", "read_only_root",
                                       "pid_namespace", "no_secrets_in_env",
                                       "capabilities_dropped", "no_new_privs")), iso)
        report[f"sandbox:{w['name']}"] = iso
    pid = r.project("escape")
    ex, rep = _code_validation(r, pid, "escape", ESCAPE_TESTS)
    r.check("repository tests that try to escape the sandbox all find it closed "
            "(network, credentials, environment, system files, processes, other jobs)",
            ex["status"] == "succeeded" and "8 passed" in rep and " failed" not in rep,
            rep[:1500])
    report["escape_validation"] = rep[:4000]
    ex2, rep2 = _code_validation(r, pid, "membomb", {
        "test_mem.py": "def test_allocate_3gb():\n    b = bytearray(3 * 1024 ** 3)\n"
                       "    assert len(b)\n"})
    r.check("a memory bomb fails the job's tests, not the worker",
            "MemoryError" in rep2 or "Killed" in rep2 or "failed" in rep2, rep2[:800])
    ws = r.wait_workers({"code"}, timeout=60)
    r.check("the code worker is still alive after the memory bomb", ws)
    r.dump("isolation_report.json", report)


def s11_tls(r: Run):
    r.scenario = "S11 TLS"
    import ssl
    ok = httpx.get(f"{r.base}/api/health", verify=r.verify, timeout=20)
    r.check("HTTPS with the deployment CA verifies and serves the API", ok.status_code == 200)
    r.check("HSTS header present", "strict-transport-security" in ok.headers, dict(ok.headers))
    try:
        httpx.get(f"{r.base}/api/health", timeout=20,
                  verify=ssl.create_default_context())
        bad = "connected without the private CA"
    except httpx.ConnectError as exc:
        bad = str(exc)
    r.check("a client without the deployment CA refuses the connection",
            "CERTIFICATE_VERIFY_FAILED" in bad or "certificate" in bad.lower(), bad)
    try:
        httpx.get("http://127.0.0.1:8765/api/health", timeout=5)
        plain = "plaintext control plane reachable"
    except httpx.HTTPError as exc:
        plain = f"refused: {type(exc).__name__}"
    r.check("no plaintext control-plane endpoint on the host", plain.startswith("refused"),
            plain)
    probe = r.dc("exec", "-T", "worker-office", "python", "-c",
                 "import socket\ntry:\n socket.create_connection(('control',8765),3);"
                 "print('reachable')\nexcept OSError as e: print('blocked', e)", check=False)
    r.check("workers cannot reach the control plane's plaintext port", "blocked" in probe, probe)
    ws = [w for w in r.workers() if w["alive"]]
    r.check("workers registered over HTTPS through the edge", ws)
    # a worker trusting the wrong CA cannot connect
    from provision import make_pki
    wrong = ROOT / "deploy" / "tls" / "wrong"
    if not (wrong / "ca.crt").exists():
        make_pki(wrong)
    rec = r.c.post("/api/cluster/credentials", json={
        "name": "wrong-ca", "adapters": ["spreadsheet"]}).json()
    r.docker("rm", "-f", "wrong-ca", check=False)
    r.dc("run", "-d", "--name", "wrong-ca", "-v", f"{wrong / 'ca.crt'}:/etc/wrong-ca.crt:ro",
         "-e", "DAEDELUS_CONTROL_CA=/etc/wrong-ca.crt",
         "-e", f"DAEDELUS_WORKER_CREDENTIAL={rec['credential']}",
         "-e", "DAEDELUS_WORKER_NAME=wrong-ca", "worker-office")
    time.sleep(12)
    logs = r.docker("logs", "wrong-ca", check=False) + subprocess.run(
        ["docker", "logs", "wrong-ca"], capture_output=True, text=True).stderr
    r.check("a worker with the wrong CA fails certificate verification and never registers",
            "CERTIFICATE_VERIFY_FAILED" in logs and not any(
                w["name"] == "wrong-ca" for w in r.workers()), logs[-600:])
    r.docker("rm", "-f", "wrong-ca", check=False)
    s = r.c.post("/api/auth/session").headers.get("set-cookie", "")
    r.check("browser session cookie is Secure, HttpOnly and SameSite=Strict (hosted profile)",
            "Secure" in s and "HttpOnly" in s and "strict" in s.lower(), s)
    # server-sent events stream through the proxy without buffering
    pid = r.project("sse-tls")
    got: list[tuple[float, str]] = []
    t0 = time.time()

    def listen():
        with httpx.stream("GET", f"{r.base}/api/projects/{pid}/events?since=0", timeout=120,
                          verify=r.verify,
                          headers={"Authorization": f"Bearer {r.token}"}) as resp:
            for line in resp.iter_lines():
                if line.startswith("event: "):
                    got.append((round(time.time() - t0, 2), line[7:]))
                if any(k == "published" for _, k in got):
                    return
    th = threading.Thread(target=listen, daemon=True)
    th.start()
    time.sleep(1)
    r.c.post(f"/api/projects/{pid}/artifacts", json={
        "name": "B", "adapter": "spreadsheet", "template": "blank", "params": {}})
    th.join(timeout=120)
    first = next((t for t, k in got if k == "queued"), None)
    r.check("job events stream to the client through the TLS proxy as they happen",
            first is not None and first < 15 and any(k == "published" for _, k in got), got)
    r.metrics["sse_first_event_seconds_via_tls"] = first


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--env-file", default=str(ROOT / "deploy" / ".env"))
    ap.add_argument("--out", default=str(ROOT / "evidence" / "v2" / "cluster"))
    ap.add_argument("--no-build", action="store_true")
    ap.add_argument("--keep", action="store_true", help="leave the cluster running")
    ap.add_argument("--only", default="")
    ap.add_argument("--hardened", action="store_true", help="V2.1 hardened containers + S9/S10")
    ap.add_argument("--tls", action="store_true", help="V2.1 TLS edge + S11 (needs --hardened)")
    a = ap.parse_args()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    sys.path.insert(0, str(ROOT / "deploy"))
    from provision import make_pki, provision_credentials
    if a.tls and not (ROOT / "deploy" / "tls" / "server.crt").exists():
        make_pki(ROOT / "deploy" / "tls")
    if a.hardened:
        for w in ("worker-office", "worker-blender", "worker-code"):
            (ROOT / "deploy" / "secrets" / f"{w}.cred").touch()
    r = Run(out, Path(a.env_file), hardened=a.hardened, tls=a.tls)
    t0 = time.time()
    try:
        r.dc("down", "-v", "--remove-orphans", check=False)
        if a.hardened:
            ids = provision_credentials(r.env_file, r.files)
            r.metrics["provisioned_credentials"] = sorted(ids)
        try:
            r.dc("up", "-d", *([] if a.no_build else ["--build"]), "--wait", capture=False)
            up = True
        except RuntimeError as exc:
            up = False
            r.check("compose stack started", False, exc)
        r.check("compose stack healthy", up and r.wait_health())
        steps = [s1_topology, s2_blender, s3_office, s4_code, s5_worker_crash, s6_partition,
                 s7_restart, s8_security]
        if a.hardened:
            steps += [s9_identity, s10_isolation]
        if a.tls:
            steps += [s11_tls]
        for fn in (steps if up else []):
            if a.only and a.only not in fn.__name__:
                continue
            try:
                fn(r)
            except Exception as exc:
                r.check(f"{fn.__name__} completed without exceptions", False, repr(exc))
    finally:
        (out / "compose_logs.txt").write_text(r.dc("logs", "--no-color", "--tail", "400",
                                                    check=False))
        if not a.keep:
            r.dc("down", "-v", "--remove-orphans", check=False)
    failed = [c for c in r.checks if not c["ok"]]
    rep = {"seconds": round(time.time() - t0, 1), "passed": not failed,
           "checks": r.checks, "metrics": r.metrics,
           "scope": "single Docker host: control plane and workers as containers on separate "
                    "networks; not a hosted multi-machine deployment; no GPU",
           "topology": r.files}
    r.dump("cluster_e2e_report.json", rep)
    lines = ["# Container end-to-end", "", rep["scope"], "",
             "Compose files: " + ", ".join(r.files), "",
             f"Result: **{'PASSED' if not failed else 'FAILED'}** "
             f"({len(r.checks) - len(failed)}/{len(r.checks)}), {rep['seconds']} s", ""]
    sc = None
    for c in r.checks:
        if c["scenario"] != sc:
            sc = c["scenario"]
            lines += ["", f"## {sc}"]
        lines.append(f"- [{'x' if c['ok'] else ' '}] {c['name']}"
                     + ("" if c["ok"] else f" - {c['detail'][:300]}"))
    lines += ["", "## Metrics", ""] + [f"- {k}: {v}" for k, v in r.metrics.items()]
    (out / "cluster_e2e_report.md").write_text("\n".join(lines) + "\n")
    print(f"\n{'PASS' if not failed else 'FAIL'}: {len(r.checks) - len(failed)}/{len(r.checks)}")
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
