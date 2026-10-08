import {
  Background,
  Controls,
  Handle,
  MiniMap,
  Position,
  ReactFlow,
  ReactFlowProvider,
  applyEdgeChanges,
  applyNodeChanges,
  type Connection,
  type Edge,
  type EdgeChange,
  type Node,
  type NodeChange,
  type NodeProps,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api } from "../api";
import { Badge, Empty, Json, Section, Status, keyLabel } from "../components/common";
import { ContextView } from "../components/ContextView";
import { useStudio } from "../state";
import type { Execution, NodeType, Workflow, WorkflowNode } from "../types";

type DNodeData = {
  wn: WorkflowNode;
  nt?: NodeType;
  status?: string;
  units?: { unit: string; status: string }[];
  issues: string[];
  subtitle?: string;
};

const PORT_COLORS: Record<string, string> = {
  sources: "#d6a84a",
  artifact: "#5aa9e6",
  revision: "#7bc86c",
  report: "#c77dff",
  files: "#9aa5b1",
  any: "#e0e0e0",
};

function DNode({ data, selected }: NodeProps<Node<DNodeData>>) {
  const { wn, nt, status, units, issues, subtitle } = data;
  const rows = Math.max(nt?.inputs.length ?? 0, nt?.outputs.length ?? 0, 1);
  return (
    <div className={`dnode cat-${nt?.category ?? "x"} ${selected ? "sel" : ""} ${issues.length ? "has-issue" : ""}`}>
      <div className="dnode-head">
        <span>{wn.label || nt?.title || wn.type}</span>
        {status && <Status s={status} />}
      </div>
      <div className="dnode-sub">
        {nt?.title ?? wn.type}
        {subtitle ? ` · ${subtitle}` : ""}
      </div>
      <div className="dnode-ports" style={{ height: rows * 22 }}>
        {nt?.inputs.map((p, i) => (
          <div key={p.name} className="port in" style={{ top: i * 22 }}>
            <Handle
              type="target"
              position={Position.Left}
              id={p.name}
              style={{ background: PORT_COLORS[p.type] ?? "#aaa", top: 11 }}
            />
            <span>
              {p.name}
              {p.required ? "*" : ""}
            </span>
          </div>
        ))}
        {nt?.outputs.map((p, i) => (
          <div key={p.name} className="port out" style={{ top: i * 22 }}>
            <span>{p.name}</span>
            <Handle
              type="source"
              position={Position.Right}
              id={p.name}
              style={{ background: PORT_COLORS[p.type] ?? "#aaa", top: 11 }}
            />
          </div>
        ))}
      </div>
      {units && units.length > 0 && (
        <div className="dnode-units">
          {units.map((u) => (
            <div key={u.unit} className={`unit status-${u.status}`}>
              {u.unit.split("#").pop()} · {u.status}
            </div>
          ))}
        </div>
      )}
      {issues.length > 0 && <div className="dnode-issue">{issues[0]}</div>}
    </div>
  );
}

const nodeTypesRF = { dnode: DNode };

function uid(prefix: string) {
  return `${prefix}_${Math.random().toString(36).slice(2, 8)}`;
}

