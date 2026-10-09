import { describe, expect, it } from "vitest";
import { frameHost, toNodes } from "../canvas/SpatialCanvas";
import { freeSpot, freeSpotAt, itemBounds, lodFor } from "../canvas/hooks";
import type { CanvasBoard, CanvasItem } from "../types";

const item = (id: string, x: number, y: number, extra: Partial<CanvasItem> = {}): CanvasItem =>
  ({
    id,
    item_type: "note",
    resource_ref: { kind: "note" },
    position: { x, y },
    size: { width: 100, height: 80 },
    z_index: 0,
    presentation_state: {},
    group_id: null,
    collapsed: false,
    metadata: {},
    ...extra,
  }) as CanvasItem;

const board = (items: CanvasItem[]): CanvasBoard =>
  ({ id: "b", project_id: "p", name: "B", schema_version: 1, revision: 1, items, connections: [], saved_views: [], viewport: { x: 0, y: 0, zoom: 1 } }) as unknown as CanvasBoard;

describe("toNodes", () => {
  const frame = item("f", 100, 100, { item_type: "frame", resource_ref: { kind: "frame" }, size: { width: 500, height: 400 } });
  const child = item("c", 150, 180, { group_id: "f" });
  const loose = item("l", 900, 900);

  it("renders frames first and children relative to their frame", () => {
    const ns = toNodes(board([loose, child, frame]), new Set(), new Set());
    expect(ns[0].id).toBe("f");
    const c = ns.find((n) => n.id === "c")!;
    expect(c.parentId).toBe("f");
    expect(c.position).toEqual({ x: 50, y: 80 });
    expect(ns.find((n) => n.id === "l")!.position).toEqual({ x: 900, y: 900 });
  });

  it("keeps frames below content even when selected, and raises selected content", () => {
    const ns = toNodes(board([frame, child, loose]), new Set(["f", "l"]), new Set());
    expect(ns.find((n) => n.id === "f")!.zIndex).toBeLessThan(0);
    expect(ns.find((n) => n.id === "l")!.zIndex).toBeGreaterThanOrEqual(500);
    expect(ns.find((n) => n.id === "c")!.zIndex).toBe(0);
  });

  it("ignores a group_id that does not point at a frame", () => {
    const ns = toNodes(board([loose, item("x", 10, 10, { group_id: "l" })]), new Set(), new Set());
    const x = ns.find((n) => n.id === "x")!;
    expect(x.parentId).toBeUndefined();
    expect(x.position).toEqual({ x: 10, y: 10 });
  });

  it("flags missing resources in node data", () => {
    const ns = toNodes(board([loose]), new Set(), new Set(["l"]));
    expect(ns[0].data.missing).toBe(true);
  });
});

describe("layout helpers", () => {
  it("itemBounds spans all items", () => {
    expect(itemBounds([item("a", 0, 0), item("b", 200, 50)])).toEqual({ x: 0, y: 0, width: 300, height: 130 });
    expect(itemBounds([])).toBeNull();
  });

  it("freeSpotAt keeps a free position and avoids occupied ones", () => {
    const items = [item("a", 0, 0)];
    expect(freeSpotAt(items, { width: 100, height: 80 }, { x: 400, y: 400 })).toEqual({ x: 400, y: 400 });
    const p = freeSpotAt(items, { width: 100, height: 80 }, { x: 10, y: 10 });
    const overlaps = p.x < 100 && p.x + 100 > 0 && p.y < 80 && p.y + 80 > 0;
    expect(overlaps).toBe(false);
  });

  it("freeSpot never lands on another item and ignores frames", () => {
    const near = item("a", 0, 0);
    const items = [near, item("below", 0, 120), item("frame", -50, -50, { item_type: "frame", size: { width: 1000, height: 1000 } })];
    const p = freeSpot(items, near.size, near);
    for (const o of items.filter((i) => i.item_type !== "frame")) {
      const hit = p.x < o.position.x + o.size.width && p.x + 100 > o.position.x && p.y < o.position.y + o.size.height && p.y + 80 > o.position.y;
      expect(hit).toBe(false);
    }
  });

  it("level of detail thresholds", () => {
    expect(lodFor(0.2)).toBe("far");
    expect(lodFor(0.6)).toBe("medium");
    expect(lodFor(1.2)).toBe("close");
  });
});

describe("frameHost (NATIVE-1 D-005)", () => {
  const fr = (id: string, x: number, y: number, w: number, h: number, group: string | null = null) =>
    item(id, x, y, { item_type: "frame", resource_ref: { kind: "frame" }, size: { width: w, height: h }, group_id: group });

  it("never makes a frame the child of a smaller frame it is dropped over", () => {
    const small = fr("small", 10, 60, 570, 300); // nested group created inside "big"
    const big = fr("big", 0, 0, 1130, 560); // its centre (565, 280) lies inside "small"
    expect(frameHost(new Map([small, big].map((i) => [i.id, i])), big)).toBeUndefined();
  });

  it("picks the smallest enclosing frame that is larger than the item", () => {
    const outer = fr("outer", 0, 0, 2000, 2000);
    const inner = fr("inner", 0, 0, 600, 600, "outer");
    const note = item("n", 100, 100);
    const items = new Map([outer, inner, note].map((i) => [i.id, i]));
    expect(frameHost(items, note)?.id).toBe("inner");
    expect(frameHost(items, inner)?.id).toBe("outer");
  });
});