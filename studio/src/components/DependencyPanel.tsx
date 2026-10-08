import { useCallback, useEffect, useState } from "react";
import { officeApi, type DependencyStatus } from "../api";
import { invalidateRevision } from "../canvas/hooks";
import { useStudio } from "../state";
import type { Artifact } from "../types";
import { Badge } from "./common";

const OFFICE = ["spreadsheet", "document", "presentation"];
const TONE: Record<string, "ok" | "warn" | "err" | "info"> = {
  synced: "ok",
  stale: "warn",
  never_synced: "info",
  needs_recalc: "warn",
  missing_source: "err",
  missing_target: "err",
};
// mirrors backend ROUTES: which target kinds a source kind can feed
const ROUTES: Record<string, string[]> = {
  range: ["table", "chart", "paragraph", "text", "title"],
  table: ["table", "chart"],
  chart: ["figure", "image"],
  section: ["text", "paragraph"],
  paragraph: ["text"],
};

/** Cross-artifact component dependencies of an Office artifact (optionally one component). */
export function DependencyPanel({ artifact, componentId }: { artifact: Artifact; componentId?: string | null }) {
  const s = useStudio();
  const [deps, setDeps] = useState<DependencyStatus[] | null>(null);
  const [busy, setBusy] = useState(false);
  const load = useCallback(() => {
    if (s.project) officeApi.dependencies(s.project.id, artifact.id).then(setDeps).catch(() => setDeps([]));
  }, [s.project, artifact.id]);
  useEffect(load, [load, s.artifacts]);
  if (!s.project || !OFFICE.includes(artifact.adapter)) return null;
  const rel = (deps ?? []).filter((d) => !componentId || d.source.component_id === componentId || d.target.component_id === componentId);
  const feeds = rel.filter((d) => d.source.artifact_id === artifact.id);
  const uses = rel.filter((d) => d.target.artifact_id === artifact.id);
  const sync = async (body: any, label: string) => {
    setBusy(true);
    const r = await s.run(officeApi.sync(s.project!.id, body), label);
    setBusy(false);
    if (r) {
      r.revisions.forEach((x) => invalidateRevision(x.revision_id));
      setDeps(r.status.filter((d) => [d.source.artifact_id, d.target.artifact_id].includes(artifact.id)));
      await s.refresh();
    }
  };
  const row = (d: DependencyStatus, outgoing: boolean) => (
    <div key={d.id} className="dep-row" data-testid="dep-row" data-status={d.status}>
      <Badge tone={TONE[d.status] ?? "info"}>{d.status.replace("_", " ")}</Badge>{" "}
      <span className="small">
        {outgoing ? (
          <>
            <code>{d.source.component_id}</code> → {d.target.artifact_name} / <code>{d.target.component_id}</code>
          </>
        ) : (
          <>
            {d.source.artifact_name} / <code>{d.source.component_id}</code> → <code>{d.target.component_id}</code>
          </>
        )}
      </span>
      <div className="muted small">
        {d.route}
        {d.reason ? ` · ${d.reason}` : ""}
      </div>
      <div className="row">
        <button className="mini-btn" disabled={busy || d.status === "synced"} onClick={() => sync({ dependency_ids: [d.id] }, `Updated ${d.target.component_id}`)}>
          Update
        </button>
        <button
          className="mini-btn"
          onClick={async () => {
            if (!confirm("Remove this dependency link? (the target content is kept)")) return;
            await s.run(officeApi.removeDependency(s.project!.id, d.id), "Dependency removed");
            load();
          }}
        >
          Unlink
        </button>
      </div>
    </div>
  );
  const staleOut = feeds.filter((d) => d.status === "stale" || d.status === "never_synced");
  const staleIn = uses.filter((d) => d.status === "stale" || d.status === "never_synced");
  return (
    <div className="dep-panel" data-testid="dependency-panel">
      <div className="row">
        <button className="primary" disabled={busy || !staleOut.length} onClick={() => sync({ dependency_ids: staleOut.map((d) => d.id) }, `Updated ${staleOut.length} dependent(s)`)} data-testid="update-dependents">
          Update dependents ({staleOut.length})
        </button>
        <button disabled={busy || !staleIn.length} onClick={() => sync({ dependency_ids: staleIn.map((d) => d.id) }, `Updated from ${staleIn.length} source(s)`)}>
          Update from sources ({staleIn.length})
        </button>
      </div>
      {deps === null && <div className="muted small">…</div>}
      {feeds.length > 0 && <b className="small">Feeds</b>}
      {feeds.map((d) => row(d, true))}
      {uses.length > 0 && <b className="small">Uses</b>}
      {uses.map((d) => row(d, false))}
      {deps !== null && !rel.length && <div className="muted small">No dependencies{componentId ? " on this component" : ""}.</div>}
      {componentId && <LinkForm artifact={artifact} componentId={componentId} onDone={load} />}
    </div>
  );
}

