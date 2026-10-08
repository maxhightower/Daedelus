import type { ReactNode } from "react";
import { useStudio } from "../state";
import type { Workflow, WorkflowNode } from "../types";

/** Schema-driven configuration form for one workflow node (shared by the inspector). */
export function NodeConfigForm({ wf, node, onChange }: { wf: Workflow; node: WorkflowNode; onChange: (n: WorkflowNode) => void }) {
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

function FragmentRow({ label, hint, children }: { label: string; hint?: string; children: ReactNode }) {
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
