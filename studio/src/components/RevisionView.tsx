import { api, fileUrl } from "../api";
import { invalidateRevision } from "../canvas/hooks";
import { useStudio } from "../state";
import type { Artifact, Revision } from "../types";
import { Badge, Json, Section, keyLabel, short } from "./common";

/** Evidence for one artifact revision: operations, results, attribution, validation. */
export function RevisionView({ a, rev, compact = false }: { a: Artifact; rev: Revision; compact?: boolean }) {
  const { project, run, refresh, select } = useStudio();
  if (!project) return null;
  const unchanged = Object.keys(rev.component_states).filter((c) => !rev.changed_components.includes(c));
  const img = rev.previews.render ?? rev.previews.composite;
  return (
    <div className="revision">
      <div className="row small">
        <b>r{rev.number}</b>
        <Badge tone={rev.origin === "manual" ? "warn" : rev.origin === "workflow" ? "info" : "default"}>{rev.origin ?? "—"}</Badge>
        <span>{rev.message}</span>
      </div>
      <div className="row small muted">
        {rev.created_at.slice(0, 19).replace("T", " ")}
        {rev.execution_id && (
          <button className="link" onClick={() => select({ kind: "resource", ref: { kind: "execution", id: rev.execution_id } })}>
            execution {short(rev.execution_id)}
          </button>
        )}
        {rev.vcs_commit && <code>git {rev.vcs_commit.slice(0, 10)}</code>}
      </div>
      {!compact && img && <img className="rev-thumb" src={fileUrl(project.id, img)} alt="" />}
      <div className="small">
        changed: {rev.changed_components.length ? rev.changed_components.map((c) => <Badge key={c} tone="warn">{c}</Badge>) : "—"}
        {rev.parent_revision_id && !compact && (
          <div>
            preserved: {unchanged.map((c) => <Badge key={c} tone="ok">{c}</Badge>)}
          </div>
        )}
      </div>
      <button
        className="mini-btn"
        onClick={async () => {
          const r = await run(api.restore(project.id, a.id, rev.id), `Restored r${rev.number} as a new revision (all views update)`);
          if (r) {
            invalidateRevision(r.id);
            refresh();
          }
        }}
      >
        Restore as new revision
      </button>
      {rev.operations.length > 0 && (
        <Section title={`Operations (${rev.operations.length})`} collapsed={compact}>
          <table className="grid small">
            <tbody>
              {rev.operations.map((o, i) => {
                const r = rev.operation_results.find((x) => x.index === i);
                return (
                  <tr key={i}>
                    <td>{o.op}</td>
                    <td>{o.component_id}</td>
                    <td>
                      <code>{JSON.stringify(o.params).slice(0, 120)}</code>
                    </td>
                    <td className="muted">{r?.status}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </Section>
      )}
      {rev.attribution.length > 0 && (
        <Section title="Attribution (declared by the planner)" collapsed>
          {rev.attribution.map((x, i) => (
            <div key={i} className={`small ${x.applied ? "" : "dim"}`}>
              <b>{x.source_name}</b> ({x.role}) → {keyLabel(x.unit, [a])}: {x.interpretation}
            </div>
          ))}
        </Section>
      )}
      {rev.validation && (
        <Section title={<>Validation {rev.validation.passed ? <Badge tone="ok">passed</Badge> : <Badge tone="err">failed</Badge>}</>} collapsed={compact}>
          {rev.validation.checks.map((c, i) => (
            <div key={i} className={`check ${c.passed ? "ok" : "fail"}`}>
              {c.passed ? "✔" : "✘"} <b>{c.name}</b> {c.detail}
            </div>
          ))}
        </Section>
      )}
      {rev.diff && (
        <Section title="Diff" collapsed>
          <pre className="json">{rev.diff}</pre>
        </Section>
      )}
      {!compact && (
        <Section title="Measurements" collapsed>
          <Json value={rev.measurements} />
        </Section>
      )}
    </div>
  );
}
