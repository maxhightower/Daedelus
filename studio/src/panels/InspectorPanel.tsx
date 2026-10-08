import { useEffect, useMemo, useState } from "react";
import { api, boardApi, fileUrl } from "../api";
import { invalidateRevision, useRevision } from "../canvas/hooks";
import { rendererFor } from "../canvas/renderers";
import { BindingEditor } from "../components/BindingEditor";
import { Badge, ComponentTree, Json, Section, Status, keyLabel, short, targetLabel } from "../components/common";
import { ContextView } from "../components/ContextView";
import { ApprovalBox, ExecutionView } from "../components/ExecutionView";
import { NodeConfigForm } from "../components/NodeConfigForm";
import { RevisionView } from "../components/RevisionView";
import { useStudio } from "../state";
import type { Artifact, CanvasItem, ResolvedContext, Revision, TargetSelector, Workflow, WorkflowNode } from "../types";

// ------------------------------------------------------------------ real component edits
function ComponentEditForm({ artifact, componentId }: { artifact: Artifact; componentId: string }) {
  const { project, run, refresh } = useStudio();
  const head = useRevision(artifact.head_revision_id);
  const comp = artifact.components.find((c) => c.id === componentId);
  const props = (head?.measurements?.[componentId] ?? {}) as Record<string, any>;
  const [color, setColor] = useState("#8a5a3b");
  const [rough, setRough] = useState(0.5);
  const [taper, setTaper] = useState(0.3);
  const [opacity, setOpacity] = useState(1);
  const [grade, setGrade] = useState({ tint: "#ffcc88", tint_strength: 0.3, brightness: 1, saturation: 1 });
  const [opName, setOpName] = useState("");
  const [params, setParams] = useState("{}");
  const [catalog, setCatalog] = useState<any[]>([]);
  useEffect(() => {
    if (project) boardApi.operations(project.id, artifact.id).then(setCatalog).catch(() => setCatalog([]));
  }, [project, artifact.id]);
  if (!project || !comp) return null;
  const apply = async (ops: any[], label: string) => {
    const r = await run(boardApi.edit(project.id, artifact.id, ops, label, artifact.head_revision_id), `New revision: ${label}`);
    if (r) {
      invalidateRevision(r.id);
      await refresh();
    }
  };
  const applicable = catalog.filter((o) => o.target_kinds.includes("*") || o.target_kinds.includes(comp.kind));
  return (
    <div className="edit-form" data-testid="component-edit">
      <div className="muted small">Edits run through the {artifact.adapter} adapter with checkpoint, scope and constraint checks and create a new revision.</div>
      {artifact.adapter === "blender" && (
        <>
          <div className="row">
            <input type="color" value={color} onChange={(e) => setColor(e.target.value)} aria-label="base colour" />
            <label className="inline">
              roughness <input type="range" min={0} max={1} step={0.05} value={rough} onChange={(e) => setRough(+e.target.value)} />
            </label>
            <button onClick={() => apply([{ op: "set_material", component_id: componentId, params: { base_color: color, roughness: rough } }], `Material ${color} on ${comp.name}`)}>
              Apply material
            </button>
          </div>
          <div className="row">
            <label className="inline">
              taper <input type="number" className="num" step={0.05} min={-1.5} max={1.5} value={taper} onChange={(e) => setTaper(+e.target.value)} aria-label="taper" />
            </label>
            <button onClick={() => apply([{ op: "set_taper", component_id: componentId, params: { factor: taper, axis: "Z" } }], `Taper ${taper} on ${comp.name}`)}>
              Apply taper
            </button>
          </div>
        </>
      )}
      {artifact.adapter === "layered2d" && comp.kind === "layer" && (
        <>
          <div className="row">
            <label className="inline">
              opacity <input type="range" min={0} max={1} step={0.05} value={opacity} onChange={(e) => setOpacity(+e.target.value)} />
            </label>
            <span>{opacity.toFixed(2)}</span>
            <button onClick={() => apply([{ op: "set_layer_props", component_id: componentId, params: { opacity } }], `Opacity ${opacity} on ${comp.name}`)}>Apply</button>
          </div>
          <div className="row">
            <input type="color" value={grade.tint} onChange={(e) => setGrade({ ...grade, tint: e.target.value })} aria-label="tint" />
            <label className="inline">
              strength <input type="range" min={0} max={1} step={0.05} value={grade.tint_strength} onChange={(e) => setGrade({ ...grade, tint_strength: +e.target.value })} />
            </label>
            <label className="inline">
              brightness <input type="range" min={0.2} max={2} step={0.05} value={grade.brightness} onChange={(e) => setGrade({ ...grade, brightness: +e.target.value })} />
            </label>
            <button onClick={() => apply([{ op: "color_grade", component_id: componentId, params: grade }], `Grade ${comp.name}`)}>Apply grade</button>
          </div>
        </>
      )}
      {artifact.adapter === "code" && <div className="small">Activate the code view (double-click) or Focus it to edit files, review the diff and commit.</div>}
      <Section title="Any adapter operation (schema-checked)" collapsed>
        <div className="row">
          <select value={opName} onChange={(e) => setOpName(e.target.value)}>
            <option value="">choose operation…</option>
            {applicable.map((o) => (
              <option key={o.name} value={o.name}>
                {o.name} — {o.description.slice(0, 50)}
              </option>
            ))}
          </select>
        </div>
        <textarea className="mono" rows={3} value={params} onChange={(e) => setParams(e.target.value)} />
        <button
          disabled={!opName}
          onClick={() => {
            let p: any;
            try {
              p = JSON.parse(params);
            } catch (e: any) {
              return alert(`Invalid JSON: ${e.message}`);
            }
            apply([{ op: opName, component_id: componentId, params: p }], `${opName} on ${comp.name}`);
          }}
        >
          Apply
        </button>
      </Section>
      {Object.keys(props).length > 0 && (
        <div className="small muted">
          measured: {Object.entries(props).map(([k, v]) => `${k} ${typeof v === "number" ? +v.toFixed(3) : v}`).join(" · ")}
        </div>
      )}
    </div>
  );
}

