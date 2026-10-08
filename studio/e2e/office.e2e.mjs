// V1.2 walkthrough: Office artifacts on the infinite canvas (real backend + LibreOffice).
//   STUDIO_URL=http://127.0.0.1:8765 OUT=../docs/screenshots/v1_2 node e2e/office.e2e.mjs
// Builds the research pipeline through the API, then works only through the studio UI:
// in-place cell edit -> stale dependency edges -> "Update dependents" -> in-place document and
// slide text edits (in Focus), checking after each step that only the intended components
// changed. No AI model is involved (deterministic local recipes).
import { createRequire } from "node:module";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const require = createRequire(import.meta.url);
const playwright = require("playwright");
const BASE = process.env.STUDIO_URL || "http://127.0.0.1:8765";
const OUT = path.resolve(process.env.OUT || "e2e-v1_2");
const ASSETS = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../../backend/daedelus/demo_assets/v12");
fs.mkdirSync(OUT, { recursive: true });
const results = [];
let scenario = "setup";
const check = (name, ok, detail = "") => {
  results.push({ scenario, name, ok: !!ok, detail: ok ? "" : String(detail).slice(0, 600) });
  console.log(`${ok ? "PASS" : "FAIL"} [${scenario}] ${name}${!ok && detail ? " - " + String(detail).slice(0, 300) : ""}`);
  return !!ok;
};
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const api = async (p, init) => {
  const r = await fetch(BASE + p, init);
  if (!r.ok) throw new Error(`${p}: ${r.status} ${await r.text()}`);
  return r.json();
};
const post = (p, body) => api(p, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(body ?? {}) });
async function until(fn, timeout = 60000, step = 400) {
  const t0 = Date.now();
  for (;;) {
    try {
      const v = await fn();
      if (v) return v;
    } catch {
      /* retry */
    }
    if (Date.now() - t0 > timeout) throw new Error("timeout waiting for condition");
    await sleep(step);
  }
}

const launchOpts = { headless: true, args: ["--use-gl=swiftshader", "--enable-unsafe-swiftshader", "--ignore-gpu-blocklist"] };
if (fs.existsSync("/opt/pw-browsers")) {
  const exe = fs
    .readdirSync("/opt/pw-browsers")
    .filter((d) => d.startsWith("chromium-"))
    .map((d) => `/opt/pw-browsers/${d}/chrome-linux/chrome`)
    .find((p) => fs.existsSync(p));
  if (exe) launchOpts.executablePath = exe;
}
const browser = await playwright.chromium.launch(launchOpts);
const page = await browser.newPage({ viewport: { width: 1680, height: 1000 } });
const pageErrors = [];
page.on("pageerror", (e) => pageErrors.push(String(e)));
const shot = (name) => page.screenshot({ path: path.join(OUT, `${name}.png`) });

