// V1.1 walkthrough: semantic understanding in the real studio (backend + Blender, no AI model).
//   STUDIO_URL=http://127.0.0.1:8765 OUT=../docs/screenshots/v1_1 node e2e/semantic.e2e.mjs
// Uses the local, measurement-based provider: it verifies the UI and the agent loop, not a
// live model's understanding (live gates are reported separately as blocked/passed).
import { createRequire } from "node:module";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const require = createRequire(import.meta.url);
const playwright = require("playwright");
const BASE = process.env.STUDIO_URL || "http://127.0.0.1:8765";
const OUT = path.resolve(process.env.OUT || "e2e-v1_1");
const ASSETS = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../../backend/daedelus/demo_assets/v11");
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
  const exe = fs.readdirSync("/opt/pw-browsers").filter((d) => d.startsWith("chromium-")).map((d) => `/opt/pw-browsers/${d}/chrome-linux/chrome`).find((p) => fs.existsSync(p));
  if (exe) launchOpts.executablePath = exe;
}
const browser = await playwright.chromium.launch(launchOpts);
const page = await browser.newPage({ viewport: { width: 1680, height: 1000 } });
const pageErrors = [];
page.on("pageerror", (e) => pageErrors.push(String(e)));
const shot = (name) => page.screenshot({ path: path.join(OUT, `${name}.png`) });

