"""Create the deterministic NATIVE-1 GPU benchmark project: one board per workload P1-P8.

    python tools/native_validation/build_perf_project.py http://127.0.0.1:PORT out.json

P1 empty board | P2 normal mixed board with one 3D view | P3 three 3D views | P4 six 3D views
(the WebGL budget) | P5 eight 3D views (static fallback expected beyond the budget) | P6 ~150
items / ~250 connections | P7 one large 3D view (measured in Focus) | P8 mixed: 3D, image, code,
spreadsheet, document, slides. All 3D views show the same Blender "Table" artifact.
"""
from __future__ import annotations

import json
import sys

import httpx

from daedelus.demo import TABLE_COMPONENTS

W3D = {"width": 420, "height": 340}


def main(base: str, out: str) -> int:
    c = httpx.Client(base_url=base, timeout=600)
    pid = c.post("/api/projects", json={"name": "GPU bench (NATIVE-1)",
                                        "description": "P1-P8 workloads"}).json()["id"]
    P = f"/api/projects/{pid}"

    def art(name, adapter, template, params, **kw):
        r = c.post(f"{P}/artifacts", json={"name": name, "adapter": adapter, "template": template,
                                          "params": params, **kw})
        r.raise_for_status()
        return r.json()["artifact"]

    table = art("Table", "blender", "components", {"components": TABLE_COMPONENTS})
    bg = art("Background", "layered2d", "layers", {"width": 640, "height": 400, "root_id": "bg"})
    code = art("Module", "code", "files", {"files": {"m.py": "def f(x):\n    return 2 * x\n"}})
    sheet = art("Sheet", "spreadsheet", "data", {"name": "Sheet"})
    doc = art("Report", "document", "blank", {"title": "Bench report", "name": "Report"})
    deck = art("Deck", "presentation", "blank", {"name": "Deck"})
    srcs = [c.post(f"{P}/sources/text", json={"name": f"ref-{i}.txt",
                                              "text": f"Reference {i}: warm palette, tapered legs."}
                   ).json() for i in range(40)]
    others = [bg, code]
    for i in range(16):
        others.append(art(f"Module {i}", "code", "files",
                          {"files": {f"m{i}.py": f"def f{i}(x):\n    return x * {i}\n"}}))

    def view(a, i, x, y, size=None):
        return {"id": f"view_{i:04d}", "item_type": "artifact_view",
                "resource_ref": {"kind": "artifact", "id": a["id"]},
                "position": {"x": x, "y": y}, "size": size or W3D}

    def src_item(s, i, x, y):
        return {"id": f"src_{i:04d}", "item_type": "source",
                "resource_ref": {"kind": "source", "id": s["id"]},
                "position": {"x": x, "y": y}, "size": {"width": 240, "height": 210}}

    def note(i, x, y):
        return {"id": f"note_{i:04d}", "item_type": "note", "resource_ref": {"kind": "note"},
                "position": {"x": x, "y": y}, "size": {"width": 240, "height": 140},
                "presentation_state": {"text": f"Note {i}: check proportions."}}

    grid = lambda k, cols=4: (k % cols * 460, k // cols * 400)  # noqa: E731
    boards: dict[str, tuple[list, list]] = {}
    boards["P1 empty"] = ([], [])
    boards["P2 normal"] = ([view(table, 0, 0, 0), view(bg, 1, 460, 0), view(code, 2, 920, 0),
                            src_item(srcs[0], 0, 0, 400), src_item(srcs[1], 1, 300, 400),
                            note(0, 600, 400), note(1, 900, 400)], [])
    boards["P3 three 3D"] = ([view(table, i, *grid(i, 3)) for i in range(3)], [])
    boards["P4 six 3D"] = ([view(table, i, *grid(i, 3)) for i in range(6)], [])
    boards["P5 eight 3D"] = ([view(table, i, *grid(i, 4)) for i in range(8)], [])
    boards["P7 focus 3D"] = ([view(table, 0, 0, 0, {"width": 900, "height": 700})], [])
    boards["P8 mixed"] = ([view(table, 0, 0, 0), view(bg, 1, 460, 0), view(code, 2, 920, 0),
                           view(sheet, 3, 0, 400), view(doc, 4, 460, 400), view(deck, 5, 920, 400)],
                          [])
    # P6: ~150 items / ~250 connections (code + layered views, sources, notes, frames)
    items, k = [], 0
    views = []
    for a in others:
        views.append(view(a, len(views), *grid(k, 14)))
        k += 1
    for i in range(len(others)):
        v = dict(views[i])
        v["id"] = f"view_{len(views):04d}"
        v["position"] = dict(zip(("x", "y"), grid(k, 14)))
        views.append(v)
        k += 1
    sitems = [src_item(s, i, *grid(k + i, 14)) for i, s in enumerate(srcs)]
    k += len(srcs)
    notes = [note(i, *grid(k + i, 14)) for i in range(60)]
    k += 60
    frames = [{"id": f"frame_{i:04d}", "item_type": "frame", "resource_ref": {"kind": "frame"},
               "position": {"x": i % 4 * 1600 - 40, "y": i // 4 * 1300 + 4000},
               "size": {"width": 1500, "height": 1200}, "z_index": -10,
               "presentation_state": {"title": f"Frame {i}", "color": "#5a7a55"}} for i in range(12)]
    items = views + sitems + notes + frames
    conns = []
    for i in range(60):
        tgt = views[i % len(views)] if i % 2 else sitems[i % len(sitems)]
        conns.append({"id": f"ann_{i:04d}", "connection_type": "annotation",
                      "source_item_id": notes[i]["id"], "target_item_id": tgt["id"],
                      "source_anchor": {"handle": "note-out"}, "target_anchor": {"handle": "ann-in"}})
    i = 0
    while len(conns) < 250:
        a, b = views[i % len(views)], views[(i * 7 + 3) % len(views)]
        i += 1
        if a["id"] == b["id"]:
            continue
        conns.append({"id": f"dep_{i:04d}", "connection_type": "dependency",
                      "source_item_id": a["id"], "target_item_id": b["id"],
                      "source_anchor": {"handle": "dep-out"}, "target_anchor": {"handle": "dep-in"}})
    boards["P6 large"] = (items, conns)

    made = {}
    for name in sorted(boards):
        # item ids unique across boards, as the studio's own uid() makes them
        pre = name.split()[0].lower()
        items = [{**it, "id": f"{pre}_{it['id']}"} for it in boards[name][0]]
        conns = [{**cn, "id": f"{pre}_{cn['id']}", "source_item_id": f"{pre}_{cn['source_item_id']}",
                  "target_item_id": f"{pre}_{cn['target_item_id']}"} for cn in boards[name][1]]
        b = c.post(f"{P}/boards", json={"name": name, "layout": "empty"}).json()
        r = c.put(f"{P}/boards/{b['id']}", json={"board": {**b, "items": items, "connections": conns},
                                                 "expected_revision": b["revision"]})
        r.raise_for_status()
        saved = r.json()
        made[name] = {"id": b["id"], "items": len(saved["items"]),
                      "connections": len(saved["connections"])}
    for b in c.get(f"{P}/boards").json():  # drop the auto-created default board
        if b["id"] not in {m["id"] for m in made.values()}:
            c.delete(f"{P}/boards/{b['id']}")
    summary = {"project": pid, "boards": made,
               "artifacts": {a["name"]: a["id"] for a in (table, bg, code, sheet, doc, deck)}}
    with open(out, "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=1)
    print(json.dumps(summary, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main(*sys.argv[1:]))