function LinkForm({ artifact, componentId, onDone }: { artifact: Artifact; componentId: string; onDone: () => void }) {
  const s = useStudio();
  const comp = artifact.components.find((c) => c.id === componentId);
  const kinds = ROUTES[comp?.kind ?? ""] ?? [];
  const [tid, setTid] = useState("");
  const [tc, setTc] = useState("");
  const [newId, setNewId] = useState("");
  const [newKind, setNewKind] = useState(kinds[0] ?? "");
  const [template, setTemplate] = useState("");
  if (!kinds.length) return <div className="muted small">A {comp?.kind} cannot feed other artifacts.</div>;
  const targets = s.artifacts.filter((a) => a.id !== artifact.id && OFFICE.includes(a.adapter));
  const target = targets.find((a) => a.id === tid);
  const comps = (target?.components ?? []).filter((c) => kinds.includes(c.kind));
  const targetKind = tc === "__new" ? newKind : comps.find((c) => c.id === tc)?.kind;
  const create = async () => {
    const options: any = {};
    if (template) options.template = template;
    if (tc === "__new") {
      const parent = target!.components.find((c) => c.kind === (target!.adapter === "presentation" ? "slide" : "section"));
      options.create = target!.adapter === "presentation" ? { slide: parent?.id, box: [0.08, 0.22, 0.84, 0.7] } : { parent: parent?.id };
    }
    const r = await s.run(
      officeApi.addDependency(s.project!.id, {
        source: { artifact_id: artifact.id, component_id: componentId },
        target: { artifact_id: tid, component_id: tc === "__new" ? newId : tc },
        target_kind: targetKind,
        options,
      }),
      "Dependency linked (stale until updated)",
    );
    if (r) onDone();
  };
  return (
    <details className="link-form">
      <summary className="small">Link {comp?.kind} to another artifact…</summary>
      <div className="form-grid">
        <label>Artifact</label>
        <select value={tid} onChange={(e) => (setTid(e.target.value), setTc(""))} aria-label="target artifact">
          <option value="">choose…</option>
          {targets.map((a) => (
            <option key={a.id} value={a.id}>
              {a.name} ({a.adapter})
            </option>
          ))}
        </select>
        <label>Component</label>
        <select value={tc} onChange={(e) => setTc(e.target.value)} disabled={!tid} aria-label="target component">
          <option value="">choose…</option>
          {comps.map((c) => (
            <option key={c.id} value={c.id}>
              {c.kind}: {c.name}
            </option>
          ))}
          <option value="__new">new component…</option>
        </select>
        {tc === "__new" && (
          <>
            <label>New id / kind</label>
            <div className="row">
              <input value={newId} onChange={(e) => setNewId(e.target.value.replace(/[^A-Za-z0-9_.-]/g, "_"))} placeholder="e.g. results_table" />
              <select value={newKind} onChange={(e) => setNewKind(e.target.value)}>
                {kinds.map((k) => (
                  <option key={k}>{k}</option>
                ))}
              </select>
            </div>
          </>
        )}
        {["paragraph", "text", "title"].includes(targetKind ?? "") && comp?.kind === "range" && (
          <>
            <label>Text template</label>
            <input value={template} onChange={(e) => setTemplate(e.target.value)} placeholder="Mean rose to {B9:.1f} ppm" />
          </>
        )}
      </div>
      <button disabled={!tid || !tc || (tc === "__new" && !newId)} onClick={create}>
        Link
      </button>
    </details>
  );
}
