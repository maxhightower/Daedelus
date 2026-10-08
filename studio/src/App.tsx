import { ExecutionBadge } from "./components/ExecutionSettings";
import { ReactFlowProvider } from "@xyflow/react";
import { useEffect, useRef, useState } from "react";
import { api } from "./api";
import { SpatialCanvas } from "./canvas/SpatialCanvas";
import { AgentDock } from "./panels/AgentDock";
import { ExplorerPanel } from "./panels/ExplorerPanel";
import { FocusEditor } from "./panels/FocusEditor";
import { InspectorPanel } from "./panels/InspectorPanel";
import { useStudio } from "./state";

const lsNum = (k: string, d: number) => {
  try {
    const v = Number(localStorage.getItem(k));
    return v > 0 ? v : d;
  } catch {
    return d;
  }
};

function Splitter({ onDrag }: { onDrag: (dx: number) => void }) {
  const start = useRef<number | null>(null);
  return (
    <div
      className="splitter"
      role="separator"
      onPointerDown={(e) => {
        start.current = e.clientX;
        (e.target as HTMLElement).setPointerCapture(e.pointerId);
      }}
      onPointerMove={(e) => {
        if (start.current === null) return;
        onDrag(e.clientX - start.current);
        start.current = e.clientX;
      }}
      onPointerUp={() => (start.current = null)}
    />
  );
}

function Welcome() {
  const s = useStudio();
  const [name, setName] = useState("");
  const [busy, setBusy] = useState(false);
  return (
    <div className="welcome">
      <h1>Daedelus Studio</h1>
      <p>One spatial workspace for references, artifacts, workflows and agents.</p>
      <div className="row">
        <input value={name} onChange={(e) => setName(e.target.value)} placeholder="Project name" />
        <button
          className="primary"
          onClick={async () => {
            const p = await s.run(api.createProject(name || "Untitled project"), "Project created");
            if (p) {
              await s.refreshProjects();
              s.selectProject(p.id);
            }
          }}
        >
          Create project
        </button>
        <button
          disabled={busy}
          onClick={async () => {
            setBusy(true);
            const r = await s.run(api.createDemo(true), "Demo project created");
            setBusy(false);
            if (r) {
              await s.refreshProjects();
              s.selectProject(r.project.id);
            }
          }}
        >
          {busy ? "Building demo (Blender)…" : "Create multimodal demo project"}
        </button>
      </div>
      {s.projects.length > 0 && (
        <>
          <h3>Open a project</h3>
          {s.projects.map((p) => (
            <div key={p.id} className="project-row" onClick={() => s.selectProject(p.id)}>
              <b>{p.name}</b> <span className="muted small">{p.description} · {p.created_at.slice(0, 16)}</span>
            </div>
          ))}
        </>
      )}
    </div>
  );
}

export default function App() {
  const s = useStudio();
  const [left, setLeft] = useState(() => lsNum("daedelus.ui.left", 280));
  const [right, setRight] = useState(() => lsNum("daedelus.ui.right", 380));
  const [showLeft, setShowLeft] = useState(true);
  const [showRight, setShowRight] = useState(true);
  useEffect(() => {
    try {
      localStorage.setItem("daedelus.ui.left", String(left));
      localStorage.setItem("daedelus.ui.right", String(right));
    } catch {
      /* per-viewer convenience only */
    }
  }, [left, right]);
  const cols = [showLeft ? `${left}px 5px` : "", "minmax(0,1fr)", showRight ? `5px ${right}px` : ""].filter(Boolean).join(" ");
  return (
    <div className="app">
      <header className="topbar">
        <div className="brand">Daedelus</div>
        <select
          value={s.project?.id ?? ""}
          onChange={(e) => s.selectProject(e.target.value || null)}
          aria-label="project"
        >
          <option value="">— projects —</option>
          {s.projects.map((p) => (
            <option key={p.id} value={p.id}>
              {p.name}
            </option>
          ))}
        </select>
        {s.project && (
          <>
            <button className={`mini-btn ${showLeft ? "on" : ""}`} onClick={() => setShowLeft(!showLeft)} title="Toggle explorer">
              ◧ Explorer
            </button>
            <button className={`mini-btn ${showRight ? "on" : ""}`} onClick={() => setShowRight(!showRight)} title="Toggle inspector">
              Inspector ◨
            </button>
            <button className={`mini-btn ${s.agentOpen ? "on" : ""}`} onClick={() => s.setAgentOpen(!s.agentOpen)}>
              Agent
            </button>
            <ExecutionBadge
              onOpen={() => {
                setShowRight(true);
                s.select({ kind: "none" });
              }}
            />
          </>
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
      </header>
      {!s.project ? (
        <Welcome />
      ) : (
        <div className="shell" style={{ gridTemplateColumns: cols }}>
          {showLeft && (
            <>
              <ExplorerPanel />
              <Splitter onDrag={(dx) => setLeft((w) => Math.min(560, Math.max(200, w + dx)))} />
            </>
          )}
          <section className="center">
            <ReactFlowProvider>
              <div className="canvas-host">
                <SpatialCanvas />
                {s.focusItemId && <FocusEditor />}
              </div>
            </ReactFlowProvider>
            <AgentDock />
          </section>
          {showRight && (
            <>
              <Splitter onDrag={(dx) => setRight((w) => Math.min(720, Math.max(280, w - dx)))} />
              <InspectorPanel />
            </>
          )}
        </div>
      )}
      {s.error && (
        <div className="toast error" onClick={() => s.setError(null)} role="alert">
          {s.error} <span className="muted">(click to dismiss)</span>
        </div>
      )}
      {s.notice && <div className="toast" role="status">{s.notice}</div>}
    </div>
  );
}
