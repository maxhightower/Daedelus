import { Handle, NodeResizer, Position, type NodeProps, type Node } from "@xyflow/react";
import { memo, useMemo, useState } from "react";
import { Badge, Status } from "../components/common";
import { useStudio } from "../state";
import type { CanvasItem } from "../types";
import { useHeadRevision, useLod } from "./hooks";
import { rendererFor, SourceBody } from "./renderers";

export type ItemNodeData = { item: CanvasItem; missing: boolean };
type P = NodeProps<Node<ItemNodeData>>;

const MIN = { artifact_view: [240, 170], source: [160, 120], operation: [180, 110], note: [140, 70], frame: [240, 160], workflow: [240, 160] } as const;

function Resizer({ item, selected }: { item: CanvasItem; selected: boolean }) {
  const { updateItem, board } = useStudio();
  const [mw, mh] = MIN[item.item_type];
  return (
    <NodeResizer
      isVisible={selected}
      minWidth={mw}
      minHeight={mh}
      lineClassName="resize-line"
      handleClassName="resize-handle"
      onResizeEnd={(_, p) => {
        const parent = item.group_id ? board?.items.find((i) => i.id === item.group_id) : null;
        updateItem(item.id, {
          size: { width: Math.round(p.width), height: Math.round(p.height) },
          position: { x: p.x + (parent?.position.x ?? 0), y: p.y + (parent?.position.y ?? 0) },
        });
      }}
    />
  );
}

function Missing({ item }: { item: CanvasItem }) {
  return (
    <div className="cnode missing">
      <div className="cnode-head">missing resource</div>
      <div className="cnode-body small">
        This view points at <code>{item.resource_ref.kind}:{item.resource_ref.id ?? item.resource_ref.node_id}</code>, which no longer exists. Remove the
        view with Delete (the board never deletes project data).
      </div>
    </div>
  );
}

// ------------------------------------------------------------------ artifact view
export const ArtifactViewNode = memo(function ArtifactViewNode({ data, selected }: P) {
  const { item, missing } = data;
  const s = useStudio();
  const lod = useLod();
  const artifact = s.artifacts.find((a) => a.id === item.resource_ref.id);
  const revision = useHeadRevision(artifact);
  if (missing || !artifact) return <Missing item={item} />;
  const renderer = rendererFor(artifact);
  const active = s.activeItemId === item.id;
  const focused = s.focusItemId === item.id;
  const sel: string | null = item.presentation_state.selected_component ?? null;
  const targets = Array.from(
    new Set([
      ...s.bindings.filter((b) => b.target.scope === "component" && b.target.artifact_id === artifact.id).map((b) => b.target.component_id!),
      ...(sel ? [sel] : []),
    ]),
  );
  const running = Object.values(s.executions).some(
    (e) => ["running", "pending"].includes(e.status) && e.node_runs.some((r) => r.status === "running" && r.units.some((u) => u.unit.startsWith(`artifact:${artifact.id}`))),
  );
  const mode = focused ? lod : active ? "active" : lod;
  const onSelectComponent = (cid: string | null) => {
    s.patchPresentation(item.id, { selected_component: cid });
    if (cid) s.select({ kind: "component", itemId: item.id, artifactId: artifact.id, componentId: cid });
    else s.select({ kind: "items", itemIds: [item.id] });
  };
  const compName = (cid: string) => artifact.components.find((c) => c.id === cid)?.name ?? cid;
  return (
    <div className={`cnode artifact ${selected ? "is-selected" : ""} ${active ? "is-active" : ""} ${focused ? "is-focused" : ""}`} data-testid={`item-${item.id}`}>
      <Resizer item={item} selected={selected && !active} />
      <Handle type="target" id="ref-in" position={Position.Left} className="h-ref" style={{ top: 18 }} />
      <Handle type="target" id="ann-in" position={Position.Top} className="h-ann" />
      <Handle type="target" id="dep-in" position={Position.Bottom} className="h-dep" style={{ left: "35%" }} />
      <Handle type="source" id="dep-out" position={Position.Bottom} className="h-dep" style={{ left: "65%" }} />
      <div className="cnode-head" title="drag to move · double-click to edit in place">
        <span className="kind-icon">{artifact.artifact_type === "model3d" ? "◆" : artifact.artifact_type === "image2d" ? "▣" : "{ }"}</span>
        <b>{artifact.name}</b>
        {item.presentation_state.label && <span className="muted"> · {item.presentation_state.label}</span>}
        <span className="spacer" />
        {revision && <Badge tone="info">r{revision.number}</Badge>}
        {running && <Badge tone="warn">updating…</Badge>}
        {active && <Badge tone="ok">editing</Badge>}
        <button className="mini-btn nodrag" title={active ? "Done (Esc)" : "Edit in place (double-click / Enter)"} onClick={() => s.setActive(active ? null : item.id)}>
          {active ? "Done" : "Edit"}
        </button>
        <button className="mini-btn nodrag" title="Focus editor" aria-label="Focus" onClick={() => s.setFocusItem(item.id)}>
          ⤢
        </button>
      </div>
      <div className="cnode-artifact">
        <div className="targets" title="components with reference bindings (connection anchors)">
          {targets.map((cid) => (
            <div
              key={cid}
              className={`target-chip ${cid === sel ? "sel" : ""}`}
              onClick={(e) => {
                e.stopPropagation();
                onSelectComponent(cid);
              }}
              title={`component ${cid}`}
            >
              <Handle type="target" id={`comp-in:${cid}`} position={Position.Left} className="h-ref" />
              {lod === "far" ? "•" : compName(cid)}
            </div>
          ))}
        </div>
        <div className={`cnode-body ${active ? "nodrag nowheel nopan" : "inert"}`}>
          <renderer.View
            item={item}
            artifact={artifact}
            revision={revision}
            mode={mode as any}
            selectedComponent={sel}
            onSelectComponent={onSelectComponent}
            patchState={(p) => s.patchPresentation(item.id, p)}
          />
        </div>
      </div>
      {lod !== "far" && (
        <div className="cnode-foot">
          {renderer.label}
          {renderer.componentPicking === "none" && " · component selection not supported"}
          {sel && ` · ${compName(sel)}`}
        </div>
      )}
    </div>
  );
});

