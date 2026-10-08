import { BaseEdge, EdgeLabelRenderer, getBezierPath, type Edge, type EdgeProps } from "@xyflow/react";
import type { CanvasConnection } from "../types";

export type TypedEdgeData = { conn: CanvasConnection; dim: boolean };

/** Connection semantics are encoded in stroke pattern + marker + label, not colour alone. */
const STYLE: Record<string, { stroke: string; dash?: string; width: number; marker?: string }> = {
  reference: { stroke: "#e3b341", dash: "7 4", width: 2, marker: "url(#mk-ref)" },
  execution: { stroke: "#7bc86c", width: 2.2, marker: "url(#mk-exe)" },
  dependency: { stroke: "#9aa5b1", dash: "2 5", width: 1.6, marker: "url(#mk-dep)" },
  annotation: { stroke: "#c79bd8", dash: "1 3", width: 1.2 },
};
const PREFIX: Record<string, string> = { reference: "ref", execution: "exec", dependency: "depends on", annotation: "note" };

export function TypedEdge({ id, sourceX, sourceY, targetX, targetY, sourcePosition, targetPosition, data, selected }: EdgeProps<Edge<TypedEdgeData>>) {
  const conn = data!.conn;
  const st = STYLE[conn.connection_type];
  const [path, lx, ly] = getBezierPath({ sourceX, sourceY, targetX, targetY, sourcePosition, targetPosition });
  const disabled = conn.presentation_state.enabled === false;
  const stale = conn.connection_type === "dependency" && conn.presentation_state.stale;
  const label = conn.presentation_state.label ? `${PREFIX[conn.connection_type]}: ${conn.presentation_state.label}` : PREFIX[conn.connection_type];
  return (
    <>
      <BaseEdge
        id={id}
        path={path}
        markerEnd={st.marker}
        style={{
          stroke: stale ? "#e0a030" : st.stroke,
          strokeDasharray: st.dash,
          strokeWidth: selected ? st.width + 1.5 : st.width,
          opacity: data!.dim ? 0.15 : disabled ? 0.4 : 0.95,
        }}
      />
      {!data!.dim && (
        <EdgeLabelRenderer>
          <div
            className={`edge-label t-${conn.connection_type} ${selected ? "sel" : ""} ${stale ? "stale" : ""}`}
            data-status={conn.presentation_state.status}
            style={{ transform: `translate(-50%, -50%) translate(${lx}px,${ly}px)` }}
          >
            {label}
            {stale ? ` · ${String(conn.presentation_state.status).replace("_", " ")}` : ""}
            {conn.presentation_state.hard && " · hard"}
            {conn.target_anchor.component_id && ` → ${conn.target_anchor.component_id}`}
          </div>
        </EdgeLabelRenderer>
      )}
    </>
  );
}

export function EdgeMarkers() {
  return (
    <svg style={{ position: "absolute", width: 0, height: 0 }}>
      <defs>
        <marker id="mk-ref" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
          <circle cx="5" cy="5" r="4" fill="#e3b341" />
        </marker>
        <marker id="mk-exe" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
          <path d="M0,0 L10,5 L0,10 z" fill="#7bc86c" />
        </marker>
        <marker id="mk-dep" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
          <path d="M0,0 L10,5 L0,10" fill="none" stroke="#9aa5b1" strokeWidth="1.5" />
        </marker>
      </defs>
    </svg>
  );
}

export const EDGE_TYPES = { typed: TypedEdge };
