import { describe, expect, it } from "vitest";
import conf from "../../src-tauri/tauri.conf.json";

describe("desktop shell configuration", () => {
  // NATIVE-1 D-001: with Tauri's default dragDropEnabled=true the Windows shell captures
  // OLE drag-and-drop, WebView2 never receives HTML5 drag events, and explorer -> canvas
  // placement and binding by drag do nothing in the packaged app.
  it("leaves drag-and-drop to the webview", () => {
    for (const w of conf.app.windows) expect(w.dragDropEnabled).toBe(false);
  });
});
