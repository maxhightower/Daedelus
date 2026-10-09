// NATIVE-1 physical-GPU benchmark of the packaged Daedelus Studio (WebView2) over CDP.
// Start the app with WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS=--remote-debugging-port=9333, build
// the boards with build_perf_project.py, then:
//   node tools/native_validation/gpu_bench.mjs perf_project.json OUT_DIR [runs=3] [label]
//
// Frame times are requestAnimationFrame intervals in the real WebView2 page. They show when
// the renderer produced frames at the display cadence (180 Hz here), not when the compositor
// presented them; long tasks (>50 ms main-thread work) are recorded as the jank signal.
// GPU utilisation / memory come from nvidia-smi (whole GPU); per-process CPU, RAM and GPU
// dedicated memory come from Windows performance counters for the app's own processes.
import { createRequire } from "node:module";
import { execFileSync, spawn } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));
const require = createRequire(path.join(here, "../../studio/package.json"));
const { chromium } = require("playwright");
const [projFile, outDir, runsArg, label] = process.argv.slice(2);
const RUNS = Number(runsArg || 3);
const proj = JSON.parse(fs.readFileSync(projFile, "utf8"));
fs.mkdirSync(outDir, { recursive: true });
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

const stats = (xs) => {
  if (!xs.length) return { n: 0 };
  const s = [...xs].sort((a, b) => a - b);
  const q = (p) => s[Math.min(s.length - 1, Math.floor(p * s.length))];
  const mean = s.reduce((a, b) => a + b, 0) / s.length;
  return { n: s.length, mean_ms: +mean.toFixed(2), fps: +(1000 / mean).toFixed(1), p50_ms: +q(0.5).toFixed(2),
           p95_ms: +q(0.95).toFixed(2), p99_ms: +q(0.99).toFixed(2), max_ms: +s[s.length - 1].toFixed(2),
           over_16_7: s.filter((x) => x > 16.7).length, over_33_3: s.filter((x) => x > 33.3).length };
};

// ------------------------------------------------------------------ process / GPU sampling
const PS = (script) => execFileSync("powershell", ["-NoProfile", "-Command", script], { encoding: "utf8" });
function appProcs() {
  const out = PS(`$p = Get-CimInstance Win32_Process | Where-Object { ($_.Name -eq 'msedgewebview2.exe' -and $_.CommandLine -match 'org\\.daedelus\\.studio') -or $_.Name -in 'daedelus-studio.exe','daedelus-server.exe' }; $p | ForEach-Object { $g = Get-Process -Id $_.ProcessId -ErrorAction SilentlyContinue; [pscustomobject]@{ pid=$_.ProcessId; name=$_.Name; type=([regex]::Match($_.CommandLine,'--type=([\\w-]+)').Groups[1].Value); cpu_s=$g.CPU; ws_mb=[math]::Round($g.WorkingSet64/1MB,1); priv_mb=[math]::Round($g.PrivateMemorySize64/1MB,1) } } | ConvertTo-Json -Compress`);
  const j = JSON.parse(out || "[]");
  return Array.isArray(j) ? j : [j];
}
function gpuMemByPid(pids) {
  const out = PS(`(Get-Counter '\\GPU Process Memory(*)\\Dedicated Usage' -ErrorAction SilentlyContinue).CounterSamples | Where-Object { $_.CookedValue -gt 0 } | ForEach-Object { [pscustomobject]@{ inst=$_.InstanceName; mb=[math]::Round($_.CookedValue/1MB,1) } } | ConvertTo-Json -Compress`);
  const rows = JSON.parse(out || "[]");
  const res = {};
  for (const r of Array.isArray(rows) ? rows : [rows]) {
    const m = /pid_(\d+)_/.exec(r.inst);
    if (m && pids.includes(+m[1])) res[m[1]] = (res[m[1]] ?? 0) + r.mb;
  }
  return res;
}
function startGpuSampler() {
  const samples = [];
  const p = spawn("nvidia-smi", ["--query-gpu=utilization.gpu,memory.used", "--format=csv,noheader,nounits", "-lms", "200"]);
  p.stdout.on("data", (d) => {
    for (const line of String(d).trim().split(/\r?\n/)) {
      const [u, m] = line.split(",").map((x) => +x.trim());
      if (!Number.isNaN(u)) samples.push({ util: u, mem: m });
    }
  });
  return () => {
    p.kill();
    const u = samples.map((s) => s.util);
    const m = samples.map((s) => s.mem);
    return { samples: samples.length, util_mean: +(u.reduce((a, b) => a + b, 0) / (u.length || 1)).toFixed(1),
             util_max: Math.max(0, ...u), mem_used_mib_max: Math.max(0, ...m) };
  };
}
const cpuSeconds = (procs) => procs.reduce((a, p) => a + (p.cpu_s ?? 0), 0);

