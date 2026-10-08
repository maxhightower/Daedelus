import { useEffect, useState } from "react";
import { api } from "../api";
import { useStudio } from "../state";
import type { Binding, Constraint } from "../types";
import { TargetPicker } from "./TargetPicker";

export const ASPECTS = [
  "geometry", "proportions", "shape", "color", "palette", "material", "texture", "lighting",
  "mood", "style", "composition", "content", "technique", "layout", "code", "code_style",
  "convention", "data", "manifest",
];

const EMPTY: Partial<Binding> = {
  role: "reference",
  aspects: [],
  target: { scope: "project" },
  instructions: "",
  priority: 0,
  strength: 0.6,
  constraint: "soft",
  constraints: [],
  allow_override: false,
  exclusions: [],
  enabled: true,
  segment: null,
};

/** Create or edit one source binding: role, aspects, scope, strength, constraints. */
export function BindingEditor({
  sourceId,
  binding,
  initialTarget,
  onDone,
}: {
  sourceId: string;
  binding?: Binding;
  initialTarget?: Binding["target"];
  onDone?: () => void;
}) {
  const { project, artifacts, run, refresh } = useStudio();
  const fresh = (): Partial<Binding> => ({ ...EMPTY, source_id: sourceId, target: initialTarget ?? EMPTY.target });
  const [b, setB] = useState<Partial<Binding>>(binding ?? fresh());
  const [aspectText, setAspectText] = useState("");
  const [scope, setScope] = useState<any>(null);
  // eslint-disable-next-line react-hooks/exhaustive-deps
  useEffect(() => setB(binding ?? fresh()), [binding, sourceId]);
  useEffect(() => {
    if (binding && project) api.bindingScope(project.id, binding.id).then(setScope).catch(() => setScope(null));
  }, [binding, project]);
  if (!project) return null;
  const roles = project.settings.roles;
  const set = (patch: Partial<Binding>) => setB((x) => ({ ...x, ...patch }));
  const art = artifacts.find((a) => a.id === b.target?.artifact_id);
  const toggleAspect = (a: string) =>
    set({ aspects: b.aspects?.includes(a) ? b.aspects.filter((x) => x !== a) : [...(b.aspects ?? []), a] });
  const setCons = (i: number, patch: Partial<Constraint>) =>
    set({ constraints: (b.constraints ?? []).map((c, j) => (j === i ? { ...c, ...patch } : c)) });

  const save = async () => {
    const body = { ...b, source_id: sourceId };
    const r = binding
      ? await run(api.patchBinding(project.id, binding.id, body), "Binding updated")
      : await run(api.createBinding(project.id, body), "Binding created");
    if (r) {
      await refresh();
      onDone?.();
    }
  };
  const remove = async () => {
    if (!binding) return;
    if (await run(api.deleteBinding(project.id, binding.id), "Binding deleted")) {
      await refresh();
      onDone?.();
    }
  };

  return (
    <div className="binding-editor">
      <div className="form-grid">
        <label>Role</label>
        <div className="row">
          <input
            list="role-list"
            value={b.role ?? ""}
            onChange={(e) => set({ role: e.target.value })}
            placeholder="reference, inspiration, guideline, … or your own"
          />
          <datalist id="role-list">
            {roles.map((r) => (
              <option key={r.name} value={r.name}>
                {r.use} — {r.description}
              </option>
            ))}
          </datalist>
          <span className="muted small">
            {roles.find((r) => r.name === b.role)?.use ?? "no profile → context only"}
          </span>
        </div>
        <label>Influences</label>
        <div className="chips">
          {ASPECTS.map((a) => (
            <button key={a} className={`chip ${b.aspects?.includes(a) ? "on" : ""}`} onClick={() => toggleAspect(a)}>
              {a}
            </button>
          ))}
          {(b.aspects ?? []).filter((a) => !ASPECTS.includes(a)).map((a) => (
            <button key={a} className="chip on" onClick={() => toggleAspect(a)}>
              {a}
            </button>
          ))}
          <input
            className="small-input"
            placeholder="+ aspect"
            value={aspectText}
            onChange={(e) => setAspectText(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && aspectText.trim()) {
                toggleAspect(aspectText.trim());
                setAspectText("");
              }
            }}
          />
        </div>
        <label>Target</label>
        <TargetPicker value={b.target ?? { scope: "project" }} onChange={(t) => set({ target: t, exclusions: [] })} artifacts={artifacts} />
        {art && (
          <>
            <label>Exclude</label>
            <div className="chips">
              {art.components
                .filter((c) => c.id !== b.target?.component_id)
                .map((c) => (
                  <button
                    key={c.id}
                    className={`chip ${b.exclusions?.includes(c.id) ? "on warn" : ""}`}
                    onClick={() =>
                      set({
                        exclusions: b.exclusions?.includes(c.id)
                          ? b.exclusions.filter((x) => x !== c.id)
                          : [...(b.exclusions ?? []), c.id],
                      })
                    }
                  >
                    {c.name}
                  </button>
                ))}
            </div>
          </>
        )}
        <label>Strength</label>
        <div className="row">
          <input type="range" min={0} max={1} step={0.05} value={b.strength ?? 0.6} onChange={(e) => set({ strength: +e.target.value })} />
          <span>{(b.strength ?? 0).toFixed(2)}</span>
          <label className="inline">
            Priority <input type="number" className="num" value={b.priority ?? 0} onChange={(e) => set({ priority: +e.target.value })} />
          </label>
        </div>
        <label>Constraint</label>
        <div className="row">
          <select value={b.constraint} onChange={(e) => set({ constraint: e.target.value as any })}>
            <option value="soft">soft (may be overridden)</option>
            <option value="hard">hard (must hold)</option>
          </select>
          <label className="inline">
            <input type="checkbox" checked={!!b.allow_override} onChange={(e) => set({ allow_override: e.target.checked })} /> allow more specific override
          </label>
          <label className="inline">
            <input type="checkbox" checked={b.enabled !== false} onChange={(e) => set({ enabled: e.target.checked })} /> enabled
          </label>
        </div>
        <label>Checks</label>
        <div>
          {(b.constraints ?? []).map((c, i) => (
            <div className="row" key={i}>
              <input value={c.property} onChange={(e) => setCons(i, { property: e.target.value })} placeholder="height" className="small-input" />
              <select value={c.op} onChange={(e) => setCons(i, { op: e.target.value as any })}>
                {["preserve", "eq", "lte", "gte", "forbid_change"].map((o) => (
                  <option key={o}>{o}</option>
                ))}
              </select>
              {c.op !== "preserve" && c.op !== "forbid_change" && (
                <input className="num" value={c.value ?? ""} onChange={(e) => setCons(i, { value: e.target.value === "" ? null : +e.target.value })} />
              )}
              <button className="link" onClick={() => set({ constraints: b.constraints!.filter((_, j) => j !== i) })}>
                remove
              </button>
            </div>
          ))}
          <button className="link" onClick={() => set({ constraints: [...(b.constraints ?? []), { property: "height", op: "preserve", tolerance: 0.001 }] })}>
            + add measurable constraint
          </button>
        </div>
        <label>Segment</label>
        <div className="row">
          <select
            value={b.segment?.kind ?? ""}
            onChange={(e) => set({ segment: e.target.value ? { kind: e.target.value as any } : null })}
          >
            <option value="">whole source</option>
            <option value="time">time range</option>
            <option value="page">page</option>
            <option value="region">region</option>
          </select>
          {b.segment?.kind === "time" && (
            <>
              <input className="num" placeholder="start s" value={b.segment.start ?? ""} onChange={(e) => set({ segment: { ...b.segment!, start: +e.target.value } })} />
              <input className="num" placeholder="end s" value={b.segment.end ?? ""} onChange={(e) => set({ segment: { ...b.segment!, end: +e.target.value } })} />
            </>
          )}
          {b.segment?.kind === "page" && (
            <input className="num" placeholder="page" value={b.segment.page ?? ""} onChange={(e) => set({ segment: { ...b.segment!, page: +e.target.value } })} />
          )}
        </div>
        <label>Instructions</label>
        <textarea rows={2} value={b.instructions ?? ""} onChange={(e) => set({ instructions: e.target.value })} placeholder="Binding-specific direction (key: value lines are machine-readable)" />
      </div>
      <div className="row end">
        {scope && (
          <span className="muted small">
            In scope: {scope.affected.map((x: any) => `${x.artifact} (${x.components.length} comp.)`).join(", ") || "nothing"}
          </span>
        )}
        {binding && (
          <button className="danger" onClick={remove}>
            Delete
          </button>
        )}
        <button className="primary" onClick={save} disabled={!b.role}>
          {binding ? "Save binding" : "Add binding"}
        </button>
      </div>
    </div>
  );
}
