"""Provision secrets for the compose deployments (V2.1).

    python deploy/provision.py [--env-file deploy/.env] [--hardened] [--tls]

* ``deploy/.env``: random API token (and a join token for the development topology) if the
  file does not exist yet.
* ``--tls``: a private development CA and a server certificate for the TLS edge
  (``deploy/tls/``). Replace with a real certificate for anything public.
* ``--hardened``: one credential per worker, created *inside* the control plane's data volume
  with ``daedelus cluster credential create`` and written to ``deploy/secrets/<worker>.cred``
  (the directory is 0700; compose mounts each file only into its own worker as a secret).

Secrets are generated locally and never printed.
"""

from __future__ import annotations

import argparse
import os
import secrets
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
WORKERS = {"worker-office": "spreadsheet,document,presentation",
           "worker-blender": "blender,layered2d",
           "worker-code": "code"}


def ensure_env(env_file: Path) -> None:
    if env_file.exists():
        return
    join = "wk-" + secrets.token_hex(16)
    env_file.write_text(f"DAEDELUS_API_TOKENS=api-{secrets.token_hex(16)}\n"
                        f"DAEDELUS_WORKER_TOKENS={join}\nDAEDELUS_WORKER_TOKEN={join}\n")
    env_file.chmod(0o600)


def make_pki(d: Path, hostnames=("localhost", "control.daedelus.internal"),
             ips=("127.0.0.1",)) -> None:
    d.mkdir(parents=True, exist_ok=True)
    run = lambda *a: subprocess.run(a, check=True, capture_output=True)  # noqa: E731
    run("openssl", "req", "-x509", "-newkey", "rsa:3072", "-nodes", "-days", "30",
        "-subj", "/CN=Daedelus development CA", "-keyout", str(d / "ca.key"),
        "-out", str(d / "ca.crt"), "-addext", "basicConstraints=critical,CA:TRUE",
        "-addext", "keyUsage=critical,keyCertSign,cRLSign")
    run("openssl", "req", "-newkey", "rsa:2048", "-nodes", "-subj", f"/CN={hostnames[0]}",
        "-keyout", str(d / "server.key"), "-out", str(d / "server.csr"))
    sans = ",".join([f"DNS:{h}" for h in hostnames] + [f"IP:{i}" for i in ips])
    (d / "server.ext").write_text(f"subjectAltName={sans}\nextendedKeyUsage=serverAuth\n")
    run("openssl", "x509", "-req", "-in", str(d / "server.csr"), "-CA", str(d / "ca.crt"),
        "-CAkey", str(d / "ca.key"), "-CAcreateserial", "-days", "30", "-out",
        str(d / "server.crt"), "-extfile", str(d / "server.ext"))
    for k in ("ca.key", "server.key"):
        (d / k).chmod(0o644 if k == "server.key" else 0o600)  # the edge container reads it


def compose(env_file: Path, files: list[str], *args: str) -> subprocess.CompletedProcess:
    cmd = ["docker", "compose"]
    for f in files:
        cmd += ["-f", str(HERE / f)]
    cmd += ["--env-file", str(env_file), *args]
    return subprocess.run(cmd, capture_output=True, text=True)


def provision_credentials(env_file: Path, files: list[str]) -> dict[str, str]:
    sec = HERE / "secrets"
    sec.mkdir(exist_ok=True)
    sec.chmod(0o700)
    for w in WORKERS:  # compose refuses to load a project whose secret files are missing
        (sec / f"{w}.cred").touch()
    ids = {}
    for w, adapters in WORKERS.items():
        r = compose(env_file, files, "run", "--rm", "--no-deps", "-T", "control", "daedelus",
                    "cluster", "credential", "create", "--name", w, "--adapters", adapters,
                    "--capabilities", "cpu")
        lines = [x for x in r.stdout.strip().splitlines() if x.startswith("ddw1.")]
        if r.returncode != 0 or not lines:
            raise RuntimeError(f"credential for {w} failed: {r.stderr[-1500:]}")
        p = sec / f"{w}.cred"
        p.write_text(lines[-1] + "\n")
        p.chmod(0o644)  # bind-mounted into the worker (uid 10001); the directory stays 0700
        ids[w] = lines[-1].split(".")[1]
    return ids


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--env-file", default=str(HERE / ".env"))
    ap.add_argument("--hardened", action="store_true")
    ap.add_argument("--tls", action="store_true")
    a = ap.parse_args()
    env = Path(a.env_file)
    ensure_env(env)
    files = ["compose.yml"] + (["compose.hardened.yml"] if a.hardened else []) + \
        (["compose.tls.yml"] if a.tls else [])
    if a.tls:
        make_pki(HERE / "tls")
        print("TLS: private CA and server certificate written to deploy/tls/")
    if a.hardened:
        ids = provision_credentials(env, files)
        print("worker credentials:", ", ".join(f"{k}={v}" for k, v in ids.items()))
    print("compose files:", " ".join(f"-f deploy/{f}" for f in files))
    return 0


if __name__ == "__main__":
    sys.exit(main())
