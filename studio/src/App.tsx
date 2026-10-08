import { AgentPanel } from "./panels/AgentPanel";
import { ArtifactsPanel } from "./panels/ArtifactsPanel";
import { ExecutionsPanel } from "./panels/ExecutionsPanel";
import { SourcesPanel } from "./panels/SourcesPanel";
import { WorkflowPanel } from "./panels/WorkflowPanel";
import { WorkspacePanel } from "./panels/WorkspacePanel";
import { useStudio, type View } from "./state";

const NAV: { id: View; label: string; hint: string }[] = [
  { id: "workspace", label: "Workspace", hint: "Artifacts, components and the scope/binding inspector" },
  { id: "sources", label: "Sources", hint: "Source library: media, meanings (roles) and scopes" },
  { id: "workflow", label: "Workflow", hint: "Visual workflow editor" },
  { id: "artifacts", label: "Artifacts", hint: "Artifact inspector: previews, revisions, diffs, attribution" },
  { id: "executions", label: "Executions", hint: "Execution monitor: progress, approvals, evidence" },
  { id: "agent", label: "Agent", hint: "Give directions to agents" },
];

export default function App() {
  const s = useStudio();
  return (
    <div className="app">
      <header className="topbar">
        <div className="brand">Daedelus</div>
        <select
          value={s.project?.id ?? ""}
          onChange={(e) => {
            s.selectProject(e.target.value || null);
            s.setView("workspace");
          }}
        >
          <option value="">— projects —</option>
          {s.projects.map((p) => (
            <option key={p.id} value={p.id}>
              {p.name}
            </option>
          ))}
        </select>
        {s.project && (
          <span className="muted small">
            {s.sources.length} sources · {s.bindings.length} bindings · {s.artifacts.length} artifacts · {s.workflows.length} workflows
          </span>
        )}
        <div className="spacer" />
        {s.health && (
          <span className="muted small adapters">
            {s.health.adapters.map((a) => (
              <span key={a.name} className={a.available ? "ok" : "bad"} title={a.unavailable_reason ?? a.description}>
                {a.available ? "●" : "○"} {a.name}
              </span>
            ))}
          </span>
        )}
        <button className="link" onClick={() => s.refresh()}>
          ↻
        </button>
      </header>
      <nav className="sidenav">
        {NAV.map((n) => (
          <button key={n.id} className={s.view === n.id ? "active" : ""} onClick={() => s.setView(n.id)} title={n.hint}>
            {n.label}
          </button>
        ))}
      </nav>
      <main className="main">
        {s.view === "workspace" && <WorkspacePanel />}
        {s.view === "sources" && <SourcesPanel />}
        {s.view === "workflow" && <WorkflowPanel />}
        {s.view === "artifacts" && <ArtifactsPanel />}
        {s.view === "executions" && <ExecutionsPanel />}
        {s.view === "agent" && <AgentPanel />}
      </main>
      {s.error && (
        <div className="toast error" onClick={() => s.setError(null)}>
          {s.error} <span className="muted">(click to dismiss)</span>
        </div>
      )}
      {s.notice && <div className="toast">{s.notice}</div>}
    </div>
  );
}
