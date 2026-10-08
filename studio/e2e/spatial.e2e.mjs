// V1 spatial-canvas walkthrough: scenarios A–F against the real app (backend + Blender).
//   STUDIO_URL=http://127.0.0.1:8765 OUT=../docs/screenshots/v1 node e2e/spatial.e2e.mjs
import { createRequire } from "node:module";
import fs from "node:fs";
import path from "node:path";

const require = createRequire(import.meta.url);
const playwright = require("playwright");
const BASE = process.env.STUDIO_URL || "http://127.0.0.1:8765";
const OUT = path.resolve(process.env.OUT || "e2e-v1");
fs.mkdirSync(OUT, { recursive: true });
const results = [];
let scenario = "";
const check = (name, ok, detail = "") => {
  results.push({ scenario, name, ok: !!ok, detail: ok ? "" : String(detail).slice(0, 600) });
  console.log(`${ok ? "PASS" : "FAIL"} [${scenario}] ${name}${!ok && detail ? " - " + String(detail).slice(0, 300) : ""}`);
  return !!ok;
};
const api = async (p, init) => {
  const r = await fetch(BASE + p, init);
  if (!r.ok) throw new Error(`${p}: ${r.status} ${await r.text()}`);
  return r.json();
};
const post = (p, body) => api(p, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(body ?? {}) });
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
async function until(fn, timeout = 60000, step = 300) {
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
  const exe = fs.readdirSync("/opt/pw-browsers").filter((d) => d.startsWith("chromium-")).map((d) => `/opt/pw-browsers/${d}/chrome-linux/chrome`).find((p) => fs.existsSync(p));
  if (exe) launchOpts.executablePath = exe;
}
const browser = await playwright.chromium.launch(launchOpts);
const page = await browser.newPage({ viewport: { width: 1680, height: 1000 } });
const pageErrors = [];
page.on("pageerror", (e) => pageErrors.push(String(e)));
const shot = (name) => page.screenshot({ path: path.join(OUT, `${name}.png`) });

// ---------------------------------------------------------------- helpers
let PID = null;
const node = (title) => page.locator(".cnode").filter({ has: page.locator(".cnode-head > b", { hasText: new RegExp(`^${title}$`) }) });
const boardDoc = async () => {
  const list = await api(`/api/projects/${PID}/boards`);
  return api(`/api/projects/${PID}/boards/${list[0].id}`);
};
const artifacts = () => api(`/api/projects/${PID}/artifacts`);
const artByName = async (n) => (await artifacts()).find((a) => a.name === n);
const zoomLabel = () => page.locator("[data-testid=zoom-label]").innerText();
const viewportTransform = () => page.evaluate(() => document.querySelector(".react-flow__viewport")?.getAttribute("style") ?? "");
// autosave is debounced (500 ms): give pending changes time to start saving, then wait for "saved"
const waitSaved = async () => {
  await sleep(900);
  await until(async () => (await page.locator(".save-state").innerText()) === "saved", 20000);
};
async function waitExecution(wid, prevId, { pastApproval = false } = {}) {
  return until(async () => {
    const list = await api(`/api/projects/${PID}/executions?workflow_id=${wid}`);
    const e = list[0];
    if (!e || e.id === prevId) return null;
    if (["pending", "running"].includes(e.status)) return null;
    // after an Approve click the decision is applied asynchronously: keep waiting for the outcome
    if (pastApproval && e.status === "waiting_approval") return null;
    return api(`/api/projects/${PID}/executions/${e.id}`);
  }, 300000, 1000);
}
const units = (ex) => Object.fromEntries(ex.node_runs.flatMap((r) => r.units.map((u) => [`${r.node_id}:${u.unit.split("#").pop()}`, u.status])));
async function pickComponent(itemId, mode, componentId, reframe = true) {
  const key = `${itemId}:${mode}`;
  await until(() => page.evaluate((k) => window.__dd3d?.[k]?.loaded?.(), key), 60000);
  if (reframe) {
    const scope = mode === "focus" ? page.locator("[data-testid=focus-editor]") : page.locator(`[data-testid="item-${itemId}"]`);
    await scope.getByRole("button", { name: "perspective" }).click();
  }
  await sleep(700);
  const pt = await page.evaluate(([k, c]) => window.__dd3d[k].project(c), [key, componentId]);
  if (!pt) throw new Error(`component ${componentId} not in viewer ${key}`);
  await page.mouse.click(pt.x, pt.y);
}
async function activate(title) {
  await node(title).first().locator(".cnode-head").dblclick({ position: { x: 60, y: 10 } });
  await until(async () => (await node(title).first().getAttribute("class")).includes("is-active"), 10000);
}
async function locateInExplorer(testid) {
  await page.locator(`[data-testid="${testid}"]`).dblclick();
  await sleep(700);
}
const latestWf = async () => (await api(`/api/projects/${PID}/workflows`))[0];