function ConfigForm({ wf, node, onChange }: { wf: Workflow; node: WorkflowNode; onChange: (n: WorkflowNode) => void }) {
  const { nodeTypes, artifacts, sources, health } = useStudio();
  const nt = nodeTypes.find((n) => n.type === node.type);
  if (!nt) return <div className="error-box">Unknown node type {node.type}</div>;
  const cfg = { ...nt.defaults, ...node.config };
  const setCfg = (k: string, v: any) => onChange({ ...node, config: { ...node.config, [k]: v } });
  // connected artifact (for agent nodes)
  const artEdge = wf.edges.find((e) => e.target === node.id && e.target_port === "artifact");
  const artNode = artEdge && wf.nodes.find((n) => n.id === artEdge.source);
  const art = artifacts.find((a) => a.id === artNode?.config.artifact_id);
  const adapter = health?.adapters.find((a) => a.name === art?.adapter);
  const chips = (k: string, options: string[]) => {
    const cur: string[] = cfg[k] ?? [];
    return (
      <div className="chips">
        {options.map((o) => (
          <button key={o} className={`chip ${cur.includes(o) ? "on" : ""}`} onClick={() => setCfg(k, cur.includes(o) ? cur.filter((x) => x !== o) : [...cur, o])}>
            {o}
          </button>
        ))}
      </div>
    );
  };
  const field = (k: string, sch: any) => {
    const v = cfg[k];
    if (k === "artifact_id")
      return (
        <select value={v ?? ""} onChange={(e) => setCfg(k, e.target.value)}>
          <option value="">— choose artifact —</option>
          {artifacts.map((a) => (
            <option key={a.id} value={a.id}>
              {a.name} ({a.adapter})
            </option>
          ))}
        </select>
      );
    if (k === "target_component")
      return (
        <select value={v ?? ""} onChange={(e) => setCfg(k, e.target.value)}>
          <option value="">artifact root</option>
          {art?.components.map((c) => (
            <option key={c.id} value={c.id}>
              {c.name} [{c.kind}]
            </option>
          ))}
        </select>
      );
    if (k === "provider")
      return (
        <select value={v ?? ""} onChange={(e) => setCfg(k, e.target.value)}>
          <option value="">project default</option>
          {health?.providers.map((p) => (
            <option key={p.name} value={p.name}>
              {p.name} {p.available ? "" : `(unavailable: ${p.detail})`}
            </option>
          ))}
        </select>
      );
    if (k === "source_ids") return (
      <div className="chips">
        {sources.map((s) => {
          const cur: string[] = cfg[k] ?? [];
          return (
            <button key={s.id} className={`chip ${cur.includes(s.id) ? "on" : ""}`} onClick={() => setCfg(k, cur.includes(s.id) ? cur.filter((x) => x !== s.id) : [...cur, s.id])}>
              {s.name}
            </button>
          );
        })}
        <span className="muted small">(none selected = all sources)</span>
      </div>
    );
    if (k === "media_types") return chips(k, ["text", "image", "video", "video_url", "document", "model3d", "code", "audio", "url"]);
    if (k === "allowed_ops") return adapter ? chips(k, adapter.operations.map((o) => o.name)) : <span className="muted small">connect an artifact first</span>;
    if (k === "formats") return chips(k, Array.from(new Set((health?.adapters ?? []).flatMap((a) => a.export_formats))));
    if (k === "validation") return chips(k, ["file_reopens", "json_valid"]);
    if (sch.type === "array" && sch.items?.enum) return chips(k, sch.items.enum);
    if (sch.enum)
      return (
        <select value={v ?? ""} onChange={(e) => setCfg(k, e.target.value)}>
          {sch.enum.map((o: string) => (
            <option key={o}>{o}</option>
          ))}
        </select>
      );
    if (sch.type === "boolean") return <input type="checkbox" checked={!!v} onChange={(e) => setCfg(k, e.target.checked)} />;
    if (sch.type === "number" || sch.type === "integer")
      return <input type="number" className="num" value={v ?? ""} onChange={(e) => setCfg(k, e.target.value === "" ? undefined : +e.target.value)} />;
    if (sch.type === "object" && sch.properties)
      return (
        <div className="row">
          {Object.entries<any>(sch.properties).map(([sk]) => (
            <label key={sk} className="inline">
              {sk}{" "}
              <input type="number" className="num" value={v?.[sk] ?? ""} onChange={(e) => setCfg(k, { ...(v ?? {}), [sk]: +e.target.value })} />
            </label>
          ))}
        </div>
      );
    if (k === "instructions" || k === "message")
      return <textarea rows={3} value={v ?? ""} onChange={(e) => setCfg(k, e.target.value)} placeholder="key: value lines are machine-readable directives" />;
    return <input value={v ?? ""} onChange={(e) => setCfg(k, e.target.value)} />;
  };
  return (
    <div className="form-grid">
      <label>Label</label>
      <input value={node.label} onChange={(e) => onChange({ ...node, label: e.target.value })} />
      {Object.entries<any>(nt.config_schema.properties ?? {}).map(([k, sch]) => (
        <FragmentRow key={k} label={k} hint={sch.description}>
          {field(k, sch)}
        </FragmentRow>
      ))}
    </div>
  );
}

