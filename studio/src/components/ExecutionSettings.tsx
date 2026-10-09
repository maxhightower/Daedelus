import { useCallback, useEffect, useState } from "react";
import { clusterApi, type ExecutionSettings, type ExecutionTarget, type JobStatus } from "../api";
import { useStudio } from "../state";
import { Badge, short } from "./common";

const LABEL: Record<ExecutionTarget, string> = {
  automatic: "Automatic",
  local: "Local",
  cloud_cpu: "Cloud CPU",
  cloud_gpu: "Cloud GPU",
};
const HELP: Record<ExecutionTarget, string> = {
  automatic: "this machine when its tools are installed, otherwise a connected CPU worker; never a GPU worker",
  local: "this machine only",
  cloud_cpu: "connected CPU workers only - fails (no local fallback) when none offers the adapter",
  cloud_gpu: "connected GPU workers only - fails (no local fallback) when none offers the adapter",
};
const TONE: Record<string, string> = { succeeded: "ok", failed: "err", timed_out: "err", conflict: "err", cancelled: "warn", running: "info", leased: "info", queued: "default" };

/** Where adapter work runs, which workers are connected and what they are doing. */
export function ExecutionSettingsPanel() {
  const s = useStudio();
  const [cfg, setCfg] = useState<ExecutionSettings | null>(null);
  const load = useCallback(() => {
    if (s.project) clusterApi.settings(s.project.id).then(setCfg).catch(() => setCfg(null));
  }, [s.project]);
  useEffect(load, [load, s.jobs.length]);
  if (!s.project || !cfg) return <div className="muted small">…</div>;
  const blocked = (t: ExecutionTarget) => Object.entries(cfg.adapters).filter(([, a]) => (t === "local" ? !a.local : t === "cloud_cpu" ? !a.cloud_cpu : t === "cloud_gpu" ? !a.cloud_gpu : !a.local && !a.cloud_cpu)).map(([n]) => n);
  const setTarget = async (t: ExecutionTarget) => {
    const prev = cfg;
    setCfg({ ...cfg, target: t }); // optimistic; reverted if the server refuses
    const r = await s.run(clusterApi.setTarget(s.project!.id, t), `Execution target: ${LABEL[t]}`);
    setCfg(r ?? prev);
  };
  const active = s.jobs.filter((j) => ["queued", "leased", "running"].includes(j.state));
  return (
    <div className="exec-settings" data-testid="execution-settings">
      <div className="row small">
        <b>Execution</b>
        <span className="spacer" />
        <Badge tone={s.live === "live" ? "ok" : s.live === "reconnecting" ? "warn" : "default"} title="project event stream (server-sent events)">
          {s.live === "live" ? "● live updates" : s.live === "reconnecting" ? "reconnecting…" : s.live}
        </Badge>
      </div>
      <div className="target-choice" role="radiogroup" aria-label="execution target">
        {cfg.targets.map((t) => (
          <label key={t} className={`target-opt ${cfg.target === t ? "on" : ""}`} title={HELP[t]}>
            <input type="radio" name="exec-target" checked={cfg.target === t} onChange={() => setTarget(t)} data-testid={`target-${t}`} />
            {LABEL[t]}
          </label>
        ))}
      </div>
      <div className="muted small">{HELP[cfg.target]}</div>
      {blocked(cfg.target).length > 0 && (
        <div className="warning small" data-testid="target-warning">
          {LABEL[cfg.target]} cannot run: {blocked(cfg.target).join(", ")} - such runs fail with an explicit error instead of running elsewhere.
        </div>
      )}
      <table className="grid small avail">
        <thead>
          <tr>
            <th>adapter</th>
            <th>local</th>
            <th>cloud CPU</th>
            <th>cloud GPU</th>
          </tr>
        </thead>
        <tbody>
          {Object.entries(cfg.adapters).map(([n, a]) => (
            <tr key={n}>
              <td>{n}</td>
              <td title={a.local_detail}>{a.local ? "✓" : "—"}</td>
              <td>{a.cloud_cpu ? "✓" : "—"}</td>
              <td>{a.cloud_gpu ? "✓" : "—"}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <b className="small">Workers {cfg.cluster.workers_enabled ? "" : "(worker pool disabled on this control plane)"}</b>
      {cfg.cluster.workers.map((w) => (
        <div key={w.id} className="small worker-row" data-testid="worker-row">
          <Badge tone={w.alive ? "ok" : "default"}>{w.alive ? "connected" : "gone"}</Badge> <b>{w.name}</b> · {w.capabilities.join("+")} · {w.adapters.join(", ")}
          <div className="muted">
            {w.deployment || "process"}
            {w.host ? ` on ${w.host}` : ""} · isolation {w.isolation?.profile ?? "unknown"}
            {w.isolation?.verified ? " (self-test passed)" : " (unverified)"}
            {w.credential_kind ? ` · ${w.credential_kind} credential` : ""}
          </div>
        </div>
      ))}
      {cfg.cluster.workers_enabled && !cfg.cluster.workers.length && <div className="muted small">No worker has connected.</div>}
      <JobList jobs={s.jobs.slice(0, 25)} active={active.length} />
    </div>
  );
}

function JobList({ jobs, active }: { jobs: JobStatus[]; active: number }) {
  const s = useStudio();
  if (!jobs.length) return <div className="muted small">No remote jobs in this project.</div>;
  return (
    <div className="job-list" data-testid="job-list">
      <b className="small">
        Jobs ({active} active)
      </b>
      {jobs.map((j) => (
        <div key={j.id} className="job-row small" data-state={j.state}>
          <Badge tone={TONE[j.state] ?? "default"}>{j.state.replace("_", " ")}</Badge> {j.adapter}.{j.method}
          <span className="muted">
            {" "}
            · {j.requires} · attempt {j.attempt}/{j.max_attempts}
            {j.worker_id ? ` · ${short(j.worker_id)}` : ""}
          </span>
          {["leased", "running"].includes(j.state) && <progress max={1} value={j.progress} />}
          {["queued", "leased", "running"].includes(j.state) && (
            <button className="mini-btn" onClick={() => s.run(clusterApi.cancel(s.project!.id, j.id), "Cancellation requested")}>
              cancel
            </button>
          )}
          {j.error && <div className="muted">{j.error}</div>}
        </div>
      ))}
    </div>
  );
}

/** Where a node run executed (decision + jobs), from its outputs. */
export function NodeExecutionInfo({ info }: { info: any }) {
  if (!info) return null;
  return (
    <div className="small node-exec" data-testid="node-execution">
      <b>Execution</b> requested <code>{info.target}</code>
      {Object.entries(info.decisions ?? {}).map(([ad, d]: any) => (
        <div key={ad}>
          {ad}: <Badge tone={d.resolved === "local" ? "default" : "info"}>{d.resolved}</Badge> <span className="muted">{d.reason}</span>
        </div>
      ))}
      {(info.jobs ?? []).length > 0 && (
        <div className="muted">
          {info.jobs.filter((j: any) => !j.cached).length} job(s): {info.jobs.filter((j: any) => !j.cached).map((j: any) => `${j.method}${j.attempts > 1 ? `×${j.attempts}` : ""}${j.seconds != null ? ` ${Number(j.seconds).toFixed(1)}s` : ""}`).join(", ")} · workers{" "}
          {Array.from(new Set(info.jobs.map((j: any) => j.worker_id).filter(Boolean))).map((w: any) => short(w)).join(", ")}
          {info.jobs.some((j: any) => j.cached) && ` · ${info.jobs.filter((j: any) => j.cached).length} inspection(s) reused (unchanged files)`}
          {(() => {
            const real = info.jobs.filter((j: any) => !j.cached);
            const where = Array.from(new Set(real.map((j: any) => j.deployment).filter(Boolean)));
            const bytes = real.reduce((n: number, j: any) => n + (j.bytes_in ?? 0) + (j.bytes_out ?? 0), 0);
            const iso = Array.from(new Set(real.map((j: any) => j.isolation).filter(Boolean)));
            return (
              <>
                {where.length > 0 && ` · ran on ${where.join("/")} worker(s)`}
                {iso.length > 0 && ` · isolation ${iso.join("/")}`}
                {bytes > 0 && ` · ${(bytes / 1024).toFixed(0)} KiB transferred`}
              </>
            );
          })()}
          {info.jobs.some((j: any) => (j.attempts ?? 1) > 1) && " · retried"}
          {info.jobs.some((j: any) => j.state && !["succeeded", "cache_hit"].includes(j.state)) && (
            <div className="error-box">{info.jobs.filter((j: any) => j.state && !["succeeded", "cache_hit"].includes(j.state)).map((j: any) => `${j.method}: ${j.state} ${j.error ?? ""}`).join("; ")}</div>
          )}
        </div>
      )}
    </div>
  );
}

/** Top-bar indicator: current target, live-update state, active jobs. Opens the settings. */
export function ExecutionBadge({ onOpen }: { onOpen: () => void }) {
  const s = useStudio();
  const [target, setTarget] = useState<ExecutionTarget | null>(null);
  useEffect(() => {
    if (s.project) clusterApi.settings(s.project.id).then((c) => setTarget(c.target)).catch(() => setTarget(null));
  }, [s.project, s.jobs.length]);
  const active = s.jobs.filter((j) => ["queued", "leased", "running"].includes(j.state)).length;
  if (!s.project) return null;
  return (
    <button className="mini-btn exec-badge" onClick={onOpen} data-testid="execution-badge" title="Execution target, workers and jobs">
      ⚙ {target ? LABEL[target] : "…"}
      {active > 0 && <span className="badge badge-info">{active} job(s)</span>}
      <span className={`live-dot ${s.live}`} title={`event stream: ${s.live}`} />
    </button>
  );
}
