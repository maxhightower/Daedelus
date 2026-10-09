"""Dump a project's persisted state (board layout, sources, bindings, artifacts, workflows)
from a running backend, for before/after restart comparison.

    python tools/native_validation/project_state.py http://127.0.0.1:PORT "Project name" out.json
    python tools/native_validation/project_state.py --compare before.json after.json
"""
from __future__ import annotations

import json
import os
import sys

import httpx


def dump(base: str, name: str) -> dict:
    c = httpx.Client(base_url=base, timeout=60)
    p = next(p for p in c.get("/api/projects").json() if p["name"] == name)
    pid = p["id"]
    boards = c.get(f"/api/projects/{pid}/boards").json()
    out = {"project": {"id": pid, "name": p["name"]}, "boards": [], "sources": [], "bindings": [],
           "artifacts": [], "workflows": []}
    for b in boards:
        full = c.get(f"/api/projects/{pid}/boards/{b['id']}").json()
        out["boards"].append({
            "id": full["id"], "name": full["name"],
            "items": sorted(({"id": i["id"], "type": i["item_type"],
                              "ref": i.get("resource_ref"), "position": i["position"],
                              "size": i["size"]} for i in full["items"]), key=lambda i: i["id"]),
            "connections": len(full.get("connections", [])),
            "saved_views": len(full.get("saved_views", []))})
    for s in c.get(f"/api/projects/{pid}/sources").json():
        out["sources"].append({"id": s["id"], "name": s["name"], "media_type": s["media_type"],
                               "sha256": s.get("sha256")})
    for bd in c.get(f"/api/projects/{pid}/bindings").json():
        out["bindings"].append({"id": bd["id"], "source": bd["source_id"], "role": bd["role"],
                                "target": bd["target"], "aspects": bd.get("aspects")})
    for a in c.get(f"/api/projects/{pid}/artifacts").json():
        out["artifacts"].append({"id": a["id"], "name": a["name"], "adapter": a["adapter"],
                                 "head": a["head_revision_id"],
                                 "components": sorted(x["id"] for x in a["components"])})
    for w in c.get(f"/api/projects/{pid}/workflows").json():
        out["workflows"].append({"id": w["id"], "name": w["name"], "version": w["version"],
                                 "nodes": sorted(n["type"] for n in w["nodes"])})
    out["bindings"].sort(key=lambda b: b["id"])
    return out


def main() -> int:
    if sys.argv[1] == "--compare":
        a, b = (json.load(open(f, encoding="utf-8")) for f in sys.argv[2:4])
        same = a == b
        print("IDENTICAL" if same else "DIFFERENT")
        if not same:
            for k in a:
                if a[k] != b.get(k):
                    print(f"  differs: {k}")
        return 0 if same else 1
    state = dump(sys.argv[1], sys.argv[2])
    os.makedirs(os.path.dirname(sys.argv[3]) or ".", exist_ok=True)
    with open(sys.argv[3], "w", encoding="utf-8") as f:
        json.dump(state, f, indent=1, ensure_ascii=False)
    print(json.dumps({k: (len(v) if isinstance(v, list) else v) for k, v in state.items()}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
