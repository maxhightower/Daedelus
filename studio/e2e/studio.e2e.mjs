// End-to-end walkthrough of the real studio against a real backend.
// Drives the UI (no API shortcuts for the steps it tests), asserts outcomes and saves screenshots.
//
//   STUDIO_URL=http://127.0.0.1:8765 OUT=../docs/screenshots node e2e/studio.e2e.mjs
import { createRequire } from "node:module";
import fs from "node:fs";
import path from "node:path";

const require = createRequire(import.meta.url);
let playwright;
try {
  playwright = require("playwright");
} catch {
  playwright = require(process.env.PLAYWRIGHT_MODULE || "/opt/node22/lib/node_modules/playwright");
}

const BASE = process.env.STUDIO_URL || "http://127.0.0.1:8765";
const OUT = path.resolve(process.env.OUT || "e2e-screenshots");
fs.mkdirSync(OUT, { recursive: true });
const results = [];
const check = (name, ok, detail = "") => {
  results.push({ name, ok: !!ok, detail });
  console.log(`${ok ? "PASS" : "FAIL"} ${name}${!ok && detail ? " - " + detail : ""}`);
};

const api = async (p, init) => {
  const r = await fetch(BASE + p, init);
  if (!r.ok) throw new Error(`${p}: ${r.status} ${await r.text()}`);
  return r.json();
};
async function waitExecution(pid, eid, timeout = 240000) {
  const t0 = Date.now();
  for (;;) {
    const e = await api(`/api/projects/${pid}/executions/${eid}`);
    if (!["pending", "running"].includes(e.status)) return e;
    if (Date.now() - t0 > timeout) throw new Error("execution timeout");
    await new Promise((r) => setTimeout(r, 1000));
  }
}

const launchOpts = { headless: true, args: ["--use-gl=swiftshader", "--enable-unsafe-swiftshader", "--ignore-gpu-blocklist"] };
if (fs.existsSync("/opt/pw-browsers/chromium")) {
  const dirs = fs.readdirSync("/opt/pw-browsers").filter((d) => d.startsWith("chromium-"));
  const exe = dirs.map((d) => `/opt/pw-browsers/${d}/chrome-linux/chrome`).find((p) => fs.existsSync(p));
  if (exe) launchOpts.executablePath = exe;
}
const browser = await playwright.chromium.launch(launchOpts);
const page = await browser.newPage({ viewport: { width: 1600, height: 980 } });
const consoleErrors = [];
page.on("pageerror", (e) => consoleErrors.push(String(e)));
const shot = async (name) => page.screenshot({ path: path.join(OUT, `${name}.png`) });
const nav = async (label) => page.locator(".sidenav button", { hasText: label }).click();

