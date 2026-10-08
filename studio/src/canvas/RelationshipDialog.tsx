import { useState } from "react";
import { api } from "../api";
import { ASPECTS } from "../components/BindingEditor";
import { targetLabel } from "../components/common";
import { useStudio } from "../state";
import type { Constraint, TargetSelector } from "../types";

export interface PendingRelationship {
  sourceId: string;
  target: TargetSelector;
}

/** Configure a reference relationship; confirming creates a real backend SourceBinding. */
export function RelationshipDialog({ pending, onClose }: { pending: PendingRelationship; onClose: (bindingId?: string) => void }) {
  const { project, sources, artifacts, run, refresh, reloadBoard } = useStudio();
  const src = sources.find((s) => s.id === pending.sourceId);
  const [role, setRole] = useState("reference");
  const [aspects, setAspects] = useState<string[]>(["shape"]);
  const [strength, setStrength] = useState(0.8);
  const [hard, setHard] = useState(false);
  const [constraints, setConstraints] = useState<Constraint[]>([]);
  const [instructions, setInstructions] = useState("");
  if (!project || !src) return null;
  const roles = project.settings.roles;
  const confirm = async () => {
    const b = await run(
      api.createBinding(project.id, {
        source_id: src.id,
        role,
        aspects,
        target: pending.target,
        strength,
        constraint: hard ? "hard" : "soft",
        constraints,
        instructions,
      }),
      "Relationship created (source binding saved)",
    );
    if (b) {
      await refresh();
      await reloadBoard();
      onClose(b.id);
    }
  };
  return (
    <div className="modal-backdrop" onMouseDown={() => onClose()}>
      <div className="modal" role="dialog" aria-label="Configure relationship" onMouseDown={(e) => e.stopPropagation()}>
        <h3>New reference relationship</h3>
        <div className="rel-summary">
          <div>
            <span className="muted">Source</span> <b>{src.name}</b> <span className="muted small">({src.media_type}, {src.processing.state})</span>
          </div>
          <div>
            <span className="muted">Target</span> <b data-testid="rel-target">{targetLabel(pending.target, artifacts)}</b>
            {pending.target.component_id && <code>{pending.target.component_id}</code>}
          </div>
        </div>
        <div className="form-grid">
          <label>Role</label>
          <div className="row">
            <input list="rel-roles" value={role} onChange={(e) => setRole(e.target.value)} aria-label="role" />
            <datalist id="rel-roles">
              {roles.map((r) => (
                <option key={r.name} value={r.name}>
                  {r.use}
                </option>
              ))}
            </datalist>
            <span className="muted small">{roles.find((r) => r.name === role)?.description ?? "custom role (treated as context until profiled)"}</span>
          </div>
          <label>Influences</label>
          <div className="chips">
            {ASPECTS.map((a) => (
              <button key={a} className={`chip ${aspects.includes(a) ? "on" : ""}`} onClick={() => setAspects(aspects.includes(a) ? aspects.filter((x) => x !== a) : [...aspects, a])}>
                {a}
              </button>
            ))}
          </div>
          <label>Strength</label>
          <div className="row">
            <input type="range" min={0} max={1} step={0.05} value={strength} onChange={(e) => setStrength(+e.target.value)} aria-label="strength" />
            <span>{strength.toFixed(2)}</span>
            <label className="inline">
              <input type="checkbox" checked={hard} onChange={(e) => setHard(e.target.checked)} /> hard constraint
            </label>
          </div>
          <label>Constraints</label>
          <div>
            {constraints.map((c, i) => (
              <div className="row" key={i}>
                <input className="small-input" value={c.property} onChange={(e) => setConstraints(constraints.map((x, j) => (j === i ? { ...x, property: e.target.value } : x)))} />
                <select value={c.op} onChange={(e) => setConstraints(constraints.map((x, j) => (j === i ? { ...x, op: e.target.value as any } : x)))}>
                  {["preserve", "eq", "lte", "gte"].map((o) => (
                    <option key={o}>{o}</option>
                  ))}
                </select>
                {c.op !== "preserve" && (
                  <input className="num" value={c.value ?? ""} onChange={(e) => setConstraints(constraints.map((x, j) => (j === i ? { ...x, value: +e.target.value } : x)))} />
                )}
                <button className="link" onClick={() => setConstraints(constraints.filter((_, j) => j !== i))}>
                  remove
                </button>
              </div>
            ))}
            <button className="link" onClick={() => setConstraints([...constraints, { property: "height", op: "preserve", tolerance: 0.001 }])}>
              + preserve height (or other measurable property)
            </button>
          </div>
          <label>Instructions</label>
          <textarea rows={2} value={instructions} onChange={(e) => setInstructions(e.target.value)} placeholder="optional; key: value lines are machine-readable" />
        </div>
        <div className="row end">
          <button onClick={() => onClose()}>Cancel</button>
          <button className="primary" onClick={confirm} disabled={!role || aspects.length === 0}>
            Create binding
          </button>
        </div>
      </div>
    </div>
  );
}
