// V1 board performance measurement: a board with >=150 items and >=200 connections.
//   STUDIO_URL=http://127.0.0.1:8765 OUT=../docs/evidence/v1 node e2e/perf.e2e.mjs
// Records real numbers only. Headless Chromium with SwiftShader (software GL) and no GPU is a
// pessimistic environment: absolute frame times are not representative of a desktop GPU, but
// they are comparable between runs and show how the board scales.
import { createRequire } from "node:module";
import fs from "node:fs";
import path from "node:path";

const require = createRequire(import.meta.url);
const playwright = require("playwright");
const BASE = process.env.STUDIO_URL || "http://127.0.0.1:8765";
const OUT = path.resolve(process.env.OUT || "e2e-v1");
fs.mkdirSync(OUT, { recursive: true });
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const api = async (p, init) => {
  const r = await fetch(BASE + p, init);
  if (!r.ok) throw new Error(`${p}: ${r.status} ${await r.text()}`);
  return r.json();
};
const post = (p, body) => api(p, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(body ?? {}) });
const put = (p, body) => api(p, { method: "PUT", headers: { "content-type": "application/json" }, body: JSON.stringify(body ?? {}) });
const uid = (p, i) => `${p}_${String(i).padStart(4, "0")}`;
const stats = (xs) => {
  const s = [...xs].sort((a, b) => a - b);
  const q = (p) => s[Math.min(s.length - 1, Math.floor(p * s.length))];
  return { n: s.length, mean: +(s.reduce((a, b) => a + b, 0) / (s.length || 1)).toFixed(2), p50: +q(0.5).toFixed(2), p95: +q(0.95).toFixed(2), max: +(s[s.length - 1] ?? 0).toFixed(2) };
};

// ------------------------------------------------------------------ build the project + board
const t0 = Date.now();
const project = await post("/api/projects", { name: "Perf board", description: "≥150 items / ≥200 connections" });
const PID = project.id;
const sources = [];
for (let i = 0; i < 40; i++) sources.push(await post(`/api/projects/${PID}/sources/text`, { name: `note-source-${i}.txt`, text: `Reference ${i}: keep proportions, warm palette, ${"detail ".repeat(i % 7)}` }));
const artifacts = [];
for (let i = 0; i < 16; i++)
  artifacts.push(await post(`/api/projects/${PID}/artifacts`, { name: `Module ${i}`, adapter: "code", template: "files", params: { files: { [`m${i}.py`]: `def f${i}(x):\n    return x * ${i}\n` } } }));
