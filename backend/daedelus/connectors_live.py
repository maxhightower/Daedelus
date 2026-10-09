"""Opt-in live connector harness (V2.1, handoff section 7).

    DAEDELUS_LIVE_CONNECTORS=1 DAEDELUS_GOOGLE_TOKEN=... DAEDELUS_LIVE_GOOGLE_FOLDER=<id> \\
        daedelus connectors-live --connector google --out evidence/v2_1/connectors/google
    DAEDELUS_LIVE_CONNECTORS=1 DAEDELUS_MSGRAPH_TOKEN=... DAEDELUS_LIVE_MSGRAPH_FOLDER=<id> \\
        daedelus connectors-live --connector msgraph --out evidence/v2_1/connectors/msgraph

Safety rules (enforced here, not just documented):

* runs only with ``DAEDELUS_LIVE_CONNECTORS=1``, a token and a *designated test folder*;
* lists only that folder; creates files named ``daedelus-live-test-<run id>.*`` in it;
* modifies, conflicts on and deletes **only files this run created** (ids recorded on
  creation); everything else is read-only;
* cleans up in ``finally`` and verifies the files are gone.

Tests: authentication, discovery, creation, import, targeted modification (one cell),
publication, version retrieval, a stale-version conflict caused by an out-of-band edit, a
permission/not-found failure, Google-native export (optional ``DAEDELUS_LIVE_GOOGLE_NATIVE_ID``),
cleanup. Without the gate or the credentials the report says BLOCKED and names what is missing.
"""

from __future__ import annotations

import json
import os
import secrets
import tempfile
import time
from pathlib import Path
from typing import Any

from . import connectors as cn

ENV = {"google": ("DAEDELUS_GOOGLE_TOKEN", "DAEDELUS_LIVE_GOOGLE_FOLDER"),
       "msgraph": ("DAEDELUS_MSGRAPH_TOKEN", "DAEDELUS_LIVE_MSGRAPH_FOLDER")}
SCOPES = {"google": "https://www.googleapis.com/auth/drive.file (files the app created or the "
                    "user opened with it) - add drive.readonly only to import pre-existing "
                    "Google-native files",
          "msgraph": "Files.ReadWrite (delegated; the signed-in user's files). For a tighter "
                     "grant use Files.SelectedOperations.Selected with the test folder assigned"}


def _xlsx(path: Path, value: Any) -> Path:
    import openpyxl
    wb = openpyxl.Workbook()
    wb.active.title = "Data"
    wb.active["A1"], wb.active["B1"] = "value", value
    wb.save(path)
    return path