// ------------------------------------------------------------------ source
export const SourceNode = memo(function SourceNode({ data, selected }: P) {
  const { item, missing } = data;
  const s = useStudio();
  const lod = useLod();
  const src = s.sources.find((x) => x.id === item.resource_ref.id);
  if (missing || !src) return <Missing item={item} />;
  const projectWide = s.bindings.filter((b) => b.source_id === src.id && b.target.scope === "project");
  const n = s.bindings.filter((b) => b.source_id === src.id).length;
  return (
    <div className={`cnode source ${selected ? "is-selected" : ""}`} data-testid={`item-${item.id}`}>
      <Resizer item={item} selected={selected} />
      <Handle type="target" id="ann-in" position={Position.Top} className="h-ann" />
      <Handle type="source" id="ref-out" position={Position.Right} className="h-ref" style={{ top: 18 }} title="drag onto an artifact or component to bind" />
      <div className="cnode-head">
        <span className="kind-icon">◉</span>
        <b title={src.name}>{src.name}</b>
        <span className="spacer" />
        <Badge>{src.media_type}</Badge>
      </div>
      <div className="cnode-body inert">
        <SourceBody s={src} lod={lod} />
      </div>
      {lod !== "far" && (
        <div className="cnode-foot">
          {n} binding{n === 1 ? "" : "s"}
          {projectWide.map((b) => (
            <Badge key={b.id} tone="info" title="bound to the whole project">
              project · {b.role}
            </Badge>
          ))}
        </div>
      )}
    </div>
  );
});