try {
  // ---------------------------------------------------------------- pipeline via the API
  const project = await post("/api/projects", { name: "Research pipeline", description: "V1.2 walkthrough" });
  const PID = project.id;
  const P = (p) => `/api/projects/${PID}${p}`;
  const data = await post(P("/sources/path"), { path: path.join(ASSETS, "co2_annmean_mlo.csv"), name: "Mauna Loa CO2 (NOAA GML)" });
  const notes = await post(P("/sources/path"), { path: path.join(ASSETS, "research_notes.md"), name: "Research notes" });
  const guide = await post(P("/sources/path"), { path: path.join(ASSETS, "visual_guide.md"), name: "Visual guide" });
  const mk = async (name, adapter, params) => (await post(P("/artifacts"), { name, adapter, template: "blank", params })).artifact;
  const wb = await mk("CO2 analysis", "spreadsheet", { name: "CO2 analysis" });
  const doc = await mk("CO2 report", "document", { title: "Atmospheric CO2 at Mauna Loa", name: "CO2 report" });
  const deck = await mk("CO2 briefing", "presentation", { name: "CO2 briefing" });
  const bind = (s, a, role) => post(P("/bindings"), { source_id: s.id, role, aspects: [], target: { scope: "artifact", artifact_id: a.id } });
  await bind(data, wb, "reference");
  await bind(notes, doc, "reference");
  await bind(data, doc, "context");
  await bind(guide, deck, "guideline");
  await bind(notes, deck, "context");
  const links = [
    { source: { artifact_id: wb.id, component_id: "summary_values" }, target: { artifact_id: doc.id, component_id: "results_table" }, target_kind: "table", options: { create: { parent: "results", after: "results_text" } } },
    { source: { artifact_id: wb.id, component_id: "decade_chart" }, target: { artifact_id: doc.id, component_id: "fig_decades" }, target_kind: "figure", options: { create: { parent: "results", after: "results_table", width_cm: 14, caption: "Figure 1. Mean CO2 by decade" } } },
    { source: { artifact_id: wb.id, component_id: "summary_values" }, target: { artifact_id: doc.id, component_id: "results_text" }, options: { template: "Decade means rose from {B2:.1f} ppm in the {A2} to {B9:.1f} ppm in the {A9}." } },
    { source: { artifact_id: wb.id, component_id: "decade_means" }, target: { artifact_id: deck.id, component_id: "deck_chart" }, target_kind: "chart", options: { create: { slide: "data_slide", box: [0.08, 0.22, 0.84, 0.7], type: "column", title: "Mean CO2 by decade (ppm)" } } },
    { source: { artifact_id: doc.id, component_id: "discussion" }, target: { artifact_id: deck.id, component_id: "findings.body" }, options: { max_paragraphs: 2 } },
  ];
  const agent = (id, aid, label, instructions) => [
    { id: `art_${id}`, type: "artifact", label, config: { artifact_id: aid } },
    { id, type: "agent", label: `${label} agent`, config: { instructions, fan_out: false } },
  ];
  const nodes = [
    { id: "src", type: "sources", label: "Sources", config: {} },
    ...agent("build_wb", wb.id, "Workbook", "Build the analysis workbook from the dataset.\nValue label: CO2 (ppm)"),
    ...agent("build_doc", doc.id, "Report", "Write the report from the research notes."),
    ...agent("build_deck", deck.id, "Deck", "Title: Atmospheric CO2 at Mauna Loa\nBuild the slide deck."),
    { id: "deps", type: "dependencies", label: "Link components", config: { links } },
  ];
  const edges = [];
  for (const n of ["build_wb", "build_doc", "build_deck"]) {
    edges.push({ id: `a_${n}`, source: `art_${n}`, source_port: "artifact", target: n, target_port: "artifact" });
    edges.push({ id: `s_${n}`, source: "src", source_port: "sources", target: n, target_port: "sources" });
    edges.push({ id: `d_${n}`, source: n, source_port: "revision", target: "deps", target_port: "after" });
  }
  const wf = await post(P("/workflows"), { name: "Research analysis and presentation", nodes, edges });
  await post(P(`/workflows/${wf.id}/execute`), { mode: "incremental" });
  const ex = await until(async () => {
    const e = (await api(P(`/executions?workflow_id=${wf.id}`)))[0];
    return e && !["pending", "running"].includes(e.status) ? api(P(`/executions/${e.id}`)) : null;
  }, 300000, 1500);
  check("pipeline execution succeeded", ex.status === "succeeded", ex.error);
  const deps0 = await api(P("/dependencies"));
  check("5 dependencies, all synced", deps0.length === 5 && deps0.every((d) => d.status === "synced"), deps0.map((d) => d.status).join(","));

  // ---------------------------------------------------------------- 1. board with office views
  scenario = "1 board";
  await page.goto(BASE);
  await page.locator("select[aria-label=project]").waitFor();
  await page.selectOption("select[aria-label=project]", PID);
  await page.locator(".react-flow__node").first().waitFor({ timeout: 30000 });
  await sleep(1500);
  const item = async (name) => {
    const n = page.locator(".cnode.artifact", { hasText: name }).first();
    if (!(await n.count())) {
      await page.locator(`[data-testid="ex-artifact-${name}"]`).dblclick();
      await sleep(800);
    }
    return page.locator(".cnode.artifact", { hasText: name }).first();
  };
  const sheetNode = await item("CO2 analysis");
  const docNode = await item("CO2 report");
  const deckNode = await item("CO2 briefing");
  await page.locator(".react-flow__controls-fitview").click().catch(() => {});
  await sleep(1500);
  check("the three office artifacts are views on the canvas", (await sheetNode.count()) && (await docNode.count()) && (await deckNode.count()));
  const depEdges = await page.locator(".edge-label.t-dependency").count();
  check("component dependencies are drawn as dependency connections", depEdges >= 5, depEdges);
  await shot("v12_01_board");

  // ---------------------------------------------------------------- 2. spreadsheet in place
  scenario = "2 spreadsheet";
  await sheetNode.locator(".cnode-head").click({ position: { x: 40, y: 6 } });
  await page.keyboard.press("Shift+Digit2");
  await sleep(1200);
  await sheetNode.locator("button", { hasText: "Edit" }).first().click();
  await sheetNode.locator("[data-testid=sheet-view]").waitFor({ timeout: 20000 });
  await sheetNode.locator(".tab-chip", { hasText: "Summary" }).click();
  await sleep(500);
  const b9 = sheetNode.locator('td[data-cell="B9"]');
  await b9.click();
  const fbar = await sheetNode.locator("[data-testid=formula-bar] input").inputValue();
  check("formula bar shows the native AVERAGEIFS formula", /^=AVERAGEIFS\(/.test(fbar), fbar);
  const shown = parseFloat(await b9.innerText());
  check("the grid shows the LibreOffice-calculated value (not the formula)", shown > 415 && shown < 430, shown);
  await sheetNode.locator(".layer-chip", { hasText: "SummaryValues" }).click();
  const hiCount = await until(async () => {
    const n = await sheetNode.locator("td.hi").count();
    return n >= 27 ? n : null;
  }, 10000).catch(async () => sheetNode.locator("td.hi").count());
  check("selecting a named range highlights its cells", hiCount >= 27, hiCount);
  check("charts are listed on their sheet", (await sheetNode.locator(".chart-card").count()) >= 1);
  await shot("v12_02_sheet_in_place");
  // edit a data cell in place: the 2025 value (demonstration edit, not NOAA data)
  await sheetNode.locator(".tab-chip", { hasText: "Data" }).click();
  await sleep(400);
  const last = await sheetNode.locator("td[data-cell^=A]").evaluateAll((tds) => tds.map((t) => [t.dataset.cell, t.textContent]).filter(([, v]) => v === "2025"));
  const row = last[0][0].slice(1);
  await sheetNode.locator(`td[data-cell="B${row}"]`).click();
  const revBefore = (await api(P(`/artifacts/${wb.id}`))).head_revision_id;
  await sheetNode.locator("[data-testid=formula-bar] input").fill("500");
  await sheetNode.locator("[data-testid=formula-bar] button", { hasText: "Apply" }).click();
  await until(async () => (await api(P(`/artifacts/${wb.id}`))).head_revision_id !== revBefore, 60000);
  check("the cell edit became a new workbook revision", true);
  const stale = await until(async () => {
    const d = await api(P("/dependencies"));
    return d.filter((x) => x.status === "stale").length === 4 ? d : null;
  }, 30000);
  check("exactly the four dependents of the summary went stale; slide text from the report did not", stale.find((d) => d.target.component_id === "findings.body").status === "synced");
  await sheetNode.locator("button", { hasText: "Done" }).first().click();
  await page.locator(".react-flow__controls-fitview").click().catch(() => {});
  await page.keyboard.press("Shift+Digit1");
  await sleep(1200);
  await until(async () => (await page.locator(".edge-label.stale").count()) >= 4, 20000);
  check("stale dependency connections are marked on the canvas", (await page.locator(".edge-label.stale").count()) >= 4);
  await shot("v12_03_stale_dependencies");

  // ---------------------------------------------------------------- 3. update dependents
  scenario = "3 update dependents";
  const statesBefore = {
    doc: (await api(P(`/revisions/${(await api(P(`/artifacts/${doc.id}`))).head_revision_id}`))).component_states,
    deck: (await api(P(`/revisions/${(await api(P(`/artifacts/${deck.id}`))).head_revision_id}`))).component_states,
  };
  await sheetNode.locator(".cnode-head").click({ position: { x: 40, y: 6 } });
  await page.locator("[data-testid=dependency-panel]").first().waitFor({ timeout: 15000 });
  await shot("v12_04_dependency_panel");
  await page.locator("[data-testid=update-dependents]").first().click();
  await until(async () => (await api(P("/dependencies"))).every((d) => d.status === "synced"), 120000, 1000);
  check("Update dependents synced every dependency", true);
  const statesAfter = {
    doc: (await api(P(`/revisions/${(await api(P(`/artifacts/${doc.id}`))).head_revision_id}`))).component_states,
    deck: (await api(P(`/revisions/${(await api(P(`/artifacts/${deck.id}`))).head_revision_id}`))).component_states,
  };
  const moved = (a, b) => Object.keys(b).filter((k) => a[k] !== b[k]).sort();
  const md = moved(statesBefore.doc, statesAfter.doc).filter((k) => k !== "document");
  const mk2 = moved(statesBefore.deck, statesAfter.deck).filter((k) => !["presentation", "data_slide"].includes(k));
  check("report: only the table, figure and results sentence changed", JSON.stringify(md) === JSON.stringify(["fig_decades", "results_table", "results_text"]), md);
  check("deck: only the data chart changed", JSON.stringify(mk2) === JSON.stringify(["deck_chart"]), mk2);
  await until(async () => (await page.locator(".edge-label.stale").count()) === 0, 30000);
  check("no stale connections remain on the canvas", true);
  await sleep(1500);
  await shot("v12_05_updated");

  // ---------------------------------------------------------------- 4. document in Focus
  scenario = "4 document focus";
  await docNode.locator("button[aria-label=Focus]").click();
  const focus = page.locator("[data-testid=focus-editor]");
  await focus.locator("[data-testid=doc-view]").waitFor({ timeout: 20000 });
  const results_text = await focus.locator('[data-component="results_text"]').innerText();
  check("the updated results sentence is visible in the document view", /ppm in the 2020s/.test(results_text), results_text);
  await focus.locator('[data-component="discussion_p1"]').click();
  await focus.locator("[data-testid=doc-edit] textarea").fill("This analysis describes the published annual means and their decade averages only.");
  const docRev = (await api(P(`/artifacts/${doc.id}`))).head_revision_id;
  const docStates = (await api(P(`/revisions/${docRev}`))).component_states;
  await focus.locator("[data-testid=doc-edit] button", { hasText: "Apply" }).click();
  const docRev2 = await until(async () => {
    const h = (await api(P(`/artifacts/${doc.id}`))).head_revision_id;
    return h !== docRev ? h : null;
  }, 60000);
  const docMoved = moved(docStates, (await api(P(`/revisions/${docRev2}`))).component_states).filter((k) => k !== "document");
  check("paragraph edit changed only that paragraph and its section", JSON.stringify(docMoved) === JSON.stringify(["discussion_p1"]) || JSON.stringify(docMoved) === JSON.stringify(["discussion", "discussion_p1"]), docMoved);
  await sleep(1500);
  await shot("v12_06_document_focus");
  await focus.locator(".office-tabs button", { hasText: "Print preview" }).click();
  await focus.locator(".office-pages img").first().waitFor({ timeout: 15000 });
  check("print preview shows LibreOffice-rendered pages", (await focus.locator(".office-pages img").count()) >= 1);
  await shot("v12_07_document_print_preview");
  await page.locator("[data-testid=exit-focus]").click();
  const deps2 = await api(P("/dependencies"));
  check("editing the report section made the slide text dependency stale", deps2.find((d) => d.target.component_id === "findings.body").status === "stale");

  // ---------------------------------------------------------------- 5. slides
  scenario = "5 slides";
  await deckNode.locator("button[aria-label=Focus]").click();
  await focus.locator("[data-testid=slides-view]").waitFor({ timeout: 20000 });
  await focus.locator(".slide-thumb").nth(1).click();
  await sleep(500);
  await focus.locator('[data-component="deck_chart"]').click();
  await focus.locator("[data-testid=slide-edit] .mini-chart").waitFor({ timeout: 10000 });
  check("selecting the slide chart shows its native data", true);
  await shot("v12_08_slide_chart");
  await focus.locator(".slide-thumb").nth(0).click();
  await sleep(400);
  await focus.locator('[data-component="title_slide.body"]').click();
  await focus.locator("[data-testid=slide-edit] textarea").fill("Research analysis · NOAA GML data");
  const deckRev = (await api(P(`/artifacts/${deck.id}`))).head_revision_id;
  const deckStates = (await api(P(`/revisions/${deckRev}`))).component_states;
  await focus.locator("[data-testid=slide-edit] button", { hasText: "Apply" }).click();
  const deckRev2 = await until(async () => {
    const h = (await api(P(`/artifacts/${deck.id}`))).head_revision_id;
    return h !== deckRev ? h : null;
  }, 60000);
  const deckMoved = moved(deckStates, (await api(P(`/revisions/${deckRev2}`))).component_states).filter((k) => !["presentation", "title_slide"].includes(k));
  check("slide text edit changed only that text frame", JSON.stringify(deckMoved) === JSON.stringify(["title_slide.body"]), deckMoved);
  await sleep(2000);
  await shot("v12_09_slides_focus");
  await page.locator("[data-testid=exit-focus]").click();
  const files = await api(P(`/revisions/${deckRev2}/files`));
  check("native .pptx remains the artifact entry", JSON.stringify(files).includes("presentation.pptx"));
  check("no uncaught page errors", pageErrors.length === 0, pageErrors.join(" | "));
} catch (e) {
  check("walkthrough completed without exceptions", false, e?.stack || e);
  await shot("zz_failure").catch(() => {});
} finally {
  await browser.close();
  fs.writeFileSync(path.join(OUT, "office_e2e_results.json"), JSON.stringify(results, null, 1));
  const failed = results.filter((r) => !r.ok);
  console.log(`\n${failed.length ? "FAIL" : "PASS"}: ${results.length - failed.length}/${results.length}`);
  process.exit(failed.length ? 1 : 0);
}
