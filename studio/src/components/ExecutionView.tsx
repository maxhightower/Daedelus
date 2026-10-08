import { useEffect, useState } from "react";
import { api } from "../api";
import { useStudio } from "../state";
import type { Execution, NodeRun } from "../types";
import { Badge, Json, Section, Status, keyLabel, short } from "./common";
import { ContextView } from "./ContextView";
import { NodeExecutionInfo } from "./ExecutionSettings";

export function ApprovalBox({ ex, r }: { ex: Execution; r: NodeRun }) {
  const { project, artifacts, run, watchExecution } = useStudio();
  const [note, setNote] = useState("");
  if (!project) return null;
  const decide = async (approve: boolean) => {
    if (await run(api.decide(project.id, ex.id, r.node_id, approve, note), approve ? "Approved" : "Rejected")) watchExecution(ex.workflow_id);
  };
  return (
    <div className="approval">
      <b>Approval required: {r.node_id}</b>
      <div className="small">{r.approval?.message}</div>
      {r.approval?.pending && (
        <table className="grid small">
          <tbody>
            {r.approval.pending.ops.map(([unit, o]: any, i: number) => (
              <tr key={i}>
                <td>{keyLabel(unit, artifacts)}</td>
                <td>{o.op}</td>
                <td>
                  <code>{JSON.stringify(o.params).slice(0, 100)}</code>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      <input value={note} onChange={(e) => setNote(e.target.value)} placeholder="note (optional)" />
      <div className="row">
        <button className="primary" onClick={() => decide(true)}>
          Approve
        </button>
        <button className="danger" onClick={() => decide(false)}>
          Reject
        </button>
      </div>
    </div>
  );
}

function UnitContext({ cid }: { cid: string }) {
  const { project, artifacts } = useStudio();
  const [ctx, setCtx] = useState<any>(null);
  useEffect(() => {
    if (project) api.context(project.id, cid).then(setCtx);
  }, [project, cid]);
  return ctx ? <ContextView ctx={ctx} artifacts={artifacts} /> : <div className="muted">…</div>;
}

/** Execution details: node and unit status, errors, approvals, replay. */
export function ExecutionView({ executionId }: { executionId: string }) {
  const { project, artifacts, workflows, run } = useStudio();
  const [ex, setEx] = useState<Execution | null>(null);
  const [replay, setReplay] = useState<any>(null);
  useEffect(() => {
    if (!project) return;
    let t: any;
    let alive = true;
    const tick = async () => {
      const e = await api.execution(project.id, executionId).catch(() => null);
      if (!alive || !e) return;
      setEx(e);
      if (["pending", "running"].includes(e.status)) t = setTimeout(tick, 1000);
    };
    tick();
    return () => {
      alive = false;
      clearTimeout(t);
    };
  }, [project, executionId]);
  if (!ex || !project) return <div className="muted">loading execution…</div>;
  const wf = workflows.find((w) => w.id === ex.workflow_id);
  return (
    <div>
      <h3>
        Execution {short(ex.id)} <Status s={ex.status} />
      </h3>
      <div className="muted small">
        {wf?.name} v{ex.workflow_version} · {ex.mode} · {ex.created_at.slice(0, 19).replace("T", " ")}
      </div>
      {ex.error && <div className="error-box">{ex.error}</div>}
      {ex.node_runs
        .filter((r) => r.status === "waiting_approval")
        .map((r) => (
          <ApprovalBox key={r.node_id} ex={ex} r={r} />
        ))}
      <button disabled={["pending", "running", "waiting_approval"].includes(ex.status)} onClick={async () => setReplay(await run(api.replay(project.id, ex.id)))}>
        Reproduce (replay)
      </button>
      {replay && (
        <div className={replay.reproducible ? "ok-box" : "error-box"}>
          {replay.results.length === 0 ? "No revisions were produced by this execution." : replay.reproducible ? "Reproduced: identical operations and component states." : "Not reproducible."}
        </div>
      )}
      {ex.node_runs.map((r) => (
        <Section key={r.node_id} title={<>{r.node_id} <Status s={r.status} /></>} collapsed={!r.units.length && !r.error}>
          {r.error && <div className="error-box">{r.error}</div>}
          <NodeExecutionInfo info={(r.outputs as any)?.execution} />
          {r.units.map((u) => (
            <div key={u.unit} className="unit-detail small">
              <Status s={u.status} /> {keyLabel(u.unit, artifacts)} <span className="muted">— {u.reason}</span>
              {u.error && <div className="error-box">{u.error}</div>}
              {u.plan && <Json value={u.plan.operations} max={140} />}
              {u.context_id && (
                <Section title="Resolved inputs" collapsed>
                  <UnitContext cid={u.context_id} />
                </Section>
              )}
            </div>
          ))}
          {r.outputs.revision?.revision_number && <Badge tone="ok">→ revision {r.outputs.revision.revision_number}</Badge>}
          {r.logs.length > 0 && (
            <Section title="Logs" collapsed>
              <pre className="json">{r.logs.join("\n")}</pre>
            </Section>
          )}
        </Section>
      ))}
    </div>
  );
}