try {
  await page.goto(BASE);
  await page.getByText("Daedelus Studio").waitFor();
  await shot("01_welcome");

  // 1. Create the demo project through the UI (real ingestion + real Blender artifact creation)
  await page.getByRole("button", { name: /Create multimodal demo project/ }).click();
  await page.locator(".artifact-block").first().waitFor({ timeout: 180000 });
  const pid = await page.evaluate(() => localStorage.getItem("daedelus.project"));
  check("demo project created from the UI", !!pid);
  const arts = await api(`/api/projects/${pid}/artifacts`);
  check("three artifacts created (3D, 2D, code)", arts.map((a) => a.artifact_type).sort().join() === "code,image2d,model3d");
  await page.waitForTimeout(1500);
  await shot("02_workspace");

  // 2. Scope & binding inspector on Table -> Legs
  await page.locator(".tree-row", { hasText: "Legs" }).first().click();
  await page.getByText("Resolved context").waitFor();
  await page.waitForTimeout(800);
  const ctxText = await page.locator(".context").innerText();
  check("legs context shows inherited style guide and own leg inspiration",
    ctxText.includes("Project style guide") && ctxText.includes("Leg inspiration (tapered)"));
  await shot("03_scope_inspector_legs");

  // 3. Source library: one source with multiple bindings
  await nav("Sources");
  await page.locator(".source-card", { hasText: "Background inspiration (sunset)" }).click();
  await page.locator(".binding-card").nth(1).waitFor();
  const nb = await page.locator(".detail-col .binding-card").count();
  check("sunset image has two independent bindings (reference + inspiration)", nb === 2, String(nb));
  await shot("04_source_multiple_bindings");
  await page.locator(".source-card", { hasText: "Leg inspiration (tapered)" }).click();
  await page.locator(".detail-col .binding-card").first().click();
  await page.locator(".binding-editor").waitFor();
  await shot("05_binding_editor");

  // 4. Workflow editor
  await nav("Workflow");
  await page.locator(".react-flow__node").first().waitFor();
  const nodeCount = await page.locator(".react-flow__node").count();
  check("workflow canvas shows 9 nodes", nodeCount === 9, String(nodeCount));
  await page.getByRole("button", { name: "Validate", exact: true }).click();
  await page.waitForTimeout(800);
  await shot("06_workflow_editor");
  // select the 3D agent and dry-run its plan
  await page.locator(".react-flow__node", { hasText: "3D agent" }).click();
  await page.getByRole("button", { name: /Preview plan/ }).click();
  await page.getByText("Planned operations (not applied)").waitFor({ timeout: 60000 });
  await shot("07_agent_plan_preview");

  // 5. Run the workflow from the UI
  await page.locator(".react-flow__pane").click({ position: { x: 20, y: 20 } });
  await page.getByRole("button", { name: "▶ Run" }).click();
  await page.waitForTimeout(1500);
  const wid = (await api(`/api/projects/${pid}/workflows`))[0].id;
  let execs = await api(`/api/projects/${pid}/executions?workflow_id=${wid}`);
  check("execution started from the UI", execs.length === 1);
  const ex = await waitExecution(pid, execs[0].id);
  check("first execution succeeded", ex.status === "succeeded", ex.error || "");
  await page.waitForTimeout(2500);
  await shot("08_workflow_after_run");

  // 6. Artifact inspector: 3D, 2D, code
  await nav("Artifacts");
  await page.locator(".detail-col .revision").waitFor();
  await page.waitForTimeout(3000);
  await shot("09_artifact_table_3d");
  const tableRev = await page.locator(".detail-col").innerText();
  check("table revision shows attribution and validation", tableRev.includes("Attribution") && tableRev.includes("component_preservation"));
  const sel = page.locator(".list-col select").first();
  const bgId = arts.find((a) => a.artifact_type === "image2d").id;
  await sel.selectOption(bgId);
  await page.waitForTimeout(2000);
  await shot("10_artifact_background_2d");
  const manId = arts.find((a) => a.artifact_type === "code").id;
  await sel.selectOption(manId);
  await page.locator(".monaco-diff-editor, .monaco-editor").first().waitFor({ timeout: 60000 });
  await page.waitForTimeout(2500);
  await shot("11_artifact_manifest_diff");

  // 7. Change ONE binding in the UI: leg inspiration role inspiration -> reference
  await nav("Sources");
  await page.locator(".source-card", { hasText: "Leg inspiration (tapered)" }).click();
  await page.locator(".detail-col .binding-card").first().click();
  const roleInput = page.locator(".binding-editor input[list=role-list]");
  await roleInput.fill("reference");
  await page.getByRole("button", { name: "Save binding" }).click();
  await page.waitForTimeout(1000);
  const legsB = (await api(`/api/projects/${pid}/bindings`)).find((b) => b.target.component_id === "legs" && b.role === "reference");
  check("binding role changed through the UI", !!legsB);

  // impact analysis in the workflow editor
  await nav("Workflow");
  await page.locator(".react-flow__node").first().waitFor();
  await page.getByRole("button", { name: "Impact", exact: true }).click();
  await page.getByText("Impact: what would run now").waitFor({ timeout: 60000 });
  const imp = await api(`/api/projects/${pid}/workflows/${wid}/impact`);
  const stale = imp.nodes.flatMap((n) => (n.units || []).filter((u) => u.stale).map((u) => u.unit.split("#").pop()));
  check("impact after role change: only legs (+ downstream manifest) stale", JSON.stringify(stale) === JSON.stringify(["legs"]), JSON.stringify(stale));
  await shot("12_impact_after_role_change");
  await page.getByRole("button", { name: "▶ Run" }).click();
  await page.waitForTimeout(1500);
  execs = await api(`/api/projects/${pid}/executions?workflow_id=${wid}`);
  const ex2 = await waitExecution(pid, execs[0].id);
  check("incremental execution succeeded", ex2.status === "succeeded", ex2.error || "");
  const units = Object.fromEntries(ex2.node_runs.flatMap((r) => r.units.map((u) => [u.unit.split("#").pop(), u.status])));
  check("only the legs unit executed; background unit skipped",
    units.legs === "succeeded" && units.table === "skipped" && units.tabletop === "skipped" && units.background === "skipped" && units.sky === "skipped",
    JSON.stringify(units));

  // 8. Execution monitor + replay
  await nav("Executions");
  await page.locator(".detail-col h2").waitFor();
  await page.getByRole("button", { name: /Reproduce/ }).click();
  await page.locator(".ok-box, .error-box").first().waitFor({ timeout: 120000 });
  const rep = await page.locator(".ok-box").count();
  check("replay from the UI reports reproducible", rep > 0);
  await shot("13_execution_monitor_replay");

  // 9. Agent conversation
  await nav("Agent");
  const tp = page.locator(".composer .target-picker select");
  await tp.first().selectOption({ label: "Table (model3d)" });
  await tp.nth(1).selectOption("tabletop");
  await page.locator(".composer .chip", { hasText: "color" }).click();
  await page.locator(".composer textarea").fill("Make the tabletop lighter.\nbase_color: #c8a27a");
  await page.getByRole("button", { name: "Send", exact: true }).click();
  await page.locator(".msg.assistant").first().waitFor({ timeout: 60000 });
  const reply = await page.locator(".msg.assistant").last().innerText();
  check("agent reply lists the units that will re-run", reply.includes("Units that will re-run") && reply.includes("tabletop"), reply);
  await shot("14_agent_conversation");

  check("no uncaught page errors", consoleErrors.length === 0, consoleErrors.join(" | "));
} catch (e) {
  check("walkthrough completed without exceptions", false, String(e?.stack || e));
  await shot("zz_failure");
} finally {
  await browser.close();
  fs.writeFileSync(path.join(OUT, "e2e_results.json"), JSON.stringify(results, null, 1));
  const failed = results.filter((r) => !r.ok);
  console.log(`\n${failed.length ? "FAIL" : "PASS"}: ${results.length - failed.length}/${results.length}`);
  process.exit(failed.length ? 1 : 0);
}