// ------------------------------------------------------------------ page helpers
const browser = await chromium.connectOverCDP("http://127.0.0.1:9333");
const page = browser.contexts()[0].pages().find((p) => p.url().includes("tauri.localhost"));
const errors = [];
page.on("console", (m) => { if (m.type() === "error" || m.type() === "warning") errors.push(`${m.type()}: ${m.text()}`.slice(0, 300)); });
page.on("pageerror", (e) => errors.push(`pageerror: ${e}`.slice(0, 300)));
const cdp = await page.context().newCDPSession(page);
await cdp.send("Performance.enable");

const startFrames = () => page.evaluate(() => {
  const w = window;
  if (!w.__ltObs) {
    w.__ltObs = new PerformanceObserver((l) => l.getEntries().forEach((e) => w.__lt.push(e.duration)));
    w.__ltObs.observe({ type: "longtask", buffered: false });
  }
  w.__frames = []; w.__lt = []; w.__stop = false; let last = performance.now();
  const tick = (t) => { w.__frames.push(t - last); last = t; if (!w.__stop) requestAnimationFrame(tick); };
  requestAnimationFrame(tick);
});
const stopFrames = () => page.evaluate(() => { window.__stop = true; return { frames: window.__frames.slice(1), lt: window.__lt.slice() }; });

async function measure(name, fn) {
  const before = appProcs();
  const stopGpu = startGpuSampler();
  await startFrames();
  const t0 = Date.now();
  await fn();
  const wall = (Date.now() - t0) / 1000;
  await sleep(150);
  const { frames, lt } = await stopFrames();
  const gpu = stopGpu();
  const after = appProcs();
  const cores = 20;
  return { action: name, wall_s: +wall.toFixed(2), frames: stats(frames),
           long_tasks: { count: lt.length, max_ms: Math.round(Math.max(0, ...lt)), total_ms: Math.round(lt.reduce((a, b) => a + b, 0)) },
           cpu_pct_of_machine: +(((cpuSeconds(after) - cpuSeconds(before)) / wall / cores) * 100).toFixed(1),
           cpu_pct_of_one_core: +(((cpuSeconds(after) - cpuSeconds(before)) / wall) * 100).toFixed(1),
           gpu };
}

const canvasBox = async () => page.locator(".react-flow__pane").first().boundingBox();
async function pan() {
  const b = await canvasBox();
  const cx = b.x + b.width / 2, cy = b.y + b.height / 2;
  for (const [dx, dy] of [[-300, -120], [300, 120]]) {
    await page.mouse.move(cx, cy); await page.mouse.down();
    for (let i = 1; i <= 40; i++) { await page.mouse.move(cx + (dx * i) / 40, cy + (dy * i) / 40); await sleep(16); }
    await page.mouse.up();
  }
}
async function wheelZoom() {
  const b = await canvasBox();
  await page.mouse.move(b.x + b.width / 2, b.y + b.height / 2);
  for (let i = 0; i < 12; i++) { await page.mouse.wheel(0, -120); await sleep(40); }
  for (let i = 0; i < 24; i++) { await page.mouse.wheel(0, 120); await sleep(40); }
  for (let i = 0; i < 12; i++) { await page.mouse.wheel(0, -120); await sleep(40); }
}
async function dragNode() {
  const head = page.locator(".react-flow__node .cnode-head").first();
  if (!(await head.count())) return;
  const h = await head.boundingBox();
  const x = h.x + 30, y = h.y + h.height / 2;
  await page.mouse.move(x, y); await page.mouse.down();
  for (let i = 1; i <= 40; i++) { await page.mouse.move(x + 3 * i, y + 1.5 * i); await sleep(16); }
  for (let i = 39; i >= 0; i--) { await page.mouse.move(x + 3 * i, y + 1.5 * i); await sleep(16); }
  await page.mouse.up();
}
const fit = () => page.keyboard.press("Shift+1");
const view3d = () => page.locator(".react-flow__node canvas").first();

