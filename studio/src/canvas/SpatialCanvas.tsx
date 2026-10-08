import {
  Background,
  MiniMap,
  ReactFlow,
  applyNodeChanges,
  useReactFlow,
  type Connection,
  type Edge,
  type Node,
  type NodeChange,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api } from "../api";
import { useStudio } from "../state";
import type { CanvasBoard, CanvasConnection, CanvasItem, ResourceRef, Workflow } from "../types";
import { EDGE_TYPES, EdgeMarkers, type TypedEdgeData } from "./edges";
import { NODE_TYPES, type ItemNodeData } from "./nodes";
import { RelationshipDialog, type PendingRelationship } from "./RelationshipDialog";
import { CanvasToolbar } from "./Toolbar";
import { freeSpot, itemBounds, uid } from "./hooks";


const isFrame = (i: CanvasItem) => i.item_type === "frame" || i.item_type === "workflow";

function depth(items: Map<string, CanvasItem>, it: CanvasItem): number {
  let d = 0;
  let cur = it;
  while (cur.group_id && items.has(cur.group_id) && d < 20) {
    cur = items.get(cur.group_id)!;
    d++;
  }
  return d;
}

/** Board items -> React Flow nodes. Board positions are absolute; RF children are parent-relative. */
export function toNodes(board: CanvasBoard, selected: Set<string>, missing: Set<string>): Node<ItemNodeData>[] {
  const byId = new Map(board.items.map((i) => [i.id, i]));
  const ordered = [...board.items].sort((a, b) => {
    const fa = isFrame(a) ? 0 : 1;
    const fb = isFrame(b) ? 0 : 1;
    return fa - fb || depth(byId, a) - depth(byId, b) || a.z_index - b.z_index;
  });
  return ordered.map((it) => {
    const parent = it.group_id ? byId.get(it.group_id) : undefined;
    const validParent = parent && isFrame(parent) ? parent : undefined;
    return {
      id: it.id,
      type: it.item_type,
      position: validParent ? { x: it.position.x - validParent.position.x, y: it.position.y - validParent.position.y } : it.position,
      parentId: validParent?.id,
      width: it.size.width,
      height: it.size.height,
      style: { width: it.size.width, height: it.size.height },
      // selected items come to the front so their handles and resizers are reachable
      zIndex: isFrame(it) ? -100 + depth(byId, it) : selected.has(it.id) ? 500 + it.z_index : it.z_index,
      selected: selected.has(it.id),
      dragHandle: ".cnode-head",
      data: { item: it, missing: missing.has(it.id) },
    } as Node<ItemNodeData>;
  });
}