for (let i = 0; i < 8; i++) artifacts.push(await post(`/api/projects/${PID}/artifacts`, { name: `Layer doc ${i}`, adapter: "layered2d", template: "layers", params: { width: 320, height: 200 } }));
for (let i = 0; i < 30; i++) {
  const a = artifacts[i % artifacts.length];
  await post(`/api/projects/${PID}/bindings`, { source_id: sources[i].id, role: "style", aspects: ["style"], target: { scope: "artifact", artifact_id: a.id } });
}
const board = await post(`/api/projects/${PID}/boards`, { name: "Perf board", layout: "empty" });
const items = [];
const cols = 14;
const at = (k) => ({ x: (k % cols) * 460, y: Math.floor(k / cols) * 400 });
let k = 0;
const views = [];
for (const a of artifacts) views.push({ id: uid("view", views.length), item_type: "artifact_view", resource_ref: { kind: "artifact", id: a.id }, position: at(k++), size: { width: 420, height: 340 } });
for (let i = 0; i < 16; i++) views.push({ ...views[i], id: uid("view", views.length), position: at(k++) }); // second views of the same artifacts
items.push(...views);
const srcItems = sources.map((s, i) => ({ id: uid("src", i), item_type: "source", resource_ref: { kind: "source", id: s.id }, position: at(k++), size: { width: 240, height: 210 } }));
items.push(...srcItems);
const notes = Array.from({ length: 60 }, (_, i) => ({ id: uid("note", i), item_type: "note", resource_ref: { kind: "note" }, position: at(k++), size: { width: 240, height: 140 }, presentation_state: { text: `Note ${i}: review proportions and palette.` } }));
items.push(...notes);
const frames = Array.from({ length: 12 }, (_, i) => ({ id: uid("frame", i), item_type: "frame", resource_ref: { kind: "frame" }, position: { x: (i % 4) * 1600 - 40, y: Math.floor(i / 4) * 1300 + 4000 }, size: { width: 1500, height: 1200 }, z_index: -10, presentation_state: { title: `Frame ${i}`, color: "#5a7a55" } }));
items.push(...frames);
const connections = [];
for (let i = 0; i < 60; i++) {
  const tgt = i % 2 ? views[i % views.length] : srcItems[i % srcItems.length];
  connections.push({ id: uid("ann", i), connection_type: "annotation", source_item_id: notes[i].id, target_item_id: tgt.id, source_anchor: { handle: "note-out" }, target_anchor: { handle: "ann-in" } });
}
for (let i = 0; i < 140; i++) {
  const a = views[i % views.length];
  const b = views[(i * 7 + 3) % views.length];
  if (a.id === b.id) continue;
  connections.push({ id: uid("dep", i), connection_type: "dependency", source_item_id: a.id, target_item_id: b.id, source_anchor: { handle: "dep-out" }, target_anchor: { handle: "dep-in" } });
}
while (connections.length < 200) {
  const i = connections.length;
  connections.push({ id: uid("dep", 1000 + i), connection_type: "dependency", source_item_id: views[i % 20].id, target_item_id: views[(i % 20) + 20].id, source_anchor: { handle: "dep-out" }, target_anchor: { handle: "dep-in" } });
}
const saved = await put(`/api/projects/${PID}/boards/${board.id}`, { board: { ...board, items, connections, missing_items: undefined }, expected_revision: board.revision });
const derivedCount = saved.connections.filter((c) => c.derived).length;
const storedCount = saved.connections.filter((c) => !c.derived).length;
// make it the only board so the project opens on it
for (const b of await api(`/api/projects/${PID}/boards`)) if (b.id !== board.id) await api(`/api/projects/${PID}/boards/${b.id}`, { method: "DELETE" });
const setupSec = (Date.now() - t0) / 1000;
console.log(`board: ${saved.items.length} items, ${storedCount} stored + ${derivedCount} derived connections (setup ${setupSec.toFixed(1)}s)`);

// ------------------------------------------------------------------ measure in the browser
const launchOpts = { headless: true, args: ["--use-gl=swiftshader", "--enable-unsafe-swiftshader", "--ignore-gpu-blocklist", "--enable-precise-memory-info"] };
if (fs.existsSync("/opt/pw-browsers")) {
  const exe = fs.readdirSync("/opt/pw-browsers").filter((d) => d.startsWith("chromium-")).map((d) => `/opt/pw-browsers/${d}/chrome-linux/chrome`).find((p) => fs.existsSync(p));
  if (exe) launchOpts.executablePath = exe;
}
const browser = await playwright.chromium.launch(launchOpts);
const page = await browser.newPage({ viewport: { width: 1680, height: 1000 } });
const pageErrors = [];
page.on("pageerror", (e) => pageErrors.push(String(e)));
await page.goto(BASE);
await page.locator("select[aria-label=project]").waitFor();
const tOpen = Date.now();
await page.selectOption("select[aria-label=project]", PID);
await page.locator(".react-flow__node").first().waitFor({ timeout: 60000 });
await page.waitForFunction(() => document.querySelector(".save-state")?.textContent === "saved", null, { timeout: 60000 });
await sleep(300);
const openMs = Date.now() - tOpen;

const dom = () =>
  page.evaluate(() => ({
    nodes: document.querySelectorAll(".react-flow__node").length,
    edges: document.querySelectorAll(".react-flow__edge").length,
    elements: document.getElementsByTagName("*").length,
    heapMB: performance.memory ? +(performance.memory.usedJSHeapSize / 1048576).toFixed(1) : null,
    zoom: document.querySelector("[data-testid=zoom-label]")?.textContent ?? "",
  }));
const startFrames = () =>
  page.evaluate(() => {
    const w = window;
    w.__frames = [];
    let last = performance.now();
    w.__frameStop = false;
    const tick = (t) => {
      w.__frames.push(t - last);
      last = t;
      if (!w.__frameStop) requestAnimationFrame(tick);
    };
    requestAnimationFrame(tick);
  });
const stopFrames = () =>
  page.evaluate(() => {
    window.__frameStop = true;
    return window.__frames.slice(1);
  });