async function activate3d() {
  const c = view3d();
  const b = await c.boundingBox();
  const t0 = Date.now();
  await page.mouse.dblclick(b.x + b.width / 2, b.y + b.height / 2);
  await page.locator(".react-flow__node .badge", { hasText: "editing" }).first().waitFor({ timeout: 10000 }).catch(() => {});
  return Date.now() - t0;
}
async function orbit() {
  const b = await view3d().boundingBox();
  const x = b.x + b.width / 2, y = b.y + b.height / 2;
  await page.mouse.move(x, y); await page.mouse.down();
  for (let i = 1; i <= 60; i++) { await page.mouse.move(x + 80 * Math.sin(i / 10), y + 40 * Math.cos(i / 10)); await sleep(16); }
  await page.mouse.up();
}
async function openBoard(name) {
  const row = page.locator(".ex-row", { hasText: name }).first();
  const t0 = Date.now();
  await row.click();
  await page.waitForFunction((n) => document.querySelector(".board-name")?.textContent?.trim() === n, name, { timeout: 30000 });
  await page.waitForFunction(() => document.querySelectorAll(".react-flow__node").length >= 0, null, { timeout: 30000 });
  await sleep(200);
  return Date.now() - t0;
}
const saveLatency = async () => {
  const t0 = Date.now();
  await page.waitForFunction(() => document.querySelector(".save-state")?.textContent?.trim() === "saved", null, { timeout: 30000 }).catch(() => {});
  return Date.now() - t0;
};
const domCounts = () => page.evaluate(() => {
  const cs = [...document.querySelectorAll(".react-flow__node canvas")];
  return { nodes: document.querySelectorAll(".react-flow__node").length, edges: document.querySelectorAll(".react-flow__edge").length,
           webgl_canvases: cs.length, static_3d: document.querySelectorAll(".react-flow__node .static-3d, .react-flow__node [data-static3d]").length,
           zoom: document.querySelector("[data-testid=zoom-label]")?.textContent ?? "", heap_mb: performance.memory ? +(performance.memory.usedJSHeapSize / 1048576).toFixed(1) : null };
});

