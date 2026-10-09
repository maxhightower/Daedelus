"""Worker-host side of the hosted multi-machine end-to-end run (runs on host B).

    python deploy/hosted/worker_supervisor.py --key key.json --handoff handoff/ --out evidence/

1. Opens the sealed handoff from the control host (control URL + per-worker credentials).
2. Records evidence about THIS machine (hostname, hashed machine/boot/VM ids, public IP).
3. Follows the phases the control host publishes as a commit status (context
   ``daedelus-hosted-phase``; description = compact JSON) and runs the requested worker
   processes, e.g. ``{"p":2,"w":[["w-crash","a","crash_after_lease"]]}``. ``{"p":"done"}`` ends.

Workers run in the hosted profile: per-worker credential, HTTPS with certificate
verification, bubblewrap job sandbox (required for code jobs).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
CONTEXT = "daedelus-hosted-phase"


def sh(*a: str) -> str:
    r = subprocess.run(a, capture_output=True, text=True)
    return r.stdout.strip() if r.returncode == 0 else ""


def h(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()[:16] if s else ""


def host_evidence() -> dict:
    vm = sh("curl", "-s", "-m", "5", "-H", "Metadata:true",
            "http://169.254.169.254/metadata/instance/compute?api-version=2021-02-01")
    try:
        vmj = json.loads(vm)
    except ValueError:
        vmj = {}
    return {"hostname": socket.gethostname(), "runner_name": os.environ.get("RUNNER_NAME"),
            "machine_id_sha256": h(Path("/etc/machine-id").read_text().strip()
                                   if Path("/etc/machine-id").exists() else ""),
            "boot_id_sha256": h(Path("/proc/sys/kernel/random/boot_id").read_text().strip()),
            "cloud_vm_id_sha256": h(vmj.get("vmId", "")), "cloud_location": vmj.get("location"),
            "cloud_vm_size": vmj.get("vmSize"),
            "public_ip": sh("curl", "-s", "-m", "10", "https://api.ipify.org"),
            "kernel": platform.release(), "cpus": os.cpu_count(), "time": time.time()}


def phase() -> dict | None:
    local = os.environ.get("DAEDELUS_HOSTED_PHASE_FILE")  # local dry run without GitHub
    if local:
        try:
            return json.loads(Path(local).read_text())
        except (OSError, ValueError):
            return None
    repo, sha = os.environ["GITHUB_REPOSITORY"], os.environ["GITHUB_SHA"]
    out = sh("gh", "api", f"repos/{repo}/commits/{sha}/statuses?per_page=50")
    try:
        sts = [s for s in json.loads(out) if s.get("context") == CONTEXT]
    except ValueError:
        return None
    if not sts:
        return None
    st = max(sts, key=lambda s: s["id"])
    try:
        return json.loads(st.get("description") or "")
    except ValueError:
        return None


class Pool:
    def __init__(self, control: str, creds: dict, out: Path, secrets_dir: Path):
        self.control, self.creds, self.out, self.sec = control, creds, out, secrets_dir
        self.procs: dict[str, subprocess.Popen] = {}
        self.exits: dict[str, int] = {}

    def start(self, name: str, cred: str, fault: str) -> None:
        cf = self.sec / f"cred_{name}"  # never inside the uploaded evidence directory
        fd = os.open(cf, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write(self.creds[cred])
        env = {**os.environ, "DAEDELUS_PROFILE": "hosted",
               "DAEDELUS_WORKER_CREDENTIAL_FILE": str(cf), "DAEDELUS_WORKER_FAULT": fault,
               "DAEDELUS_JOB_ISOLATION": "bwrap", "DAEDELUS_WORKER_DEPLOYMENT": "hosted",
               "DAEDELUS_WORKER_ALLOWED_COMMANDS": "python -m pytest",
               "DAEDELUS_WORKER_DIR": str(self.out / f"work_{name}")}
        for k in ("DAEDELUS_WORKER_TOKEN", "DAEDELUS_WORKER_CREDENTIAL", "GH_TOKEN",
                  "GITHUB_TOKEN"):
            env.pop(k, None)
        log = open(self.out / f"worker_{name}.log", "ab")
        self.procs[name] = subprocess.Popen(
            [sys.executable, "-m", "daedelus.cli", "worker", "--control", self.control,
             "--name", name], env=env, stdout=log, stderr=subprocess.STDOUT,
            start_new_session=True)
        print(f"started worker {name} (cred {cred}, fault '{fault}')", flush=True)

    def stop_all(self) -> None:
        for name, p in self.procs.items():
            if p.poll() is None:
                p.send_signal(signal.SIGTERM)
        t0 = time.time()
        for name, p in self.procs.items():
            try:
                p.wait(timeout=max(1, 25 - (time.time() - t0)))
            except subprocess.TimeoutExpired:
                os.killpg(p.pid, signal.SIGKILL)
                p.wait()
            self.exits[name] = p.returncode
        self.procs = {}

    def poll(self) -> None:
        for name, p in list(self.procs.items()):
            if p.poll() is not None and name not in self.exits:
                self.exits[name] = p.returncode
                print(f"worker {name} exited with {p.returncode}", flush=True)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--key", required=True)
    ap.add_argument("--handoff", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--timeout", type=float, default=2400)
    a = ap.parse_args()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    ho = Path(a.handoff)
    sys.path.insert(0, str(HERE))
    from handoff import open_
    import shutil
    import tempfile
    sec = Path(tempfile.mkdtemp(prefix="dd_creds_"))
    open_(a.key, str(ho / "sealed.json"), str(sec / "creds.json"))
    creds = json.loads((sec / "creds.json").read_text())
    control = (ho / "control_url.txt").read_text().strip()
    ev = host_evidence()
    ev["control_url"] = control
    (out / "worker_host.json").write_text(json.dumps(ev, indent=1))
    print("WORKER_HOST_EVIDENCE " + json.dumps(ev), flush=True)
    probe = subprocess.run([sys.executable, "-c",
                            "import json,tempfile;from pathlib import Path;"
                            "from daedelus.distributed.sandbox import Sandbox;"
                            "print(json.dumps(Sandbox(Path(tempfile.mkdtemp()),'bwrap').probe()))"],
                           capture_output=True, text=True)
    print("WORKER_SANDBOX_PROBE " + probe.stdout.strip()[-1500:] + probe.stderr[-500:], flush=True)
    pool = Pool(control, creds, out, sec)
    current = None
    t0 = time.time()
    while time.time() - t0 < a.timeout:
        pool.poll()
        ph = phase()
        if ph and ph.get("p") != (current or {}).get("p"):
            print(f"phase -> {ph}", flush=True)
            pool.stop_all()
            if ph.get("p") == "done":
                break
            for name, cred, fault in ph.get("w", []):
                pool.start(name, cred, fault)
            current = ph
        time.sleep(4)
    pool.stop_all()
    (out / "worker_exits.json").write_text(json.dumps(pool.exits, indent=1))
    print("WORKER_EXITS " + json.dumps(pool.exits), flush=True)
    shutil.rmtree(sec, ignore_errors=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
