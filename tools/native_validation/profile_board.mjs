// CPU-profile a pan over one board in the packaged app's WebView2 (CDP on :9333) and print the
// functions with the most self time, to locate frame-time cost before optimising.
//   node tools/native_validation/profile_board.mjs perf_project.json "P6 large" OUT.json [zoom_clicks]
import { createRequire } from "node:module";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));
const require = createRequire(path.join(here, "../../studio/package.json"));
const { chromium } = require("playwright");
const [projFile, boardName, out, zoomClicks] = process.argv.slice(2);
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
await page.mouse.move(cx, cy);
for (let i = 0; i < Number(zoomClicks ?? 0); i++) { await page.mouse.wheel(0, -120); await sleep(60); }
await sleep(800);
const zoom = await page.evaluate(() => document.querySelector("[data-testid=zoom-label]")?.textContent ?? "");
const cdp = await page.context().newCDPSession(page);
await cdp.send("Profiler.enable");
await cdp.send("Profiler.setSamplingInterval", { interval: 200 });
await cdp.send("Profiler.start");
for (const [dx, dy] of [[-300, -120], [300, 120], [-300, -120], [300, 120]]) {
  await page.mouse.move(cx, cy); await page.mouse.down();
  for (let i = 1; i <= 40; i++) { await page.mouse.move(cx + (dx * i) / 40, cy + (dy * i) / 40); await sleep(16); }
  await page.mouse.up();
}
const { profile } = await cdp.send("Profiler.stop");
const byId = new Map(profile.nodes.map((n) => [n.id, n]));
const self = new Map();
const dt = profile.timeDeltas;
profile.samples.forEach((id, i) => {
  const n = byId.get(id);
  const f = n.callFrame;
  const key = `${f.functionName || "(anonymous)"} ${f.url.split("/").pop()}:${f.lineNumber + 1}`;
  self.set(key, (self.get(key) ?? 0) + (dt[i] ?? 0) / 1000);
});
const total = [...self.values()].reduce((a, b) => a + b, 0);
const top = [...self.entries()].sort((a, b) => b[1] - a[1]).slice(0, 25)
  .map(([k, v]) => ({ fn: k, self_ms: +v.toFixed(1), pct: +((100 * v) / total).toFixed(1) }));
const res = { board: boardName, zoom, total_sampled_ms: +total.toFixed(1), top };
console.log(JSON.stringify(res, null, 1));
fs.writeFileSync(out, JSON.stringify(res, null, 1));
await browser.close().catch(() => {});
