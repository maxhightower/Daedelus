import { useEffect, useState } from "react";
import { api } from "../api";
import { ASPECTS } from "../components/BindingEditor";
import { Badge, keyLabel, targetLabel } from "../components/common";
import { ContextPackageView } from "../components/SemanticView";
import { TargetPicker } from "../components/TargetPicker";
import { useStudio } from "../state";
import type { ContextPackage, TargetSelector } from "../types";

/**
 * Directions are recorded as instruction sources bound to an explicit target (default: the
 * current selection), so they are scoped, versioned and attributable. Nothing runs until the
 * user previews the impact and runs the workflow.
 */
export function AgentDock() {
  const s = useStudio();
  const [text, setText] = useState("");
  const [aspects, setAspects] = useState<string[]>([]);
  const [override, setOverride] = useState<TargetSelector | null>(null);
  const [wid, setWid] = useState("");
  const [msgs, setMsgs] = useState<any[]>([]);
  const [impact, setImpact] = useState<any>(null);
  const [ctxOpen, setCtxOpen] = useState(false);
  const [pkg, setPkg] = useState<ContextPackage | null>(null);
  const target = override ?? s.agentTarget;
  const loadPkg = async () => {
    if (!s.project) return;
    setPkg((await s.run(api.agentContext(s.project.id, { artifact_id: target.artifact_id ?? null, component_id: target.component_id ?? null }))) ?? null);
  };
  useEffect(() => {
    if (ctxOpen) loadPkg();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ctxOpen, target.scope, target.artifact_id, target.component_id]);
  useEffect(() => {
    if (s.project && s.agentOpen) api.messages(s.project.id).then(setMsgs);
  }, [s.project, s.agentOpen]);
  useEffect(() => setOverride(null), [s.agentTarget.scope, s.agentTarget.artifact_id, s.agentTarget.component_id]);
  useEffect(() => {
    if (!wid && s.workflows[0]) setWid(s.workflows[0].id);
  }, [s.workflows, wid]);
  if (!s.project) return null;
  const live = (s.health?.providers ?? []).filter((p) => p.live);
  const send = async () => {
    if (!text.trim()) return;
    const r = await s.run(api.sendMessage(s.project!.id, { text, target, aspects, workflow_id: wid || undefined }));
    if (r) {
      setText("");
      setImpact(r.impact);
      setMsgs(await api.messages(s.project!.id));
      await s.refresh();
      await s.reloadBoard();
    }
  };
  return (
    <div className={`agent-dock ${s.agentOpen ? "open" : ""}`} data-testid="agent-dock">
      <div className="agent-dock-head" onClick={() => s.setAgentOpen(!s.agentOpen)}>
        <b>Agent</b>
        <span className="muted small">
          target: <span data-testid="agent-target">{targetLabel(target, s.artifacts)}</span>
        </span>
        <span className="spacer" />
        <span className="muted small">{s.agentOpen ? "▾ collapse" : "▴ expand"}</span>
      </div>
      {s.agentOpen && (
        <div className="agent-dock-body">
          <div className="agent-log">
            {msgs.slice(-8).map((m) => (
              <div key={m.id} className={`msg ${m.role}`}>
                <div className="who">{m.role === "user" ? `You → ${keyLabel(m.target ?? "project", s.artifacts)}` : "Daedelus"}</div>
                <div className="text">{m.text}</div>
              </div>
            ))}
            {!msgs.length && <div className="muted small">Select a component on the board, then describe the change. Directions are stored as scoped instruction sources.</div>}
          </div>
          <div className="agent-compose">
            <div className="row">
              <span className="muted small">Target</span>
              <TargetPicker value={target} onChange={setOverride} artifacts={s.artifacts} />
              {override && (
                <button className="link small" onClick={() => setOverride(null)}>
                  use selection
                </button>
              )}
            </div>
            <div className="chips">
              {ASPECTS.slice(0, 14).map((a) => (
                <button key={a} className={`chip ${aspects.includes(a) ? "on" : ""}`} onClick={() => setAspects(aspects.includes(a) ? aspects.filter((x) => x !== a) : [...aspects, a])}>
                  {a}
                </button>
              ))}
            </div>
            <textarea
              rows={2}
              value={text}
              onChange={(e) => setText(e.target.value)}
              placeholder="e.g. Make this leg thicker toward the bottom, preserve its height.  (key: value lines such as taper: 0.4 are machine-readable)"
              data-testid="agent-text"
              onKeyDown={(e) => e.key === "Enter" && (e.metaKey || e.ctrlKey) && send()}
            />
            <div className="row">
              <select value={wid} onChange={(e) => setWid(e.target.value)} aria-label="workflow">
                {s.workflows.map((w) => (
                  <option key={w.id} value={w.id}>
                    {w.name}
                  </option>
                ))}
              </select>
              <button className="primary" onClick={send} data-testid="agent-send">
                Record direction
              </button>
              <button disabled={!wid} onClick={async () => setImpact(await s.run(api.impact(s.project!.id, wid)))}>
                Preview impact
              </button>
              <button
                disabled={!wid}
                onClick={async () => {
                  const e = await s.run(api.execute(s.project!.id, wid), "Execution started");
                  if (e) s.watchExecution(wid);
                }}
              >
                ▶ Run workflow
              </button>
              <button className={ctxOpen ? "on" : ""} onClick={() => setCtxOpen(!ctxOpen)} data-testid="agent-context-toggle">
                What the agent will use
              </button>
              <span className="spacer" />
              <span className="muted small" data-testid="provider-status">
                {live.map((p) => (
                  <span key={p.name} title={p.detail} className={p.available ? "ok" : "bad"}>
                    {p.available ? "●" : "○"} {p.name}{" "}
                  </span>
                ))}
                · local heuristic: measurement-based, not semantic
              </span>
            </div>
            {ctxOpen && pkg && (
              <div className="agent-context">
                <ContextPackageView
                  pkg={pkg}
                  onAnalyse={async () => {
                    for (const sid of pkg.missing_analyses) await s.run(api.analyze(s.project!.id, sid, { provider: "heuristic" }));
                    await loadPkg();
                  }}
                />
              </div>
            )}
            {impact && (
              <div className="impact small" data-testid="agent-impact">
                <b>Will re-run:</b>{" "}
                {impact.nodes.flatMap((n: any) => (n.units ?? []).filter((u: any) => u.stale)).map((u: any) => (
                  <Badge key={u.unit} tone="warn" title={u.reasons.join("; ")}>
                    {keyLabel(u.unit, s.artifacts)}
                  </Badge>
                ))}
                {!impact.nodes.some((n: any) => (n.units ?? []).some((u: any) => u.stale)) && "nothing (all units up to date)"}
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
