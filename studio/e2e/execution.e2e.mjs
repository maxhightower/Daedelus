// V2 walkthrough: execution targets, workers, jobs and live (SSE) updates in the studio.
//   STUDIO_URL=http://127.0.0.1:8765 WORKER_TOKEN=... OUT=../docs/screenshots/v2 node e2e/execution.e2e.mjs
// The backend must run with DAEDELUS_WORKER_TOKENS containing WORKER_TOKEN. This script starts
// real worker processes (`daedelus worker`) against it.
import { createRequire } from "node:module";
import { spawn } from "node:child_process";
import fs from "node:fs";
import path from "node:path";

const require = createRequire(import.meta.url);
const playwright = require("playwright");
const BASE = process.env.STUDIO_URL || "http://127.0.0.1:8765";
const OUT = path.resolve(process.env.OUT || "e2e-v2");
const WORKER_TOKEN = process.env.WORKER_TOKEN;
const DAEDELUS = process.env.DAEDELUS_BIN || "daedelus";
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
const workers = [];
function startWorker(name, fault = "") {
  const p = spawn(DAEDELUS, ["worker", "--control", BASE, "--name", name, "--adapters", "spreadsheet,document,presentation"], {
    env: { ...process.env, DAEDELUS_WORKER_TOKEN: WORKER_TOKEN, DAEDELUS_WORKER_FAULT: fault },
    stdio: "ignore",
    detached: true,
  });
  workers.push(p);
  return p;
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
const requests = [];
page.on("request", (r) => requests.push({ t: Date.now(), url: r.url(), method: r.method() }));
const shot = (name) => page.screenshot({ path: path.join(OUT, `${name}.png`) });

try {
  if (!WORKER_TOKEN) throw new Error("WORKER_TOKEN is required");
  const project = await post("/api/projects", { name: "Distributed execution", description: "V2 walkthrough" });
  const PID = project.id;
  const P = (p) => `/api/projects/${PID}${p}`;
  startWorker("office-worker-1");
  await until(async () => (await api("/api/cluster/status")).workers.some((w) => w.name === "office-worker-1" && w.alive), 60000);
  check("a real worker process registered with the control plane", true);

  await page.goto(BASE);
  await page.locator("select[aria-label=project]").waitFor();
  await page.selectOption("select[aria-label=project]", PID);
  await page.locator("[data-testid=execution-badge]").waitFor({ timeout: 30000 });
  await page.locator("[data-testid=execution-badge]").click();
  await page.locator("[data-testid=execution-settings]").waitFor({ timeout: 15000 });

  // ---------------------------------------------------------------- 1. target selection
  scenario = "1 target";
  await until(async () => /live updates/.test(await page.locator("[data-testid=execution-settings]").innerText()), 15000);
  check("the event stream is connected (live updates)", true);
  await page.locator("[data-testid=target-cloud_cpu]").check();
  await until(async () => (await api(P("/execution"))).target === "cloud_cpu", 10000);
  check("choosing Cloud CPU in the UI sets the project's execution target", true);
  const wtext = await page.locator("[data-testid=worker-row]").first().innerText();
  check("the connected worker is listed with its capabilities and adapters", /office-worker-1/.test(wtext) && /cpu/.test(wtext) && /spreadsheet/.test(wtext), wtext);
  const created = await post(P("/artifacts"), { name: "Remote book", adapter: "spreadsheet", template: "data", params: { sheets: [{ id: "data", title: "Data", rows: [["x", "y"], [1, 2]] }] } });
  check("an artifact created under Cloud CPU", created.artifact.id);
  await until(async () => (await page.locator("[data-testid=job-list] .job-row").count()) >= 3, 20000);
  const jl = await page.locator("[data-testid=job-list]").innerText();
  check("remote jobs appear live in the job list (pushed over SSE)", /spreadsheet\.create/.test(jl) && /succeeded/.test(jl), jl.slice(0, 400));
  await shot("v2_01_execution_settings_jobs");

  // ---------------------------------------------------------------- 2. node execution record
  scenario = "2 workflow";
  const wf = await post(P("/workflows"), {
    name: "Remote edit",
    nodes: [
      { id: "a", type: "artifact", label: "Book", config: { artifact_id: created.artifact.id } },
      { id: "g", type: "agent", label: "Remote agent", config: { instructions: "Edit the book.", fan_out: false } },
    ],
    edges: [{ id: "e", source: "a", source_port: "artifact", target: "g", target_port: "artifact" }],
  });
  const ex0 = await post(P(`/workflows/${wf.id}/execute`), {});
  const ex = await until(async () => {
    const e = await api(P(`/executions/${ex0.id}`));
    return ["pending", "running"].includes(e.status) ? null : e;
  }, 120000, 800);
  const nr = ex.node_runs.find((r) => r.node_id === "g");
  check("node run records the resolved target and the worker", nr.outputs.execution?.decisions?.spreadsheet?.resolved === "cloud_cpu", JSON.stringify(nr.outputs.execution ?? {}).slice(0, 300));
  await page.locator('[data-testid="ex-workflows"] .ex-row', { hasText: "Remote edit" }).first().click();
  await page.getByRole("button", { name: "Execution details" }).click();
  await page.locator(".section header", { hasText: /^g/ }).first().click().catch(() => {});
  await page.locator("[data-testid=node-execution]").first().waitFor({ timeout: 15000 });
  const info = await page.locator("[data-testid=node-execution]").first().innerText();
  check("the run view shows where the node executed (target, reason, worker jobs)", /cloud_cpu/.test(info) && /job\(s\)/.test(info), info);
  await shot("v2_02_workflow_remote");

  // ---------------------------------------------------------------- 3. no silent fallback
  scenario = "3 no fallback";
  await page.locator("[data-testid=execution-badge]").click();
  await page.locator("[data-testid=target-cloud_gpu]").check();
  await page.locator("[data-testid=target-warning]").waitFor({ timeout: 10000 });
  const warn = await page.locator("[data-testid=target-warning]").innerText();
  check("Cloud GPU without a GPU worker is flagged before running", /Cloud GPU cannot run/.test(warn) && /explicit error/.test(warn), warn);
  const r = await fetch(BASE + P("/artifacts"), { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ name: "gpu", adapter: "spreadsheet", template: "blank", params: {} }) });
  const body = await r.text();
  check("work under Cloud GPU fails explicitly instead of running locally", r.status === 400 && /not falling back to local/.test(body), body);
  await shot("v2_03_gpu_unavailable");

  // ---------------------------------------------------------------- 4. cancellation from the UI
  scenario = "4 cancel";
  await page.locator("[data-testid=target-cloud_cpu]").check();
  await until(async () => (await api(P("/execution"))).target === "cloud_cpu", 10000);
  workers.forEach((w) => process.kill(-w.pid, "SIGKILL"));
  startWorker("slow-worker", "slow:40");
  await until(async () => (await api("/api/cluster/status")).workers.some((w) => w.name === "slow-worker" && w.alive), 60000);
  const pending = fetch(BASE + P("/artifacts"), { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ name: "cancel me", adapter: "spreadsheet", template: "blank", params: {} }) });
  await page.locator(".job-row[data-state=running] button, .job-row[data-state=leased] button").first().waitFor({ timeout: 30000 });
  await shot("v2_04_running_job");
  await page.locator(".job-row[data-state=running] button, .job-row[data-state=leased] button").first().click();
  const res = await pending;
  const jobs = await api(P("/jobs"));
  check("cancel in the UI stops the running job (state cancelled)", jobs.some((j) => j.state === "cancelled"), JSON.stringify(jobs.map((j) => j.state)));
  check("the cancelled request created nothing", res.status === 400 && !(await api(P("/artifacts"))).some((a) => a.name === "cancel me"), res.status);
  await until(async () => (await page.locator(".job-row[data-state=cancelled]").count()) >= 1, 15000);
  await shot("v2_05_cancelled");

  // ---------------------------------------------------------------- 5. no polling
  scenario = "5 events not polling";
  const t0 = Date.now();
  await sleep(10000);
  const idle = requests.filter((q) => q.t > t0 && /\/api\//.test(q.url) && !/\/events/.test(q.url));
  check("no API polling while idle for 10 s (updates arrive over the event stream)", idle.length === 0, idle.map((q) => q.url).join("\n"));
  check("one event-stream connection per project view", requests.filter((q) => /\/events/.test(q.url)).length >= 1);
  check("no uncaught page errors", pageErrors.length === 0, pageErrors.join(" | "));
} catch (e) {
  check("walkthrough completed without exceptions", false, e?.stack || e);
  await shot("zz_failure").catch(() => {});
} finally {
  for (const w of workers) {
    try {
      process.kill(-w.pid, "SIGKILL");
    } catch {
      /* already gone */
    }
  }
  await browser.close();
  fs.writeFileSync(path.join(OUT, "execution_e2e_results.json"), JSON.stringify(results, null, 1));
  const failed = results.filter((r) => !r.ok);
  console.log(`\n${failed.length ? "FAIL" : "PASS"}: ${results.length - failed.length}/${results.length}`);
  process.exit(failed.length ? 1 : 0);
}