function FragmentRow({ label, hint, children }: { label: string; hint?: string; children: React.ReactNode }) {
  return (
    <>
      <label title={hint}>{label.replace(/_/g, " ")}</label>
      <div>
        {children}
        {hint && <div className="muted small">{hint}</div>}
      </div>
    </>
  );
}

function Editor() {
  const { project, workflows, nodeTypes, artifacts, focus, setFocus, run, refresh, notify } = useStudio();
  const wid = focus.workflow ?? workflows[0]?.id;
  const [wf, setWf] = useState<Workflow | null>(null);
  const [dirty, setDirty] = useState(false);
  const [versions, setVersions] = useState<{ version: number; created_at: string }[]>([]);
  const [selected, setSelected] = useState<string | null>(null);
  const [issues, setIssues] = useState<{ node_id: string; level: string; message: string }[]>([]);
  const [impact, setImpact] = useState<any>(null);
  const [preview, setPreview] = useState<any>(null);
  const [exec, setExec] = useState<Execution | null>(null);
  const [rfNodes, setRfNodes] = useState<Node<DNodeData>[]>([]);
  const [rfEdges, setRfEdges] = useState<Edge[]>([]);
  const fileRef = useRef<HTMLInputElement>(null);

  const load = useCallback(
    async (id: string, version?: number) => {
      if (!project) return;
      const w = await run(api.workflow(project.id, id, version));
      if (w) {
        setWf(w);
        setDirty(false);
        setVersions((await run(api.versions(project.id, id))) ?? []);
        setImpact(null);
        setPreview(null);
      }
    },
    [project, run],
  );
  useEffect(() => {
    if (wid) load(wid);
    else setWf(null);
  }, [wid, load]);

  // latest execution for status overlay (polls while running)
  useEffect(() => {
    if (!project || !wf) return;
    let alive = true;
    let timer: any;
    const tick = async () => {
      try {
        const list = await api.executions(project.id, wf.id);
        if (list[0]) {
          const e = await api.execution(project.id, list[0].id);
          if (!alive) return;
          setExec(e);
          if (["pending", "running"].includes(e.status)) timer = setTimeout(tick, 1200);
          else if (exec && exec.id === e.id && exec.status !== e.status) refresh();
        } else setExec(null);
      } catch {
        /* ignore polling errors */
      }
    };
    tick();
    return () => {
      alive = false;
      clearTimeout(timer);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [project, wf?.id, exec?.id, exec?.status]);

  const issuesByNode = useMemo(() => {
    const m: Record<string, string[]> = {};
    issues.forEach((i) => (m[i.node_id] = [...(m[i.node_id] ?? []), i.message]));
    return m;
  }, [issues]);

  useEffect(() => {
    if (!wf) return;
    setRfNodes((prev) =>
      wf.nodes.map((n) => {
        const run = exec?.workflow_id === wf.id ? exec.node_runs.find((r) => r.node_id === n.id) : undefined;
        const nt = nodeTypes.find((t) => t.type === n.type);
        const art = n.type === "artifact" ? artifacts.find((a) => a.id === n.config.artifact_id)?.name : undefined;
        return {
          id: n.id,
          type: "dnode",
          position: n.position,
          selected: prev.find((p) => p.id === n.id)?.selected ?? n.id === selected,
          data: {
            wn: n,
            nt,
            status: run?.status,
            units: run?.units.map((u) => ({ unit: u.unit, status: u.status })),
            issues: issuesByNode[n.id] ?? [],
            subtitle: art,
          },
        };
      }),
    );
    setRfEdges(
      wf.edges.map((e) => ({
        id: e.id,
        source: e.source,
        sourceHandle: e.source_port,
        target: e.target,
        targetHandle: e.target_port,
        animated: exec?.status === "running",
      })),
    );
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [wf, exec, nodeTypes, issuesByNode, artifacts]);

  const update = (next: Workflow) => {
    setWf(next);
    setDirty(true);
  };

  const onNodesChange = (changes: NodeChange<Node<DNodeData>>[]) => {
    setRfNodes((ns) => applyNodeChanges(changes, ns));
    if (!wf) return;
    let changed = false;
    let nodes = wf.nodes;
    for (const c of changes) {
      if (c.type === "position" && c.position && !c.dragging) {
        nodes = nodes.map((n) => (n.id === c.id ? { ...n, position: c.position! } : n));
        changed = true;
      }
      if (c.type === "select" && c.selected) setSelected(c.id);
      if (c.type === "remove") {
        nodes = nodes.filter((n) => n.id !== c.id);
        changed = true;
      }
    }
    if (changed) {
      const ids = new Set(nodes.map((n) => n.id));
      update({ ...wf, nodes, edges: wf.edges.filter((e) => ids.has(e.source) && ids.has(e.target)) });
    }
  };
  const onEdgesChange = (changes: EdgeChange[]) => {
    setRfEdges((es) => applyEdgeChanges(changes, es));
    if (!wf) return;
    const removed = changes.filter((c) => c.type === "remove").map((c) => (c as any).id);
    if (removed.length) update({ ...wf, edges: wf.edges.filter((e) => !removed.includes(e.id)) });
  };
  const portType = (nodeId: string, port: string, dir: "in" | "out") => {
    const n = wf?.nodes.find((x) => x.id === nodeId);
    const nt = nodeTypes.find((t) => t.type === n?.type);
    return (dir === "in" ? nt?.inputs : nt?.outputs)?.find((p) => p.name === port);
  };
  const isValidConnection = (c: Connection | Edge) => {
    const s = portType(c.source!, c.sourceHandle!, "out");
    const t = portType(c.target!, c.targetHandle!, "in");
    if (!s || !t || c.source === c.target) return false;
    if (!(s.type === t.type || s.type === "any" || t.type === "any")) return false;
    if (!t.multiple && wf?.edges.some((e) => e.target === c.target && e.target_port === c.targetHandle)) return false;
    return true;
  };
  const onConnect = (c: Connection) => {
    if (!wf || !isValidConnection(c)) return;
    update({
      ...wf,
      edges: [...wf.edges, { id: uid("e"), source: c.source!, source_port: c.sourceHandle!, target: c.target!, target_port: c.targetHandle! }],
    });
  };
  const addNode = (nt: NodeType) => {
    if (!wf) return;
    const n: WorkflowNode = {
      id: uid(nt.type),
      type: nt.type,
      label: nt.title,
      config: JSON.parse(JSON.stringify(nt.defaults)),
      position: { x: 100 + Math.random() * 200, y: 100 + Math.random() * 200 },
    };
    update({ ...wf, nodes: [...wf.nodes, n] });
    setSelected(n.id);
  };

  if (!project) return <Empty>Select a project.</Empty>;

  const save = async () => {
    if (!wf) return;
    const r = await run(api.saveWorkflow(project.id, wf), "Workflow saved as a new version");
    if (r) {
      setIssues(r.issues);
      await refresh();
      await load(wf.id);
    }
  };
  const validate = async () => wf && setIssues((await run(api.validateWorkflow(project.id, wf))) ?? []);
  const execute = async (mode: string) => {
    if (!wf) return;
    if (dirty) {
      notify("Save the workflow before running it.");
      return;
    }
    const e = await run(api.execute(project.id, wf.id, mode), `Execution started (${mode})`);
    if (e) setExec(e);
  };
  const doExport = async () => {
    if (!wf) return;
    const doc = await run(api.exportWorkflow(project.id, wf.id));
    if (!doc) return;
    const blob = new Blob([JSON.stringify(doc, null, 2)], { type: "application/json" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = `${wf.name.replace(/\W+/g, "_")}.v${wf.version}.daedelus.json`;
    a.click();
  };
  const doImport = async (f: File) => {
    const doc = JSON.parse(await f.text());
    const r = await run(api.importWorkflow(project.id, doc), "Workflow imported");
    if (r) {
      await refresh();
      setFocus("workflow", r.workflow.id);
      setIssues(r.issues);
    }
  };
  const selNode = wf?.nodes.find((n) => n.id === selected);
  const selRun = exec?.node_runs.find((r) => r.node_id === selected);

  return (
    <div className="workflow-panel">
      <div className="toolbar">
        <select value={wid ?? ""} onChange={(e) => setFocus("workflow", e.target.value)}>
          {workflows.map((w) => (
            <option key={w.id} value={w.id}>
              {w.name}
            </option>
          ))}
        </select>
        {wf && (
          <select value={wf.version} onChange={(e) => load(wf.id, +e.target.value)} title="Load a saved version (saving it creates a new version)">
            {versions.map((v) => (
              <option key={v.version} value={v.version}>
                v{v.version} · {v.created_at.slice(5, 16)}
              </option>
            ))}
          </select>
        )}
        <button
          onClick={async () => {
            const w = await run(api.createWorkflow(project.id, { name: "New workflow", nodes: [], edges: [] } as any), "Workflow created");
            if (w) {
              await refresh();
              setFocus("workflow", w.id);
            }
          }}
        >
          New
        </button>
        <button className={dirty ? "primary" : ""} onClick={save} disabled={!wf}>
          Save{dirty ? " *" : ""}
        </button>
        <button onClick={validate} disabled={!wf}>
          Validate
        </button>
        <button onClick={async () => wf && setImpact(await run(api.impact(project.id, wf.id)))} disabled={!wf || dirty}>
          Impact
        </button>
        <button className="primary" onClick={() => execute("incremental")} disabled={!wf}>
          ▶ Run
        </button>
        <button onClick={() => execute("full")} disabled={!wf} title="Re-execute every unit regardless of fingerprints">
          Run all
        </button>
        <button onClick={doExport} disabled={!wf}>
          Export
        </button>
        <button onClick={() => fileRef.current?.click()}>Import</button>
        <input ref={fileRef} type="file" accept=".json" hidden onChange={(e) => e.target.files?.[0] && doImport(e.target.files[0])} />
        {exec && (
          <span className="muted small">
            last run <Status s={exec.status} /> {exec.created_at.slice(11, 19)}
          </span>
        )}
      </div>
      <div className="wf-body">
        <div className="palette">
          <div className="muted small">Add node</div>
          {nodeTypes.map((nt) => (
            <button key={nt.type} className={`pal cat-${nt.category}`} onClick={() => addNode(nt)} title={nt.description}>
              + {nt.title}
            </button>
          ))}
          <div className="muted small legend">
            {Object.entries(PORT_COLORS).map(([k, c]) => (
              <div key={k}>
                <span className="dot" style={{ background: c }} /> {k}
              </div>
            ))}
          </div>
        </div>
        <div className="canvas">
          {wf ? (
            <ReactFlow
              nodes={rfNodes}
              edges={rfEdges}
              nodeTypes={nodeTypesRF}
              onNodesChange={onNodesChange}
              onEdgesChange={onEdgesChange}
              onConnect={onConnect}
              isValidConnection={isValidConnection}
              onPaneClick={() => setSelected(null)}
              fitView
              deleteKeyCode={["Delete", "Backspace"]}
              colorMode="dark"
            >
              <Background />
              <MiniMap pannable zoomable />
              <Controls />
            </ReactFlow>
          ) : (
            <Empty>No workflow. Create one.</Empty>
          )}
        </div>
        <div className="inspector">
          {wf && !selNode && (
            <>
              <h3>Workflow</h3>
              <div className="form-grid">
                <label>Name</label>
                <input value={wf.name} onChange={(e) => update({ ...wf, name: e.target.value })} />
                <label>Description</label>
                <textarea rows={3} value={wf.description} onChange={(e) => update({ ...wf, description: e.target.value })} />
              </div>
              <div className="muted small">
                id {wf.id} · version {wf.version} · {wf.nodes.length} nodes · {wf.edges.length} edges
              </div>
            </>
          )}
          {issues.length > 0 && (
            <Section title={`Validation (${issues.length})`}>
              {issues.map((i, k) => (
                <div key={k} className={i.level === "error" ? "error-box" : "warning"}>
                  [{i.node_id || "graph"}] {i.message}
                </div>
              ))}
            </Section>
          )}
          {issues.length === 0 && wf && <div className="muted small">No validation issues reported (click Validate).</div>}
          {impact && (
            <Section title="Impact: what would run now">
              <div className="small">
                Affected artifacts: {impact.affected_artifacts.map((a: string) => artifacts.find((x) => x.id === a)?.name ?? a).join(", ") || "none"}
              </div>
              {impact.nodes.map((n: any) => (
                <div key={n.node_id} className="small">
                  <b>{n.label || n.node_id}</b> ({n.artifact})
                  {n.error && <div className="error-box">{n.error}</div>}
                  <ul>
                    {(n.units ?? []).map((u: any) => (
                      <li key={u.unit} className={u.stale ? "" : "dim"}>
                        {keyLabel(u.unit, artifacts)}: {u.stale ? u.reasons.join("; ") : "up to date"}
                      </li>
                    ))}
                  </ul>
                </div>
              ))}
            </Section>
          )}
          {selNode && wf && (
            <>
              <h3>
                {selNode.label || selNode.type} <span className="muted small">{selNode.id}</span>
              </h3>
              <div className="muted small">{nodeTypes.find((t) => t.type === selNode.type)?.description}</div>
              <ConfigForm wf={wf} node={selNode} onChange={(n) => update({ ...wf, nodes: wf.nodes.map((x) => (x.id === n.id ? n : x)) })} />
              {selNode.type === "agent" && (
                <div className="row">
                  <button
                    disabled={dirty}
                    onClick={async () => setPreview(await run(api.preview(project.id, wf.id, selNode.id, true)))}
                    title="Resolve inputs and plan every unit without applying anything"
                  >
                    Preview plan (dry run)
                  </button>
                </div>
              )}
              {preview && preview.node_id === selNode.id && (
                <Section title="Planned operations (not applied)">
                  {preview.units.map((u: any) => (
                    <div key={u.unit} className="unit-preview">
                      <b>{keyLabel(u.unit, artifacts)}</b> {u.stale ? <Badge tone="warn">stale</Badge> : <Badge>up to date</Badge>}
                      {u.error && <div className="error-box">{u.error}</div>}
                      {u.plan && (
                        <table className="grid small">
                          <tbody>
                            {u.plan.operations.map((o: any, i: number) => (
                              <tr key={i}>
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
                      <Section title="Context" collapsed>
                        <ContextView ctx={u.context} artifacts={artifacts} />
                      </Section>
                    </div>
                  ))}
                </Section>
              )}
              {selRun && (
                <Section title={<>Last run: <Status s={selRun.status} /></>}>
                  {selRun.error && <div className="error-box">{selRun.error}</div>}
                  {selRun.units.map((u) => (
                    <div key={u.unit} className="small">
                      {keyLabel(u.unit, artifacts)} — <Status s={u.status} /> {u.reason}
                    </div>
                  ))}
                  <Json value={selRun.outputs} max={200} />
                </Section>
              )}
              <button
                className="danger"
                onClick={() => {
                  update({ ...wf, nodes: wf.nodes.filter((n) => n.id !== selNode.id), edges: wf.edges.filter((e) => e.source !== selNode.id && e.target !== selNode.id) });
                  setSelected(null);
                }}
              >
                Remove node
              </button>
            </>
          )}
        </div>
      </div>
    </div>
  );
}

export function WorkflowPanel() {
  return (
    <ReactFlowProvider>
      <Editor />
    </ReactFlowProvider>
  );
}
