// Record a Chromium rendering trace (main, compositor, raster, GPU threads) in the packaged
// app's WebView2 while panning one board, and aggregate total duration by event name and
// thread. Shows whether frame cost is layout/paint, rasterization or GPU work.
//   node tools/native_validation/trace_board.mjs perf_project.json "P6 large" OUT.json
import { createRequire } from "node:module";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));
const require = createRequire(path.join(here, "../../studio/package.json"));
const { chromium } = require("playwright");
const [projFile, boardName, out] = process.argv.slice(2);
const proj = JSON.parse(fs.readFileSync(projFile, "utf8"));
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const browser = await chromium.connectOverCDP("http://127.0.0.1:9333");
const page = browser.contexts()[0].pages().find((p) => p.url().includes("tauri.localhost"));
if (!(await page.locator(".ex-row", { hasText: boardName }).count())) {
  await page.selectOption("select[aria-label=project]", proj.project);
  await sleep(1500);
}
await page.locator(".ex-row", { hasText: boardName }).first().click();
await sleep(1500);
await page.keyboard.press("Shift+1");
await sleep(1000);
const b = await page.locator(".react-flow__pane").first().boundingBox();
const cx = b.x + b.width / 2, cy = b.y + b.height / 2;
const cdp = await browser.newBrowserCDPSession();
const events = [];
cdp.on("Tracing.dataCollected", (d) => events.push(...d.value));
const done = new Promise((r) => cdp.on("Tracing.tracingComplete", r));
await cdp.send("Tracing.start", {
  transferMode: "ReportEvents",
  traceConfig: { includedCategories: ["devtools.timeline", "disabled-by-default-devtools.timeline",
    "disabled-by-default-devtools.timeline.frame", "cc", "viz", "gpu", "blink", "benchmark"] },
});
const t0 = Date.now();
for (const [dx, dy] of [[-300, -120], [300, 120]]) {
  await page.mouse.move(cx, cy); await page.mouse.down();
  for (let i = 1; i <= 40; i++) { await page.mouse.move(cx + (dx * i) / 40, cy + (dy * i) / 40); await sleep(16); }
  await page.mouse.up();
}
const wall = Date.now() - t0;
await cdp.send("Tracing.end");
await done;
const threads = new Map();
for (const e of events) if (e.ph === "M" && e.name === "thread_name") threads.set(`${e.pid}:${e.tid}`, e.args.name);
const agg = new Map();
for (const e of events) {
  if (e.ph !== "X" || !e.dur) continue;
  const th = threads.get(`${e.pid}:${e.tid}`) ?? "?";
  const key = `${th} | ${e.name}`;
  const a = agg.get(key) ?? { ms: 0, n: 0, max: 0 };
  a.ms += e.dur / 1000; a.n++; a.max = Math.max(a.max, e.dur / 1000);
  agg.set(key, a);
}
const top = [...agg.entries()].sort((a, b) => b[1].ms - a[1].ms).slice(0, 30)
  .map(([k, v]) => ({ event: k, total_ms: +v.ms.toFixed(1), count: v.n, max_ms: +v.max.toFixed(1) }));
const frames = events.filter((e) => e.name === "PipelineReporter" && e.ph === "b").length;
const res = { board: boardName, wall_ms: wall, trace_events: events.length, pipeline_frames: frames, top };
console.log(JSON.stringify(res, null, 1));
fs.writeFileSync(out, JSON.stringify(res, null, 1));
await browser.close().catch(() => {});
