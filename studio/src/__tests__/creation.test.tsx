import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { CreateResource } from "../components/CreateResource";
const { state, createArtifact, createWorkflow } = vi.hoisted(() => ({ state: {} as any, createArtifact: vi.fn(), createWorkflow: vi.fn() }));
vi.mock("../state", () => ({ useStudio: () => state }));
vi.mock("../api", () => ({ api: { createArtifact, createWorkflow } }));
let root: Root, host: HTMLDivElement;
let close: ReturnType<typeof vi.fn>;
const render = async (kind: "artifact" | "workflow" = "artifact") => { await act(async () => root.render(<CreateResource kind={kind} onClose={close} />)); };
const name = async (value: string) => { await act(async () => {
  const input = host.querySelector("input")!;
  Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")!.set!.call(input, value);
  input.dispatchEvent(new Event("input", { bubbles: true }));
}); };
const submit = async () => { await act(async () => { host.querySelector("form")!.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true })); }); };
beforeEach(() => {
  (globalThis as any).IS_REACT_ACT_ENVIRONMENT = true;
  vi.resetAllMocks(); host = document.createElement("div"); document.body.append(host); root = createRoot(host); close = vi.fn();
  Object.assign(state, {
    project: { id: "p1" }, health: { adapters: ["blender", "layered2d", "code", "document", "spreadsheet", "presentation"].map((name) => ({ name, available: true, templates: ["empty", "blank", "files"] })) },
    refresh: vi.fn().mockResolvedValue(undefined), placeResource: vi.fn().mockResolvedValue({ id: "item1" }), select: vi.fn(), notify: vi.fn(), setError: vi.fn(),
  });
  createArtifact.mockResolvedValue({ artifact: { id: "a1" } }); createWorkflow.mockResolvedValue({ id: "w1" });
});
afterEach(async () => { await act(async () => root.unmount()); host.remove(); });
describe("Explorer creation", () => {
  it.each([["blender", "empty"], ["layered2d", "blank"], ["code", "files"], ["document", "blank"], ["spreadsheet", "blank"], ["presentation", "blank"]])("creates and places %s", async (adapter, template) => {
    await render(); await name("  New asset  ");
    await act(async () => { const select = host.querySelector("select")!; select.value = adapter; select.dispatchEvent(new Event("change", { bubbles: true })); });
    await submit();
    expect(createArtifact).toHaveBeenCalledWith("p1", { name: "New asset", adapter, template, params: {} });
    expect(state.placeResource).toHaveBeenCalledWith({ kind: "artifact", id: "a1" });
    expect(state.select).toHaveBeenCalledWith({ kind: "items", itemIds: ["item1"] }); expect(close).toHaveBeenCalledOnce();
  });
  it("creates an independent workflow", async () => {
    await render("workflow"); await name("Another workflow"); await submit();
    expect(createWorkflow).toHaveBeenCalledWith("p1", { name: "Another workflow", nodes: [], edges: [] });
    expect(state.placeResource).toHaveBeenCalledWith({ kind: "workflow", id: "w1" });
  });
  it("blocks whitespace names", async () => { await render(); await name(" "); await submit(); expect(createArtifact).not.toHaveBeenCalled(); });
  it("explains unavailable tools", async () => {
    Object.assign(state.health.adapters.find((a: any) => a.name === "document"), { available: false, unavailable_reason: "Install document support" });
    await render(); await name("Doc"); await submit();
    expect(createArtifact).not.toHaveBeenCalled(); expect(host.textContent).toContain("Install document support");
  });
  it("retains input and supports retry after API failure", async () => {
    createArtifact.mockRejectedValueOnce(new Error("Tool failed"));
    await render(); await name("Draft"); await submit();
    expect(host.querySelector('[role="alert"]')?.textContent).toBe("Tool failed");
    expect(host.querySelector("input")?.value).toBe("Draft"); expect(close).not.toHaveBeenCalled();
    await submit(); expect(close).toHaveBeenCalledOnce();
  });
  it("preserves the resource if placement fails", async () => {
    state.placeResource.mockResolvedValue(undefined); await render(); await name("Draft"); await submit();
    expect(createArtifact).toHaveBeenCalledOnce();
    expect(state.select).toHaveBeenCalledWith({ kind: "resource", ref: { kind: "artifact", id: "a1" } }); expect(close).toHaveBeenCalledOnce();
  });
  it("prevents double submit and placement after leaving the project", async () => {
    let resolve!: (value: any) => void; createArtifact.mockReturnValue(new Promise((r) => { resolve = r; }));
    await render(); await name("Draft"); await submit(); await submit(); expect(createArtifact).toHaveBeenCalledOnce();
    await act(async () => root.render(<div>Other project</div>));
    await act(async () => resolve({ artifact: { id: "a1" } }));
    expect(state.placeResource).not.toHaveBeenCalled(); expect(close).not.toHaveBeenCalled();
  });
});
