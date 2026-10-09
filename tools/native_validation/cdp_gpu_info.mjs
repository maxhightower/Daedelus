// Attach to the packaged app's WebView2 over CDP (the app must be started with
// WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS=--remote-debugging-port=9333) and report whether
// rendering is hardware accelerated: browser GPU feature status, GPU devices, and the
// WebGL renderer string seen by the page.
//   node tools/native_validation/cdp_gpu_info.mjs [http://127.0.0.1:9333] [out.json]
import { createRequire } from "node:module";
import { writeFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));
const require = createRequire(path.join(here, "../../studio/package.json"));
const { chromium } = require("playwright");

const endpoint = process.argv[2] ?? "http://127.0.0.1:9333";
const out = process.argv[3];
const browser = await chromium.connectOverCDP(endpoint);
const page = browser.contexts()[0].pages().find((p) => p.url().includes("tauri.localhost"));
const bs = await browser.newBrowserCDPSession();
const info = await bs.send("SystemInfo.getInfo");
const webgl = await page.evaluate(() => {
  const res = {};
  for (const kind of ["webgl2", "webgl"]) {
    const c = document.createElement("canvas");
    const gl = c.getContext(kind);
    if (!gl) { res[kind] = null; continue; }
    const dbg = gl.getExtension("WEBGL_debug_renderer_info");
    res[kind] = {
      vendor: dbg ? gl.getParameter(dbg.UNMASKED_VENDOR_WEBGL) : gl.getParameter(gl.VENDOR),
      renderer: dbg ? gl.getParameter(dbg.UNMASKED_RENDERER_WEBGL) : gl.getParameter(gl.RENDERER),
      maxTextureSize: gl.getParameter(gl.MAX_TEXTURE_SIZE),
    };
    gl.getExtension("WEBGL_lose_context")?.loseContext();
  }
  return { ...res, devicePixelRatio: window.devicePixelRatio, ua: navigator.userAgent,
           screen: `${screen.width}x${screen.height}`, inner: `${innerWidth}x${innerHeight}` };
});
const result = {
  captured: new Date().toISOString(),
  featureStatus: info.gpu.featureStatus,
  devices: info.gpu.devices.map((d) => ({ vendor: d.vendorString, device: d.deviceString, driver: d.driverVersion })),
  driverBugWorkarounds: info.gpu.driverBugWorkarounds?.length ?? 0,
  page: webgl,
};
console.log(JSON.stringify(result, null, 1));
if (out) writeFileSync(out, JSON.stringify(result, null, 1));
await browser.close().catch(() => {});