export function SpatialCanvas() {
  const s = useStudio();
  const rf = useReactFlow();
  const board = s.board;
  const [nodes, setNodes] = useState<Node<ItemNodeData>[]>([]);
  const [pending, setPending] = useState<PendingRelationship | null>(null);
  const dragging = useRef(false);
  const wrapper = useRef<HTMLDivElement>(null);
  const savedViewport = useRef<{ x: number; y: number; zoom: number } | null>(null);
  const loadedBoard = useRef<string | null>(null);

  const selectedIds = useMemo(() => {
    const sel = s.selection;
    if (sel.kind === "items") return new Set(sel.itemIds);
    if (sel.kind === "component" && sel.itemId) return new Set([sel.itemId]);
    return new Set<string>();
  }, [s.selection]);
  const missing = useMemo(() => new Set(board?.missing_items ?? []), [board?.missing_items]);

  useEffect(() => {
    if (!board || dragging.current) return;
    // keep React Flow's measurements: a node object without `measured` loses its handle bounds,
    // and since its DOM size did not change it is never re-measured (its edges would vanish)
    setNodes((prev) => {
      const measured = new Map(prev.map((n) => [n.id, n.measured]));
      return toNodes(board, selectedIds, missing).map((n) => (measured.get(n.id) ? { ...n, measured: measured.get(n.id) } : n));
    });
  }, [board, selectedIds, missing]);

  // restore the board's saved viewport when a board is opened
  useEffect(() => {
    if (!board || loadedBoard.current === board.id) return;
    loadedBoard.current = board.id;
    const v = board.viewport;
    setTimeout(() => {
      if (board.items.length && (v.x !== 0 || v.y !== 0 || v.zoom !== 1)) rf.setViewport(v);
      else {
        const b = itemBounds(board.items);
        if (b) rf.fitBounds(b, { padding: 0.08 });
      }
    }, 30);
  }, [board, rf]);

  // Focus is a presentation change: remember the board viewport and restore it exactly on exit
  useEffect(() => {
    if (s.focusItemId) savedViewport.current = rf.getViewport();
    else if (savedViewport.current) {
      rf.setViewport(savedViewport.current);
      savedViewport.current = null;
    }
  }, [s.focusItemId, rf]);

  useEffect(() => {
    s.registerLocator((id: string) => {
      const it = s.board?.items.find((i) => i.id === id);
      const b = it && itemBounds([it]);
      if (b) {
        // ~1.1x zoom on the item, centred
        const pad = Math.max(b.width, b.height) * 0.25;
        rf.fitBounds({ x: b.x - pad, y: b.y - pad, width: b.width + 2 * pad, height: b.height + 2 * pad }, { duration: 350 });
      }
      s.select({ kind: "items", itemIds: [id] });
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [rf, s.board]);

  const itemById = useCallback((id: string) => board?.items.find((i) => i.id === id), [board]);

  // ----------------------------------------------------------- edges
  const edges: Edge<TypedEdgeData>[] = useMemo(() => {
    if (!board) return [];
    const f = s.linkFilter;
    const selConn = s.selection.kind === "connection" ? s.selection.connectionId : null;
    return board.connections
      .filter((c) => f[c.connection_type])
      .filter((c) => !f.selectionOnly || selectedIds.has(c.source_item_id) || selectedIds.has(c.target_item_id) || c.id === selConn)
      .map((c) => ({
        id: c.id,
        source: c.source_item_id,
        target: c.target_item_id,
        sourceHandle: c.source_anchor.handle ?? undefined,
        targetHandle: c.target_anchor.handle ?? undefined,
        type: "typed",
        selected: c.id === selConn,
        zIndex: c.id === selConn ? 1000 : 0,
        data: { conn: c, dim: false },
      }));
  }, [board, s.linkFilter, selectedIds, s.selection]);
  // automation/diagnostics: what the canvas was asked to draw
  useEffect(() => {
    (window as any).__ddCanvas = { connections: board?.connections.length ?? 0, edges: edges.length, nodes: nodes.length };
  });

  // ----------------------------------------------------------- node changes
  const onNodesChange = useCallback((changes: NodeChange<Node<ItemNodeData>>[]) => {
    setNodes((ns) => {
      const byId = new Map(ns.map((n) => [n.id, n]));
      const relevant = changes.filter((c) => {
        if (c.type === "remove") return false;
        if (c.type === "dimensions" && !c.resizing && c.dimensions) {
          const n = byId.get(c.id);
          return !n?.measured || n.measured.width !== c.dimensions.width || n.measured.height !== c.dimensions.height;
        }
        return true;
      });
      return relevant.length ? applyNodeChanges(relevant, ns) : ns;
    });
  }, []);

  const onNodeDragStop = useCallback(
    (_: any, __: Node, dragged: Node[]) => {
      dragging.current = false;
      if (!board) return;
      const items = new Map(board.items.map((i) => [i.id, { ...i }]));
      const absOf = (n: Node) => {
        const it = items.get(n.id)!;
        const parent = it.group_id ? items.get(it.group_id) : undefined;
        return parent ? { x: n.position.x + parent.position.x, y: n.position.y + parent.position.y } : n.position;
      };
      let dropRelationship: PendingRelationship | null = null;
      for (const n of dragged) {
        const it = items.get(n.id);
        if (!it) continue;
        const abs = absOf(n);
        const dx = abs.x - it.position.x;
        const dy = abs.y - it.position.y;
        it.position = { x: Math.round(abs.x), y: Math.round(abs.y) };
        if (isFrame(it)) {
          // children move with their frame (positions are stored absolute)
          const move = (fid: string) =>
            items.forEach((c) => {
              if (c.group_id === fid && !dragged.find((d) => d.id === c.id)) {
                c.position = { x: Math.round(c.position.x + dx), y: Math.round(c.position.y + dy) };
                if (isFrame(c)) move(c.id);
              }
            });
          move(it.id);
        }
        // regroup: the smallest frame containing the item's centre becomes its parent
        const cx = it.position.x + it.size.width / 2;
        const cy = it.position.y + it.size.height / 2;
        const descendants = new Set<string>();
        const collect = (fid: string) => items.forEach((c) => c.group_id === fid && (descendants.add(c.id), collect(c.id)));
        collect(it.id);
        const host = [...items.values()]
          .filter((f) => isFrame(f) && f.id !== it.id && !descendants.has(f.id))
          .filter((f) => cx >= f.position.x && cx <= f.position.x + f.size.width && cy >= f.position.y && cy <= f.position.y + f.size.height)
          .sort((a, b) => a.size.width * a.size.height - b.size.width * b.size.height)[0];
        it.group_id = host?.id ?? null;
        // dropping a reference onto an artifact view opens the relationship editor
        if (it.item_type === "source" && dragged.length === 1) {
          const tgt = [...items.values()].find(
            (a) => a.item_type === "artifact_view" && cx >= a.position.x && cx <= a.position.x + a.size.width && cy >= a.position.y && cy <= a.position.y + a.size.height,
          );
          if (tgt) {
            const comp = tgt.presentation_state.selected_component;
            dropRelationship = {
              sourceId: it.resource_ref.id!,
              target: comp
                ? { scope: "component", artifact_id: tgt.resource_ref.id, component_id: comp }
                : { scope: "artifact", artifact_id: tgt.resource_ref.id },
            };
          }
        }
      }
      s.updateBoard((b) => ({ ...b, items: b.items.map((i) => items.get(i.id) ?? i) }));
      if (dropRelationship) setPending(dropRelationship);
    },
    [board, s],
  );

  // ----------------------------------------------------------- connections
  const kindOf = (id: string) => itemById(id)?.item_type;
  const isValidConnection = useCallback(
    (c: Connection | Edge) => {
      const sh = c.sourceHandle ?? "";
      const th = c.targetHandle ?? "";
      if (c.source === c.target) return false;
      if (sh === "ref-out") return kindOf(c.target) === "artifact_view" && (th === "ref-in" || th.startsWith("comp-in:"));
      if (sh === "dep-out") return th === "dep-in";
      if (sh === "note-out") return th === "ann-in";
      if (sh.startsWith("out:") && th.startsWith("in:")) {
        const a = itemById(c.source)?.resource_ref;
        const b = itemById(c.target)?.resource_ref;
        if (!a || !b || a.workflow_id !== b.workflow_id) return false;
        const wf = s.workflows.find((w) => w.id === a.workflow_id);
        const sn = wf?.nodes.find((n) => n.id === a.node_id);
        const tn = wf?.nodes.find((n) => n.id === b.node_id);
        const sp = s.nodeTypes.find((t) => t.type === sn?.type)?.outputs.find((p) => `out:${p.name}` === sh);
        const tp = s.nodeTypes.find((t) => t.type === tn?.type)?.inputs.find((p) => `in:${p.name}` === th);
        if (!sp || !tp) return false;
        if (!(sp.type === tp.type || sp.type === "any" || tp.type === "any")) return false;
        if (!tp.multiple && wf?.edges.some((e) => e.target === tn!.id && e.target_port === tp.name)) return false;
        return true;
      }
      return false;
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [board, s.workflows, s.nodeTypes],
  );

  const saveWorkflow = async (wf: Workflow, ok: string) => {
    if (!s.project) return;
    const r = await s.run(api.saveWorkflow(s.project.id, wf), ok);
    if (r) {
      if (r.issues.some((i: any) => i.level === "error")) s.notify(`Saved v${r.workflow.version} with validation issues — see inspector`);
      await s.refresh();
      await s.reloadBoard();
    }
  };

  const onConnect = useCallback(
    async (c: Connection) => {
      if (!board || !isValidConnection(c)) return;
      const sh = c.sourceHandle ?? "";
      const th = c.targetHandle ?? "";
      if (sh === "ref-out") {
        const src = itemById(c.source)!;
        const tgt = itemById(c.target)!;
        const comp = th.startsWith("comp-in:") ? th.slice(8) : null;
        setPending({
          sourceId: src.resource_ref.id!,
          target: comp ? { scope: "component", artifact_id: tgt.resource_ref.id, component_id: comp } : { scope: "artifact", artifact_id: tgt.resource_ref.id },
        });
      } else if (sh.startsWith("out:")) {
        const a = itemById(c.source)!.resource_ref;
        const b = itemById(c.target)!.resource_ref;
        const wf = s.workflows.find((w) => w.id === a.workflow_id)!;
        await saveWorkflow(
          { ...wf, edges: [...wf.edges, { id: uid("e"), source: a.node_id!, source_port: sh.slice(4), target: b.node_id!, target_port: th.slice(3) }] },
          "Execution edge added (new workflow version)",
        );
      } else {
        const conn: CanvasConnection = {
          id: uid("conn"),
          connection_type: sh === "dep-out" ? "dependency" : "annotation",
          source_item_id: c.source,
          target_item_id: c.target,
          source_anchor: { handle: sh },
          target_anchor: { handle: th },
          domain_ref: { kind: "none" },
          presentation_state: { label: sh === "dep-out" ? "uses" : "" },
          derived: false,
        };
        s.updateBoard((b) => ({ ...b, connections: [...b.connections, conn] }));
      }
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [board, isValidConnection, s.workflows],
  );

  // ----------------------------------------------------------- deletion (board vs domain semantics)
  const deleteSelection = useCallback(async () => {
    if (!board || !s.project) return;
    const sel = s.selection;
    if (sel.kind === "items") {
      const ids = new Set(sel.itemIds);
      s.updateBoard((b) => ({
        ...b,
        items: b.items.filter((i) => !ids.has(i.id)).map((i) => (i.group_id && ids.has(i.group_id) ? { ...i, group_id: null } : i)),
        connections: b.connections.filter((c) => !ids.has(c.source_item_id) && !ids.has(c.target_item_id)),
      }));
      s.select({ kind: "none" });
      s.notify("Removed from the board (project resources are unchanged)");
    } else if (sel.kind === "connection") {
      const c = board.connections.find((x) => x.id === sel.connectionId);
      if (!c) return;
      if (c.connection_type === "reference" && c.domain_ref.id) {
        if (!confirm("Delete this reference relationship? This deletes the underlying source binding.")) return;
        if (await s.run(api.deleteBinding(s.project.id, c.domain_ref.id), "Source binding deleted")) {
          await s.refresh();
          await s.reloadBoard();
        }
      } else if (c.connection_type === "execution" && c.domain_ref.workflow_id) {
        const wf = s.workflows.find((w) => w.id === c.domain_ref.workflow_id);
        if (wf) await saveWorkflow({ ...wf, edges: wf.edges.filter((e) => e.id !== c.domain_ref.id) }, "Execution edge removed (new workflow version)");
      } else {
        s.updateBoard((b) => ({ ...b, connections: b.connections.filter((x) => x.id !== c.id) }));
      }
      s.select({ kind: "none" });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [board, s.selection, s.project, s.workflows]);

  // ----------------------------------------------------------- keyboard: one level at a time
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const t = e.target as HTMLElement;
      const typing = t.closest("input, textarea, select, [contenteditable=true], .monaco-editor");
      if (e.key === "Escape") {
        if (pending) return setPending(null);
        if (typing && t.closest(".monaco-editor")) return; // Monaco uses Escape for its widgets
        if (s.focusItemId) return s.setFocusItem(null);
        if (s.activeItemId) return s.setActive(null);
        return s.select({ kind: "none" });
      }
      if (typing) return;
      const mod = e.ctrlKey || e.metaKey;
      if (mod && e.key.toLowerCase() === "z") {
        e.preventDefault();
        return e.shiftKey ? s.redo() : s.undo();
      }
      if (mod && e.key.toLowerCase() === "y") {
        e.preventDefault();
        return s.redo();
      }
      if (s.focusItemId) return;
      if (e.key === "Enter" && s.selection.kind === "items" && s.selection.itemIds.length === 1) {
        const it = itemById(s.selection.itemIds[0]);
        if (it && e.shiftKey && it.item_type === "artifact_view") {
          e.preventDefault();
          s.setFocusItem(it.id); // Level 3
        } else if (it && ["artifact_view", "note"].includes(it.item_type)) {
          e.preventDefault();
          s.setActive(it.id); // Level 2
        }
      }
      if ((e.key === "Delete" || e.key === "Backspace") && !s.activeItemId) {
        e.preventDefault();
        deleteSelection();
      }
      if (e.shiftKey && e.code === "Digit1") {
        const b = itemBounds(board!.items);
        if (b) rf.fitBounds(b, { padding: 0.08, duration: 300 });
      }
      if (e.shiftKey && e.code === "Digit2" && selectedIds.size) {
        const b = itemBounds(board!.items.filter((i) => selectedIds.has(i.id)));
        if (b) rf.fitBounds(b, { padding: 0.25, duration: 300 });
      }
      if (mod && e.key.toLowerCase() === "d") {
        e.preventDefault();
        duplicateView();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  });

  const duplicateView = () => {
    const id = s.selection.kind === "items" ? s.selection.itemIds[0] : s.selection.kind === "component" ? s.selection.itemId : null;
    const src = id ? itemById(id) : undefined;
    if (!src || src.item_type !== "artifact_view") return;
    const views = board!.items.filter((i) => i.resource_ref.id === src.resource_ref.id).length;
    const copy: CanvasItem = {
      ...JSON.parse(JSON.stringify(src)),
      id: uid("item"),
      group_id: null,
      position: freeSpot(board!.items, src.size, src),
      presentation_state: { ...src.presentation_state, label: `view ${views + 1}`, selected_component: null },
    };
    s.updateBoard((b) => ({ ...b, items: [...b.items, copy] }));
    s.select({ kind: "items", itemIds: [copy.id] });
    s.notify("New view of the same artifact (shared revisions, independent camera)");
  };

  // ----------------------------------------------------------- drop from the explorer
  const onDrop = async (e: React.DragEvent) => {
    const raw = e.dataTransfer.getData("application/daedelus-ref");
    if (!raw || !board) return;
    e.preventDefault();
    const ref: ResourceRef = JSON.parse(raw);
    const p = rf.screenToFlowPosition({ x: e.clientX, y: e.clientY });
    const over = board.items.find(
      (a) => a.item_type === "artifact_view" && p.x >= a.position.x && p.x <= a.position.x + a.size.width && p.y >= a.position.y && p.y <= a.position.y + a.size.height,
    );
    if (ref.kind === "source" && over) {
      const comp = over.presentation_state.selected_component;
      if (!s.itemsFor(ref).length) await s.placeResource(ref, over.position.x - 300, p.y);
      setPending({
        sourceId: ref.id!,
        target: comp ? { scope: "component", artifact_id: over.resource_ref.id, component_id: comp } : { scope: "artifact", artifact_id: over.resource_ref.id },
      });
      return;
    }
    const it = await s.placeResource(ref, Math.round(p.x), Math.round(p.y));
    if (it) s.select({ kind: "items", itemIds: [it.id] });
  };

  if (!board) return <div className="empty">No board open.</div>;
  return (
    <div className="spatial" ref={wrapper} onDragOver={(e) => e.preventDefault()} onDrop={onDrop} data-testid="spatial-canvas">
      <EdgeMarkers />
      <ReactFlow
        nodes={nodes}
        edges={edges}
        nodeTypes={NODE_TYPES as any}
        edgeTypes={EDGE_TYPES}
        onNodesChange={onNodesChange}
        elevateNodesOnSelect={false}
        onNodeDragStart={() => (dragging.current = true)}
        onNodeDragStop={onNodeDragStop}
        onConnect={onConnect}
        isValidConnection={isValidConnection}
        onNodeClick={(e, n) => {
          if ((e.target as HTMLElement).closest(".nodrag, .target-chip")) return; // handled inside the view
          if (e.shiftKey || e.metaKey || e.ctrlKey) {
            const cur = s.selection.kind === "items" ? s.selection.itemIds : [];
            s.select({ kind: "items", itemIds: cur.includes(n.id) ? cur.filter((x) => x !== n.id) : [...cur, n.id] });
          } else s.select({ kind: "items", itemIds: [n.id] });
          if (s.activeItemId && s.activeItemId !== n.id) s.setActive(null);
        }}
        onNodeDoubleClick={(e, n) => {
          if ((e.target as HTMLElement).closest(".nodrag")) return;
          const it = itemById(n.id);
          if (it && ["artifact_view", "note"].includes(it.item_type)) s.setActive(n.id);
        }}
        onEdgeClick={(_, ed) => s.select({ kind: "connection", connectionId: ed.id })}
        onPaneClick={() => {
          s.select({ kind: "none" });
          s.setActive(null);
        }}
        onSelectionEnd={() => {
          const ids = rf.getNodes().filter((n) => n.selected).map((n) => n.id);
          if (ids.length) s.select({ kind: "items", itemIds: ids });
        }}
        onMoveEnd={(_, v) => {
          const cur = s.board?.viewport;
          if (cur && Math.abs(cur.x - v.x) < 0.5 && Math.abs(cur.y - v.y) < 0.5 && Math.abs(cur.zoom - v.zoom) < 1e-4) return;
          s.updateBoard((b) => ({ ...b, viewport: { x: v.x, y: v.y, zoom: v.zoom } }), { history: false });
        }}
        minZoom={0.05}
        maxZoom={4}
        zoomOnDoubleClick={false}
        deleteKeyCode={null}
        selectionKeyCode="Shift"
        multiSelectionKeyCode={["Meta", "Control"]}
        onlyRenderVisibleElements
        proOptions={{ hideAttribution: true }}
        colorMode="dark"
      >
        <Background gap={24} />
        <MiniMap pannable zoomable nodeColor={(n) => (n.type === "frame" || n.type === "workflow" ? "#2c3442" : n.type === "artifact_view" ? "#3f6ca8" : n.type === "source" ? "#a08033" : "#5a6070")} />
      </ReactFlow>
      <CanvasToolbar onDuplicate={duplicateView} />
      {pending && (
        <RelationshipDialog
          pending={pending}
          onClose={(bid) => {
            setPending(null);
            if (bid && pending.target.scope === "component")
              s.select({ kind: "component", itemId: null, artifactId: pending.target.artifact_id!, componentId: pending.target.component_id! });
          }}
        />
      )}
    </div>
  );
}
