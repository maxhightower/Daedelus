"""Command line entry point: ``daedelus serve | demo | run | replay | adapters``."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path


def _ws(args) -> Path:
    return Path(args.workspace or os.environ.get("DAEDELUS_WORKSPACE") or
                Path.home() / ".daedelus" / "workspace")


def _pid_alive(pid: int) -> bool:
    if sys.platform == "win32":
        import ctypes

        k32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        handle = k32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return False
        code = ctypes.c_ulong()
        k32.GetExitCodeProcess(handle, ctypes.byref(code))
        k32.CloseHandle(handle)
        return code.value == 259  # STILL_ACTIVE
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _watch_parent(pid: int) -> None:
    """Stop the backend when the desktop shell that started it is gone (no orphaned servers)."""
    import threading
    import time

    def loop() -> None:
        while True:
            time.sleep(1.5)
            if not _pid_alive(pid):
                os._exit(0)

    threading.Thread(target=loop, daemon=True, name="parent-watchdog").start()


def _cluster_cmd(args) -> int:
    """Offline credential administration against the workspace's cluster database."""
    from .distributed.identity import CredentialStore
    from .distributed.queue import JobQueue
    from .distributed.service import Audit

    root = Path(_ws(args)) / "cluster"
    q = JobQueue(root / "queue.db")
    creds = CredentialStore(q)
    audit = Audit(root / "audit.jsonl")

    def emit(secret: str, out: str | None) -> None:
        if out:
            fd = os.open(out, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w") as f:
                f.write(secret + "\n")
            print(f"credential written to {out}")
        else:
            print(secret)
    try:
        if args.cred_cmd == "create":
            ads = [a.strip() for a in args.adapters.split(",")] if args.adapters else None
            caps = [c.strip() for c in args.capabilities.split(",") if c.strip()]
            rec, wire = creds.create(args.name, adapters=ads, capabilities=caps)
            audit.record("worker_credential_created", actor="cli", target=rec["id"],
                         name=args.name, adapters=ads, capabilities=caps)
            print(json.dumps({k: rec[k] for k in ("id", "name", "adapters", "capabilities")}),
                  file=sys.stderr)
            emit(wire, args.out)
        elif args.cred_cmd == "list":
            print(json.dumps(creds.list(), indent=1))
        elif args.cred_cmd == "rotate":
            wire = creds.rotate(args.id, grace_s=args.grace)
            audit.record("worker_credential_rotated", actor="cli", target=args.id,
                         grace_s=args.grace)
            emit(wire, args.out)
        elif args.cred_cmd == "revoke":
            jobs = creds.revoke(args.id, args.reason)
            audit.record("worker_revoked", actor="cli", target=args.id, reason=args.reason,
                         leases_revoked=jobs)
            print(json.dumps({"id": args.id, "state": "revoked", "leases_revoked": jobs}))
    except KeyError as exc:
        print(f"unknown or inactive credential: {exc}", file=sys.stderr)
        return 1
    finally:
        q.close()
    return 0


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if argv[:1] == ["worker"]:
        from .distributed.worker import main as worker_main

        return worker_main(argv[1:])
    ap = argparse.ArgumentParser(prog="daedelus")
    ap.add_argument("--workspace", help="workspace directory (default ~/.daedelus/workspace)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("serve", help="run the API (and the built studio UI if present)")
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--port", type=int, default=8765)
    s.add_argument("--studio-dist", default=None)
    s.add_argument("--parent-pid", type=int, default=None,
                   help="exit when this process ends (used by the desktop shell)")
    s.add_argument("--tls-cert", default=os.environ.get("DAEDELUS_TLS_CERT"),
                   help="serve HTTPS with this certificate chain (PEM)")
    s.add_argument("--tls-key", default=os.environ.get("DAEDELUS_TLS_KEY"))
    s.add_argument("--tls-client-ca", default=os.environ.get("DAEDELUS_TLS_CLIENT_CA"),
                   help="also request client certificates signed by this CA (optional mTLS)")
    s.add_argument("--behind-tls-proxy", action="store_true",
                   default=bool(os.environ.get("DAEDELUS_BEHIND_TLS_PROXY")),
                   help="plain HTTP on a private interface; a TLS proxy terminates HTTPS "
                        "(trusts X-Forwarded-* from DAEDELUS_FORWARDED_ALLOW_IPS)")
    cs = sub.add_parser("cluster", help="worker credentials (V2.1): create / list / rotate / "
                                        "revoke")
    csub = cs.add_subparsers(dest="cluster_cmd", required=True)
    cc = csub.add_parser("credential", help="manage per-worker credentials")
    ccs = cc.add_subparsers(dest="cred_cmd", required=True)
    c_new = ccs.add_parser("create", help="create a credential (printed once)")
    c_new.add_argument("--name", required=True)
    c_new.add_argument("--adapters", default=None, help="comma-separated; default: any")
    c_new.add_argument("--capabilities", default="cpu", help="cpu or cpu,gpu")
    c_new.add_argument("--out", default=None, help="write the credential to this file (0600)")
    ccs.add_parser("list", help="list credentials (secrets are never shown)")
    c_rot = ccs.add_parser("rotate", help="issue a new secret")
    c_rot.add_argument("id")
    c_rot.add_argument("--grace", type=float, default=0.0, help="seconds the old secret stays valid")
    c_rot.add_argument("--out", default=None)
    c_rev = ccs.add_parser("revoke", help="revoke a credential and its leases")
    c_rev.add_argument("id")
    c_rev.add_argument("--reason", default="revoked by operator")
    d = sub.add_parser("demo", help="build the multimodal demo and run all isolation scenarios")
    d.add_argument("--out", default="evidence/demo")
    d.add_argument("--no-video", action="store_true", help="skip the YouTube URL source")
    d11 = sub.add_parser("demo-v11", help="V1.1 semantic demonstrations A-E (evidence output)")
    d11.add_argument("--out", default="evidence/v1_1")
    d11.add_argument("--live", default=None, choices=["anthropic", "gemini"],
                     help="also run live gates with this provider (needs credentials)")
    sub.add_parser("worker", help="run a V2 execution worker (see --help after 'worker')",
                   add_help=False)
    d12 = sub.add_parser("demo-v12", help="V1.2 research analysis & presentation pipeline")
    d12.add_argument("--out", default="evidence/v1_2")
    r = sub.add_parser("run", help="execute a workflow")
    r.add_argument("project")
    r.add_argument("workflow")
    r.add_argument("--mode", default="incremental", choices=["incremental", "full"])
    rp = sub.add_parser("replay", help="reproduce an execution from its saved inputs")
    rp.add_argument("project")
    rp.add_argument("execution")
    sub.add_parser("adapters", help="list adapters and their availability")
    args = ap.parse_args(argv)

    if args.cmd == "serve":
        import uvicorn

        from .api import create_app

        if args.parent_pid:
            _watch_parent(args.parent_pid)
        import ipaddress

        from . import profile
        try:
            loopback = ipaddress.ip_address(args.host).is_loopback
        except ValueError:
            loopback = args.host == "localhost"
        if not loopback and not os.environ.get("DAEDELUS_API_TOKENS"):
            print(f"refusing to listen on {args.host} without DAEDELUS_API_TOKENS: a non-loopback "
                  "bind would expose every project unauthenticated", file=sys.stderr)
            return 2
        tls = bool(args.tls_cert)
        if tls and not args.tls_key:
            print("--tls-key is required with --tls-cert", file=sys.stderr)
            return 2
        if profile.hosted() and not tls and not loopback and not args.behind_tls_proxy:
            print("hosted profile: refusing a plaintext control plane on a non-loopback "
                  "interface. Serve HTTPS (--tls-cert/--tls-key) or put it behind a TLS proxy "
                  "on a private network (--behind-tls-proxy).", file=sys.stderr)
            return 2
        if profile.hosted() and not os.environ.get("DAEDELUS_API_TOKENS"):
            print("hosted profile: DAEDELUS_API_TOKENS is required", file=sys.stderr)
            return 2
        app = create_app(_ws(args), args.studio_dist)
        kw: dict = {}
        if tls:
            import ssl
            kw.update(ssl_certfile=args.tls_cert, ssl_keyfile=args.tls_key)
            if args.tls_client_ca:
                kw.update(ssl_ca_certs=args.tls_client_ca, ssl_cert_reqs=ssl.CERT_OPTIONAL)
        if args.behind_tls_proxy:
            kw.update(proxy_headers=True, forwarded_allow_ips=os.environ.get(
                "DAEDELUS_FORWARDED_ALLOW_IPS", "127.0.0.1"))
        scheme = "https" if tls else "http"
        print(f"DAEDELUS_LISTENING {scheme}://{args.host}:{args.port}", flush=True)
        uvicorn.run(app, host=args.host, port=args.port, log_level="warning", **kw)
        return 0
    if args.cmd == "cluster":
        return _cluster_cmd(args)
    if args.cmd == "demo":
        from .scenarios import run_scenarios

        out = Path(args.out).resolve()
        rep = run_scenarios(_ws(args) if args.workspace else out / "workspace", out,
                            include_video=not args.no_video)
        print(f"\n{'PASS' if rep['passed'] else 'FAIL'}: "
              f"{sum(c['passed'] for c in rep['checks'])}/{len(rep['checks'])} checks; "
              f"report: {out / 'demo_report.md'}")
        return 0 if rep["passed"] else 1
    if args.cmd == "demo-v11":
        from .scenarios_v11 import run as run_v11

        out = Path(args.out).resolve()
        rep = run_v11(out, live=args.live)
        bad = [d for d in rep["demos"] if d["status"] == "failed"]
        for d in rep["demos"]:
            print(f"{d['id']}: {d['status'].upper()} ({d['verification']})"
                  + (f" - blocked by: {d['blocked_by']}" if d["blocked_by"] else ""))
        return 1 if bad else 0
    if args.cmd == "demo-v12":
        from .scenarios_v12 import run as run_v12

        rep = run_v12(Path(args.out).resolve())
        for d in rep["demos"]:
            print(f"{d['id']}: {d['status'].upper()} ({d['verification']})")
        return 0 if all(d["status"] == "passed" for d in rep["demos"]) else 1
    if args.cmd in ("run", "replay"):
        from .engine import Engine
        from .store import Workspace

        store = Workspace(_ws(args)).open(args.project)
        eng = Engine(store)
        if args.cmd == "run":
            ex = eng.execute(args.workflow, mode=args.mode)
            print(json.dumps({"id": ex.id, "status": ex.status.value, "error": ex.error,
                              "nodes": {n.node_id: n.status.value for n in ex.node_runs}},
                             indent=1))
            return 0 if ex.status.value == "succeeded" else 1
        rep = eng.replay(args.execution)
        print(json.dumps(rep, indent=1))
        return 0 if rep["reproducible"] else 1
    if args.cmd == "adapters":
        from .adapters import registry

        for a in registry().values():
            info = a.info()
            print(f"{info.name:10s} available={info.available} "
                  f"{info.unavailable_reason or ''}")
        return 0
    return 2


if __name__ == "__main__":
    sys.exit(main())
