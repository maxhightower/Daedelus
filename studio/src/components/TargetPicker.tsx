import type { Artifact, TargetSelector } from "../types";

/** Hierarchical scope selector: project > artifact > component > sub-component. */
export function TargetPicker({
  value,
  onChange,
  artifacts,
}: {
  value: TargetSelector;
  onChange: (t: TargetSelector) => void;
  artifacts: Artifact[];
}) {
  const art = artifacts.find((a) => a.id === value.artifact_id);
  const depth = (cid: string): number => {
    let d = 0;
    let c = art?.components.find((x) => x.id === cid);
    while (c?.parent_id) {
      d++;
      c = art?.components.find((x) => x.id === c!.parent_id);
    }
    return d;
  };
  const ordered = () => {
    if (!art) return [];
    const out: string[] = [];
    const walk = (pid: string | null) =>
      art.components
        .filter((c) => (c.parent_id ?? null) === pid)
        .forEach((c) => {
          out.push(c.id);
          walk(c.id);
        });
    walk(null);
    return out;
  };
  return (
    <div className="target-picker">
      <select
        value={value.scope === "project" ? "" : value.artifact_id ?? ""}
        onChange={(e) =>
          onChange(e.target.value ? { scope: "artifact", artifact_id: e.target.value } : { scope: "project" })
        }
      >
        <option value="">Entire project</option>
        {artifacts.map((a) => (
          <option key={a.id} value={a.id}>
            {a.name} ({a.artifact_type})
          </option>
        ))}
      </select>
      {art && (
        <select
          value={value.scope === "component" ? value.component_id ?? "" : ""}
          onChange={(e) =>
            onChange(
              e.target.value
                ? { scope: "component", artifact_id: art.id, component_id: e.target.value }
                : { scope: "artifact", artifact_id: art.id },
            )
          }
        >
          <option value="">Whole artifact</option>
          {ordered().map((cid) => {
            const c = art.components.find((x) => x.id === cid)!;
            return (
              <option key={cid} value={cid}>
                {"  ".repeat(depth(cid))}
                {c.name} [{c.kind}]
              </option>
            );
          })}
        </select>
      )}
    </div>
  );
}
