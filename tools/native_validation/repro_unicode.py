"""D-002 reproduction: non-cp1252 text flowing through a workflow on Windows.

Creates a demo project (no network video source) on a running backend, adds a guideline
text source containing characters outside cp1252, binds it project-wide, executes the demo
workflow and reports each node's status. Usage:

    python tools/native_validation/repro_unicode.py http://127.0.0.1:PORT out.json
"""
from __future__ import annotations

import json
import os
import sys
import time

import httpx

GUIDE = "Leg profile → taper to 40 mm at the floor; keep tabletop ≤ 0.75 m; finish: CO₂-neutral oil."


def main(base: str, out: str, guide: str = GUIDE) -> int:
    c = httpx.Client(base_url=base, timeout=600)
    demo = c.post("/api/projects/demo", params={"include_video": "false"}).json()
    pid = demo.get("project_id") or demo.get("project", {}).get("id") or demo.get("id")
    src = c.post(f"/api/projects/{pid}/sources/text",
                 json={"name": "Unicode guideline", "text": guide}).json()
    c.post(f"/api/projects/{pid}/bindings", json={
        "source_id": src["id"], "role": "guideline", "target": {"scope": "project"},
        "aspects": ["style"], "strength": 0.8}).raise_for_status()
    wf = c.get(f"/api/projects/{pid}/workflows").json()[0]
    ex = c.post(f"/api/projects/{pid}/workflows/{wf['id']}/execute",
                json={"mode": "full", "nodes": None}).json()
    eid = ex["id"]
    for _ in range(600):
        ex = c.get(f"/api/projects/{pid}/executions/{eid}").json()
        if ex["status"] not in ("pending", "running", "queued"):
            break
        time.sleep(1)
    runs = [{"node": r["node_id"], "type": r["node_type"], "status": r["status"],
             "error": (r.get("error") or "")[:300],
             "unit_errors": [(u.get("error") or "")[:300] for u in r.get("units", [])
                             if u.get("error")]} for r in ex.get("node_runs", [])]
    result = {"project": pid, "execution": eid, "status": ex["status"], "node_runs": runs,
              "source_text_roundtrip_ok": c.get(f"/api/projects/{pid}/sources/{src['id']}")
              .json().get("extracted", {}).get("text", "").startswith(guide[:20])}
    os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=1, ensure_ascii=False)
    print(json.dumps(result, indent=1, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    # optional third argument "--ascii" runs the control with an ASCII-only guideline
    ascii_only = len(sys.argv) > 3 and sys.argv[3] == "--ascii"
    sys.exit(main(sys.argv[1], sys.argv[2],
                  GUIDE.encode("ascii", "replace").decode() if ascii_only else GUIDE))
