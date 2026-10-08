import { useEffect, useRef, useState } from "react";
import { api } from "../api";
import { ASPECTS } from "../components/BindingEditor";
import { Empty, keyLabel } from "../components/common";
import { TargetPicker } from "../components/TargetPicker";
import { useStudio } from "../state";
import type { TargetSelector } from "../types";

/**
 * Conversational direction. Each message becomes an instruction source bound to the chosen
 * target, so it is versioned, scoped and attributable like any other input. Nothing executes
 * until the workflow runs (optionally behind an approval checkpoint).
 */
export function AgentPanel() {
  const { project, artifacts, workflows, health, run, refresh, setView, setFocus } = useStudio();
  const [msgs, setMsgs] = useState<any[]>([]);
  const [text, setText] = useState("");
  const [target, setTarget] = useState<TargetSelector>({ scope: "project" });
  const [aspects, setAspects] = useState<string[]>([]);
  const [wid, setWid] = useState<string>("");
  const [lastImpact, setLastImpact] = useState<any>(null);
  const endRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (project) api.messages(project.id).then(setMsgs);
  }, [project]);
  useEffect(() => {
    if (!wid && workflows[0]) setWid(workflows[0].id);
  }, [workflows, wid]);
  useEffect(() => endRef.current?.scrollIntoView({ behavior: "smooth" }), [msgs]);
  if (!project) return <Empty>Select a project.</Empty>;
  const ai = health?.providers.find((p) => p.name === "anthropic");
  const send = async () => {
    if (!text.trim()) return;
    const r = await run(api.sendMessage(project.id, { text, target, aspects, workflow_id: wid || undefined }));
    if (r) {
      setText("");
      setLastImpact(r.impact);
      setMsgs(await api.messages(project.id));
      refresh();
    }
  };
  return (
    <div className="agent">
      <div className="chat">
        {msgs.length === 0 && (
          <Empty>
            Tell the agent what to change, and where. Example: target “Table → Legs”, aspects “shape”, message “taper: 0.6”.
          </Empty>
        )}
        {msgs.map((m) => (
          <div key={m.id} className={`msg ${m.role}`}>
            <div className="who">
              {m.role === "user" ? `You → ${keyLabel(m.target ?? "project", artifacts)}` : "Daedelus"}
            </div>
            <div className="text">{m.text}</div>
          </div>
        ))}
        <div ref={endRef} />
      </div>
      <div className="composer">
        <div className="row">
          <span className="muted small">Target</span>
          <TargetPicker value={target} onChange={setTarget} artifacts={artifacts} />
          <span className="muted small">Workflow</span>
          <select value={wid} onChange={(e) => setWid(e.target.value)}>
            {workflows.map((w) => (
              <option key={w.id} value={w.id}>
                {w.name}
              </option>
            ))}
          </select>
        </div>
        <div className="chips">
          {ASPECTS.slice(0, 14).map((a) => (
            <button key={a} className={`chip ${aspects.includes(a) ? "on" : ""}`} onClick={() => setAspects(aspects.includes(a) ? aspects.filter((x) => x !== a) : [...aspects, a])}>
              {a}
            </button>
          ))}
        </div>
        <textarea rows={3} value={text} onChange={(e) => setText(e.target.value)} placeholder="Direction for the agent (key: value lines are machine-readable; free text is passed to AI planners)" onKeyDown={(e) => e.key === "Enter" && (e.metaKey || e.ctrlKey) && send()} />
        <div className="row end">
          <span className="muted small">
            Planner: heuristic (deterministic) by default · Claude provider {ai?.available ? "available" : `unavailable (${ai?.detail})`}
          </span>
          <button className="primary" onClick={send}>
            Send
          </button>
          {lastImpact && wid && (
            <button
              onClick={async () => {
                const e = await run(api.execute(project.id, wid), "Execution started");
                if (e) {
                  setFocus("execution", e.id);
                  setView("executions");
                }
              }}
            >
              Run workflow now
            </button>
          )}
        </div>
      </div>
    </div>
  );
}