const results = { environment: { browser: "Chromium (Playwright) headless", gl: "SwiftShader software rendering, no GPU", viewport: "1680x1000" }, board: { items: saved.items.length, stored_connections: storedCount, derived_connections: derivedCount, total_connections: saved.connections.length, setup_seconds: +setupSec.toFixed(1) }, measurements: {} };
results.measurements.open_project_to_board_ms = openMs;

await page.getByRole("button", { name: "Fit" }).click();
await sleep(1200);
results.measurements.fit_all = await dom();
await page.screenshot({ path: path.join(OUT, "v1_perf_fit_all.png") });

// pan: drag the empty pane in steps
const pane = await page.locator(".react-flow__pane").boundingBox();
await startFrames();
const tPan = Date.now();
for (let r = 0; r < 4; r++) {
  await page.mouse.move(pane.x + 200, pane.y + pane.height - 60);
  await page.mouse.down();
  await page.mouse.move(pane.x + 700, pane.y + pane.height - 160, { steps: 30 });
  await page.mouse.up();
}
const panFrames = await stopFrames();
results.measurements.pan_far = { duration_ms: Date.now() - tPan, frame_interval_ms: stats(panFrames), frames_over_50ms: panFrames.filter((f) => f > 50).length };

// zoom in/out with the wheel across LOD thresholds
await page.mouse.move(pane.x + pane.width / 2, pane.y + pane.height / 2);
await startFrames();
const tZoom = Date.now();
for (let i = 0; i < 25; i++) {
  await page.mouse.wheel(0, -120);
  await sleep(16);
}
const zoomedIn = await dom();
for (let i = 0; i < 25; i++) {
  await page.mouse.wheel(0, 120);
  await sleep(16);
}
const zoomFrames = await stopFrames();
results.measurements.zoom_cycle = { duration_ms: Date.now() - tZoom, frame_interval_ms: stats(zoomFrames), frames_over_50ms: zoomFrames.filter((f) => f > 50).length, dom_when_zoomed_in: zoomedIn };

// close-up: zoom to one item and pan around at close LOD (lazy mounting keeps the DOM small)
await page.locator(".react-flow__node-artifact_view").first().locator(".cnode-head").click({ force: true });
await page.getByRole("button", { name: "Selection" }).click();
await sleep(1200);
results.measurements.close_view = await dom();
await startFrames();
const tPanClose = Date.now();
for (let r = 0; r < 4; r++) {
  await page.mouse.move(pane.x + pane.width - 100, pane.y + pane.height - 40);
  await page.mouse.down();
  await page.mouse.move(pane.x + 150, pane.y + pane.height - 60, { steps: 30 });
  await page.mouse.up();
}
const closeFrames = await stopFrames();
results.measurements.pan_close = { duration_ms: Date.now() - tPanClose, frame_interval_ms: stats(closeFrames), frames_over_50ms: closeFrames.filter((f) => f > 50).length, dom_after: await dom() };
await page.screenshot({ path: path.join(OUT, "v1_perf_close.png") });

// drag one node and wait for the layout autosave round trip
await page.getByRole("button", { name: "Fit" }).click();
await sleep(1000);
const head = page.locator(".react-flow__node-note .cnode-head").first();
const hb = await head.boundingBox();
const tDrag = Date.now();
await page.mouse.move(hb.x + 5, hb.y + 2);
await page.mouse.down();
await page.mouse.move(hb.x + 80, hb.y + 60, { steps: 10 });
await page.mouse.up();
await page.waitForFunction(() => document.querySelector(".save-state")?.textContent !== "saved", null, { timeout: 5000 }).catch(() => {});
await page.waitForFunction(() => document.querySelector(".save-state")?.textContent === "saved", null, { timeout: 30000 });
results.measurements.drag_to_saved_ms = Date.now() - tDrag; // includes the 500 ms autosave debounce
const boardAfter = await api(`/api/projects/${PID}/boards/${board.id}`);
results.measurements.save_payload_kb = +(JSON.stringify({ ...boardAfter, connections: boardAfter.connections.filter((c) => !c.derived) }).length / 1024).toFixed(1);
results.measurements.final = await dom();
results.page_errors = pageErrors;
await browser.close();
fs.writeFileSync(path.join(OUT, "perf_results.json"), JSON.stringify(results, null, 1));
console.log(JSON.stringify(results, null, 1));
const ok = saved.items.length >= 150 && saved.connections.length >= 200 && pageErrors.length === 0;
console.log(ok ? "PERF RUN COMPLETE" : "PERF RUN INCOMPLETE");
process.exit(ok ? 0 : 1);
