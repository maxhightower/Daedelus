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


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="daedelus")
    ap.add_argument("--workspace", help="workspace directory (default ~/.daedelus/workspace)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("serve", help="run the API (and the built studio UI if present)")
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--port", type=int, default=8765)
    s.add_argument("--studio-dist", default=None)
    d = sub.add_parser("demo", help="build the multimodal demo and run all isolation scenarios")
    d.add_argument("--out", default="evidence/demo")
    d.add_argument("--no-video", action="store_true", help="skip the YouTube URL source")
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

        app = create_app(_ws(args), args.studio_dist)
        print(f"DAEDELUS_LISTENING http://{args.host}:{args.port}", flush=True)
        uvicorn.run(app, host=args.host, port=args.port, log_level="warning")
        return 0
    if args.cmd == "demo":
        from .scenarios import run_scenarios

        out = Path(args.out).resolve()
        rep = run_scenarios(_ws(args) if args.workspace else out / "workspace", out,
                            include_video=not args.no_video)
        print(f"\n{'PASS' if rep['passed'] else 'FAIL'}: "
              f"{sum(c['passed'] for c in rep['checks'])}/{len(rep['checks'])} checks; "
              f"report: {out / 'demo_report.md'}")
        return 0 if rep["passed"] else 1
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
