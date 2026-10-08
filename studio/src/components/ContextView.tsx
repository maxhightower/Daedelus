import type { Artifact, ResolvedContext } from "../types";
import { Badge, keyLabel } from "./common";

/** One segment of a scope path: just that level's own name (the path supplies the hierarchy). */
function segmentLabel(key: string, artifacts: Artifact[]) {
  const m = key.match(/^artifact:([^#]+)(?:#(.+))?$/);
  if (!m) return keyLabel(key, artifacts);
  const a = artifacts.find((x) => x.id === m[1]);
  if (!a) return keyLabel(key, artifacts);
  return m[2] ? (a.components.find((c) => c.id === m[2])?.name ?? m[2]) : a.name;
}

/** Inspectable view of what a target receives from the source library. */
export function ContextView({ ctx, artifacts }: { ctx: ResolvedContext; artifacts: Artifact[] }) {
  return (
    <div className="context">
      <div className="muted small">
        Scope path: {ctx.target_path.map((k) => segmentLabel(k, artifacts)).join("  ›  ")}
      </div>
      {ctx.conflicts.length > 0 && (
        <div className="conflicts">
          {ctx.conflicts.map((c, i) => (
            <div key={i} className={c.resolved ? "conflict resolved" : "conflict"}>
              <b>{c.resolved ? "Resolved conflict" : "UNRESOLVED CONFLICT"}</b> on <code>{c.property}</code>: {c.detail}
              {c.resolution && <div className="muted">→ {c.resolution}</div>}
            </div>
          ))}
        </div>
      )}
      {ctx.warnings.map((w, i) => (
        <div key={i} className="warning">
          {w}
        </div>
      ))}
      <table className="grid">
        <thead>
          <tr>
            <th>Source</th>
            <th>Role</th>
            <th>Aspects</th>
            <th>Relation</th>
            <th>Weight</th>
            <th>Applies</th>
            <th>Notes</th>
          </tr>
        </thead>
        <tbody>
          {ctx.entries.map((e) => (
            <tr key={e.binding.id} className={e.applies ? "" : "dim"}>
              <td>
                {e.source_name}
                <div className="muted small">
                  {e.media_type} · {e.processing_state}
                </div>
              </td>
              <td>
                {e.binding.role} <span className="muted small">({e.role_use})</span>
                {e.binding.constraint === "hard" && <Badge tone="warn">hard</Badge>}
              </td>
              <td>{(e.relation === "inherited" ? e.cascading_aspects : e.binding.aspects).join(", ") || "—"}</td>
              <td>
                <Badge tone={e.relation === "self" ? "ok" : e.relation === "inherited" ? "info" : "default"}>
                  {e.relation}
                </Badge>
                <div className="muted small">{keyLabel(e.anchor, artifacts)}</div>
              </td>
              <td>{e.effective_weight}</td>
              <td>{e.applies ? "yes" : "no"}</td>
              <td className="small">{e.notes.join("; ")}</td>
            </tr>
          ))}
        </tbody>
      </table>
      {ctx.constraints.length > 0 && (
        <div className="small">
          <b>Constraints:</b>
          <ul>
            {ctx.constraints.map((c, i) => (
              <li key={i} className={c.active ? "" : "dim"}>
                {c.hard ? "hard" : "soft"}: {c.constraint.property} {c.constraint.op}{" "}
                {c.constraint.value ?? ""} — from “{c.source}”{c.active ? "" : " (inactive)"}
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}
