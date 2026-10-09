// Zoom from the fit view onto a node with an injected stylesheet on/off and screenshot the
// node both ways, to check text sharpness after zooming (will-change: transform side effect).
//   node tools/native_validation/sharpness_check.mjs "P6 large" OUT_DIR "css text" [wheel_clicks]
import { createRequire } from "node:module";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));
const require = createRequire(path.join(here, "../../studio/package.json"));
const { chromium } = require("playwright");
const [boardName, outDir, css, clicksArg] = process.argv.slice(2);
const clicks = Number(clicksArg ?? 10);
fs.mkdirSync(outDir, { recursive: true });
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const browser = await chromium.connectOverCDP("http://127.0.0.1:9333");
const page = browser.contexts()[0].pages().find((p) => p.url().includes("tauri.localhost"));
await page.locator(".ex-row", { hasText: boardName }).first().click();
await sleep(1500);
const setCss = (on) => page.evaluate(([on, css]) => {
  let el = document.getElementById("ab-css");
  if (on && !el) { el = document.createElement("style"); el.id = "ab-css"; el.textContent = css; document.head.append(el); }
  if (!on && el) el.remove();
}, [on, css]);
for (const on of [true, false]) {
  await setCss(on);
  await page.keyboard.press("Shift+1");
  await sleep(1200);
  const node = page.locator(".react-flow__node-note").first();
  const nb = await node.boundingBox();
  await page.mouse.move(nb.x + nb.width / 2, nb.y + nb.height / 2);
  for (let i = 0; i < clicks; i++) { await page.mouse.wheel(0, -120); await sleep(60); }
  await sleep(2000);
  const zoom = await page.evaluate(() => document.querySelector("[data-testid=zoom-label]")?.textContent ?? "");
  const box = await node.boundingBox();
  const clip = { x: Math.max(0, box.x), y: Math.max(0, box.y), width: Math.min(box.width, 700), height: Math.min(box.height, 400) };
  await page.screenshot({ path: path.join(outDir, `sharpness_${on ? "with" : "without"}.png`), clip });
  console.log(`${on ? "with" : "without"} css: zoom ${zoom}`);
}
await setCss(false);
await browser.close().catch(() => {});
