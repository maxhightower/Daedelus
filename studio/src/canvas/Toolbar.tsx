import { useReactFlow, useStore } from "@xyflow/react";
import { useState } from "react";
import { api } from "../api";
import { useStudio } from "../state";
import type { CanvasItem } from "../types";
import { freeSpotAt, itemBounds, lodFor, uid } from "./hooks";


export function CanvasToolbar({ onDuplicate }: { onDuplicate: () => void }) {
  const s = useStudio();
  const rf = useReactFlow();
  const zoom = useStore((st) => st.transform[2]);
  const [menu, setMenu] = useState<"" | "op" | "links" | "views">("");
  const board = s.board!;
  const sel = s.selection.kind === "items" ? s.selection.itemIds : s.selection.kind === "component" && s.selection.itemId ? [s.selection.itemId] : [];
  const selItem = sel.length === 1 ? board.items.find((i) => i.id === sel[0]) : undefined;

  const center = () => {
    const el = document.querySelector(".spatial")!.getBoundingClientRect();
    return rf.screenToFlowPosition({ x: el.left + el.width / 2, y: el.top + el.height / 2 });
  };
  const addItem = (it: Partial<CanvasItem>) => {
    const c = center();
    const item: CanvasItem = {
      id: uid("item"),
      item_type: "note",
      resource_ref: { kind: "note" },
      position: { x: Math.round(c.x - 120), y: Math.round(c.y - 70) },
      size: { width: 240, height: 140 },
      z_index: 0,
      presentation_state: {},
      group_id: null,
      collapsed: false,
      metadata: {},
      ...it,
    } as CanvasItem;
    s.updateBoard((b) => ({ ...b, items: [...b.items, item] }));
    s.select({ kind: "items", itemIds: [item.id] });
    return item;
  };
  const addFrame = () => {
    const members = board.items.filter((i) => sel.includes(i.id) && i.item_type !== "frame" && i.item_type !== "workflow");
    if (members.length) {
      const x0 = Math.min(...members.map((m) => m.position.x)) - 30;
      const y0 = Math.min(...members.map((m) => m.position.y)) - 60;
      const x1 = Math.max(...members.map((m) => m.position.x + m.size.width)) + 30;
      const y1 = Math.max(...members.map((m) => m.position.y + m.size.height)) + 30;
      const f: CanvasItem = {
        id: uid("item"),
        item_type: "frame",
        resource_ref: { kind: "frame" },
        position: { x: x0, y: y0 },
        size: { width: x1 - x0, height: y1 - y0 },
        z_index: -10,
        presentation_state: { title: "New frame", color: "#5a7a55" },
        // nest inside the frame the members already share, so moving that frame moves this one
        group_id: members.every((m) => m.group_id === members[0].group_id) ? members[0].group_id : null,
        collapsed: false,
        metadata: {},
      };
      const ids = new Set(members.map((m) => m.id));
      s.updateBoard((b) => ({ ...b, items: [...b.items.map((i) => (ids.has(i.id) ? { ...i, group_id: f.id } : i)), f] }));
      s.select({ kind: "items", itemIds: [f.id] });
    } else addItem({ item_type: "frame", resource_ref: { kind: "frame" }, size: { width: 600, height: 400 }, z_index: -10, presentation_state: { title: "New frame", color: "#5a7a55" } });
  };
  const addOperation = async (type: string) => {
    setMenu("");
    if (!s.project) return;
    // target workflow: the selected workflow group / operation, else the first workflow on this board
    const wfId =
      (selItem?.item_type === "workflow" && selItem.resource_ref.id) ||
      (selItem?.item_type === "operation" && selItem.resource_ref.workflow_id) ||
      board.items.find((i) => i.item_type === "workflow")?.resource_ref.id ||
      s.workflows[0]?.id;
    let wf = s.workflows.find((w) => w.id === wfId);
    if (!wf) {
      wf = await s.run(api.createWorkflow(s.project.id, { name: "Board workflow", nodes: [], edges: [] } as any));
      if (!wf) return;
    }
    const nt = s.nodeTypes.find((t) => t.type === type)!;
    const node = { id: uid(type), type, label: nt.title, config: JSON.parse(JSON.stringify(nt.defaults)), position: { x: 0, y: 0 } };
    const r = await s.run(api.saveWorkflow(s.project.id, { ...wf, nodes: [...wf.nodes, node] }), `${nt.title} added to “${wf.name}” (v${wf.version + 1})`);
    if (!r) return;
    await s.refresh();
    const frame = board.items.find((i) => i.item_type === "workflow" && i.resource_ref.id === wf!.id);
    const c = center();
    const spot = freeSpotAt(board.items, { width: 230, height: 150 }, c);
    const it = await s.placeResource({ kind: "workflow_node", workflow_id: wf.id, node_id: node.id }, spot.x, spot.y);
    if (it && frame) s.updateItem(it.id, { group_id: frame.id }, { history: false });
    if (it) s.select({ kind: "items", itemIds: [it.id] });
  };
  const lf = s.linkFilter;
  return (
    <div className="canvas-toolbar" onMouseDown={(e) => e.stopPropagation()}>
      <b className="board-name">{board.name}</b>
      <span className={`save-state ${s.saveState}`} title="board layout autosave">
        {s.saveState === "saved" ? "saved" : s.saveState === "error" ? "save failed" : "saving…"}
      </span>
      <span className="tb-sep" />
      <button onClick={() => rf.zoomOut({ duration: 150 })} title="Zoom out">−</button>
      <span className="zoom-label" data-testid="zoom-label" title={`level of detail: ${lodFor(zoom)}`}>
        {Math.round(zoom * 100)}% · {lodFor(zoom)}
      </span>
      <button onClick={() => rf.zoomIn({ duration: 150 })} title="Zoom in">+</button>
      <button
        onClick={() => {
          const b = itemBounds(board.items);
          if (b) rf.fitBounds(b, { padding: 0.08, duration: 300 });
        }}
        title="Zoom to fit (Shift+1)"
      >
        Fit
      </button>
      <button
        disabled={!sel.length}
        onClick={() => {
          const b = itemBounds(board.items.filter((i) => sel.includes(i.id)));
          if (b) rf.fitBounds(b, { padding: 0.25, duration: 300 });
        }}
        title="Zoom to selection (Shift+2)"
      >
        Selection
      </button>
      <span className="tb-sep" />
      <button onClick={() => addItem({ presentation_state: { text: "", color: "#4a4020" } })} title="Add a note">+ Note</button>
      <button onClick={addFrame} title="Add a frame (groups the selection if any)">+ Frame</button>
      <div className="tb-menu">
        <button onClick={() => setMenu(menu === "op" ? "" : "op")}>+ Operation ▾</button>
        {menu === "op" && (
          <div className="tb-pop">
            {s.nodeTypes.map((t) => (
              <button key={t.type} onClick={() => addOperation(t.type)} title={t.description}>
                {t.title}
              </button>
            ))}
          </div>
        )}
      </div>
      <button disabled={selItem?.item_type !== "artifact_view"} onClick={onDuplicate} title="Another view of the same artifact (Ctrl+D)">
        Duplicate view
      </button>
      <span className="tb-sep" />
      <button disabled={!s.canUndo} onClick={s.undo} title="Undo layout change (Ctrl+Z) — artifact edits use revisions">↶</button>
      <button disabled={!s.canRedo} onClick={s.redo} title="Redo layout change (Ctrl+Shift+Z)">↷</button>
      <div className="tb-menu">
        <button onClick={() => setMenu(menu === "links" ? "" : "links")}>Links ▾</button>
        {menu === "links" && (
          <div className="tb-pop">
            {(["reference", "execution", "dependency", "annotation"] as const).map((k) => (
              <label key={k} className="inline">
                <input type="checkbox" checked={lf[k]} onChange={(e) => s.setLinkFilter({ ...lf, [k]: e.target.checked })} /> {k}
              </label>
            ))}
            <label className="inline">
              <input type="checkbox" checked={lf.selectionOnly} onChange={(e) => s.setLinkFilter({ ...lf, selectionOnly: e.target.checked })} /> only for selection
            </label>
          </div>
        )}
      </div>
      <div className="tb-menu">
        <button onClick={() => setMenu(menu === "views" ? "" : "views")}>Views ▾</button>
        {menu === "views" && (
          <div className="tb-pop">
            <button
              onClick={() => {
                const name = prompt("Name this view", `View ${board.saved_views.length + 1}`);
                if (name) s.updateBoard((b) => ({ ...b, saved_views: [...b.saved_views, { id: uid("view"), name, viewport: rf.getViewport() }] }), { history: false });
                setMenu("");
              }}
            >
              Save current view…
            </button>
            {board.saved_views.map((v) => (
              <button
                key={v.id}
                onClick={() => {
                  rf.setViewport(v.viewport, { duration: 300 });
                  setMenu("");
                }}
              >
                ◎ {v.name}
              </button>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