def run(connector: str, out: Path) -> dict[str, Any]:
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    tok_env, folder_env = ENV[connector]
    rep: dict[str, Any] = {"connector": connector, "scopes_required": SCOPES[connector],
                           "checks": [], "created": [], "cleanup": None}
    missing = [v for v in ("DAEDELUS_LIVE_CONNECTORS", tok_env, folder_env)
               if not os.environ.get(v)]
    if missing:
        rep["status"] = "blocked"
        rep["blocked_by"] = "missing " + ", ".join(missing)
        _write(out, rep)
        return rep
    folder = os.environ[folder_env]
    c = cn.registry()[connector]
    run_id = secrets.token_hex(4)

    def check(name, ok, detail=""):
        rep["checks"].append({"name": name, "ok": bool(ok), "detail": str(detail)[:500]})
        print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + ("" if ok else f" - {detail}"))
        return bool(ok)
    from .adapters import get_adapter
    from .artifacts import native_path
    from .editing import apply_manual_edit
    from .models import PlannedOperation
    from .store import Workspace
    tmp = Path(tempfile.mkdtemp(prefix="dd_live_conn_"))
    ws = Workspace(tmp / "ws")
    _, st = ws.create_project("live connector test")
    created: list[str] = []
    try:
        files = c.list(folder)
        check("authentication and discovery in the designated folder", True,
              f"{len(files)} file(s)")
        rf = c.create(folder, f"daedelus-live-test-{run_id}.xlsx", _xlsx(tmp / "t.xlsx", 1))
        created.append(rf.remote_id)
        rep["created"].append(rf.remote_id)
        check("created a test workbook in the folder", rf.remote_id, rf.name)
        art, rev = cn.import_remote(st, c, rf.remote_id)
        check("imported it as an editable artifact", art.adapter == "spreadsheet")
        sheet = next(x.id for x in st.get_artifact(art.id).components if x.kind == "sheet")
        apply_manual_edit(st, art.id, [PlannedOperation(
            op="set_cells", component_id=sheet, params={"cells": {"B1": 2}})],
            message="live connector test edit")
        new = cn.publish_remote(st, art.id, c)
        check("published the targeted modification (version changed)",
              new.version != rf.version, (rf.version, new.version))
        revs = c.revisions(rf.remote_id)
        check("version history retrievable", len(revs) >= 1, len(revs))
        # out-of-band edit (another client), then a stale publish must be refused
        other = c.upload(rf.remote_id, _xlsx(tmp / "o.xlsx", 99), new.version)
        apply_manual_edit(st, art.id, [PlannedOperation(
            op="set_cells", component_id=sheet, params={"cells": {"B1": 3}})],
            message="stale edit")
        try:
            cn.publish_remote(st, art.id, c)
            check("a stale publish is refused", False, "publish succeeded over a newer version")
        except cn.ConnectorConflict as exc:
            check("a stale publish is refused (conflict, nothing overwritten)",
                  not exc.written, str(exc)[:200])
        _, back = c.download(rf.remote_id, tmp / "dl")
        import openpyxl
        check("the out-of-band version survived", openpyxl.load_workbook(back)["Data"]["B1"]
              .value == 99, other.version)
        try:
            c.get("daedelus-no-such-file-" + run_id)
            check("a missing/forbidden file is refused", False)
        except (cn.ConnectorError, cn.ConnectorBlocked) as exc:
            check("a missing/forbidden file is refused", True, type(exc).__name__)
        nat = os.environ.get("DAEDELUS_LIVE_GOOGLE_NATIVE_ID") if connector == "google" else None
        if nat:
            a2, _ = cn.import_remote(st, c, nat)
            check("Google-native document exported to OOXML", a2.metadata["remote"]["exported"])
        rep["status"] = "live" if all(x["ok"] for x in rep["checks"]) else "failed"
    except (cn.ConnectorBlocked, cn.ConnectorError) as exc:
        check("live connector run completed", False, f"{type(exc).__name__}: {exc}")
        rep["status"] = "failed"
    finally:
        gone = []
        for rid in created:  # only what this run created
            try:
                c.delete(rid)
                try:
                    c.get(rid)
                except (cn.ConnectorError, cn.ConnectorBlocked):
                    gone.append(rid)
            except Exception as exc:
                rep.setdefault("cleanup_errors", []).append(f"{rid}: {exc}")
        rep["cleanup"] = {"created": created, "deleted_and_verified": gone}
        ws.close()
    rep["finished"] = time.time()
    _write(out, rep)
    return rep


def _write(out: Path, rep: dict[str, Any]) -> None:
    (out / "connector_report.json").write_text(json.dumps(rep, indent=1, default=str))
    lines = [f"# Live connector test — {rep['connector']}", "",
             f"Status: **{rep.get('status')}**" + (f" — {rep['blocked_by']}"
                                                   if rep.get("blocked_by") else ""), "",
             f"Least-privilege scope: {rep['scopes_required']}", ""]
    lines += [f"- [{'x' if c['ok'] else ' '}] {c['name']}" for c in rep["checks"]]
    if rep.get("cleanup"):
        lines += ["", f"Cleanup: {json.dumps(rep['cleanup'])}"]
    (out / "connector_report.md").write_text("\n".join(lines) + "\n")
