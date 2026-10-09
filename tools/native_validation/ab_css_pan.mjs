// A/B a CSS change on one board inside the live WebView2: pan frame times without and with
// an injected stylesheet, alternating 3x, plus a close-zoom screenshot for text sharpness.
//   node tools/native_validation/ab_css_pan.mjs perf_project.json "P6 large" OUT_DIR "css text"
import { createRequire } from "node:module";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));
const require = createRequire(path.join(here, "../../studio/package.json"));
const { chromium } = require("playwright");
const [projFile, boardName, outDir, css] = process.argv.slice(2);
const proj = JSON.parse(fs.readFileSync(projFile, "utf8"));
fs.mkdirSync(outDir, { recursive: true });
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const stats = (xs) => {
  const s = [...xs].sort((a, b) => a - b);
  const q = (p) => s[Math.min(s.length - 1, Math.floor(p * s.length))];
  const mean = s.reduce((a, b) => a + b, 0) / s.length;
  return { n: s.length, mean_ms: +mean.toFixed(2), fps: +(1000 / mean).toFixed(1), p50_ms: +q(0.5).toFixed(1),
           p95_ms: +q(0.95).toFixed(1), p99_ms: +q(0.99).toFixed(1), max_ms: +s[s.length - 1].toFixed(1) };
};
const browser = await chromium.connectOverCDP("http://127.0.0.1:9333");
const page = browser.contexts()[0].pages().find((p) => p.url().includes("tauri.localhost"));
if (!(await page.locator(".ex-row", { hasText: boardName }).count())) {
  await page.selectOption("select[aria-label=project]", proj.project);
  await sleep(1500);
}
await page.locator(".ex-row", { hasText: boardName }).first().click();
await sleep(1500);
const setCss = (on) => page.evaluate(([on, css]) => {
  let el = document.getElementById("ab-css");
  if (on && !el) { el = document.createElement("style"); el.id = "ab-css"; el.textContent = css; document.head.append(el); }
  if (!on && el) el.remove();
}, [on, css]);
async function panFrames() {
  await page.keyboard.press("Shift+1");
  await sleep(1200);
  const b = await page.locator(".react-flow__pane").first().boundingBox();
  const cx = b.x + b.width / 2, cy = b.y + b.height / 2;
  await page.evaluate(() => { const w = window; w.__f = []; w.__s = false; let l = performance.now();
    const t = (x) => { w.__f.push(x - l); l = x; if (!w.__s) requestAnimationFrame(t); }; requestAnimationFrame(t); });
  for (const [dx, dy] of [[-300, -120], [300, 120]]) {
    await page.mouse.move(cx, cy); await page.mouse.down();
    for (let i = 1; i <= 40; i++) { await page.mouse.move(cx + (dx * i) / 40, cy + (dy * i) / 40); await sleep(16); }
    await page.mouse.up();
  }
  return page.evaluate(() => { window.__s = true; return window.__f.slice(1); });
}
const res = { board: boardName, css, without: [], with: [] };
for (let r = 0; r < 3; r++) {
  await setCss(false); res.without.push(stats(await panFrames()));
  await setCss(true); res.with.push(stats(await panFrames()));
}
// sharpness check: zoom in to "close" with the CSS on, then screenshot part of the canvas
const b = await page.locator(".react-flow__pane").first().boundingBox();
await page.mouse.move(b.x + b.width / 2, b.y + b.height / 2);
for (let i = 0; i < 25; i++) { await page.mouse.wheel(0, -120); await sleep(50); }
await sleep(1500);
await page.screenshot({ path: path.join(outDir, "ab_css_zoomed_with.png"), clip: { x: b.x, y: b.y, width: Math.min(900, b.width), height: Math.min(600, b.height) } });
await setCss(false);
await sleep(800);
await page.screenshot({ path: path.join(outDir, "ab_css_zoomed_without.png"), clip: { x: b.x, y: b.y, width: Math.min(900, b.width), height: Math.min(600, b.height) } });
console.log(JSON.stringify(res, null, 1));
fs.writeFileSync(path.join(outDir, "ab_css_pan.json"), JSON.stringify(res, null, 1));
await browser.close().catch(() => {});