// ------------------------------------------------------------------ workflow operation
export const OperationNode = memo(function OperationNode({ data, selected }: P) {
  const { item, missing } = data;
  const s = useStudio();
  const lod = useLod();
  const wf = s.workflows.find((w) => w.id === item.resource_ref.workflow_id);
  const node = wf?.nodes.find((n) => n.id === item.resource_ref.node_id);
  const nt = s.nodeTypes.find((t) => t.type === node?.type);
  if (missing || !wf || !node || !nt) return <Missing item={item} />;
  const ex = s.executions[wf.id];
  const runInfo = ex && ex.workflow_version <= wf.version ? ex.node_runs.find((r) => r.node_id === node.id) : undefined;
  const status = runInfo?.status ?? "idle";
  const cfg = { ...nt.defaults, ...node.config };
  const art = node.type === "artifact" ? s.artifacts.find((a) => a.id === cfg.artifact_id) : null;
  const rows = Math.max(nt.inputs.length, nt.outputs.length, 1);
  return (
    <div className={`cnode operation cat-${nt.category} st-${status} ${selected ? "is-selected" : ""}`} data-testid={`item-${item.id}`}>
      <Resizer item={item} selected={selected} />
      <Handle type="target" id="ann-in" position={Position.Top} className="h-ann" />
      <div className="cnode-head">
        <span className="kind-icon">⚙</span>
        <b>{node.label || nt.title}</b>
        <span className="spacer" />
        <Status s={status} />
      </div>
      <div className="op-sub">
        {nt.title}
        {art && ` · ${art.name}`}
        {node.type === "agent" && ` · ${cfg.provider || s.project?.settings.default_provider}${cfg.target_component ? ` → ${cfg.target_component}` : ""}`}
      </div>
      <div className="op-ports" style={{ height: rows * 20 }}>
        {nt.inputs.map((p, i) => (
          <div key={p.name} className="port in" style={{ top: i * 20 }}>
            <Handle type="target" id={`in:${p.name}`} position={Position.Left} className={`h-port t-${p.type}`} />
            {p.name}
            {p.required ? "*" : ""}
          </div>
        ))}
        {nt.outputs.map((p, i) => (
          <div key={p.name} className="port out" style={{ top: i * 20 }}>
            {p.name}
            <Handle type="source" id={`out:${p.name}`} position={Position.Right} className={`h-port t-${p.type}`} />
          </div>
        ))}
      </div>
      {lod !== "far" && runInfo && runInfo.units.length > 0 && (
        <div className="op-units">
          {runInfo.units.map((u) => (
            <div key={u.unit} className={`unit status-${u.status}`}>
              {u.unit.split("#").pop()} · {u.status === "skipped" ? "up to date" : u.status}
            </div>
          ))}
        </div>
      )}
      {runInfo?.error && <div className="op-error">{runInfo.error.slice(0, 160)}</div>}
      {status === "waiting_approval" && <div className="op-approval">awaiting approval — see inspector</div>}
    </div>
  );
});

// ------------------------------------------------------------------ note
export const NoteNode = memo(function NoteNode({ data, selected }: P) {
  const { item } = data;
  const { updateItem, activeItemId, setActive } = useStudio();
  const editing = activeItemId === item.id;
  const [text, setText] = useState<string>(item.presentation_state.text ?? "");
  const color = item.presentation_state.color ?? "#4a4020";
  return (
    <div className={`cnode note ${selected ? "is-selected" : ""}`} style={{ background: color }} data-testid={`item-${item.id}`}>
      <Resizer item={item} selected={selected} />
      <Handle type="source" id="note-out" position={Position.Right} className="h-ann" />
      <Handle type="target" id="ann-in" position={Position.Top} className="h-ann" />
      <div className="cnode-head note-head">note</div>
      {editing ? (
        <textarea
          className="note-text nodrag nowheel"
          autoFocus
          value={text}
          onChange={(e) => setText(e.target.value)}
          onBlur={() => {
            updateItem(item.id, { presentation_state: { ...item.presentation_state, text } });
            setActive(null);
          }}
        />
      ) : (
        <div className="note-text">{item.presentation_state.text || <span className="muted">double-click to write</span>}</div>
      )}
    </div>
  );
});

// ------------------------------------------------------------------ frame / workflow group
export const FrameNode = memo(function FrameNode({ data, selected }: P) {
  const { item } = data;
  const s = useStudio();
  const wf = item.item_type === "workflow" ? s.workflows.find((w) => w.id === item.resource_ref.id) : null;
  const ex = wf ? s.executions[wf.id] : undefined;
  const [editing, setEditing] = useState(false);
  const title = wf ? `Workflow: ${wf.name}` : item.presentation_state.title ?? "Frame";
  const color = item.presentation_state.color ?? "#3f6ca8";
  const counts = useMemo(() => (s.board?.items ?? []).filter((i) => i.group_id === item.id).length, [s.board, item.id]);
  return (
    <div className={`cnode frame ${selected ? "is-selected" : ""}`} style={{ borderColor: color }} data-testid={`item-${item.id}`}>
      <Resizer item={item} selected={selected} />
      <div className="cnode-head frame-head" style={{ background: `${color}55` }} onDoubleClick={() => !wf && setEditing(true)}>
        {editing ? (
          <input
            className="nodrag"
            autoFocus
            defaultValue={title}
            onBlur={(e) => {
              s.updateItem(item.id, { presentation_state: { ...item.presentation_state, title: e.target.value } });
              setEditing(false);
            }}
            onKeyDown={(e) => e.key === "Enter" && (e.target as HTMLInputElement).blur()}
          />
        ) : (
          <b>{title}</b>
        )}
        <span className="muted small"> {counts} items</span>
        <span className="spacer" />
        {wf && <span className="muted small">v{wf.version}</span>}
        {ex && <Status s={ex.status} />}
      </div>
    </div>
  );
});

export const NODE_TYPES = {
  artifact_view: ArtifactViewNode,
  source: SourceNode,
  operation: OperationNode,
  note: NoteNode,
  frame: FrameNode,
  workflow: FrameNode,
};
