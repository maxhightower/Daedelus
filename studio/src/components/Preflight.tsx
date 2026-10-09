import { useState } from "react";
import { api, type Preflight } from "../api";
import { useStudio } from "../state";
import type { ExecutionBudget } from "../types";
import { Badge } from "./common";

/** Run a workflow, first surfacing where work will execute and which models it may call
 * (V2.1): when live models or remote workers are involved the user confirms the summary. */
export function useRunWithPreflight() {
  const s = useStudio();
  const [pending, setPending] = useState<{ wid: string; mode: string; pf: Preflight } | null>(null);
  const go = async (wid: string, mode: string) => {
    const e = await s.run(api.execute(s.project!.id, wid, mode), `Execution started (${mode})`);
    if (e) s.watchExecution(wid);
  };
  const start = async (wid: string, mode = "incremental") => {
    if (!s.project) return;
    const pf = await api.preflight(s.project.id, wid).catch(() => null);
    if (pf && pf.needs_confirmation) setPending({ wid, mode, pf });
    else await go(wid, mode);
  };
  const box = pending ? (
    <PreflightBox
      pf={pending.pf}
      onConfirm={async () => {
        const p = pending;
        setPending(null);
        await go(p.wid, p.mode);
      }}
      onCancel={() => setPending(null)}
    />
  ) : null;
  return { start, box };
}

export function PreflightBox({ pf, onConfirm, onCancel }: { pf: Preflight; onConfirm: () => void; onCancel: () => void }) {
  const b = pf.budget;
  return (
    <div className="preflight small" data-testid="preflight">
      <b>Before running</b>
      {pf.nodes.map((n) => (
        <div key={n.node_id} className="pf-node">
          <b>{n.label || n.node_id}</b>
          {n.execution &&
            Object.entries(n.execution.adapters).map(([ad, d]) => (
              <div key={ad}>
                {ad}:{" "}
                {d.resolved ? (
                  <>
                    <Badge tone={d.resolved === "local" ? "default" : "info"}>{d.resolved === "local" ? "this machine" : `${d.where ?? "worker"} worker`}</Badge>
                    {d.worker && <span className="muted"> {d.worker}</span>}
                    {d.resolved !== "local" && (
                      <span className="muted">
                        {" "}
                        · isolation {d.isolation ?? "?"}
                        {d.isolation_verified ? " (verified)" : " (unverified)"}
                      </span>
                    )}
                  </>
                ) : (
                  <Badge tone="err">unavailable: {d.error}</Badge>
                )}
              </div>
            ))}
          {n.model && (
            <div>
              model: <code>{n.model.provider}</code>
              {n.model.model && <> · <code>{n.model.model}</code></>}{" "}
              {n.model.live ? <Badge tone={n.model.available ? "warn" : "err"}>{n.model.available ? "live API calls" : "not configured"}</Badge> : <Badge tone="default">deterministic, no API calls</Badge>}
              {n.model.live && <span className="muted"> · price {n.model.price_known ? "known" : "unknown"}</span>}
              {n.model.evaluate_revise_loop && <span className="muted"> · up to {n.model.max_iterations} correction(s)</span>}
            </div>
          )}
        </div>
      ))}
      <div className="pf-budget">
        Limits: {b.max_model_calls} model calls · ${Number(b.max_cost_usd).toFixed(2)} · {Number(b.max_tokens).toLocaleString()} tokens · {b.max_seconds} s · {b.max_iterations} correction(s) · job timeout {b.job_timeout_s} s
      </div>
      <div className="muted">Spend: {pf.spend_bound}</div>
      <div className="row">
        <button className="primary" onClick={onConfirm} data-testid="preflight-confirm">
          Run
        </button>
        <button onClick={onCancel}>Cancel</button>
      </div>
    </div>
  );
}

/** What an execution consumed against its budget. Unknown prices are shown as unknown. */
export function BudgetSummary({ b }: { b?: ExecutionBudget }) {
  if (!b || !b.used) return null;
  const u = b.used;
  const prov = Object.entries(b.by_provider ?? {});
  return (
    <div className="budget small" data-testid="budget-summary">
      <b>Usage</b> {u.model_calls} model call(s) · {u.input_tokens.toLocaleString()} in / {u.output_tokens.toLocaleString()} out tokens · cost <b>{u.cost}</b> · {u.seconds} s
      {b.limits && (
        <span className="muted">
          {" "}
          (limits {b.limits.max_model_calls} calls · ${Number(b.limits.max_cost_usd).toFixed(2)} · {b.limits.max_seconds} s)
        </span>
      )}
      {prov.map(([p, x]) => (
        <div key={p} className="muted">
          {p}: {x.calls} call(s) {x.models.length ? `· ${x.models.join(", ")}` : ""} · {x.cost_known ? `$${x.cost_usd.toFixed(4)}` : "cost unknown"}
        </div>
      ))}
      {(b.refusals ?? []).map((r, i) => (
        <div key={i} className="warning">
          budget: {r}
        </div>
      ))}
    </div>
  );
}
