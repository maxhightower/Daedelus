"""Control-host side of the hosted multi-machine end-to-end run (runs on host A).

    python deploy/hosted/control_e2e.py prepare --url https://x.trycloudflare.com --pub pub.txt \
        --handoff handoff/ --workspace ws/ --out evidence/
    python deploy/hosted/control_e2e.py run --url ... --workspace ws/ --out evidence/

``prepare`` starts the control plane (hosted profile, plaintext only on 127.0.0.1 behind the
TLS tunnel), creates two per-worker credentials and seals them to the worker host's public key.
``run`` publishes phases (commit status ``daedelus-hosted-phase``) that the worker host follows
and executes the mandatory hosted tests (handoff section 6.3) through the public HTTPS URL:

 H1 a worker registers from a different machine (peer address, host evidence)
 H2 the worker downloads hashed inputs        H3 it executes Blender operations
 H4 native outputs return                     H5 results are validated and published
 H6 the studio event stream receives progress through the TLS endpoint
 H7 a control-plane restart does not corrupt the run (execution resumes, jobs reused)
 H8 a worker shutdown triggers recovery (lease expiry -> retry on another worker identity)
 H9 a late result cannot overwrite a newer revision (version conflict, nothing published)
 H10 unauthorised network access is denied (anonymous API, forged worker credential,
     query-string token, cloud-metadata SSRF from the control host, sandboxed code job without
     network on the worker host)
 plus the deterministic agent workflow end to end (6.4 with the AI portion deterministic) and
 credential revocation of a live worker.
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path
from urllib.parse import urlparse

import httpx

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
CONTEXT = "daedelus-hosted-phase"
STATE = "control_state.json"


def sh(*a: str) -> str:
    r = subprocess.run(a, capture_output=True, text=True)
    return r.stdout.strip() if r.returncode == 0 else ""


class Ctl:
    def __init__(self, a):
        self.url = a.url.rstrip("/")
        self.ws = Path(a.workspace).resolve()
        self.out = Path(a.out).resolve()
        self.out.mkdir(parents=True, exist_ok=True)
        st = self.out / STATE
        self.state = json.loads(st.read_text()) if st.exists() else {}

    def save(self):
        (self.out / STATE).write_text(json.dumps(self.state, indent=1))

    # ---------------------------------------------------------------- server
    def env(self) -> dict:
        host = urlparse(self.url).hostname
        e = {**os.environ, "DAEDELUS_PROFILE": "hosted",
             "DAEDELUS_API_TOKENS": self.state["api_token"],
             "DAEDELUS_ALLOWED_HOSTS": f"{host},127.0.0.1,localhost",
             "DAEDELUS_FORWARDED_ALLOW_IPS": "127.0.0.1,::1",
             "DAEDELUS_LEASE_S": "20", "DAEDELUS_WORKER_TOKENS": ""}
        for k in ("GH_TOKEN", "GITHUB_TOKEN"):
            e.pop(k, None)
        return e

    def start_server(self) -> int:
        log = open(self.out / "control_server.log", "ab")
        dist = ROOT / "studio" / "dist"
        p = subprocess.Popen([sys.executable, "-m", "daedelus.cli", "--workspace", str(self.ws),
                              "serve", "--host", "127.0.0.1", "--port", "8765",
                              "--behind-tls-proxy"] + (["--studio-dist", str(dist)]
                                                       if dist.exists() else []),
                             env=self.env(), stdout=log, stderr=subprocess.STDOUT,
                             start_new_session=True)
        self.state["server_pid"] = p.pid
        self.save()
        for _ in range(120):
            try:
                if httpx.get("http://127.0.0.1:8765/api/health", timeout=2).status_code == 200:
                    return p.pid
            except httpx.HTTPError:
                pass
            time.sleep(0.5)
        raise RuntimeError("control plane did not start")

    def stop_server(self) -> None:
        pid = self.state.get("server_pid")
        if pid:
            try:
                os.killpg(pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            for _ in range(40):
                try:
                    os.kill(pid, 0)
                    time.sleep(0.25)
                except ProcessLookupError:
                    break

    def cli(self, *args: str) -> str:
        r = subprocess.run([sys.executable, "-m", "daedelus.cli", "--workspace", str(self.ws),
                            *args], capture_output=True, text=True, env=self.env())
        if r.returncode:
            raise RuntimeError(r.stderr[-1500:])
        return r.stdout


def prepare(a) -> int:
    import secrets
    c = Ctl(a)
    c.state["api_token"] = "api-" + secrets.token_hex(24)
    c.save()
    creds = {}
    for name in ("a", "b"):
        out = c.cli("cluster", "credential", "create", "--name", f"hosted-worker-{name}",
                    "--adapters", "blender,layered2d,code", "--capabilities", "cpu")
        creds[name] = [x for x in out.splitlines() if x.startswith("ddw1.")][-1]
    c.state["credential_ids"] = {k: v.split(".")[1] for k, v in creds.items()}
    c.save()
    ho = Path(a.handoff)
    ho.mkdir(parents=True, exist_ok=True)
    tmp = c.out / ".creds.json"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(creds, f)
    sys.path.insert(0, str(HERE))
    from handoff import seal
    seal(a.pub, str(tmp), str(ho / "sealed.json"))
    tmp.unlink()
    (ho / "control_url.txt").write_text(c.url + "\n")
    c.start_server()
    ev = {"hostname": socket.gethostname(), "runner_name": os.environ.get("RUNNER_NAME"),
          "public_ip": sh("curl", "-s", "-m", "10", "https://api.ipify.org"),
          "machine_id_sha256": __import__("hashlib").sha256(
              Path("/etc/machine-id").read_text().strip().encode()).hexdigest()[:16]
          if Path("/etc/machine-id").exists() else ""}
    ev["boot_id_sha256"] = __import__("hashlib").sha256(
        Path("/proc/sys/kernel/random/boot_id").read_text().strip().encode()).hexdigest()[:16]
    vm = sh("curl", "-s", "-m", "5", "-H", "Metadata:true",
            "http://169.254.169.254/metadata/instance/compute?api-version=2021-02-01")
    try:
        ev["cloud_vm_id_sha256"] = __import__("hashlib").sha256(
            json.loads(vm)["vmId"].encode()).hexdigest()[:16]
        ev["cloud_location"] = json.loads(vm).get("location")
    except (ValueError, KeyError):
        pass
    c.state["control_host"] = ev
    wh = Path(a.pub).parent / "host.json"  # published by the worker host with its key
    c.state["worker_host"] = json.loads(wh.read_text()) if wh.exists() else {}
    c.save()
    print("CONTROL_HOST_EVIDENCE " + json.dumps(ev), flush=True)
    return 0


# ---------------------------------------------------------------------------- run
class Run(Ctl):
    def __init__(self, a):
        super().__init__(a)
        self.checks: list[dict] = []
        self.metrics: dict = {}
        self.scenario = "setup"
        self.c = httpx.Client(base_url=self.url, timeout=600,
                              headers={"Authorization": f"Bearer {self.state['api_token']}"})

    def check(self, name, ok, detail="") -> bool:
        self.checks.append({"scenario": self.scenario, "name": name, "ok": bool(ok),
                            "detail": "" if ok else str(detail)[:800]})
        print(f"{'PASS' if ok else 'FAIL'} [{self.scenario}] {name}"
              + ("" if ok else f" - {str(detail)[:300]}"), flush=True)
        return bool(ok)

    def phase(self, p, workers=()):
        desc = json.dumps({"p": p, "w": [list(w) for w in workers]}, separators=(",", ":"))
        assert len(desc) <= 140, desc
        local = os.environ.get("DAEDELUS_HOSTED_PHASE_FILE")  # local dry run without GitHub
        if local:
            Path(local).write_text(desc)
            print(f"published phase {desc} (local file)", flush=True)
            return
        repo, sha = os.environ["GITHUB_REPOSITORY"], os.environ["GITHUB_SHA"]
        state = "success" if p == "done" else "pending"  # leave no dangling pending status
        subprocess.run(["gh", "api", f"repos/{repo}/statuses/{sha}", "-f", f"state={state}",
                        "-f", f"context={CONTEXT}", "-f", f"description={desc}"],
                       capture_output=True, check=True)
        print(f"published phase {desc}", flush=True)

    def workers(self, alive=True):
        ws = self.c.get("/api/cluster/status").json()["workers"]
        return [w for w in ws if w["alive"]] if alive else ws

    def wait_worker(self, name, timeout=600):
        t0 = time.time()
        while time.time() - t0 < timeout:
            for w in self.workers():
                if w["name"] == name:
                    return w
            time.sleep(2)
        return None

    def project(self, name, target="cloud_cpu"):
        pid = self.c.post("/api/projects", json={"name": name}).json()["id"]
        self.c.put(f"/api/projects/{pid}/execution", json={"target": target}).raise_for_status()
        return pid

    def jobs(self, pid):
        return self.c.get(f"/api/projects/{pid}/jobs?limit=500").json()

    def wait_execution(self, pid, eid, timeout=1200):
        t0 = time.time()
        while time.time() - t0 < timeout:
            try:
                ex = self.c.get(f"/api/projects/{pid}/executions/{eid}").json()
                if ex.get("status") not in ("pending", "running"):
                    return ex
            except (httpx.HTTPError, ValueError):
                pass
            time.sleep(2)
        raise TimeoutError(eid)

    def dump(self, name, data):
        (self.out / name).write_text(json.dumps(data, indent=1, default=str))


ROCKET = [
    {"id": "rocket", "name": "Rocket", "primitive": "empty"},
    {"id": "body", "name": "Body", "primitive": "cylinder", "parent": "rocket",
     "size": [0.18, 0.18, 1.6], "location": [0, 0, 0.8]},
    {"id": "nose", "name": "Nose", "primitive": "cone", "parent": "rocket",
     "size": [0.18, 0.18, 0.4], "location": [0, 0, 1.8]},
    {"id": "fins", "name": "Fins", "primitive": "cube", "parent": "rocket",
     "size": [0.5, 0.04, 0.3], "location": [0, 0, 0.15]}]
LAYERS = [{"id": "bg", "name": "Background", "fill": {"type": "solid", "colors": ["#223344"]}},
          {"id": "fg", "name": "Foreground", "fill": {"type": "solid", "colors": ["#88aa66"]},
           "shape": {"type": "rect", "box": [0, 0.6, 1, 0.4]}}]


def h1_h6(r: Run):
    r.scenario = "H1 independent hosts"
    w = r.wait_worker("w-main")
    r.check("the worker on the other host registered over HTTPS", w, r.workers(False))
    if not w:
        return None
    ctl = r.state["control_host"]
    r.check("the worker's network address is not the control host's",
            w.get("peer") and w["peer"] not in ("127.0.0.1", "::1", ctl.get("public_ip")),
            (w.get("peer"), ctl.get("public_ip")))
    # GitHub runner VMs share an image hostname, so compare per-boot and per-VM identifiers
    # (each side publishes sha256 prefixes; the raw ids are never printed)
    wh = r.state.get("worker_host") or {}
    ids = {k: (ctl.get(k), wh.get(k)) for k in ("boot_id_sha256", "cloud_vm_id_sha256")}
    r.check("the worker runs on a different machine (kernel boot id and cloud VM id differ)",
            ids["boot_id_sha256"][1] and ids["boot_id_sha256"][0] != ids["boot_id_sha256"][1]
            and (not (ids["cloud_vm_id_sha256"][0] and ids["cloud_vm_id_sha256"][1])
                 or ids["cloud_vm_id_sha256"][0] != ids["cloud_vm_id_sha256"][1]),
            {**ids, "hostnames": (ctl.get("hostname"), w.get("host"))})
    r.metrics["machine_identity"] = {**ids, "worker_public_ip_differs": bool(
        wh.get("public_ip") and wh.get("public_ip") != ctl.get("public_ip"))}
    iso = w.get("isolation") or {}
    r.check("the worker reports a verified bubblewrap job sandbox", iso.get("profile") == "bwrap"
            and iso.get("verified") and (iso.get("controls") or {}).get("network_isolated"), iso)
    r.check("the worker authenticated with a provisioned per-worker credential",
            w.get("credential_kind") == "provisioned" and w["id"] in
            r.state["credential_ids"].values(), w)
    r.dump("hosted_workers_phase1.json", r.workers(False))
    # H6: events stream through the public TLS endpoint while H2-H5 run
    r.scenario = "H2-H6 remote Blender"
    pid = r.project("hosted-blender")
    got: list = []
    diag: dict = {"lines": 0}
    t0 = time.time()

    def listen():
        try:
            with httpx.stream("GET", f"{r.url}/api/projects/{pid}/events?since=0", timeout=300,
                              headers={"Authorization": f"Bearer {r.state['api_token']}",
                                       "Accept-Encoding": "identity"}) as s:
                diag.update(status=s.status_code, headers={
                    k: v for k, v in s.headers.items() if k.lower() in (
                        "content-type", "content-encoding", "cache-control",
                        "transfer-encoding", "cf-cache-status", "server")})
                for line in s.iter_lines():
                    diag["lines"] += 1
                    diag.setdefault("first_line_s", round(time.time() - t0, 2))
                    if line.startswith("event: "):
                        got.append((round(time.time() - t0, 2), line[7:]))
                    if sum(1 for _, k in got if k == "published") >= 2:
                        return
        except httpx.HTTPError as exc:
            got.append((None, f"stream error {exc}"))
    th = threading.Thread(target=listen, daemon=True)
    th.start()
    time.sleep(1)
    t1 = time.time()
    a = r.c.post(f"/api/projects/{pid}/artifacts", json={
        "name": "Rocket", "adapter": "blender", "template": "components",
        "params": {"components": ROCKET}})
    r.check("Blender artifact created by the remote worker", a.status_code == 200, a.text[:400])
    if a.status_code != 200:
        return None
    aid = a.json()["artifact"]["id"]
    e = r.c.post(f"/api/projects/{pid}/artifacts/{aid}/edit", json={
        "message": "hosted edit", "operations": [
            {"op": "set_material", "component_id": "body",
             "params": {"base_color": "#d8d8d8", "roughness": 0.4}},
            {"op": "add_primitive", "params": {"id": "tower", "primitive": "cube",
                                               "parent": "rocket", "size": [0.2, 0.2, 2.2],
                                               "location": [0.8, 0, 1.1]}}]})
    r.metrics["hosted_blender_create_edit_seconds"] = round(time.time() - t1, 1)
    r.check("Blender operations executed on the remote worker", e.status_code == 200,
            e.text[:400])
    th.join(timeout=120)
    jobs = r.jobs(pid)
    wid = w["id"]
    r.check("every job ran on the remote worker", jobs and all(
        j["state"] == "succeeded" and j["worker_id"] == wid for j in jobs),
        [(j["method"], j["state"], j["worker_id"]) for j in jobs])
    detail = [r.c.get(f"/api/projects/{pid}/jobs/{j['id']}").json() for j in jobs]
    apply = [d for d in detail if d["request"]["method"] == "apply"]
    r.check("the worker downloaded the hashed inputs (apply job read the native files)",
            apply and apply[0]["request"]["files"] > 0, apply[:1])
    pubs = [d["publication"] for d in detail if d["publication"]]
    r.check("native outputs returned and were published atomically (digests recorded)",
            pubs and all(p.get("digest") for p in pubs), pubs)
    rev = e.json() if e.status_code == 200 else {}
    val = rev.get("validation") or {}
    r.check("the published revision was validated (file reopens)", any(
        c.get("name") == "file_reopens" and c.get("passed") for c in val.get("checks", [])), val)
    render = (rev.get("previews") or {}).get("render")
    img = r.c.get(f"/api/projects/{pid}/files/{render}") if render else None
    r.check("the render made on the worker is served by the control plane over HTTPS",
            img is not None and img.status_code == 200 and img.content[:4] == b"\x89PNG")
    if img is not None and img.status_code == 200:
        (r.out / "hosted_render.png").write_bytes(img.content)
    first = next((t for t, k in got if k == "queued"), None)
    r.check("the event stream delivered job progress through the public TLS endpoint",
            first is not None and {"leased", "succeeded", "published"} <= {k for _, k in got},
            got[:20])
    r.metrics["sse_first_event_seconds"] = first
    r.metrics["sse_diagnostics"] = diag
    r.metrics["job_seconds"] = [round(d["result"]["seconds"], 2) for d in detail if d["result"]]
    r.dump("hosted_blender_jobs.json", detail)
    return pid, aid


def e2e_agent(r: Run):
    """6.4 end to end with the deterministic provider: references bound to one component,
    planned operations run on the remote worker, evaluated and corrected in a bounded loop."""
    r.scenario = "E2E agent workflow (deterministic provider)"
    pid = r.project("hosted-agent")
    a = r.c.post(f"/api/projects/{pid}/artifacts", json={
        "name": "Rocket", "adapter": "blender", "template": "components",
        "params": {"components": ROCKET}})
    if not r.check("model created remotely", a.status_code == 200, a.text[:300]):
        return
    art = a.json()["artifact"]
    photo = ROOT / "backend" / "daedelus" / "demo_assets" / "v21" / "rocket_launch_photo.jpg"
    with open(photo, "rb") as f:
        src = r.c.post(f"/api/projects/{pid}/sources/upload",
                       files={"files": (photo.name, f)}).json()[0]
    guide = r.c.post(f"/api/projects/{pid}/sources/text", json={
        "name": "Material note", "text": "Body: roughness: 0.35\nKeep the nose and fins.\n"}
    ).json()
    for s, role in ((src, "reference"), (guide, "guideline")):
        r.c.post(f"/api/projects/{pid}/bindings", json={
            "source_id": s["id"], "role": role, "aspects": ["color", "material"],
            "target": {"scope": "component", "artifact_id": art["id"],
                       "component_id": "body"}}).raise_for_status()
    wf = r.c.post(f"/api/projects/{pid}/workflows", json={
        "name": "hosted agent", "parameters": {"budget": {"max_model_calls": 10,
                                                          "max_seconds": 900}},
        "nodes": [{"id": "src", "type": "sources", "label": "Sources", "config": {}},
                  {"id": "a", "type": "artifact", "label": "Rocket",
                   "config": {"artifact_id": art["id"]}},
                  {"id": "agent", "type": "agent", "label": "Agent", "config": {
                      "instructions": "Adapt the body's material from the references.",
                      "provider": "heuristic", "fan_out": True, "understand": True,
                      "loop": {"enabled": True, "max_iterations": 2}}}],
        "edges": [{"id": "e1", "source": "a", "source_port": "artifact", "target": "agent",
                   "target_port": "artifact"},
                  {"id": "e2", "source": "src", "source_port": "sources", "target": "agent",
                   "target_port": "sources"}]}).json()
    t0 = time.time()
    ex = r.wait_execution(pid, r.c.post(f"/api/projects/{pid}/workflows/{wf['id']}/execute",
                                        json={}).json()["id"])
    r.metrics["hosted_agent_workflow_seconds"] = round(time.time() - t0, 1)
    r.check("agent workflow succeeded with all adapter work on the remote worker",
            ex["status"] == "succeeded", ex.get("error"))
    nr = next(n for n in ex["node_runs"] if n["node_id"] == "agent")
    d = (nr.get("outputs") or {}).get("execution", {}).get("decisions", {})
    r.check("the node resolved to the remote CPU worker", d and all(
        x["resolved"] == "cloud_cpu" for x in d.values()), d)
    r.check("the execution recorded its budget and usage", ex.get("budget", {}).get("used"),
            ex.get("budget"))
    comps = r.c.get(f"/api/projects/{pid}/artifacts/{art['id']}").json()["components"]
    r.check("the targeted component and its siblings exist after publication",
            {"body", "nose", "fins"} <= {c["id"] for c in comps})
    r.dump("hosted_agent_execution.json", ex)
    r.state["agent_project"] = pid
    r.save()


def h8_worker_shutdown(r: Run):
    r.scenario = "H8 worker shutdown"
    r.phase(2, [("w-crash", "a", "crash_after_lease")])
    w = r.wait_worker("w-crash", timeout=300)
    r.check("a worker that will die mid-job is the only worker", w and len(r.workers()) == 1,
            r.workers())
    pid = r.project("hosted-crash")
    res: dict = {}
    t = threading.Thread(target=lambda: res.update(r=r.c.post(
        f"/api/projects/{pid}/artifacts", json={"name": "Poster", "adapter": "layered2d",
                                                "template": "layers", "params": {
                                                    "width": 64, "height": 48,
                                                    "root_id": "poster", "layers": LAYERS}})))
    t.start()
    job = None
    for _ in range(120):
        js = r.jobs(pid)
        if js and js[0]["attempt"] >= 1:
            job = js[0]
            break
        time.sleep(1)
    r.check("the doomed worker leased the job", job, r.jobs(pid))
    t_kill = time.time()
    r.phase(3, [("w-main", "b", "")])
    t.join(timeout=600)
    r.metrics["hosted_recovery_after_worker_death_seconds"] = round(time.time() - t_kill, 1)
    r.check("the request completed after the worker died",
            res.get("r") is not None and res["r"].status_code == 200,
            res.get("r") is not None and res["r"].text[:300])
    if job:
        d = r.c.get(f"/api/projects/{pid}/jobs/{job['id']}").json()
        r.check("lease expired, job retried (attempt 2) on another worker identity",
                d["status"]["state"] == "succeeded" and d["status"]["attempt"] >= 2 and
                d["status"]["worker_id"] == r.state["credential_ids"]["b"], d["status"])
        r.dump("hosted_worker_death_job.json", d)


def h7_h9(r: Run):
    r.scenario = "H9 late result vs newer revision"
    r.phase(4, [("w-slow", "a", "slow:20")])
    r.wait_worker("w-slow", timeout=300)
    pid = r.project("hosted-conflict", "local")
    a = r.c.post(f"/api/projects/{pid}/artifacts", json={
        "name": "Poster", "adapter": "layered2d", "template": "layers",
        "params": {"width": 64, "height": 48, "root_id": "poster", "layers": LAYERS}}).json()
    aid = a["artifact"]["id"]
    r.c.put(f"/api/projects/{pid}/execution", json={"target": "cloud_cpu"}).raise_for_status()
    res: dict = {}
    t = threading.Thread(target=lambda: res.update(r=r.c.post(
        f"/api/projects/{pid}/artifacts/{aid}/edit", json={
            "message": "slow remote edit", "operations": [
                {"op": "color_grade", "component_id": "fg", "params": {"tint": "#ff0000"}}]})))
    t.start()
    for _ in range(120):
        if any(j["method"] == "apply" and j["state"] in ("leased", "running")
               for j in r.jobs(pid)):
            break
        time.sleep(1)
    art = r.c.get(f"/api/projects/{pid}/artifacts/{aid}").json()
    native = r.ws / "projects" / pid / art["native_dir"]
    (native / "newer_change.txt").write_text("a newer change made while the job ran")
    t.join(timeout=600)
    r.check("the late result was refused (version conflict)", res.get("r") is not None and
            res["r"].status_code >= 400 and "conflict" in res["r"].text, res.get("r") and
            res["r"].text[:300])
    r.check("the newer change was not overwritten", (native / "newer_change.txt").exists())
    r.check("the job is marked conflict, nothing published", any(
        j["state"] == "conflict" for j in r.jobs(pid)), r.jobs(pid))
    # H7: restart the control plane while a remote job of a workflow runs
    r.scenario = "H7 control-plane restart"
    pid2 = r.project("hosted-restart")
    a2 = r.c.post(f"/api/projects/{pid2}/artifacts", json={
        "name": "Poster", "adapter": "layered2d", "template": "layers",
        "params": {"width": 64, "height": 48, "root_id": "poster", "layers": LAYERS}}).json()
    note = r.c.post(f"/api/projects/{pid2}/sources/text", json={
        "name": "grade", "text": "tint: #ffcc88\nsaturation: 0.8\n"}).json()
    r.c.post(f"/api/projects/{pid2}/bindings", json={
        "source_id": note["id"], "role": "guideline", "aspects": ["color"],
        "target": {"scope": "artifact", "artifact_id": a2["artifact"]["id"]}}).raise_for_status()
    wf = r.c.post(f"/api/projects/{pid2}/workflows", json={
        "name": "restart", "nodes": [
            {"id": "a", "type": "artifact", "label": "a",
             "config": {"artifact_id": a2["artifact"]["id"]}},
            {"id": "g", "type": "agent", "label": "g", "config": {
                "instructions": "tint: #ffcc88", "fan_out": False, "provider": "heuristic"}}],
        "edges": [{"id": "e", "source": "a", "source_port": "artifact", "target": "g",
                   "target_port": "artifact"}]}).json()
    eid = r.c.post(f"/api/projects/{pid2}/workflows/{wf['id']}/execute", json={}).json()["id"]
    running = None
    for _ in range(180):
        running = next((j for j in r.jobs(pid2) if j["method"] == "apply" and
                        j["state"] in ("leased", "running")), None)
        if running:
            break
        time.sleep(1)
    r.check("a remote apply job is in flight when the control plane restarts", running)
    r.stop_server()
    t_r = time.time()
    r.start_server()
    r.check("control plane back after restart (same volume, same tunnel)",
            r.c.get("/api/health").status_code == 200)
    ex = r.wait_execution(pid2, eid)
    r.metrics["hosted_execution_after_restart_seconds"] = round(time.time() - t_r, 1)
    r.check("the interrupted execution resumed and succeeded", ex["status"] == "succeeded",
            ex.get("error"))
    if running:
        same = [j for j in r.jobs(pid2) if j["id"] == running["id"]]
        r.check("the in-flight remote job finished and was reused, not recomputed",
                same and same[0]["state"] == "succeeded", same)
    r.dump("hosted_restart_execution.json", ex)


ESCAPE = {"test_escape.py": (
    "import os, socket, pytest\n\n"
    "@pytest.mark.parametrize('host', ['1.1.1.1', '8.8.8.8'])\n"
    "def test_no_network(host):\n    with pytest.raises(OSError):\n"
    "        socket.create_connection((host, 443), timeout=3)\n\n"
    "def test_no_secrets():\n    assert not [k for k in os.environ if any(m in k.upper() for m "
    "in ('TOKEN', 'SECRET', 'CREDENTIAL', 'API_KEY'))]\n\n"
    "def test_no_home_or_cloud_metadata():\n    with pytest.raises(OSError):\n"
    "        socket.create_connection(('169.254.169.254', 80), timeout=3)\n")}


def h10_security(r: Run):
    r.scenario = "H10 unauthorised access"
    r.phase(5, [("w-main", "b", "")])
    r.wait_worker("w-main", timeout=300)
    anon = httpx.Client(base_url=r.url, timeout=30)
    r.check("anonymous API request refused", anon.get("/api/projects").status_code == 401)
    r.check("query-string token refused", anon.get(
        f"/api/projects?token={r.state['api_token']}").status_code == 401)
    r.check("forged worker credential refused", anon.post(
        "/api/cluster/workers", json={"name": "x"},
        headers={"Authorization": "Bearer ddw1.wkr_000000000000." + "A" * 43}).status_code == 401)
    r.check("worker endpoints refuse an API token", anon.post(
        "/api/cluster/lease", json={"worker_id": "x"},
        headers={"Authorization": f"Bearer {r.state['api_token']}"}).status_code == 401)
    try:
        httpx.get("http://" + urlparse(r.url).hostname + "/api/health", timeout=10,
                  follow_redirects=False)
        plain = "plaintext reachable"
    except httpx.HTTPError as exc:
        plain = f"refused ({type(exc).__name__})"
    pr = None
    try:
        pr = httpx.get("http://" + urlparse(r.url).hostname + "/api/health", timeout=10,
                       follow_redirects=False)
    except httpx.HTTPError:
        pass
    r.check("no plaintext control-plane endpoint is public (HTTP is refused or redirected)",
            pr is None or pr.status_code in (301, 302, 307, 308, 400, 403, 404, 426), plain)
    pid = r.project("hosted-ssrf", "local")
    s = r.c.post(f"/api/projects/{pid}/sources/url", json={
        "url": "http://169.254.169.254/metadata/instance?api-version=2021-02-01"}).json()
    r.check("URL ingestion of the cloud metadata service is refused on a real cloud host",
            s["processing"]["state"] == "failed" and "refused" in (s["processing"]["error"] or ""),
            s["processing"])
    pid2 = r.project("hosted-escape")
    a = r.c.post(f"/api/projects/{pid2}/artifacts", json={
        "name": "escape", "adapter": "code", "template": "files", "params": {"files": ESCAPE},
        "metadata": {"test_command": "python -m pytest -q"}})
    if r.check("code artifact created on the remote worker", a.status_code == 200, a.text[:300]):
        aid = a.json()["artifact"]["id"]
        wf = r.c.post(f"/api/projects/{pid2}/workflows", json={"name": "t", "nodes": [
            {"id": "a", "type": "artifact", "label": "a", "config": {"artifact_id": aid}},
            {"id": "v", "type": "validate", "label": "v", "config": {"checks": ["tests"],
                                                                     "fail_on_error": False}}],
            "edges": [{"id": "e", "source": "a", "source_port": "artifact", "target": "v",
                       "target_port": "revision"}]}).json()
        ex = r.wait_execution(pid2, r.c.post(
            f"/api/projects/{pid2}/workflows/{wf['id']}/execute", json={}).json()["id"])
        rep = json.dumps(ex["node_runs"][-1].get("outputs", {}))
        r.check("repository tests on the remote worker find no network, no secrets and no "
                "cloud metadata service (all 4 escape attempts fail)", "4 passed" in rep, rep[:900])
    r.scenario = "Revocation of a live remote worker"
    cid = r.state["credential_ids"]["b"]
    rv = r.c.post(f"/api/cluster/credentials/{cid}/revoke", json={"reason": "hosted e2e"})
    r.check("credential revoked", rv.status_code == 200, rv.text[:300])
    time.sleep(25)
    r.check("the revoked remote worker disappeared from the live pool",
            not any(w["id"] == cid for w in r.workers()), r.workers())


def run(a) -> int:
    r = Run(a)
    t0 = time.time()
    r.phase(1, [("w-main", "a", "")])
    steps = [h1_h6, e2e_agent, h8_worker_shutdown, h7_h9, h10_security]
    try:
        for fn in steps:
            try:
                fn(r)
            except Exception as exc:
                r.check(f"{fn.__name__} completed without exceptions", False, repr(exc))
    finally:
        r.phase("done")
        aud = r.c.get("/api/cluster/audit?n=2000").json()
        r.dump("hosted_audit.json", aud)
        r.dump("hosted_workers_final.json", r.workers(False))
        r.stop_server()
    failed = [c for c in r.checks if not c["ok"]]
    rep = {"passed": not failed, "seconds": round(time.time() - t0, 1), "checks": r.checks,
           "metrics": r.metrics, "control_host": r.state.get("control_host"),
           "control_url_host": urlparse(r.url).hostname,
           "topology": "control plane on GitHub-hosted runner A behind a Cloudflare quick "
                       "tunnel (public HTTPS, real certificate); worker on GitHub-hosted runner "
                       "B connecting over the public internet"}
    r.dump("hosted_report.json", rep)
    print("HOSTED_REPORT " + json.dumps({"passed": rep["passed"], "metrics": rep["metrics"],
                                         "n": len(r.checks), "failed": len(failed)}), flush=True)
    print(f"{'PASS' if not failed else 'FAIL'}: {len(r.checks) - len(failed)}/{len(r.checks)}")
    return 0 if not failed else 1


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["prepare", "run"])
    ap.add_argument("--url", required=True)
    ap.add_argument("--workspace", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--pub")
    ap.add_argument("--handoff")
    a = ap.parse_args()
    return prepare(a) if a.cmd == "prepare" else run(a)


if __name__ == "__main__":
    sys.exit(main())
