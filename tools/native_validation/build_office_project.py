"""Build the V1.2 Office pipeline (workbook -> report -> deck) on a running backend, the same
way studio/e2e/office.e2e.mjs does, and download the generated native files.

    python tools/native_validation/build_office_project.py http://127.0.0.1:PORT OUT_DIR [name]

Sources are uploaded (multipart) from backend/daedelus/demo_assets/v12, so this works against
the packaged sidecar, which does not bundle those assets.
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

import httpx

ASSETS = Path(__file__).resolve().parents[2] / "backend" / "daedelus" / "demo_assets" / "v12"


def main(base: str, out: str, name: str = "Office interop (NATIVE-1)") -> int:
    c = httpx.Client(base_url=base, timeout=600)
    pid = c.post("/api/projects", json={"name": name, "description": "NATIVE-1 Office"}).json()["id"]
    P = f"/api/projects/{pid}"

    def upload(fname: str) -> dict:
        with open(ASSETS / fname, "rb") as f:
            r = c.post(f"{P}/sources/upload", files={"files": (fname, f.read())})
        r.raise_for_status()
        return r.json()[0]

    data = upload("co2_annmean_mlo.csv")
    notes = upload("research_notes.md")
    guide = upload("visual_guide.md")

    def mk(label: str, adapter: str, params: dict) -> dict:
        r = c.post(f"{P}/artifacts", json={"name": label, "adapter": adapter, "template": "blank",
                                          "params": params})
        r.raise_for_status()
        return r.json()["artifact"]

    wb = mk("CO2 analysis", "spreadsheet", {"name": "CO2 analysis"})
    doc = mk("CO2 report", "document", {"title": "Atmospheric CO2 at Mauna Loa", "name": "CO2 report"})
    deck = mk("CO2 briefing", "presentation", {"name": "CO2 briefing"})
    for s, a, role in ((data, wb, "reference"), (notes, doc, "reference"), (data, doc, "context"),
                       (guide, deck, "guideline"), (notes, deck, "context")):
        c.post(f"{P}/bindings", json={"source_id": s["id"], "role": role, "aspects": [],
                                      "target": {"scope": "artifact", "artifact_id": a["id"]}}
               ).raise_for_status()
    links = [
        {"source": {"artifact_id": wb["id"], "component_id": "summary_values"},
         "target": {"artifact_id": doc["id"], "component_id": "results_table"}, "target_kind": "table",
         "options": {"create": {"parent": "results", "after": "results_text"}}},
        {"source": {"artifact_id": wb["id"], "component_id": "decade_chart"},
         "target": {"artifact_id": doc["id"], "component_id": "fig_decades"}, "target_kind": "figure",
         "options": {"create": {"parent": "results", "after": "results_table", "width_cm": 14,
                                "caption": "Figure 1. Mean CO2 by decade"}}},
        {"source": {"artifact_id": wb["id"], "component_id": "summary_values"},
         "target": {"artifact_id": doc["id"], "component_id": "results_text"},
         "options": {"template": "Decade means rose from {B2:.1f} ppm in the {A2} to {B9:.1f} ppm "
                                 "in the {A9}."}},
        {"source": {"artifact_id": wb["id"], "component_id": "decade_means"},
         "target": {"artifact_id": deck["id"], "component_id": "deck_chart"}, "target_kind": "chart",
         "options": {"create": {"slide": "data_slide", "box": [0.08, 0.22, 0.84, 0.7],
                                "type": "column", "title": "Mean CO2 by decade (ppm)"}}},
        {"source": {"artifact_id": doc["id"], "component_id": "discussion"},
         "target": {"artifact_id": deck["id"], "component_id": "findings.body"},
         "options": {"max_paragraphs": 2}},
    ]

    def agent(nid: str, aid: str, label: str, instructions: str) -> list[dict]:
        return [{"id": f"art_{nid}", "type": "artifact", "label": label, "config": {"artifact_id": aid}},
                {"id": nid, "type": "agent", "label": f"{label} agent",
                 "config": {"instructions": instructions, "fan_out": False}}]

    nodes = [{"id": "src", "type": "sources", "label": "Sources", "config": {}},
             *agent("build_wb", wb["id"], "Workbook",
                    "Build the analysis workbook from the dataset.\nValue label: CO2 (ppm)"),
             *agent("build_doc", doc["id"], "Report", "Write the report from the research notes."),
             *agent("build_deck", deck["id"], "Deck",
                    "Title: Atmospheric CO2 at Mauna Loa\nBuild the slide deck."),
             {"id": "deps", "type": "dependencies", "label": "Link components", "config": {"links": links}}]
    edges = []
    for n in ("build_wb", "build_doc", "build_deck"):
        edges += [{"id": f"a_{n}", "source": f"art_{n}", "source_port": "artifact", "target": n,
                   "target_port": "artifact"},
                  {"id": f"s_{n}", "source": "src", "source_port": "sources", "target": n,
                   "target_port": "sources"},
                  {"id": f"d_{n}", "source": n, "source_port": "revision", "target": "deps",
                   "target_port": "after"}]
    wf = c.post(f"{P}/workflows", json={"name": "Research analysis and presentation",
                                        "nodes": nodes, "edges": edges}).json()
    ex = c.post(f"{P}/workflows/{wf['id']}/execute", json={"mode": "incremental"}).json()
    t0 = time.time()
    while ex["status"] in ("pending", "running", "queued") and time.time() - t0 < 600:
        time.sleep(1.5)
        ex = c.get(f"{P}/executions/{ex['id']}").json()
    deps = c.get(f"{P}/dependencies").json()
    os.makedirs(out, exist_ok=True)
    files = {}
    for a in (wb, doc, deck):
        art = c.get(f"{P}/artifacts/{a['id']}").json()
        rel = f"{art['native_dir']}/{art['entry']}"
        blob = c.get(f"{P}/files/{rel}")
        blob.raise_for_status()
        dst = Path(out) / art["entry"]
        dst.write_bytes(blob.content)
        files[a["name"]] = {"artifact_id": a["id"], "head_revision": art["head_revision_id"],
                            "file": str(dst), "bytes": len(blob.content)}
    summary = {"project": pid, "workflow": wf["id"], "execution": ex["id"], "status": ex["status"],
               "nodes": {r["node_id"]: r["status"] for r in ex.get("node_runs", [])},
               "dependencies": [d["status"] for d in deps], "files": files}
    (Path(out) / "office_project.json").write_text(json.dumps(summary, indent=1), encoding="utf-8")
    print(json.dumps(summary, indent=1))
    return 0 if ex["status"] == "succeeded" else 1


if __name__ == "__main__":
    sys.exit(main(*sys.argv[1:]))