try {
  // ---------------------------------------------------------------- project via the API
  const project = await post("/api/projects", { name: "Semantic tree", description: "V1.1 walkthrough" });
  const PID = project.id;
  const P = (p) => `/api/projects/${PID}${p}`;
  const photo = await post(P("/sources/path"), { path: path.join(ASSETS, "tree_trunk_photo.jpg"), name: "Twisted trunk photo" });
  const style = await post(P("/sources/path"), { path: path.join(ASSETS, "island_tree_01_render.png"), name: "Stylised tree reference" });
  const guide = await post(P("/sources/text"), { name: "Game asset guide", text: "# Game asset guide\nStylized low-poly look.\nThe model must have no more than 400 polygons.\nKeep the trunk visibly twisted.\n" });
  const created = await post(P("/artifacts"), { name: "Tree", adapter: "blender", template: "empty", params: { root_id: "tree", name: "Tree" } });
  const art = created.artifact;
  const tgt = { scope: "artifact", artifact_id: art.id };
  await post(P("/bindings"), { source_id: photo.id, role: "reference", aspects: ["geometry", "shape", "color"], target: tgt, segment: { kind: "region", region: [0.03, 0, 0.5, 0.97], note: "the trunk" } });
  await post(P("/bindings"), { source_id: style.id, role: "inspiration", aspects: ["style", "color"], target: tgt });
  await post(P("/bindings"), { source_id: guide.id, role: "constraint", constraint: "hard", aspects: ["geometry", "style"], target: tgt });
  const wf = await post(P("/workflows"), {
    name: "Create tree",
    nodes: [
      { id: "src", type: "sources", label: "Sources", config: {} },
      { id: "art", type: "artifact", label: "Tree", config: { artifact_id: art.id } },
      { id: "agent", type: "agent", label: "Tree agent", config: { instructions: "Create a stylized twisted tree from the references.", understand: true, fan_out: false, loop: { enabled: true, max_iterations: 3 } } },
    ],
    edges: [
      { id: "e1", source: "art", source_port: "artifact", target: "agent", target_port: "artifact" },
      { id: "e2", source: "src", source_port: "sources", target: "agent", target_port: "sources" },
    ],
  });
  check("project, sources, bindings and workflow created", wf.id && art.id);

  await page.goto(BASE);
  await page.locator("select[aria-label=project]").waitFor();
  await page.selectOption("select[aria-label=project]", PID);
  await page.locator(".react-flow__node").first().waitFor({ timeout: 30000 });
  await sleep(1500);

  // ---------------------------------------------------------------- 1. analyse a source
  scenario = "1 source understanding";
  await page.locator('[data-testid="ex-source-Twisted trunk photo"]').dblclick();
  await page.locator("[data-testid=source-inspector]").waitFor({ timeout: 10000 });
  const provOpts = await page.locator("select[aria-label='analysis provider'] option").allInnerTexts();
  check("live providers are listed but marked unavailable without credentials", provOpts.some((o) => /anthropic.*unavailable/.test(o)) && provOpts.some((o) => /gemini.*unavailable/.test(o)), provOpts.join(" | "));
  await page.locator("[data-testid=analyze-source]").click();
  await page.locator("[data-testid=analysis-card]").first().waitFor({ timeout: 30000 });
  const card = page.locator("[data-testid=analysis-card]").first();
  const cardText = await card.innerText();
  check("analysis card shows provider, status and limitations", /heuristic/.test(cardText) && /partial/.test(cardText) && /cannot tell what the image depicts/.test(cardText), cardText.slice(0, 300));
  check("observations show how they were obtained (measured)", (await card.locator(".badge", { hasText: "measured" }).count()) > 0);
  await shot("v11_01_source_analysis");

  // ---------------------------------------------------------------- 2. context package
  scenario = "2 context package";
  await page.locator('[data-testid="ex-artifact-Tree"]').dblclick();
  await sleep(500);
  await page.locator(".agent-dock-head").click();
  await page.locator("[data-testid=agent-context-toggle]").click();
  await page.locator("[data-testid=context-package]").waitFor({ timeout: 15000 });
  const missing = page.locator("[data-testid=analyse-missing]");
  if (await missing.count()) {
    await missing.click();
    await until(async () => (await page.locator("[data-testid=analyse-missing]").count()) === 0, 30000);
  }
  const pkgText = await page.locator("[data-testid=context-package]").innerText();
  check("context package lists each source with its user-assigned meaning", /reference/.test(pkgText) && /inspiration/.test(pkgText) && /constraint/.test(pkgText), pkgText.slice(0, 400));
  check("the polygon limit is shown as enforced (hard constraint binding)", /enforced/.test(pkgText) && /poly_count lte 400/.test(pkgText), pkgText.slice(0, 600));
  check("region-bound reference is visible", /region \[0\.03/.test(pkgText) || /\[0\.03,0\.00/.test(pkgText), pkgText.slice(0, 600));
  check("provider status line shows live providers unavailable", /○ anthropic/.test(await page.locator("[data-testid=provider-status]").innerText()));
  await shot("v11_02_agent_context_package");
  await page.evaluate(() => document.querySelectorAll(".agent-context, .agent-dock-body").forEach((e) => (e.style.maxHeight = "none")));
  await page.locator("[data-testid=context-package]").screenshot({ path: path.join(OUT, "v11_02b_context_package_detail.png") });

  // ---------------------------------------------------------------- 3. plan preview
  scenario = "3 plan preview";
  await page.locator('[data-testid="ex-workflows"] .ex-row', { hasText: "Tree agent" }).first().dblclick().catch(async () => {
    await page.locator('[data-testid="ex-workflows"] .ex-row').first().dblclick();
  });
  await sleep(600);
  const opNode = page.locator(".cnode.operation", { hasText: "Tree agent" }).first();
  await opNode.locator(".cnode-head").click({ position: { x: 30, y: 6 } });
  await page.locator("[data-testid=operation-inspector]").waitFor();
  await page.locator("[data-testid=operation-inspector] .section header button", { hasText: "Dry run" }).click();
  await page.locator("[data-testid=preview-plan]").click();
  await page.locator("[data-testid=plan-preview]").first().waitFor({ timeout: 30000 });
  const pv = await page.locator("[data-testid=plan-preview]").first().innerText();
  check("preview shows planned operations without applying them", /add_primitive/.test(pv) && /extrude_faces/.test(pv), pv.slice(0, 300));
  check("preview labels the recipe as deterministic (no understanding claimed)", /deterministic recipe/.test(pv), pv.slice(0, 500));
  const revsBefore = (await api(P(`/artifacts/${art.id}/revisions`))).length;
  check("preview created no revision", revsBefore === 1, revsBefore);
  await shot("v11_03_plan_preview");

  // ---------------------------------------------------------------- 4. run with the loop
  scenario = "4 run + loop";
  await page.locator("[data-testid=workflow-actions]").getByRole("button", { name: "▶ Run" }).click();
  const ex = await until(async () => {
    const e = (await api(P(`/executions?workflow_id=${wf.id}`)))[0];
    return e && !["pending", "running"].includes(e.status) ? api(P(`/executions/${e.id}`)) : null;
  }, 300000, 1500);
  check("execution succeeded", ex.status === "succeeded", ex.error);
  const loop = ex.node_runs.find((r) => r.node_id === "agent").outputs.loop;
  check("loop reached its objective", loop.status === "achieved", loop.stop_reason);
  await page.locator("[data-testid=op-loop]").first().waitFor({ timeout: 20000 });
  check("operation node shows the loop result on the canvas", /achieved/.test(await page.locator("[data-testid=op-loop]").first().innerText()));
  await opNode.locator(".cnode-head").click({ position: { x: 30, y: 6 } });
  await page.locator("[data-testid=loop-view]").waitFor({ timeout: 15000 });
  const lv = await page.locator("[data-testid=loop-view]").innerText();
  check("loop view shows measured findings per iteration", /poly_count lte 400/.test(lv) && /constraint/.test(lv), lv.slice(0, 400));
  check("loop view shows the render of each iteration", (await page.locator(".loop-thumb").count()) >= 1);
  await shot("v11_04_loop_result");
  await page.locator("[data-testid=loop-view]").screenshot({ path: path.join(OUT, "v11_04b_loop_detail.png") });
  // the created model on the board
  await page.locator(".agent-dock-head").click();
  await page.locator('[data-testid="ex-artifact-Tree"]').dblclick();
  await sleep(4000);
  const framed = await page.evaluate(() => {
    const k = Object.keys(window.__dd3d ?? {}).find((x) => x.endsWith(":board"));
    const v = k && window.__dd3d[k];
    if (!v || !v.loaded()) return null;
    const pt = v.project("canopy");
    return pt && Number.isFinite(pt.x) && Number.isFinite(pt.y) ? pt : null;
  });
  check("3D view of the new model is framed (canopy projects to a finite point)", !!framed, JSON.stringify(framed));
  await shot("v11_05_tree_on_board");
  const head = (await api(P(`/artifacts/${art.id}`))).head_revision_id;
  const rev = await api(P(`/revisions/${head}`));
  check("final revision satisfies the polygon budget (measured)", rev.measurements.tree.poly_count <= 400, rev.measurements.tree.poly_count);
  check("no uncaught page errors", pageErrors.length === 0, pageErrors.join(" | "));
} catch (e) {
  check("walkthrough completed without exceptions", false, e?.stack || e);
  await shot("zz_failure").catch(() => {});
} finally {
  await browser.close();
  fs.writeFileSync(path.join(OUT, "semantic_e2e_results.json"), JSON.stringify(results, null, 1));
  const failed = results.filter((r) => !r.ok);
  console.log(`\n${failed.length ? "FAIL" : "PASS"}: ${results.length - failed.length}/${results.length}`);
  process.exit(failed.length ? 1 : 0);
}
