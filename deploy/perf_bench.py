"""Distributed-execution benchmark: same workloads, same machine, V2 vs V2.1 (Phase 5).

    python deploy/perf_bench.py --label v21 --out evidence/v2_1/performance/v21 [--repeat 3]
    python deploy/perf_bench.py --label v2 --pythonpath /path/to/v2-worktree/backend ...

Starts a control plane and ONE worker process (all adapters) as separate OS processes on this
host, then drives the workloads through the HTTP API only (identical for V2 and V2.1):

  W1 Blender create / edit / render        W2 Office research pipeline (V1.2)
  W3 code edit + allowlisted tests         W4 mixed media (layered image + workbook edits)
  W5 cancellation and retry (time to cancel a running job; crash -> retry recovery)

Each workload runs ``--repeat`` times. The first repetition after (re)starting the worker is
"cold" (empty worker blob cache, fresh processes); later ones are "warm". Per run it records
wall time, job count per method, and from the job event log: queue wait (queued -> leased) and
worker time (leased -> terminal). V2.1 workers also report fetch / startup / method / upload
seconds and bytes moved.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import signal
import socket
import statistics
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "deploy"))


def free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


class Env:
    def __init__(self, a):
        self.a = a
        self.tmp = Path(tempfile.mkdtemp(prefix="dd_perf_"))
        self.port = free_port()
        self.url = f"http://127.0.0.1:{self.port}"
        self.token = "perf-join-token-" + "x" * 24
        self.env = {**os.environ, "DAEDELUS_WORKER_TOKENS": self.token,
                    "DAEDELUS_LEASE_S": "15", "PYTHONDONTWRITEBYTECODE": "1"}
        for k in ("DAEDELUS_API_TOKENS", "DAEDELUS_PROFILE"):
            self.env.pop(k, None)
        if a.pythonpath:
            self.env["PYTHONPATH"] = a.pythonpath
        if a.isolation:
            self.env["DAEDELUS_JOB_ISOLATION"] = a.isolation
        self.procs: list[subprocess.Popen] = []
        self.worker: subprocess.Popen | None = None
        self.c = httpx.Client(base_url=self.url, timeout=900)

    def py(self, *args: str, log: str, env: dict | None = None) -> subprocess.Popen:
        code = ("import sys; sys.path.insert(0, %r) if %r else None; "
                "from daedelus.cli import main; sys.exit(main())"
                % (self.a.pythonpath or "", bool(self.a.pythonpath)))
        return subprocess.Popen([sys.executable, "-I", "-c", code, *args], cwd=str(self.tmp),
                                env=env or self.env, stdout=open(self.tmp / log, "ab"),
                                stderr=subprocess.STDOUT, start_new_session=True)

    def start(self):
        self.procs.append(self.py("--workspace", str(self.tmp / "ws"), "serve", "--port",
                                  str(self.port), log="server.log"))
        for _ in range(120):
            try:
                if self.c.get("/api/health").status_code == 200:
                    break
            except httpx.HTTPError:
                time.sleep(0.25)
        self.start_worker()

    def start_worker(self, name="perf-w", fault=""):
        self.stop_worker()
        wd = self.tmp / f"wk_{name}"
        shutil.rmtree(wd, ignore_errors=True)  # cold: empty blob cache
        env = {**self.env, "DAEDELUS_WORKER_TOKEN": self.token, "DAEDELUS_WORKER_FAULT": fault,
               "DAEDELUS_ALLOW_INSECURE_CONTROL": "1"}
        self.worker = self.py("worker", "--control", self.url, "--name", name, "--work-dir",
                              str(wd), log=f"worker_{name}.log", env=env)
        for _ in range(240):
            ws = self.c.get("/api/cluster/status").json()["workers"]
            if any(w["name"] == name and w["alive"] for w in ws):
                return
            time.sleep(0.25)
        raise RuntimeError("worker did not register: " +
                           (self.tmp / f"worker_{name}.log").read_text()[-2000:])

    def stop_worker(self):
        if self.worker and self.worker.poll() is None:
            os.killpg(self.worker.pid, signal.SIGTERM)
            try:
                self.worker.wait(timeout=20)
            except subprocess.TimeoutExpired:
                os.killpg(self.worker.pid, signal.SIGKILL)
        self.worker = None

    def close(self):
        self.stop_worker()
        for p in self.procs:
            if p.poll() is None:
                os.killpg(p.pid, signal.SIGTERM)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def project(self, name, target="cloud_cpu"):
        pid = self.c.post("/api/projects", json={"name": name}).json()["id"]
        self.c.put(f"/api/projects/{pid}/execution", json={"target": target}).raise_for_status()
        return pid

    def wait_execution(self, pid, eid, timeout=1200):
        t0 = time.time()
        while time.time() - t0 < timeout:
            ex = self.c.get(f"/api/projects/{pid}/executions/{eid}").json()
            if ex["status"] not in ("pending", "running"):
                return ex
            time.sleep(0.2)
        raise TimeoutError(eid)


# ---------------------------------------------------------------------------- workloads
def w1_blender(e: Env) -> str:
    pid = e.project("w1")
    a = e.c.post(f"/api/projects/{pid}/artifacts", json={
        "name": "Lamp", "adapter": "blender", "template": "empty",
        "params": {"root_id": "lamp", "name": "Lamp"}})
    a.raise_for_status()
    e.c.post(f"/api/projects/{pid}/artifacts/{a.json()['artifact']['id']}/edit", json={
        "message": "add base and shade", "operations": [
            {"op": "add_primitive", "params": {"id": "base", "primitive": "cylinder",
                                               "parent": "lamp", "size": [0.4, 0.4, 0.1]}},
            {"op": "add_primitive", "params": {"id": "shade", "primitive": "cone",
                                               "parent": "lamp", "location": [0, 0, 0.8],
                                               "size": [0.5, 0.5, 0.4]}},
            {"op": "set_material", "component_id": "shade",
             "params": {"base_color": "#e8c070"}}]}).raise_for_status()
    return pid


def w2_office(e: Env) -> str:
    from cluster_e2e import _pipeline
    pid = e.project("w2")
    wf, *_ = _pipeline(e, pid)
    ex = e.wait_execution(pid, e.c.post(f"/api/projects/{pid}/workflows/{wf['id']}/execute",
                                        json={}).json()["id"])
    if ex["status"] != "succeeded":
        raise RuntimeError(f"office pipeline {ex['status']}: {ex.get('error')}")
    return pid


def w3_code(e: Env) -> str:
    pid = e.project("w3")
    a = e.c.post(f"/api/projects/{pid}/artifacts", json={
        "name": "lib", "adapter": "code", "template": "files", "params": {"files": {
            "lib.py": "def add(a, b):\n    return a - b\n",
            "test_lib.py": "from lib import add\n\ndef test_add():\n    assert add(2, 3) == 5\n"}},
        "metadata": {"test_command": "python -m pytest -q"}}).json()["artifact"]
    e.c.post(f"/api/projects/{pid}/artifacts/{a['id']}/edit", json={
        "message": "fix", "operations": [{"op": "write_file", "params": {
            "path": "lib.py", "content": "def add(a, b):\n    return a + b\n"}}]}
    ).raise_for_status()
    wf = e.c.post(f"/api/projects/{pid}/workflows", json={"name": "t", "nodes": [
        {"id": "a", "type": "artifact", "label": "a", "config": {"artifact_id": a["id"]}},
        {"id": "v", "type": "validate", "label": "v", "config": {"checks": ["tests"]}}],
        "edges": [{"id": "e", "source": "a", "source_port": "artifact", "target": "v",
                   "target_port": "revision"}]}).json()
    ex = e.wait_execution(pid, e.c.post(f"/api/projects/{pid}/workflows/{wf['id']}/execute",
                                        json={}).json()["id"])
    if ex["status"] != "succeeded":
        raise RuntimeError(f"code tests {ex['status']}: {ex.get('error')}")
    return pid


def w4_mixed(e: Env) -> str:
    pid = e.project("w4")
    img = e.c.post(f"/api/projects/{pid}/artifacts", json={
        "name": "Poster", "adapter": "layered2d", "template": "layers", "params": {
            "width": 256, "height": 256, "root_id": "poster", "layers": [
                {"id": "bg", "name": "Bg", "fill": {"type": "solid", "colors": ["#223344"]}},
                {"id": "fg", "name": "Fg", "fill": {"type": "solid", "colors": ["#88aa66"]},
                 "shape": {"type": "rect", "box": [0, 0.6, 1, 0.4]}}]}}).json()["artifact"]
    e.c.post(f"/api/projects/{pid}/artifacts/{img['id']}/edit", json={
        "message": "grade", "operations": [{"op": "color_grade", "component_id": "fg",
                                            "params": {"tint": "#ff8800"}}]}).raise_for_status()
    wb = e.c.post(f"/api/projects/{pid}/artifacts", json={
        "name": "Book", "adapter": "spreadsheet", "template": "data", "params": {"sheets": [
            {"id": "data", "title": "Data", "rows": [["a", "b"], [1, 2], [3, 4]]}]}}
    ).json()["artifact"]
    e.c.post(f"/api/projects/{pid}/artifacts/{wb['id']}/edit", json={
        "message": "sum", "operations": [{"op": "set_cells", "component_id": "data",
                                          "params": {"cells": {"C2": "=A2+B2"}}}]}
    ).raise_for_status()
    return pid


def w5_cancel_retry(e: Env) -> dict:
    out = {}
    e.start_worker("slow", fault="slow:30")
    pid = e.project("w5c")
    t = threading.Thread(target=lambda: e.c.post(f"/api/projects/{pid}/artifacts", json={
        "name": "B", "adapter": "spreadsheet", "template": "blank", "params": {}}))
    t.start()
    job = None
    for _ in range(200):
        js = e.c.get(f"/api/projects/{pid}/jobs").json()
        job = next((j for j in js if j["state"] in ("leased", "running")), None)
        if job:
            break
        time.sleep(0.1)
    t0 = time.time()
    e.c.post(f"/api/projects/{pid}/jobs/{job['id']}/cancel")
    while e.c.get(f"/api/projects/{pid}/jobs/{job['id']}").json()["status"]["state"] != \
            "cancelled":
        time.sleep(0.1)
    out["time_to_cancel_seconds"] = round(time.time() - t0, 2)
    t.join(timeout=60)
    e.start_worker("crasher", fault="crash_after_lease")
    pid2 = e.project("w5r")
    res: dict = {}
    t = threading.Thread(target=lambda: res.update(r=e.c.post(
        f"/api/projects/{pid2}/artifacts", json={"name": "B", "adapter": "spreadsheet",
                                                 "template": "blank", "params": {}})))
    t.start()
    for _ in range(200):
        if e.worker.poll() is not None:
            break
        time.sleep(0.1)
    t_crash = time.time()
    e.start_worker("perf-w")
    t.join(timeout=300)
    out["recovery_after_crash_seconds"] = round(time.time() - t_crash, 2)
    out["recovered"] = bool(res.get("r") is not None and res["r"].status_code == 200)
    return out


def job_stats(e: Env, pid: str) -> dict:
    jobs = e.c.get(f"/api/projects/{pid}/jobs?limit=1000").json()
    by: dict[str, int] = {}
    qwait = wtime = 0.0
    phases: dict[str, float] = {}
    bytes_in = bytes_out = 0
    for j in jobs:
        by[j["method"]] = by.get(j["method"], 0) + 1
        d = e.c.get(f"/api/projects/{pid}/jobs/{j['id']}").json()
        ts = {}
        for ev in d["events"]:
            ts.setdefault(ev["kind"], ev["ts"])
        if "queued" in ts and "leased" in ts:
            qwait += ts["leased"] - ts["queued"]
        end = next((ts[k] for k in ("succeeded", "failed", "cancelled", "timed_out")
                    if k in ts), None)
        if end and "leased" in ts:
            wtime += end - ts["leased"]
        res = d.get("result") or {}
        for k, v in (res.get("timings") or {}).items():
            phases[k] = phases.get(k, 0.0) + float(v)
        bytes_in += res.get("bytes_in") or 0
        bytes_out += res.get("bytes_out") or 0
    return {"jobs": len(jobs), "by_method": dict(sorted(by.items())),
            "queue_wait_s": round(qwait, 3), "worker_time_s": round(wtime, 3),
            "worker_phases_s": {k: round(v, 3) for k, v in sorted(phases.items())},
            "bytes_in": bytes_in, "bytes_out": bytes_out}


WORKLOADS = {"W1_blender": w1_blender, "W2_office": w2_office, "W3_code": w3_code,
             "W4_mixed": w4_mixed}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--label", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--pythonpath", default="")
    ap.add_argument("--repeat", type=int, default=3)
    ap.add_argument("--isolation", default="", help="V2.1 only: process | bwrap")
    ap.add_argument("--only", default="")
    a = ap.parse_args()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    e = Env(a)
    rep: dict = {"label": a.label, "pythonpath": a.pythonpath or "(installed)",
                 "isolation": a.isolation or "default", "repeat": a.repeat, "workloads": {},
                 "host": {"cpus": os.cpu_count(), "kernel": os.uname().release}}
    try:
        e.start()
        for name, fn in WORKLOADS.items():
            if a.only and a.only not in name:
                continue
            runs = []
            for i in range(a.repeat):
                if i == 0:
                    e.start_worker()  # cold
                t0 = time.time()
                pid = fn(e)
                wall = time.time() - t0
                st = job_stats(e, pid)
                runs.append({"wall_s": round(wall, 2), "cache": "cold" if i == 0 else "warm",
                             **st})
                print(f"{a.label} {name} run {i + 1}: {wall:.1f}s, {st['jobs']} jobs, "
                      f"queue {st['queue_wait_s']}s", flush=True)
            walls = [r["wall_s"] for r in runs]
            warm = [r["wall_s"] for r in runs if r["cache"] == "warm"]
            rep["workloads"][name] = {
                "runs": runs, "wall_mean_s": round(statistics.mean(walls), 2),
                "wall_stdev_s": round(statistics.stdev(walls), 2) if len(walls) > 1 else 0.0,
                "cold_s": runs[0]["wall_s"], "warm_mean_s": round(statistics.mean(warm), 2)
                if warm else None, "jobs": runs[-1]["jobs"]}
        if not a.only or "W5" in a.only:
            rep["workloads"]["W5_cancel_retry"] = w5_cancel_retry(e)
    finally:
        e.close()
    (out / f"perf_{a.label}.json").write_text(json.dumps(rep, indent=1))
    print(json.dumps({k: (v.get("wall_mean_s"), v.get("jobs")) if isinstance(v, dict) and
                      "runs" in v else v for k, v in rep["workloads"].items()}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
