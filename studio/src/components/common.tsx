import { useState, type ReactNode } from "react";
import type { Artifact, Component, TargetSelector } from "../types";

export function Status({ s }: { s: string }) {
  return <span className={`status status-${s}`}>{s.replace("_", " ")}</span>;
}

export function Badge({ children, tone = "default", title }: { children: ReactNode; tone?: string; title?: string }) {
  return (
    <span className={`badge badge-${tone}`} title={title}>
      {children}
    </span>
  );
}

export function Section({ title, children, actions, collapsed = false }: { title: ReactNode; children: ReactNode; actions?: ReactNode; collapsed?: boolean }) {
  const [open, setOpen] = useState(!collapsed);
  return (
    <section className="section">
      <header>
        <button className="link" onClick={() => setOpen(!open)}>
          {open ? "▾" : "▸"} {title}
        </button>
        <div className="actions">{actions}</div>
      </header>
      {open && <div className="section-body">{children}</div>}
    </section>
  );
}

export function Json({ value, max = 400 }: { value: any; max?: number }) {
  return <pre className="json" style={{ maxHeight: max }}>{JSON.stringify(value, null, 2)}</pre>;
}

export function Empty({ children }: { children: ReactNode }) {
  return <div className="empty">{children}</div>;
}

export function short(id?: string | null, n = 8) {
  if (!id) return "";
  const i = id.indexOf("_");
  return i > 0 ? id.slice(i + 1, i + 1 + n) : id.slice(0, n);
}

export function targetLabel(t: TargetSelector, artifacts: Artifact[]) {
  if (t.scope === "project") return "Project (all artifacts)";
  const a = artifacts.find((x) => x.id === t.artifact_id);
  const an = a?.name ?? t.artifact_id;
  if (t.scope === "artifact") return `${an}`;
  const chain: string[] = [];
  let c: Component | undefined = a?.components.find((x) => x.id === t.component_id);
  while (c) {
    chain.unshift(c.name);
    const pid: string | null | undefined = c.parent_id;
    c = pid ? a?.components.find((x) => x.id === pid) : undefined;
  }
  return `${an} → ${chain.length ? chain.join(" → ") : t.component_id}`;
}

export function keyLabel(key: string, artifacts: Artifact[]) {
  if (key === "project") return "Project";
  const m = key.match(/^artifact:([^#]+)(?:#(.+))?$/);
  if (!m) return key;
  return targetLabel(
    m[2] ? { scope: "component", artifact_id: m[1], component_id: m[2] } : { scope: "artifact", artifact_id: m[1] },
    artifacts,
  );
}

export function ComponentTree({
  artifact,
  selected,
  onSelect,
  annotate,
}: {
  artifact: Artifact;
  selected?: string | null;
  onSelect?: (cid: string) => void;
  annotate?: (c: Component) => ReactNode;
}) {
  const kids = (pid: string | null) => artifact.components.filter((c) => (c.parent_id ?? null) === pid);
  const render = (c: Component, depth: number): ReactNode => (
    <div key={c.id}>
      <div
        className={`tree-row ${selected === c.id ? "selected" : ""}`}
        style={{ paddingLeft: 6 + depth * 14 }}
        onClick={() => onSelect?.(c.id)}
      >
        <span className="kind">{c.kind}</span> {c.name}
        <span className="muted"> {c.id !== c.name ? c.id : ""}</span>
        {annotate?.(c)}
      </div>
      {kids(c.id).map((k) => render(k, depth + 1))}
    </div>
  );
  return <div className="tree">{kids(null).map((c) => render(c, 0))}</div>;
}