// ------------------------------------------------------------------ run
await page.reload(); // the project list is loaded once at start-up (D-006)
await page.locator("select[aria-label=project]").waitFor();
await page.selectOption("select[aria-label=project]", proj.project);
await page.locator(".ex-row").first().waitFor();
await sleep(1500);
const env = await page.evaluate(() => ({ inner: `${innerWidth}x${innerHeight}`, dpr: devicePixelRatio }));
const results = { label: label ?? "", started: new Date().toISOString(), viewport: env, runs: RUNS, workloads: {} };
const order = ["P1 empty", "P2 normal", "P3 three 3D", "P4 six 3D", "P5 eight 3D", "P6 large", "P7 focus 3D", "P8 mixed"];
for (const name of process.env.SHARED_ONLY ? [] : order) {
  const w = { open_ms: [], activation_ms: [], save_ms: [], actions: [] };
  for (let run = 1; run <= RUNS; run++) {
    if (run > 1) { await openBoard(order[(order.indexOf(name) + 1) % order.length]); await sleep(500); }
    w.open_ms.push(await openBoard(name));
    await fit(); await sleep(1500);
    if (run === 1) w.dom = await domCounts();
    w.actions.push(await measure("idle", () => sleep(2000)));
    w.actions.push(await measure("pan", pan));
    w.actions.push(await measure("wheel_zoom", wheelZoom));
    await fit(); await sleep(800);
    if (name !== "P1 empty") {
      w.actions.push(await measure("drag_node", dragNode));
      w.save_ms.push(await saveLatency());
    }
    if ((await view3d().count()) && name !== "P7 focus 3D") {
      w.activation_ms.push(await activate3d());
      w.actions.push(await measure("orbit_3d", orbit));
      await page.keyboard.press("Escape"); await page.keyboard.press("Escape"); await sleep(300);
    }
    if (name === "P7 focus 3D") {
      await page.locator(".react-flow__node .cnode-head").first().click(); await sleep(300);
      const t0 = Date.now();
      await page.getByRole("button", { name: "Focus", exact: true }).first().click();
      await page.getByRole("button", { name: /Exit Focus/ }).waitFor({ timeout: 10000 });
      w.focus_enter_ms = (w.focus_enter_ms ?? []).concat(Date.now() - t0);
      await sleep(800);
      w.actions.push(await measure("focus_orbit_3d", orbit));
      const t1 = Date.now();
      await page.keyboard.press("Escape");
      await page.getByRole("button", { name: /Exit Focus/ }).waitFor({ state: "detached", timeout: 10000 }).catch(() => {});
      w.focus_exit_ms = (w.focus_exit_ms ?? []).concat(Date.now() - t1);
      await page.keyboard.press("Escape"); await sleep(300);
    }
    if (run === 1) {
      const procs = appProcs();
      w.processes = { webview2_count: procs.filter((p) => p.name === "msedgewebview2.exe").length,
                      webview2_ws_mb: +procs.filter((p) => p.name === "msedgewebview2.exe").reduce((a, p) => a + p.ws_mb, 0).toFixed(1),
                      webview2_gpu_process_ws_mb: procs.find((p) => p.type === "gpu-process")?.ws_mb ?? null,
                      sidecar_ws_mb: +procs.filter((p) => p.name === "daedelus-server.exe").reduce((a, p) => a + p.ws_mb, 0).toFixed(1),
                      shell_ws_mb: procs.find((p) => p.name === "daedelus-studio.exe")?.ws_mb ?? null,
                      gpu_dedicated_mb: gpuMemByPid(procs.map((p) => p.pid)) };
      const m = await cdp.send("Performance.getMetrics");
      w.cdp_metrics = Object.fromEntries(m.metrics.filter((x) => ["JSHeapUsedSize", "Nodes", "LayoutCount", "RecalcStyleCount"].includes(x.name)).map((x) => [x.name, x.value]));
    }
  }
  results.workloads[name] = w;
  console.log(`${name}: open ${w.open_ms.join("/")} ms; ` + w.actions.filter((a, i) => i < 6).map((a) => `${a.action} p95 ${a.frames.p95_ms} max ${a.frames.max_ms}`).join("; "));
}

// shared artifact updated while shown in six views (P4): time until every view shows the new revision
await openBoard("P4 six 3D"); await fit(); await sleep(1500);
const upd = await measure("shared_update_six_views", async () => {
  const base = process.env.BACKEND_URL;
  const t0 = Date.now();
  const r = await fetch(`${base}/api/projects/${proj.project}/artifacts/${proj.artifacts.Table}/edit`, {
    method: "POST", headers: { "content-type": "application/json" },
    body: JSON.stringify({ operations: [{ op: "set_taper", component_id: "leg_fl", params: { factor: 0.25 } }],
                           message: "NATIVE-1 shared update" }) });
  const rev = await r.json();
  const tApi = Date.now();
  results.shared_update_api = { status: r.status, ms: tApi - t0, revision: rev.number };
  // every 3D view fetches the new revision's model (GET .../revisions/<id>/glb)
  const snap = rev.id ?? "@@none@@";
  const seen = [];
  const onResp = (resp) => { if (resp.url().includes(snap)) seen.push(Date.now() - tApi); };
  page.on("response", onResp);
  for (let i = 0; i < 200 && seen.length < 6; i++) await sleep(100);
  page.off("response", onResp);
  await sleep(500);
  results.shared_update_views = { fetches_seen: seen.length, first_ms_after_revision: seen[0] ?? null, sixth_ms_after_revision: seen[5] ?? null };
});
results.shared_update = upd;
results.console = errors.slice(0, 50);
results.finished = new Date().toISOString();
fs.writeFileSync(path.join(outDir, `gpu_bench${label ? "_" + label : ""}.json`), JSON.stringify(results, null, 1));
console.log("console errors/warnings:", errors.length);
await browser.close().catch(() => {});
