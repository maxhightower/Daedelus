import { useEffect, useState } from "react";
import { api, fileUrl } from "../api";
import { BindingEditor } from "../components/BindingEditor";
import { ContextView } from "../components/ContextView";
import { Badge, ComponentTree, Section, targetLabel } from "../components/common";
import { TargetPicker } from "../components/TargetPicker";
import { useStudio } from "../state";
import type { ResolvedContext, Revision, TargetSelector } from "../types";

const TEMPLATES: Record<string, { template: string; params: any }> = {
  blender: {
    template: "components",
    params: {
      components: [
        { id: "root", name: "Root", primitive: "empty" },
        { id: "body", name: "Body", primitive: "cube", size: [1, 1, 1], location: [0, 0, 0.5], parent: "root" },
      ],
    },
  },
  layered2d: {
    template: "layers",
    params: { width: 640, height: 400, layers: [{ id: "base", name: "Base", fill: { type: "solid", colors: ["#dddddd"] } }] },
  },
  code: { template: "files", params: { files: { "README.md": "# New artifact\n" } } },
};

function NewArtifact() {
  const { project, health, run, refresh } = useStudio();
  const [adapter, setAdapter] = useState("layered2d");
  const [name, setName] = useState("");
  const [params, setParams] = useState(JSON.stringify(TEMPLATES.layered2d.params, null, 1));
  const [template, setTemplate] = useState(TEMPLATES.layered2d.template);
  if (!project) return null;
  return (
    <Section title="New artifact" collapsed>
      <div className="form-grid">
        <label>Adapter</label>
        <select
          value={adapter}
          onChange={(e) => {
            setAdapter(e.target.value);
            setTemplate(TEMPLATES[e.target.value]?.template ?? "");
            setParams(JSON.stringify(TEMPLATES[e.target.value]?.params ?? {}, null, 1));
          }}
        >
          {health?.adapters.map((a) => (
            <option key={a.name} value={a.name} disabled={!a.available}>
              {a.name} — {a.artifact_types.join(", ")} {a.available ? "" : `(unavailable: ${a.unavailable_reason})`}
            </option>
          ))}
        </select>
        <label>Name</label>
        <input value={name} onChange={(e) => setName(e.target.value)} />
        <label>Template</label>
        <select value={template} onChange={(e) => setTemplate(e.target.value)}>
          {health?.adapters.find((a) => a.name === adapter)?.templates.map((t) => <option key={t}>{t}</option>)}
        </select>
        <label>Parameters</label>
        <textarea rows={6} className="mono" value={params} onChange={(e) => setParams(e.target.value)} />
      </div>
      <button
        className="primary"
        onClick={async () => {
          let p: any;
          try {
            p = JSON.parse(params);
          } catch (e: any) {
            alert(`Invalid JSON: ${e.message}`);
            return;
          }
          if (await run(api.createArtifact(project.id, { name: name || "Untitled", adapter, template, params: p }), "Artifact created")) refresh();
        }}
      >
        Create artifact
      </button>
    </Section>
  );
}

function HeadPreview({ artifactId, revisionId }: { artifactId: string; revisionId?: string | null }) {
  const { project } = useStudio();
  const [rev, setRev] = useState<Revision | null>(null);
  useEffect(() => {
    if (project && revisionId) api.revision(project.id, revisionId).then(setRev).catch(() => setRev(null));
  }, [project, revisionId, artifactId]);
  if (!rev || !project) return null;
  const img = rev.previews.render ?? rev.previews.composite;
  return (
    <div className="head-preview">
      {img ? <img src={fileUrl(project.id, img)} alt="" /> : <div className="muted small">{rev.vcs_commit ? `git ${rev.vcs_commit.slice(0, 10)}` : "no raster preview"}</div>}
      <div className="muted small">
        rev {rev.number} · {rev.message}
      </div>
    </div>
  );
}

