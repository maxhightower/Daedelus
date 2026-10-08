import { useEffect, useState } from "react";
import { api } from "../api";
import { Badge, Empty, Json, Section, Status, keyLabel, short } from "../components/common";
import { ContextView } from "../components/ContextView";
import { useStudio } from "../state";
import type { Execution, ExecutionSummary, ResolvedContext } from "../types";

function UnitContext({ cid }: { cid: string }) {
  const { project, artifacts } = useStudio();
  const [ctx, setCtx] = useState<ResolvedContext | null>(null);
  useEffect(() => {
    if (project) api.context(project.id, cid).then(setCtx);
  }, [project, cid]);
  return ctx ? <ContextView ctx={ctx} artifacts={artifacts} /> : <div className="muted">…</div>;
}

export function ExecutionsPanel() {
  const { project, artifacts, workflows, focus, setFocus, run, refresh } = useStudio();
  const [list, setList] = useState<ExecutionSummary[]>([]);
  const [ex, setEx] = useState<Execution | null>(null);
  const [replay, setReplay] = useState<any>(null);
  const [note, setNote] = useState("");
  const eid = focus.execution ?? list[0]?.id;

  useEffect(() => {
    if (!project) return;
    let t: any;
    const tick = async () => {
      const l = await api.executions(project.id).catch(() => []);
      setList(l);
      if (l.some((e) => ["pending", "running"].includes(e.status))) t = setTimeout(tick, 1500);
    };
    tick();
    return () => clearTimeout(t);
  }, [project, ex?.status]);

  useEffect(() => {
    if (!project || !eid) return;
    let t: any;
    let alive = true;
    const tick = async () => {
      const e = await api.execution(project.id, eid).catch(() => null);
      if (!alive || !e) return;
      setEx(e);
      if (["pending", "running"].includes(e.status)) t = setTimeout(tick, 1000);
      else refresh();
    };
    setReplay(null);
    tick();
    return () => {
      alive = false;
      clearTimeout(t);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [project, eid]);

  if (!project) return <Empty>Select a project.</Empty>;
  const wfName = (id: string) => workflows.find((w) => w.id === id)?.name ?? id;
  return (
    <div className="split">
      <div className="list-col">
        {list.length === 0 && <Empty>No executions yet. Run a workflow.</Empty>}
        {list.map((e) => (
          <div key={e.id} className={`rev-row ${e.id === eid ? "selected" : ""}`} onClick={() => setFocus("execution", e.id)}>
            <Status s={e.status} /> <b>{wfName(e.workflow_id)}</b> v{e.workflow_version} <span className="muted small">{e.mode}</span>
            <div className="muted small">
              {e.created_at.slice(5, 19).replace("T", " ")} · {short(e.id)}
            </div>
          </div>
        ))}
      </div>
      <div className="detail-col">
        {ex && (
          <>
            <h2>
              Execution {short(ex.id)} <Status s={ex.status} />
            </h2>
            <div className="muted small">
              {wfName(ex.workflow_id)} v{ex.workflow_version} · mode {ex.mode} · started {ex.started_at?.slice(11, 19)} · finished{" "}
              {ex.finished_at?.slice(11, 19) ?? "—"}
            </div>
            {ex.error && <div className="error-box">{ex.error}</div>}
            <div className="row">
              {["pending", "running"].includes(ex.status) && <button onClick={() => run(api.cancel(project.id, ex.id), "Cancelling")}>Cancel</button>}
              <button
                disabled={["pending", "running", "waiting_approval"].includes(ex.status)}
                onClick={async () => setReplay(await run(api.replay(project.id, ex.id)))}
                title="Re-plan from saved plan requests and re-apply saved operations onto the parent revisions"
              >
                Reproduce (replay)
              </button>
            </div>
            {replay && (
              <div className={replay.reproducible ? "ok-box" : "error-box"}>
                {replay.results.length === 0
                  ? "No revisions were produced by this execution."
                  : replay.reproducible
                    ? "Reproduced: identical operations and component states."
                    : "Not reproducible:"}
                {replay.results.map((r: any, i: number) => (
                  <div key={i} className="small">
                    {r.artifact} r{r.revision}: {r.detail} {r.operations_match === false && "(operations differ)"}
                    {r.operations_match === null && "(non-deterministic planner: operations not compared)"}
                  </div>
                ))}
              </div>
            )}
            <table className="grid">
              <thead>
                <tr>
                  <th>Node</th>
                  <th>Status</th>
                  <th>Units</th>
                  <th>Detail</th>
                </tr>
              </thead>
              <tbody>
                {ex.node_runs.map((r) => (
                  <tr key={r.node_id}>
                    <td>
                      {r.node_id} <span className="muted small">{r.node_type}</span>
                    </td>
                    <td>
                      <Status s={r.status} />
                      {r.attempts > 1 && <span className="muted small"> {r.attempts} attempts</span>}
                    </td>
                    <td className="small">
                      {r.units.map((u) => (
                        <div key={u.unit}>
                          <Status s={u.status} /> {keyLabel(u.unit, artifacts)}
                          <span className="muted"> — {u.reason}</span>
                          {u.error && <div className="error-box">{u.error}</div>}
                        </div>
                      ))}
                    </td>
                    <td className="small">
                      {r.error && <div className="error-box">{r.error}</div>}
                      {r.outputs.revision?.revision_number && (
                        <button className="link" onClick={() => (setFocus("artifact", r.outputs.revision.artifact_id), refresh())}>
                          → revision {r.outputs.revision.revision_number}
                        </button>
                      )}
                      {r.outputs.report && (r.outputs.report.passed ? <Badge tone="ok">checks passed</Badge> : <Badge tone="err">checks failed</Badge>)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            {ex.node_runs
              .filter((r) => r.status === "waiting_approval")
              .map((r) => (
                <div key={r.node_id} className="approval">
                  <h3>Approval required: {r.node_id}</h3>
                  <p>{r.approval?.message}</p>
                  {r.approval?.pending && (
                    <table className="grid small">
                      <tbody>
                        {r.approval.pending.ops.map(([unit, o]: any, i: number) => (
                          <tr key={i}>
                            <td>{keyLabel(unit, artifacts)}</td>
                            <td>{o.op}</td>
                            <td>{o.component_id}</td>
                            <td>
                              <code>{JSON.stringify(o.params)}</code>
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  )}
                  <input value={note} onChange={(e) => setNote(e.target.value)} placeholder="note (optional)" />
                  <button className="primary" onClick={() => run(api.decide(project.id, ex.id, r.node_id, true, note), "Approved").then(() => setEx({ ...ex, status: "running" }))}>
                    Approve
                  </button>
                  <button className="danger" onClick={() => run(api.decide(project.id, ex.id, r.node_id, false, note), "Rejected").then(() => setEx({ ...ex, status: "running" }))}>
                    Reject
                  </button>
                </div>
              ))}
            {ex.node_runs
              .filter((r) => r.units.length || r.logs.length || Object.keys(r.outputs).length)
              .map((r) => (
                <Section key={r.node_id} title={`${r.node_id}: details`} collapsed>
                  {r.units.map((u) => (
                    <div key={u.unit} className="unit-detail">
                      <b>{keyLabel(u.unit, artifacts)}</b> <Status s={u.status} />{" "}
                      <span className="muted small">
                        fingerprint {u.fingerprint?.slice(0, 12)} (prev {u.previous_fingerprint?.slice(0, 12) ?? "none"})
                      </span>
                      {u.plan && (
                        <>
                          <div className="small">
                            planner <b>{u.plan.provider}</b> {u.plan.model} {u.plan.deterministic && <Badge tone="ok">deterministic</Badge>}
                          </div>
                          {u.plan.warnings?.map((w: string, i: number) => (
                            <div key={i} className="warning">
                              {w}
                            </div>
                          ))}
                          <Json value={u.plan.operations} max={180} />
                        </>
                      )}
                      {u.context_id && (
                        <Section title="Resolved inputs presented for this unit" collapsed>
                          <UnitContext cid={u.context_id} />
                        </Section>
                      )}
                    </div>
                  ))}
                  {r.logs.length > 0 && <pre className="json">{r.logs.join("\n")}</pre>}
                  <Json value={r.outputs} max={240} />
                </Section>
              ))}
          </>
        )}
      </div>
    </div>
  );
}