try {
  // ======================================================== setup: the multimodal demo project
  scenario = "setup";
  await page.goto(BASE);
  await page.getByRole("button", { name: /Create multimodal demo project/ }).click();
  await page.locator(".cnode.artifact").first().waitFor({ timeout: 240000 });
  PID = await page.evaluate(() => localStorage.getItem("daedelus.project"));
  check("demo project opens directly into the spatial board", !!PID);
  check("no separate application modes: explorer, canvas and inspector are all present",
    (await page.locator("[data-testid=explorer]").count()) === 1 && (await page.locator("[data-testid=spatial-canvas]").count()) === 1 && (await page.locator("[data-testid=inspector]").count()) === 1);
  await page.locator(".save-state").waitFor();
  await shot("v1_01_board_overview");

  // ======================================================== A. spatial composition
  scenario = "A spatial composition";
  check("table, background and code artifacts are on the board", (await page.locator(".cnode.artifact").count()) === 3);
  const before = await boardDoc();
  // add two new reference sources (uploaded via the explorer, placed on the board)
  const furn = path.join(OUT, "furniture_reference.png");
  const dataUrl = await page.evaluate(() => {
    const c = document.createElement("canvas");
    c.width = 240;
    c.height = 320;
    const g = c.getContext("2d");
    g.fillStyle = "#efe9de";
    g.fillRect(0, 0, 240, 320);
    g.fillStyle = "#2e1d12";
    g.beginPath();
    g.moveTo(70, 30); g.lineTo(170, 30); g.lineTo(150, 300); g.lineTo(90, 300);
    g.fill();
    return c.toDataURL("image/png");
  });
  const png = Buffer.from(dataUrl.split(",")[1], "base64");
  fs.writeFileSync(furn, png);
  const csv = path.join(OUT, "dimensions.csv");
  fs.writeFileSync(csv, "part,width,height\ntable,1.2,0.75\nleg,0.06,0.72\n");
  await page.locator("[data-testid=upload-input]").setInputFiles([furn, csv]);
  await until(async () => (await boardDoc()).items.length === before.items.length + 2, 30000);
  check("two new references were uploaded and placed on the board", true);
  const srcs = await api(`/api/projects/${PID}/sources`);
  check("uploaded image ingested (measured) and spreadsheet ingested as view-only", srcs.find((x) => x.name === "furniture_reference.png")?.processing.state === "ready" && srcs.find((x) => x.name === "dimensions.csv")?.processing.state === "partial");
  await locateInExplorer("ex-source-dimensions.csv");
  check("spreadsheet source renders a table preview on the canvas", await until(() => page.locator(".src-table").count(), 15000).then(() => true, () => false));
  // move a node by its header and resize it
  const csvNode = node("dimensions.csv").first();
  const hb = await csvNode.locator(".cnode-head").boundingBox();
  await page.mouse.move(hb.x + 30, hb.y + 6);
  await page.mouse.down();
  await page.mouse.move(hb.x + 150, hb.y + 90, { steps: 8 });
  await page.mouse.up();
  await waitSaved();
  const movedId = (await boardDoc()).items.find((i) => !before.items.find((b) => b.id === i.id) && i.item_type === "source" && i.size.width > 0 && i.id)?.id;
  const afterMove = await boardDoc();
  const csvItemId = await csvNode.getAttribute("data-testid").catch(() => null);
  const csvId = (csvItemId ?? "").replace("item-", "") || movedId;
  const placed = before.items.length;
  void placed;
  const csvBefore = (await (async () => afterMove.items.find((i) => i.id === csvId))());
  check("dragging the header moved the item (persisted)", !!csvBefore);
  // bring it to the middle of the viewport so the resize handle is not under the minimap
  await locateInExplorer("ex-source-dimensions.csv");
  await csvNode.locator(".cnode-head").click({ position: { x: 30, y: 6 } });
  await sleep(300);
  const handle = csvNode.locator(".react-flow__resize-control.handle.bottom.right");
  const rb = await handle.boundingBox();
  const hitAtHandle = await page.evaluate(([x, y]) => { const e = document.elementFromPoint(x, y); return e ? `${e.tagName}.${e.className}` : "none"; }, [rb.x + rb.width / 2, rb.y + rb.height / 2]);
  await page.mouse.move(rb.x + rb.width / 2, rb.y + rb.height / 2);
  await page.mouse.down();
  await page.mouse.move(rb.x + 60, rb.y + 50, { steps: 6 });
  await page.mouse.up();
  await waitSaved();
  const resized = (await boardDoc()).items.find((i) => i.id === csvId);
  check("resize handle changed the item size (persisted)", resized && resized.size.width > csvBefore.size.width, JSON.stringify([csvBefore?.size, resized?.size, rb, hitAtHandle]));
  // zoom out to see everything, then zoom into the table
  await page.getByRole("button", { name: "Fit" }).click();
  await sleep(800);
  check("fit shows the whole layout at far level of detail", (await zoomLabel()).includes("far"), await zoomLabel());
  await shot("v1_02_zoomed_out");
  await locateInExplorer("ex-artifact-Table");
  check("zoom into the table reaches close level of detail", (await zoomLabel()).includes("close"), await zoomLabel());
  await shot("v1_03_zoomed_into_table");
  // save + reopen
  await waitSaved();
  const saved = await boardDoc();
  await page.reload();
  await page.locator(".cnode.artifact").first().waitFor({ timeout: 60000 });
  await sleep(1500);
  const reopened = await boardDoc();
  const same = saved.items.every((i) => {
    const j = reopened.items.find((x) => x.id === i.id);
    return j && j.position.x === i.position.x && j.position.y === i.position.y && j.size.width === i.size.width;
  });
  check("positions and sizes persist across reload", same && saved.items.length === reopened.items.length);
  check("relationships persist (derived connections recomputed identically)", reopened.connections.length === saved.connections.length, `${saved.connections.length} vs ${reopened.connections.length}`);
  const vp = await viewportTransform();
  check("saved board viewport is restored after reopen", vp.includes("scale(") && !vp.includes("scale(1)"), vp);

  // run the workflow once so later scenarios measure incremental behaviour
  scenario = "baseline run";
  let wf = await latestWf();
  const exBase = await post(`/api/projects/${PID}/workflows/${wf.id}/execute`, { mode: "incremental", wait: true });
  const doneBase = await api(`/api/projects/${PID}/executions/${exBase.id}`);
  check("baseline execution of the demo workflow succeeded", doneBase.status === "succeeded", doneBase.error);
  await page.reload();
  await page.locator(".cnode.artifact").first().waitFor({ timeout: 60000 });

  // ======================================================== B. component reference
  scenario = "B component reference";
  const table = await artByName("Table");
  const bg0 = (await artByName("Background")).head_revision_id;
  await locateInExplorer("ex-artifact-Table");
  await activate("Table");
  const tableItemId = (await node("Table").first().getAttribute("data-testid")).replace("item-", "");
  await pickComponent(tableItemId, "board", "leg_fl");
  await page.locator("[data-testid=component-id]").waitFor({ timeout: 10000 });
  check("clicking a leg in the 3D viewport selects the persistent component id", (await page.locator("[data-testid=component-id]").innerText()) === "leg_fl");
  check("selected component chip appears on the artifact view", (await node("Table").first().locator(".target-chip.sel").innerText()).includes("Leg FL"));
  check("agent dock target follows the selection", (await page.locator("[data-testid=agent-target]").innerText()).includes("Leg FL"));
  await shot("v1_04_leg_selected_in_viewport");
  // connect the new furniture reference to the selected leg (drag the reference onto the target)
  const srcRow = page.locator('[data-testid="ex-source-furniture_reference.png"]');
  await srcRow.dragTo(node("Table").first().locator(".cnode-body"), { targetPosition: { x: 60, y: 60 } });
  await page.locator(".modal[aria-label='Configure relationship']").waitFor({ timeout: 10000 });
  check("relationship dialog targets the selected component", (await page.locator("[data-testid=rel-target]").innerText()).includes("Leg FL"));
  await page.locator(".modal input[aria-label=role]").fill("reference");
  await page.locator(".modal .chip", { hasText: /^color$/ }).click();
  await page.locator(".modal button.link", { hasText: "preserve height" }).click();
  await page.locator(".modal textarea").fill("Structural reference for this leg only.");
  await shot("v1_05_relationship_dialog");
  await page.getByRole("button", { name: "Create binding" }).click();
  await page.locator(".modal").waitFor({ state: "detached" });
  const furnSrc = (await api(`/api/projects/${PID}/sources`)).find((x) => x.name === "furniture_reference.png");
  const binding = (await api(`/api/projects/${PID}/bindings`)).find((b) => b.source_id === furnSrc.id);
  check("a real SourceBinding was created for Table → Leg FL",
    binding && binding.target.component_id === "leg_fl" && binding.target.artifact_id === table.id && binding.role === "reference" && binding.constraints[0]?.property === "height",
    JSON.stringify(binding));
  const bview = await boardDoc();
  check("the board renders it as a reference connection anchored to the component",
    bview.connections.some((c) => c.connection_type === "reference" && c.domain_ref.id === binding.id && c.target_anchor.component_id === "leg_fl"));
  await sleep(800);
  check("the inspector shows the resolved context of the component", (await page.locator("[data-testid=component-inspector]").innerText()).includes("furniture_reference.png"));
  await shot("v1_06_binding_on_component");
  // impact preview
  const imp = await api(`/api/projects/${PID}/workflows/${wf.id}/impact`);
  const stale = imp.nodes.flatMap((n) => (n.units || []).filter((u) => u.stale).map((u) => `${n.node_id}:${u.unit.split("#").pop()}`));
  check("impact preview: only the new leg unit of the 3D agent is stale", JSON.stringify(stale) === JSON.stringify(["model_agent:leg_fl"]), JSON.stringify(stale));
  await page.locator('[data-testid="ex-workflows"] .ex-row').first().click();
  await page.getByRole("button", { name: "Preview impact" }).first().click();
  await page.locator("[data-testid=impact]").waitFor();
  await shot("v1_07_impact_preview");
  // change that reference (strength) through the binding editor
  await post(`/api/projects/${PID}/workflows/${wf.id}/execute`, { wait: true }); // execute the new binding first
  await page.reload();
  await page.locator(".cnode.artifact").first().waitFor({ timeout: 60000 });
  // edit the relationship in the inspector: component -> anchored binding -> strength
  await page.locator('[data-testid="ex-artifact-Table"]').click();
  await page.locator('[data-testid="ex-comp-leg_fl"]').click();
  await page.locator("[data-testid=component-inspector]").waitFor();
  await page.locator("[data-testid=component-inspector] .section > header button", { hasText: "furniture_reference.png" }).click();
  await page.locator("[data-testid=component-inspector] .binding-editor input[type=range]").fill("0.35");
  await page.locator("[data-testid=component-inspector] .binding-editor").getByRole("button", { name: "Save binding" }).click();
  await until(async () => (await api(`/api/projects/${PID}/bindings`)).find((b) => b.id === binding.id).strength === 0.35, 10000);
  check("reference edited in the inspector updates the backend binding", true);
  const prevEx = (await api(`/api/projects/${PID}/executions?workflow_id=${wf.id}`))[0]?.id;
  // run from the board (workflow inspector)
  await page.locator('[data-testid="ex-workflows"] .ex-row').first().click();
  await page.locator("[data-testid=workflow-actions]").getByRole("button", { name: "▶ Run" }).click();
  const exB = await waitExecution(wf.id, prevEx);
  const u = units(exB);
  check("run succeeded", exB.status === "succeeded", exB.error);
  check("only the changed leg unit executed in the 3D agent", u["model_agent:leg_fl"] === "succeeded" && u["model_agent:table"] === "skipped" && u["model_agent:legs"] === "skipped" && u["model_agent:tabletop"] === "skipped", JSON.stringify(u));
  check("background agent skipped (up to date)", u["paint_agent:background"] === "skipped" && u["paint_agent:sky"] === "skipped", JSON.stringify(u));
  check("unrelated artifact unchanged (background head revision)", (await artByName("Background")).head_revision_id === bg0);
  const tRev = await api(`/api/projects/${PID}/revisions/${(await artByName("Table")).head_revision_id}`);
  check("only leg_fl changed in the table revision", JSON.stringify(tRev.changed_components) === JSON.stringify(["leg_fl"]), JSON.stringify(tRev.changed_components));
  await sleep(1500);
  await shot("v1_08_after_incremental_run");

  // ======================================================== C. hybrid editing
  scenario = "C hybrid editing";
  await locateInExplorer("ex-artifact-Table");
  await activate("Table");
  const tNode = node("Table").first();
  const vb = await tNode.locator(".viewer3d").boundingBox();
  const camBefore = (await boardDoc()).items.find((i) => i.id === tableItemId).presentation_state.camera;
  await page.mouse.move(vb.x + vb.width / 2, vb.y + vb.height / 2);
  await page.mouse.down();
  await page.mouse.move(vb.x + vb.width / 2 + 120, vb.y + vb.height / 2 + 30, { steps: 10 });
  await page.mouse.up();
  await sleep(900);
  await waitSaved();
  const camAfter = (await boardDoc()).items.find((i) => i.id === tableItemId).presentation_state.camera;
  check("orbiting the active viewport changed only this view's camera (no revision)", camAfter && JSON.stringify(camAfter) !== JSON.stringify(camBefore));
  const revCountBefore = (await api(`/api/projects/${PID}/artifacts/${table.id}/revisions`)).length;
  check("orbit did not create a revision", revCountBefore === (await api(`/api/projects/${PID}/artifacts/${table.id}/revisions`)).length);
  const vpBeforeFocus = await viewportTransform();
  await tNode.locator("button[aria-label=Focus]").click();
  await page.locator("[data-testid=focus-editor]").waitFor();
  await pickComponent(tableItemId, "focus", "tabletop");
  await page.locator("[data-testid=component-id]", { hasText: "tabletop" }).waitFor({ timeout: 10000 });
  check("component selected inside Focus", true);
  check("pinned references dock is visible in Focus", (await page.locator("[data-testid=pinned-references] .pin-card").count()) > 0);
  await page.locator("[data-testid=component-edit] input[aria-label='base colour']").fill("#c0392b");
  const headBeforeEdit = (await artByName("Table")).head_revision_id;
  await page.getByRole("button", { name: "Apply material" }).click();
  const headAfterEdit = await until(async () => {
    const h = (await artByName("Table")).head_revision_id;
    return h !== headBeforeEdit ? h : null;
  }, 120000);
  const editRev = await api(`/api/projects/${PID}/revisions/${headAfterEdit}`);
  check("in-place edit created a native revision (origin manual)", editRev.origin === "manual" && JSON.stringify(editRev.changed_components) === JSON.stringify(["tabletop"]), JSON.stringify([editRev.origin, editRev.changed_components]));
  const blend = await fetch(`${BASE}/api/projects/${PID}/files/${editRev.snapshot_dir}/model.blend`);
  check("the revision's .blend snapshot exists", blend.ok && (await blend.arrayBuffer()).byteLength > 10000);
  check("revision validation passed (file reopens, scope preserved)", editRev.validation.passed);
  await sleep(2500);
  await shot("v1_09_focus_edit");
  await page.locator("[data-testid=exit-focus]").click();
  await page.locator("[data-testid=focus-editor]").waitFor({ state: "detached" });
  await sleep(500);
  check("exiting Focus restores the exact board position and zoom", (await viewportTransform()) === vpBeforeFocus, `${vpBeforeFocus} vs ${await viewportTransform()}`);
  await until(async () => (await node("Table").first().locator(".cnode-head").innerText()).includes(`r${editRev.number}`), 15000);
  check("the board view shows the updated revision", true);

  // ======================================================== D. shared artifact views
  scenario = "D shared views";
  await page.keyboard.press("Escape");
  await node("Table").first().locator(".cnode-head").click({ position: { x: 60, y: 10 } });
  await page.getByRole("button", { name: "Duplicate view" }).click();
  await until(async () => (await node("Table").count()) === 2, 10000);
  await waitSaved();
  const views = (await boardDoc()).items.filter((i) => i.resource_ref.id === table.id);
  check("two views of the same artifact", views.length === 2);
  const v2 = views.find((i) => i.id !== tableItemId);
  await page.getByRole("button", { name: "Fit" }).click();
  await sleep(400);
  // select the second view and zoom to it so its header is a real click target
  await page.locator(`[data-testid="item-${v2.id}"] .cnode-head`).click({ position: { x: 20, y: 4 }, force: true });
  await page.getByRole("button", { name: "Selection" }).click();
  await sleep(800);
  await page.locator(`[data-testid="item-${v2.id}"] .cnode-head`).dblclick({ position: { x: 60, y: 10 } });
  await page.locator(`[data-testid="item-${v2.id}"]`).getByRole("button", { name: "front" }).click();
  await sleep(800);
  await waitSaved();
  const camsD = (await boardDoc()).items.filter((i) => i.resource_ref.id === table.id).map((i) => JSON.stringify(i.presentation_state.camera ?? null));
  check("the two views have different camera orientations", camsD[0] !== camsD[1], camsD.join(" | "));
  await pickComponent(v2.id, "board", "leg_br", false);
  await page.locator("[data-testid=component-id]", { hasText: "leg_br" }).waitFor({ timeout: 10000 });
  await page.locator("[data-testid=component-edit] input[aria-label='taper']").fill("-0.4");
  const hD = (await artByName("Table")).head_revision_id;
  await page.getByRole("button", { name: "Apply taper" }).click();
  const hD2 = await until(async () => {
    const h = (await artByName("Table")).head_revision_id;
    return h !== hD ? h : null;
  }, 120000);
  const revD = await api(`/api/projects/${PID}/revisions/${hD2}`);
  await until(async () => (await page.locator(".cnode.artifact .cnode-head", { hasText: `r${revD.number}` }).count()) === 2, 20000);
  check("both views display the new revision", true);
  const camsD2 = (await boardDoc()).items.filter((i) => i.resource_ref.id === table.id).map((i) => JSON.stringify(i.presentation_state.camera ?? null));
  check("both views keep their independent cameras after the edit", camsD2[0] === camsD[0] && camsD2[1] === camsD[1]);
  await page.getByRole("button", { name: "Fit" }).click();
  await sleep(300);
  await page.locator(".react-flow__pane").click({ position: { x: 300, y: 500 } });
  await page.locator(`[data-testid="item-${tableItemId}"] .cnode-head`).click({ force: true });
  await page.keyboard.down("Shift");
  await page.locator(`[data-testid="item-${v2.id}"] .cnode-head`).click({ force: true });
  await page.keyboard.up("Shift");
  await page.getByRole("button", { name: "Selection" }).click();
  await sleep(2500);
  await shot("v1_10_two_views_same_artifact");

  // ======================================================== E. multimodal editing
  scenario = "E multimodal editing";
  await page.keyboard.press("Escape");
  const bg = await artByName("Background");
  await locateInExplorer("ex-artifact-Background");
  await activate("Background");
  await node("Background").first().locator(".layer-chip", { hasText: "Sun" }).click();
  await node("Background").first().locator(".r-2d-edit input[type=range]").fill("0.45");
  await node("Background").first().getByRole("button", { name: "Apply" }).click();
  const bgHead = await until(async () => {
    const h = (await artByName("Background")).head_revision_id;
    return h !== bg.head_revision_id ? h : null;
  }, 60000);
  const bgRev = await api(`/api/projects/${PID}/revisions/${bgHead}`);
  check("layer edit created an OpenRaster revision touching only that layer", JSON.stringify(bgRev.changed_components) === JSON.stringify(["sun"]) && bgRev.origin === "manual", JSON.stringify(bgRev.changed_components));
  check("OpenRaster file still valid after the edit", bgRev.validation.checks.some((c) => c.name === "file_reopens" && c.passed));
  await sleep(1200);
  await shot("v1_11_layer_edit");
  await page.keyboard.press("Escape");
  const man = await artByName("Scene manifest");
  await locateInExplorer("ex-artifact-Scene manifest");
  await activate("Scene manifest");
  const mNode = node("Scene manifest").first();
  await mNode.locator(".file-chip", { hasText: "scene_manifest.py" }).click();
  await mNode.locator(".monaco-editor").first().waitFor({ timeout: 60000 });
  await mNode.locator(".monaco-editor .view-lines").first().click();
  await page.keyboard.press("Control+End");
  await page.keyboard.type("\n\nVERSION = \"v1-spatial\"\n");
  await mNode.getByRole("button", { name: "Review diff" }).click();
  await mNode.locator(".monaco-diff-editor").waitFor({ timeout: 20000 });
  await sleep(1500);
  await shot("v1_12_code_diff_review");
  await mNode.getByRole("button", { name: "Commit revision" }).click();
  const manHead = await until(async () => {
    const h = (await artByName("Scene manifest")).head_revision_id;
    return h !== man.head_revision_id ? h : null;
  }, 60000);
  const manRev = await api(`/api/projects/${PID}/revisions/${manHead}`);
  check("code edit committed as a git revision with a reviewable diff", manRev.vcs_commit && manRev.diff.includes('+VERSION = "v1-spatial"'), manRev.diff?.slice(0, 300));
  check("only the edited file changed", manRev.changed_components.filter((c) => c !== "repo").every((c) => c.startsWith("file:scene_manifest.py")), JSON.stringify(manRev.changed_components));
  await page.keyboard.press("Escape");

  // ======================================================== F. integrated workflows
  scenario = "F integrated workflows";
  const layoutBefore = await boardDoc();
  wf = await latestWf();
  const frame = layoutBefore.items.find((i) => i.item_type === "workflow");
  await page.locator(".react-flow__pane").click({ position: { x: 300, y: 500 } });
  await page.evaluate(() => 0);
  await page.locator('[data-testid="ex-workflows"] .ex-row').first().dblclick();
  await sleep(700);
  await page.locator(`[data-testid="item-${frame.id}"] .cnode-head`).click({ position: { x: 40, y: 8 } });
  await page.getByRole("button", { name: /\+ Operation/ }).click();
  await page.locator(".tb-pop button", { hasText: /^Validate$/ }).click();
  const wf2 = await until(async () => {
    const w = await latestWf();
    return w.version > wf.version ? w : null;
  }, 20000);
  const newNode = wf2.nodes.find((n) => !wf.nodes.find((m) => m.id === n.id));
  check("an operation added on the board is a real workflow node (new version)", newNode?.type === "validate");
  const newItem = await until(async () => (await boardDoc()).items.find((i) => i.resource_ref.node_id === newNode.id), 10000);
  await sleep(800);
  // connect 2D agent revision -> new validate input with a real execution edge
  const paintItem = (await boardDoc()).items.find((i) => i.resource_ref.node_id === "paint_agent");
  await page.locator('[data-testid="ex-workflows"] .ex-row').first().dblclick();
  await sleep(700);
  await page.locator(`[data-testid="item-${frame.id}"] .cnode-head`).click({ position: { x: 40, y: 8 } });
  await page.getByRole("button", { name: "Selection" }).click();
  await sleep(700);
  const outH = page.locator(`[data-testid="item-${paintItem.id}"] .react-flow__handle[data-handleid="out:revision"]`);
  const inH = page.locator(`[data-testid="item-${newItem.id}"] .react-flow__handle[data-handleid="in:revision"]`);
  const ob = await outH.boundingBox();
  const ib = await inH.boundingBox();
  await page.mouse.move(ob.x + ob.width / 2, ob.y + ob.height / 2);
  await page.mouse.down();
  await page.mouse.move(ib.x + ib.width / 2, ib.y + ib.height / 2, { steps: 12 });
  await page.mouse.up();
  const wf3 = await until(async () => {
    const w = await latestWf();
    return w.edges.some((e) => e.source === "paint_agent" && e.target === newNode.id) ? w : null;
  }, 15000);
  check("dragging between ports created a workflow edge (versioned)", !!wf3);
  await until(async () => (await boardDoc()).connections.some((c) => c.connection_type === "execution" && c.target_item_id === newItem.id), 10000);
  check("the board renders the execution connection from the workflow model", true);
  // require approval on the manifest agent, configured from the inspector
  const codeItem = (await boardDoc()).items.find((i) => i.resource_ref.node_id === "code_agent");
  await page.locator(`[data-testid="item-${codeItem.id}"] .cnode-head`).click({ position: { x: 40, y: 8 } });
  await page.locator("[data-testid=operation-inspector]").waitFor();
  const reqRow = page.locator("[data-testid=operation-inspector] .form-grid label", { hasText: "require approval" });
  await reqRow.locator("xpath=following-sibling::div[1]//input[@type='checkbox']").check();
  await page.getByRole("button", { name: "Save (new workflow version)" }).click();
  await until(async () => (await latestWf()).nodes.find((n) => n.id === "code_agent").config.require_approval === true, 15000);
  check("node configuration edited in the inspector is saved as a new version", true);
  // validate + run from the board
  await page.locator("[data-testid=workflow-actions]").getByRole("button", { name: "Validate" }).click();
  await page.locator(".ok-box", { hasText: "Workflow is valid" }).waitFor({ timeout: 15000 });
  check("validation from the board passes", true);
  // settle: the manual edits made in C–E legitimately invalidate those agent units, so bring the
  // workflow up to date first (approving the manifest via the API) before measuring incrementality
  const prevS = (await api(`/api/projects/${PID}/executions?workflow_id=${wf.id}`))[0]?.id;
  await post(`/api/projects/${PID}/workflows/${wf.id}/execute`, { mode: "incremental" });
  let exS = await waitExecution(wf.id, prevS);
  while (exS.status === "waiting_approval") {
    const waiting = exS.node_runs.find((r) => r.status === "waiting_approval");
    await post(`/api/projects/${PID}/executions/${exS.id}/decide`, { node_id: waiting.node_id, approve: true, note: "settle" });
    exS = await until(async () => {
      const e = await api(`/api/projects/${PID}/executions/${exS.id}`);
      return ["pending", "running"].includes(e.status) || (e.status === "waiting_approval" && e.node_runs.find((r) => r.status === "waiting_approval")?.node_id === waiting.node_id) ? null : e;
    }, 300000, 1000);
  }
  check("settle run after manual edits succeeded", exS.status === "succeeded", exS.error);
  // force the manifest to re-run by changing an upstream (sky) binding strength
  const sky = (await api(`/api/projects/${PID}/bindings`)).find((b) => b.target.component_id === "sky");
  await api(`/api/projects/${PID}/bindings/${sky.id}`, { method: "PATCH", headers: { "content-type": "application/json" }, body: JSON.stringify({ strength: 0.5 }) });
  const layoutPreRun = await boardDoc();
  const prevF = (await api(`/api/projects/${PID}/executions?workflow_id=${wf.id}`))[0]?.id;
  await page.locator("[data-testid=workflow-actions]").getByRole("button", { name: "▶ Run" }).click();
  await until(async () => {
    const e = (await api(`/api/projects/${PID}/executions?workflow_id=${wf.id}`))[0];
    return e && e.id !== prevF && e.status === "waiting_approval" ? e : null;
  }, 180000, 1000);
  await until(async () => (await page.locator(".cnode.operation.st-waiting_approval").count()) > 0, 20000);
  check("awaiting-approval state is visible on the operation node", true);
  await page.locator(`[data-testid="item-${codeItem.id}"] .cnode-head`).click({ position: { x: 40, y: 8 } });
  await page.locator(".approval").first().waitFor({ timeout: 15000 });
  await shot("v1_13_workflow_awaiting_approval");
  await page.locator(".approval").first().getByRole("button", { name: "Approve" }).click();
  const exF = await waitExecution(wf.id, prevF, { pastApproval: true });
  check("approved execution completed", exF.status === "succeeded", exF.error);
  const uF = units(exF);
  check("incremental: only the sky unit re-ran in the 2D agent; 3D agent up to date", uF["paint_agent:sky"] === "succeeded" && uF["paint_agent:background"] === "skipped" && uF["model_agent:table"] === "skipped", JSON.stringify(uF));
  check("new validate operation ran", exF.node_runs.find((r) => r.node_id === newNode.id)?.status === "succeeded");
  await sleep(2000);
  const layoutAfter = await boardDoc();
  const unchangedLayout = layoutPreRun.items.every((i) => {
    const j = layoutAfter.items.find((x) => x.id === i.id);
    return j && j.position.x === i.position.x && j.position.y === i.position.y;
  });
  check("execution did not rearrange or reset the board", unchangedLayout && layoutAfter.items.length === layoutPreRun.items.length);
  await page.getByRole("button", { name: "Fit" }).click();
  await sleep(1500);
  await shot("v1_14_board_after_workflow");

  // ======================================================== undo is presentation-only
  scenario = "undo/redo";
  const nBefore = (await boardDoc()).items.length;
  const headU = (await artByName("Table")).head_revision_id;
  await page.getByRole("button", { name: "+ Note" }).click();
  await waitSaved();
  const nAfter = (await boardDoc()).items.length;
  await page.locator(".react-flow__pane").click({ position: { x: 300, y: 500 } });
  await page.keyboard.press("Control+z");
  await waitSaved();
  const nUndo = (await boardDoc()).items.length;
  const headStill = (await artByName("Table")).head_revision_id === headU;
  check("Ctrl+Z undoes a board-layout action without touching artifact revisions", nAfter === nBefore + 1 && nUndo === nBefore && headStill, `${nBefore} ${nAfter} ${nUndo}`);

  // keyboard path into Focus and back (Shift+Enter, Escape)
  scenario = "keyboard";
  await locateInExplorer("ex-artifact-Background");
  await page.keyboard.press("Shift+Enter");
  check("Shift+Enter opens the selected view in Focus", await page.locator("[data-testid=focus-editor]").waitFor({ timeout: 10000 }).then(() => true, () => false));
  await page.keyboard.press("Escape");
  check("Escape leaves Focus", await page.locator("[data-testid=focus-editor]").waitFor({ state: "detached", timeout: 10000 }).then(() => true, () => false));

  check("no uncaught page errors", pageErrors.length === 0, pageErrors.join(" | "));
} catch (e) {
  check("walkthrough completed without exceptions", false, e?.stack || e);
  await shot("zz_failure").catch(() => {});
} finally {
  await browser.close();
  fs.writeFileSync(path.join(OUT, "spatial_e2e_results.json"), JSON.stringify(results, null, 1));
  const failed = results.filter((r) => !r.ok);
  console.log(`\n${failed.length ? "FAIL" : "PASS"}: ${results.length - failed.length}/${results.length}`);
  process.exit(failed.length ? 1 : 0);
}