export function WorkspacePanel() {
  const { project, projects, artifacts, bindings, sources, selectProject, refreshProjects, run, setView, setFocus } = useStudio();
  const [newName, setNewName] = useState("");
  const [target, setTarget] = useState<TargetSelector>({ scope: "project" });
  const [ctx, setCtx] = useState<ResolvedContext | null>(null);
  const [addSource, setAddSource] = useState("");
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    if (project) api.resolve(project.id, target).then(setCtx).catch(() => setCtx(null));
  }, [project, target, bindings]);

  if (!project)
    return (
      <div className="welcome">
        <h1>Daedelus Studio</h1>
        <p>Compose multimodal sources, give them meanings and scopes, and let agents produce editable artifacts.</p>
        <div className="row">
          <input value={newName} onChange={(e) => setNewName(e.target.value)} placeholder="Project name" />
          <button
            className="primary"
            onClick={async () => {
              const p = await run(api.createProject(newName || "Untitled project"), "Project created");
              if (p) {
                await refreshProjects();
                selectProject(p.id);
              }
            }}
          >
            Create project
          </button>
          <button
            disabled={busy}
            onClick={async () => {
              setBusy(true);
              const r = await run(api.createDemo(true), "Demo project created");
              setBusy(false);
              if (r) {
                await refreshProjects();
                selectProject(r.project.id);
              }
            }}
          >
            {busy ? "Building demo (Blender)…" : "Create multimodal demo project"}
          </button>
        </div>
        {projects.length > 0 && (
          <>
            <h3>Open a project</h3>
            {projects.map((p) => (
              <div key={p.id} className="project-row" onClick={() => selectProject(p.id)}>
                <b>{p.name}</b> <span className="muted small">{p.description} · {p.created_at.slice(0, 16)}</span>
              </div>
            ))}
          </>
        )}
      </div>
    );

  const anchored = bindings.filter((b) => sameTarget(b.target, target));
  return (
    <div className="split">
      <div className="list-col">
        <NewArtifact />
        <div className={`tree-row ${target.scope === "project" ? "selected" : ""}`} onClick={() => setTarget({ scope: "project" })}>
          <b>Project</b> <span className="muted small">{bindings.filter((b) => b.target.scope === "project").length} binding(s)</span>
        </div>
        {artifacts.map((a) => (
          <div key={a.id} className="artifact-block">
            <div
              className={`tree-row artifact ${target.scope === "artifact" && target.artifact_id === a.id ? "selected" : ""}`}
              onClick={() => setTarget({ scope: "artifact", artifact_id: a.id })}
            >
              <b>{a.name}</b> <Badge>{a.artifact_type}</Badge> <span className="muted small">{a.adapter}</span>
              <button className="link small" onClick={(e) => (e.stopPropagation(), setFocus("artifact", a.id), setView("artifacts"))}>
                inspect
              </button>
            </div>
            <HeadPreview artifactId={a.id} revisionId={a.head_revision_id} />
            <ComponentTree
              artifact={a}
              selected={target.scope === "component" && target.artifact_id === a.id ? target.component_id : null}
              onSelect={(cid) => setTarget({ scope: "component", artifact_id: a.id, component_id: cid })}
              annotate={(c) => {
                const n = bindings.filter((b) => b.target.scope === "component" && b.target.artifact_id === a.id && b.target.component_id === c.id).length;
                return n ? <Badge tone="info">{n}</Badge> : null;
              }}
            />
          </div>
        ))}
      </div>
      <div className="detail-col">
        <h2>Scope &amp; binding inspector</h2>
        <TargetPicker value={target} onChange={setTarget} artifacts={artifacts} />
        <h3>{targetLabel(target, artifacts)}</h3>
        <Section title={`Bindings anchored here (${anchored.length})`}>
          {anchored.length === 0 && <div className="muted small">None anchored at this scope.</div>}
          {anchored.map((b) => {
            const s = sources.find((x) => x.id === b.source_id);
            return (
              <div key={b.id} className="binding-card">
                <div className="row">
                  {s?.preview_path && <img className="mini" src={fileUrl(project.id, s.preview_path)} alt="" />}
                  <b>{s?.name}</b>
                  <span>as {b.role}</span>
                  <span className="muted">{b.aspects.join(", ")}</span>
                  <Badge tone={b.constraint === "hard" ? "warn" : "default"}>{b.constraint}</Badge>
                  <span className="muted small">strength {b.strength}</span>
                  <button className="link small" onClick={() => (setFocus("source", b.source_id), setView("sources"))}>
                    edit
                  </button>
                </div>
              </div>
            );
          })}
          <div className="row">
            <select value={addSource} onChange={(e) => setAddSource(e.target.value)}>
              <option value="">Assign a source to this target…</option>
              {sources.map((s) => (
                <option key={s.id} value={s.id}>
                  {s.name} ({s.media_type})
                </option>
              ))}
            </select>
          </div>
          {addSource && (
            <BindingEditor
              key={addSource + JSON.stringify(target)}
              sourceId={addSource}
              initialTarget={target}
              onDone={() => setAddSource("")}
            />
          )}
        </Section>
        <Section title="Resolved context (what an agent working on this target receives)">
          {ctx ? <ContextView ctx={ctx} artifacts={artifacts} /> : <div className="muted">…</div>}
        </Section>
      </div>
    </div>
  );
}

function sameTarget(a: TargetSelector, b: TargetSelector) {
  if (a.scope !== b.scope) return false;
  if (a.scope === "project") return true;
  if (a.artifact_id !== b.artifact_id) return false;
  return a.scope === "artifact" || a.component_id === b.component_id;
}