// ------------------------------------------------------------------ component
function ComponentInspector({ artifactId, componentId, itemId }: { artifactId: string; componentId: string; itemId: string | null }) {
  const s = useStudio();
  const artifact = s.artifacts.find((a) => a.id === artifactId);
  const [ctx, setCtx] = useState<ResolvedContext | null>(null);
  const target: TargetSelector = { scope: "component", artifact_id: artifactId, component_id: componentId };
  useEffect(() => {
    if (s.project && artifact?.components.some((c) => c.id === componentId))
      api.resolve(s.project.id, target).then(setCtx).catch(() => setCtx(null));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [s.project, artifactId, componentId, s.bindings, artifact?.head_revision_id]);
  if (!artifact) return null;
  const comp = artifact.components.find((c) => c.id === componentId);
  if (!comp) return <div className="warning">Component {componentId} is not in the current revision of {artifact.name}.</div>;
  const anchored = s.bindings.filter((b) => b.target.scope === "component" && b.target.artifact_id === artifactId && b.target.component_id === componentId);
  return (
    <div data-testid="component-inspector">
      <h3>Component · {comp.name}</h3>
      <table className="kv">
        <tbody>
          <tr><td>artifact</td><td>{artifact.name} <code>{artifact.id}</code></td></tr>
          <tr><td>component id</td><td><code data-testid="component-id">{comp.id}</code></td></tr>
          <tr><td>kind</td><td>{comp.kind} {comp.native_ref && <code>{comp.native_ref}</code>}</td></tr>
          <tr><td>path</td><td>{targetLabel(target, s.artifacts)}</td></tr>
          <tr><td>revision</td><td>{artifact.head_revision_id && <code>{short(artifact.head_revision_id)}</code>}</td></tr>
        </tbody>
      </table>
      <div className="row">
        <button onClick={() => s.setAgentOpen(true)}>Direct agent at this component…</button>
        {comp.parent_id && (
          <button onClick={() => s.select({ kind: "component", itemId, artifactId, componentId: comp.parent_id! })}>Select parent ({comp.parent_id})</button>
        )}
      </div>
      <Section title="Edit (creates a native revision)">
        <ComponentEditForm artifact={artifact} componentId={componentId} />
      </Section>
      <Section title={`Bindings anchored here (${anchored.length})`}>
        {anchored.map((b) => (
          <Section key={b.id} title={`${s.sources.find((x) => x.id === b.source_id)?.name} · ${b.role}`} collapsed>
            <BindingEditor sourceId={b.source_id} binding={b} />
          </Section>
        ))}
        {!anchored.length && <div className="muted small">Drag a reference onto this component (or connect its ◉ handle to the component chip).</div>}
      </Section>
      <Section title="Resolved context (inherited + own sources, constraints, conflicts)">
        {ctx ? <ContextView ctx={ctx} artifacts={s.artifacts} /> : <div className="muted">…</div>}
      </Section>
    </div>
  );
}

// ------------------------------------------------------------------ artifact view
function ArtifactInspector({ item, artifact }: { item: CanvasItem; artifact: Artifact }) {
  const s = useStudio();
  const [revs, setRevs] = useState<Revision[]>([]);
  const [revId, setRevId] = useState<string | null>(null);
  const rev = useRevision(revId ?? artifact.head_revision_id);
  useEffect(() => {
    if (s.project) api.revisions(s.project.id, artifact.id).then(setRevs);
    setRevId(null);
  }, [s.project, artifact.id, artifact.head_revision_id]);
  const renderer = rendererFor(artifact);
  const views = s.board?.items.filter((i) => i.resource_ref.id === artifact.id).length ?? 0;
  const constrained = s.bindings.filter((b) => b.target.artifact_id === artifact.id && b.constraints.length);
  return (
    <div data-testid="artifact-inspector">
      <h3>{artifact.name}</h3>
      <div className="row small">
        <Badge>{artifact.artifact_type}</Badge> <span className="muted">adapter {artifact.adapter}</span> <Badge tone="info">{views} view(s) on board</Badge>
      </div>
      <div className="small muted">
        native <code>{artifact.native_dir}/{artifact.entry}</code>
      </div>
      <div className="form-grid">
        <label>View label</label>
        <input value={item.presentation_state.label ?? ""} onChange={(e) => s.patchPresentation(item.id, { label: e.target.value })} placeholder="e.g. Front view" />
        <label>View</label>
        <div className="small">
          {renderer.label} · component selection: {renderer.componentPicking} {renderer.nativeEditing ? "· native editing" : "· view only"}
        </div>
      </div>
      <div className="row">
        <button onClick={() => s.setActive(item.id)}>Edit in place</button>
        <button onClick={() => s.setFocusItem(item.id)}>Focus</button>
        <button onClick={() => s.setAgentOpen(true)}>Direct agent…</button>
      </div>
      <Section title="Components">
        <ComponentTree
          artifact={artifact}
          selected={item.presentation_state.selected_component}
          onSelect={(cid) => {
            s.patchPresentation(item.id, { selected_component: cid });
            s.select({ kind: "component", itemId: item.id, artifactId: artifact.id, componentId: cid });
          }}
          annotate={(c) => {
            const n = s.bindings.filter((b) => b.target.component_id === c.id && b.target.artifact_id === artifact.id).length;
            return n ? <Badge tone="info">{n}</Badge> : null;
          }}
        />
      </Section>
      {constrained.length > 0 && (
        <Section title="Constraints">
          {constrained.map((b) =>
            b.constraints.map((c, i) => (
              <div key={`${b.id}${i}`} className="small">
                {b.constraint} · {c.property} {c.op} {c.value ?? ""} on {targetLabel(b.target, s.artifacts)}
              </div>
            )),
          )}
        </Section>
      )}
      <Section title={`Revisions (${revs.length})`}>
        <div className="rev-list">
          {[...revs].reverse().map((r) => (
            <button key={r.id} className={`rev-pill ${r.id === (revId ?? artifact.head_revision_id) ? "on" : ""}`} onClick={() => setRevId(r.id)}>
              r{r.number}
              {r.id === artifact.head_revision_id ? " (head)" : ""}
            </button>
          ))}
        </div>
        {rev && <RevisionView a={artifact} rev={rev} compact />}
      </Section>
    </div>
  );
}

// ------------------------------------------------------------------ source
function SourceInspector({ sourceId }: { sourceId: string }) {
  const s = useStudio();
  const src = s.sources.find((x) => x.id === sourceId);
  const [adding, setAdding] = useState(false);
  if (!src || !s.project) return null;
  const binds = s.bindings.filter((b) => b.source_id === src.id);
  const ex = src.extracted ?? {};
  return (
    <div data-testid="source-inspector">
      <h3>{src.name}</h3>
      <div className="row small">
        <Badge>{src.media_type}</Badge> <Status s={src.processing.state} /> <span className="muted">{src.processing.extractor}</span>
      </div>
      {src.processing.error && <div className="error-box">{src.processing.error}</div>}
      {src.processing.warnings.map((w, i) => (
        <div key={i} className="warning">
          {w}
        </div>
      ))}
      {ex.understanding && (
        <div className="small muted">
          understanding: <b>{ex.understanding.kind}</b> — {ex.understanding.note ?? ex.understanding.reason}
        </div>
      )}
      {src.preview_path && <img className="insp-preview" src={fileUrl(s.project.id, src.preview_path)} alt="" />}
      <div className="small muted">
        origin {src.provenance.origin} · sha256 {src.content_hash?.slice(0, 12)}
        {src.locator.url && (
          <>
            {" "}
            · <a href={src.locator.url} target="_blank" rel="noreferrer">link</a>
          </>
        )}
      </div>
      <Section title={`Meanings & targets (${binds.length})`}>
        {binds.map((b) => (
          <Section key={b.id} title={`${b.role} → ${targetLabel(b.target, s.artifacts)}`} collapsed>
            <BindingEditor sourceId={src.id} binding={b} />
          </Section>
        ))}
        {adding ? <BindingEditor sourceId={src.id} onDone={() => setAdding(false)} /> : <button onClick={() => setAdding(true)}>+ assign meaning</button>}
      </Section>
      <Section title="Metadata & extracted data" collapsed>
        <Json value={{ metadata: src.metadata, extracted: { ...ex, text: undefined, pages: undefined } }} />
      </Section>
    </div>
  );
}

// ------------------------------------------------------------------ workflow + operation
async function saveWf(s: ReturnType<typeof useStudio>, wf: Workflow, ok: string) {
  if (!s.project) return;
  const r = await s.run(api.saveWorkflow(s.project.id, wf), ok);
  if (r) {
    await s.refresh();
    await s.reloadBoard();
  }
  return r;
}

function WorkflowActions({ wf }: { wf: Workflow }) {
  const s = useStudio();
  const [issues, setIssues] = useState<any[] | null>(null);
  const [impact, setImpact] = useState<any>(null);
  if (!s.project) return null;
  const ex = s.executions[wf.id];
  const execute = async (mode: string) => {
    const e = await s.run(api.execute(s.project!.id, wf.id, mode), `Execution started (${mode})`);
    if (e) s.watchExecution(wf.id);
  };
  return (
    <div data-testid="workflow-actions">
      <div className="row">
        <b>{wf.name}</b> <span className="muted small">v{wf.version}</span> {ex && <Status s={ex.status} />}
      </div>
      <div className="row">
        <button onClick={async () => setIssues((await s.run(api.validateWorkflow(s.project!.id, wf))) ?? [])}>Validate</button>
        <button onClick={async () => setImpact(await s.run(api.impact(s.project!.id, wf.id)))}>Preview impact</button>
        <button className="primary" onClick={() => execute("incremental")}>▶ Run</button>
        <button onClick={() => execute("full")}>Run all</button>
        {ex && <button onClick={() => s.select({ kind: "resource", ref: { kind: "execution", id: ex.id } })}>Execution details</button>}
      </div>
      {issues && (
        <div className="small">
          {issues.length === 0 ? <div className="ok-box">Workflow is valid.</div> : issues.map((i, k) => <div key={k} className={i.level === "error" ? "error-box" : "warning"}>[{i.node_id || "graph"}] {i.message}</div>)}
        </div>
      )}
      {impact && (
        <div className="impact small" data-testid="impact">
          <b>Would run now:</b>
          {impact.nodes.map((n: any) => (
            <div key={n.node_id}>
              {n.label || n.node_id}:{" "}
              {(n.units ?? []).filter((u: any) => u.stale).map((u: any) => <Badge key={u.unit} tone="warn" title={u.reasons.join("; ")}>{keyLabel(u.unit, s.artifacts)}</Badge>)}
              {!(n.units ?? []).some((u: any) => u.stale) && <span className="muted">up to date</span>}
            </div>
          ))}
        </div>
      )}
      {ex?.node_runs.filter((r) => r.status === "waiting_approval").map((r) => <ApprovalBox key={r.node_id} ex={ex} r={r} />)}
    </div>
  );
}

function OperationInspector({ item }: { item: CanvasItem }) {
  const s = useStudio();
  const wf = s.workflows.find((w) => w.id === item.resource_ref.workflow_id);
  const node = wf?.nodes.find((n) => n.id === item.resource_ref.node_id);
  const [draft, setDraft] = useState<WorkflowNode | null>(null);
  const [preview, setPreview] = useState<any>(null);
  useEffect(() => setDraft(null), [item.id, wf?.version]);
  if (!wf || !node) return <div className="warning">This workflow node no longer exists in the latest workflow version.</div>;
  const nt = s.nodeTypes.find((t) => t.type === node.type);
  const runInfo = s.executions[wf.id]?.node_runs.find((r) => r.node_id === node.id);
  const cur = draft ?? node;
  return (
    <div data-testid="operation-inspector">
      <h3>
        {node.label || nt?.title} <span className="muted small">{node.type}</span>
      </h3>
      <div className="small muted">{nt?.description}</div>
      <div className="small">
        inputs: {nt?.inputs.map((p) => <Badge key={p.name}>{p.name}:{p.type}{p.required ? "*" : ""}</Badge>)} outputs:{" "}
        {nt?.outputs.map((p) => <Badge key={p.name}>{p.name}:{p.type}</Badge>)}
      </div>
      <div className="row small">
        status <Status s={runInfo?.status ?? "idle"} /> {runInfo?.error && <span className="error-box">{runInfo.error}</span>}
      </div>
      <Section title="Configuration">
        <NodeConfigForm wf={wf} node={cur} onChange={setDraft} />
        <div className="row">
          <button
            className="primary"
            disabled={!draft}
            onClick={async () => {
              if (await saveWf(s, { ...wf, nodes: wf.nodes.map((n) => (n.id === node.id ? draft! : n)) }, `Saved workflow v${wf.version + 1}`)) setDraft(null);
            }}
          >
            Save (new workflow version)
          </button>
          <button disabled={!draft} onClick={() => setDraft(null)}>
            Discard
          </button>
          <button
            className="danger"
            onClick={async () => {
              if (!confirm(`Remove “${node.label || node.id}” from the workflow (new version)?`)) return;
              await saveWf(s, { ...wf, nodes: wf.nodes.filter((n) => n.id !== node.id), edges: wf.edges.filter((e) => e.source !== node.id && e.target !== node.id) }, "Node removed (new workflow version)");
            }}
          >
            Remove from workflow
          </button>
        </div>
      </Section>
      {node.type === "agent" && (
        <Section title="Dry run (plan without applying)" collapsed>
          <button onClick={async () => setPreview(await s.run(api.preview(s.project!.id, wf.id, node.id, true)))}>Preview plan</button>
          {preview?.units.map((u: any) => (
            <div key={u.unit} className="small">
              <b>{keyLabel(u.unit, s.artifacts)}</b> {u.stale ? <Badge tone="warn">stale</Badge> : <Badge>up to date</Badge>}
              {u.plan?.operations.map((o: any, i: number) => (
                <div key={i}>
                  <code>{o.op}</code> {o.component_id} <code>{JSON.stringify(o.params).slice(0, 90)}</code>
                </div>
              ))}
            </div>
          ))}
        </Section>
      )}
      {runInfo && runInfo.units.length > 0 && (
        <Section title="Last run units">
          {runInfo.units.map((u) => (
            <div key={u.unit} className="small">
              <Status s={u.status} /> {keyLabel(u.unit, s.artifacts)} <span className="muted">{u.reason}</span>
            </div>
          ))}
        </Section>
      )}
      <Section title="Workflow">
        <WorkflowActions wf={wf} />
      </Section>
    </div>
  );
}

// ------------------------------------------------------------------ connection
function ConnectionInspector({ connectionId }: { connectionId: string }) {
  const s = useStudio();
  const c = s.board?.connections.find((x) => x.id === connectionId);
  const [ctx, setCtx] = useState<ResolvedContext | null>(null);
  const binding = c?.domain_ref.kind === "binding" ? s.bindings.find((b) => b.id === c.domain_ref.id) : undefined;
  useEffect(() => {
    if (s.project && binding && binding.target.scope !== "project") api.resolve(s.project.id, binding.target).then(setCtx).catch(() => setCtx(null));
  }, [s.project, binding]);
  if (!c) return <div className="muted">Connection no longer exists.</div>;
  const name = (id: string) => {
    const it = s.board?.items.find((i) => i.id === id);
    if (!it) return id;
    const r = it.resource_ref;
    return (
      s.artifacts.find((a) => a.id === r.id)?.name ??
      s.sources.find((x) => x.id === r.id)?.name ??
      s.workflows.find((w) => w.id === r.workflow_id)?.nodes.find((n) => n.id === r.node_id)?.label ??
      it.presentation_state.title ??
      it.item_type
    );
  };
  return (
    <div data-testid="connection-inspector">
      <h3>{c.connection_type} relationship</h3>
      <div className="small">
        {name(c.source_item_id)} → {name(c.target_item_id)}
        {c.target_anchor.component_id && (
          <>
            {" "}
            · component <code>{c.target_anchor.component_id}</code>
          </>
        )}
      </div>
      <div className="small muted">
        {c.derived ? `derived from the domain model (${c.domain_ref.kind} ${c.domain_ref.id})` : "board-only relationship (informational, not executable)"}
      </div>
      {binding && (
        <>
          <Section title="Binding (role, aspects, constraints)">
            <BindingEditor sourceId={binding.source_id} binding={binding} />
          </Section>
          {ctx && (
            <Section title="Resolved context of the target">
              <ContextView ctx={ctx} artifacts={s.artifacts} />
            </Section>
          )}
        </>
      )}
      {c.connection_type === "execution" && <div className="small">Workflow edge {c.domain_ref.id}: port {c.source_anchor.port} → {c.target_anchor.port}. Delete with the Delete key (creates a new workflow version).</div>}
      {!c.derived && (
        <div className="form-grid">
          <label>Label</label>
          <input
            value={c.presentation_state.label ?? ""}
            onChange={(e) => s.updateBoard((b) => ({ ...b, connections: b.connections.map((x) => (x.id === c.id ? { ...x, presentation_state: { ...x.presentation_state, label: e.target.value } } : x)) }), { history: false })}
          />
        </div>
      )}
      <div className="muted small">Press Delete to remove {c.connection_type === "reference" ? "(deletes the source binding)" : c.derived ? "(edits the workflow)" : "(board only)"}.</div>
    </div>
  );
}

// ------------------------------------------------------------------ board / frames / notes
function BoardInspector() {
  const s = useStudio();
  const b = s.board;
  const [name, setName] = useState(b?.name ?? "");
  useEffect(() => setName(b?.name ?? ""), [b?.id, b?.name]);
  const counts = useMemo(() => (b?.items ?? []).reduce<Record<string, number>>((m, i) => ((m[i.item_type] = (m[i.item_type] ?? 0) + 1), m), {}), [b?.items]);
  if (!b || !s.project) return null;
  return (
    <div data-testid="board-inspector">
      <h3>Board</h3>
      <div className="form-grid">
        <label>Name</label>
        <input
          value={name}
          onChange={(e) => setName(e.target.value)}
          onBlur={async () => {
            if (name && name !== b.name && (await s.run(boardApi.rename(s.project!.id, b.id, name), "Board renamed"))) s.updateBoard((x) => ({ ...x, name }), { history: false });
          }}
        />
      </div>
      <div className="small">
        {Object.entries(counts).map(([k, v]) => (
          <Badge key={k}>
            {v} {k.replace("_", " ")}
          </Badge>
        ))}
        <Badge>{b.connections.length} connections</Badge>
      </div>
      {(b.missing_items?.length ?? 0) > 0 && <div className="warning">{b.missing_items!.length} item(s) point at resources that no longer exist.</div>}
      <div className="small muted">
        Layout autosaves (revision {b.revision}). Undo/redo (Ctrl+Z / Ctrl+Shift+Z) affects board layout only; artifact changes are reverted through revisions.
      </div>
      <Section title="Interaction">
        <div className="small">
          Click selects · double-click / Enter edits in place · ⤢ focuses · Esc exits one level · drag a header to move · drag a reference (◉ handle, or from the
          explorer) onto an artifact or a component chip to bind it · Shift-drag box selects · Shift+1 fit · Shift+2 zoom to selection · Ctrl+D duplicates a view.
        </div>
      </Section>
    </div>
  );
}

function FrameInspector({ item }: { item: CanvasItem }) {
  const s = useStudio();
  const wf = item.item_type === "workflow" ? s.workflows.find((w) => w.id === item.resource_ref.id) : undefined;
  return (
    <div>
      <h3>{wf ? `Workflow group` : "Frame"}</h3>
      {!wf && (
        <div className="form-grid">
          <label>Title</label>
          <input value={item.presentation_state.title ?? ""} onChange={(e) => s.patchPresentation(item.id, { title: e.target.value })} />
          <label>Colour</label>
          <input type="color" value={item.presentation_state.color ?? "#3f6ca8"} onChange={(e) => s.patchPresentation(item.id, { color: e.target.value })} />
        </div>
      )}
      {wf && <WorkflowActions wf={wf} />}
      <div className="small muted">Moving the frame moves the {s.board?.items.filter((i) => i.group_id === item.id).length} items inside it.</div>
    </div>
  );
}

// ------------------------------------------------------------------ dispatcher
export function InspectorPanel() {
  const s = useStudio();
  const sel = s.selection;
  let body: React.ReactNode = <BoardInspector />;
  if (sel.kind === "component") body = <ComponentInspector artifactId={sel.artifactId} componentId={sel.componentId} itemId={sel.itemId} />;
  else if (sel.kind === "connection") body = <ConnectionInspector connectionId={sel.connectionId} />;
  else if (sel.kind === "resource" && sel.ref.kind === "execution" && sel.ref.id) body = <ExecutionView executionId={sel.ref.id} />;
  else if (sel.kind === "resource" && sel.ref.kind === "source" && sel.ref.id) body = <SourceInspector sourceId={sel.ref.id} />;
  else if (sel.kind === "resource" && sel.ref.kind === "workflow") {
    const wf = s.workflows.find((w) => w.id === sel.ref.id);
    body = wf ? <WorkflowActions wf={wf} /> : null;
  } else if (sel.kind === "resource" && sel.ref.kind === "artifact") {
    const a = s.artifacts.find((x) => x.id === sel.ref.id);
    const item = s.board?.items.find((i) => i.resource_ref.id === sel.ref.id && i.item_type === "artifact_view");
    body = a && item ? <ArtifactInspector item={item} artifact={a} /> : a ? <div className="small">“{a.name}” is not on this board. Drag it from the explorer.</div> : null;
  } else if (sel.kind === "items" && sel.itemIds.length > 1) {
    body = (
      <div>
        <h3>{sel.itemIds.length} items selected</h3>
        <div className="small muted">Use “+ Frame” to group them, Delete to remove them from the board (resources are kept).</div>
      </div>
    );
  } else if (sel.kind === "items" && sel.itemIds.length === 1) {
    const item = s.board?.items.find((i) => i.id === sel.itemIds[0]);
    if (item) {
      const r = item.resource_ref;
      if (item.item_type === "artifact_view") {
        const a = s.artifacts.find((x) => x.id === r.id);
        body = a ? <ArtifactInspector item={item} artifact={a} /> : null;
      } else if (item.item_type === "source" && r.id) body = <SourceInspector sourceId={r.id} />;
      else if (item.item_type === "operation") body = <OperationInspector item={item} />;
      else if (item.item_type === "frame" || item.item_type === "workflow") body = <FrameInspector item={item} />;
      else if (item.item_type === "note")
        body = (
          <div>
            <h3>Note</h3>
            <textarea rows={6} value={item.presentation_state.text ?? ""} onChange={(e) => s.patchPresentation(item.id, { text: e.target.value })} />
            <input type="color" value={item.presentation_state.color ?? "#4a4020"} onChange={(e) => s.patchPresentation(item.id, { color: e.target.value })} />
          </div>
        );
    }
  }
  return (
    <aside className="inspector-panel" data-testid="inspector">
      {body}
    </aside>
  );
}

export type { WorkflowNode };
