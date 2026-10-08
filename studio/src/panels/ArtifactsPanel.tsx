import { lazy, Suspense, useEffect, useState } from "react";
import { api, fileUrl } from "../api";
import { Badge, ComponentTree, Empty, Json, Section, keyLabel, short } from "../components/common";
import { ModelViewer } from "../components/ModelViewer";
import { useStudio } from "../state";
import type { Artifact, Revision } from "../types";

const CodePane = lazy(() => import("../components/Code").then((m) => ({ default: m.CodeView })));
const DiffPane = lazy(() => import("../components/Code").then((m) => ({ default: m.CodeDiff })));

function Previews({ a, rev }: { a: Artifact; rev: Revision }) {
  const { project } = useStudio();
  if (!project) return null;
  const glb = rev.previews.glb;
  const render = rev.previews.render;
  const composite = rev.previews.composite;
  const layers = Object.entries(rev.previews).filter(([k]) => k.startsWith("layer:"));
  return (
    <div className="previews">
      {glb && (
        <div>
          <div className="muted small">Interactive (GLB exported from {a.entry})</div>
          <ModelViewer url={fileUrl(project.id, glb)} />
        </div>
      )}
      {render && (
        <div>
          <div className="muted small">Blender render (Cycles)</div>
          <img className="preview" src={fileUrl(project.id, render)} alt="render" />
        </div>
      )}
      {composite && (
        <div>
          <div className="muted small">Composite</div>
          <img className="preview" src={fileUrl(project.id, composite)} alt="composite" />
        </div>
      )}
      {layers.length > 0 && (
        <div className="layers">
          {layers.map(([k, p]) => (
            <figure key={k}>
              <img className="checker" src={fileUrl(project.id, p)} alt={k} />
              <figcaption>
                {k.slice(6)} {rev.changed_components.includes(k.slice(6)) && <Badge tone="warn">changed</Badge>}
              </figcaption>
            </figure>
          ))}
        </div>
      )}
    </div>
  );
}

function CodeFiles({ rev, parent }: { rev: Revision; parent?: Revision }) {
  const { project } = useStudio();
  const [files, setFiles] = useState<string[]>([]);
  const [path, setPath] = useState<string | null>(null);
  const [cur, setCur] = useState<string>("");
  const [prev, setPrev] = useState<string | null>(null);
  useEffect(() => {
    if (!project) return;
    api.revisionFiles(project.id, rev.id).then((f) => {
      setFiles(f);
      const changed = rev.changed_components.find((c) => c.startsWith("file:"))?.slice(5);
      setPath(changed ?? f[0] ?? null);
    });
  }, [project, rev]);
  useEffect(() => {
    if (!project || !path) return;
    api.revisionFile(project.id, rev.id, path).then((r) => setCur(r.text ?? "(binary)"));
    if (parent)
      api
        .revisionFile(project.id, parent.id, path)
        .then((r) => setPrev(r.text ?? "(binary)"))
        .catch(() => setPrev(""));
    else setPrev(null);
  }, [project, rev, parent, path]);
  return (
    <div>
      <div className="row">
        <select value={path ?? ""} onChange={(e) => setPath(e.target.value)}>
          {files.map((f) => (
            <option key={f} value={f}>
              {f} {rev.changed_components.includes(`file:${f}`) ? "(changed)" : ""}
            </option>
          ))}
        </select>
        {parent && <span className="muted small">left: revision {parent.number} · right: revision {rev.number}</span>}
      </div>
      <Suspense fallback={<div className="muted">loading editor…</div>}>
        {path && (prev !== null ? <DiffPane path={path} before={prev} after={cur} /> : <CodePane path={path} text={cur} />)}
      </Suspense>
    </div>
  );
}

function RevisionDetail({ a, rev, revisions }: { a: Artifact; rev: Revision; revisions: Revision[] }) {
  const { project, run, refresh, setView, setFocus } = useStudio();
  const parent = revisions.find((r) => r.id === rev.parent_revision_id);
  if (!project) return null;
  const unchanged = Object.keys(rev.component_states).filter((c) => !rev.changed_components.includes(c));
  return (
    <div className="revision">
      <h3>
        Revision {rev.number} <span className="muted small">{rev.created_at.slice(0, 19).replace("T", " ")}</span>
      </h3>
      <div className="row small">
        <span>{rev.message}</span>
        {rev.execution_id && (
          <button className="link" onClick={() => (setFocus("execution", rev.execution_id!), setView("executions"))}>
            execution {short(rev.execution_id)}
          </button>
        )}
        {rev.vcs_commit && <code>git {rev.vcs_commit.slice(0, 10)}</code>}
        <code title="native snapshot">{rev.snapshot_dir}</code>
        <button
          onClick={async () => {
            if (await run(api.restore(project.id, a.id, rev.id), `Restored revision ${rev.number} as a new revision`)) refresh();
          }}
        >
          Restore as new revision
        </button>
      </div>
      {rev.units.length > 0 && (
        <div className="small">
          Executed units: {rev.units.map((u) => <Badge key={u}>{keyLabel(u, [a])}</Badge>)}
        </div>
      )}
      <div className="small">
        Changed: {rev.changed_components.length ? rev.changed_components.map((c) => <Badge key={c} tone="warn">{c}</Badge>) : "—"}{" "}
        {rev.parent_revision_id && (
          <>
            · Preserved: {unchanged.map((c) => <Badge key={c} tone="ok">{c}</Badge>)}
          </>
        )}
      </div>
      <Previews a={a} rev={rev} />
      {a.adapter === "code" && (
        <Section title="Files & diff">
          <CodeFiles rev={rev} parent={parent} />
        </Section>
      )}
      {rev.diff && a.adapter !== "code" && (
        <Section title="Diff" collapsed>
          <pre className="json">{rev.diff}</pre>
        </Section>
      )}
      {rev.operations.length > 0 && (
        <Section title={`Operations (${rev.operations.length})`}>
          <table className="grid small">
            <thead>
              <tr>
                <th>#</th>
                <th>Operation</th>
                <th>Component</th>
                <th>Params</th>
                <th>Result</th>
                <th>Rationale</th>
              </tr>
            </thead>
            <tbody>
              {rev.operations.map((o, i) => {
                const r = rev.operation_results.find((x) => x.index === i);
                return (
                  <tr key={i}>
                    <td>{i}</td>
                    <td>{o.op}</td>
                    <td>{o.component_id}</td>
                    <td>
                      <code>{JSON.stringify(o.params)}</code>
                    </td>
                    <td>
                      {r?.status} <span className="muted">{r?.detail}</span>
                    </td>
                    <td>{o.rationale}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </Section>
      )}
      {rev.attribution.length > 0 && (
        <Section title="Attribution (sources presented to the planner and how it says it used them)">
          <p className="muted small">Declared by the planner; not a causal measurement.</p>
          <table className="grid small">
            <thead>
              <tr>
                <th>Unit</th>
                <th>Source</th>
                <th>Role</th>
                <th>Aspects</th>
                <th>Applied</th>
                <th>Ops</th>
                <th>Interpretation</th>
              </tr>
            </thead>
            <tbody>
              {rev.attribution.map((x, i) => (
                <tr key={i} className={x.applied ? "" : "dim"}>
                  <td>{keyLabel(x.unit, [a])}</td>
                  <td>{x.source_name}</td>
                  <td>{x.role}</td>
                  <td>{x.aspects.join(", ")}</td>
                  <td>{x.applied ? "yes" : "no"}</td>
                  <td>{x.operations.join(", ")}</td>
                  <td>{x.interpretation}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </Section>
      )}
      {rev.validation && (
        <Section title={<>Validation {rev.validation.passed ? <Badge tone="ok">passed</Badge> : <Badge tone="err">failed</Badge>}</>}>
          {rev.validation.checks.map((c, i) => (
            <div key={i} className={`check ${c.passed ? "ok" : "fail"}`}>
              {c.passed ? "✔" : "✘"} <b>{c.name}</b> {c.detail}
            </div>
          ))}
        </Section>
      )}
      <Section title="Measurements" collapsed>
        <Json value={rev.measurements} />
      </Section>
    </div>
  );
}

export function ArtifactsPanel() {
  const { project, artifacts, focus, setFocus } = useStudio();
  const aid = focus.artifact ?? artifacts[0]?.id;
  const a = artifacts.find((x) => x.id === aid);
  const [revs, setRevs] = useState<Revision[]>([]);
  const [revId, setRevId] = useState<string | null>(null);
  const [rev, setRev] = useState<Revision | null>(null);
  useEffect(() => {
    if (!project || !a) return;
    api.revisions(project.id, a.id).then((r) => {
      setRevs(r);
      setRevId(a.head_revision_id ?? r[r.length - 1]?.id ?? null);
    });
  }, [project, a]);
  useEffect(() => {
    if (project && revId) api.revision(project.id, revId).then(setRev);
  }, [project, revId]);
  if (!project) return <Empty>Select a project.</Empty>;
  if (!a) return <Empty>No artifacts yet.</Empty>;
  return (
    <div className="split">
      <div className="list-col">
        <select value={aid} onChange={(e) => setFocus("artifact", e.target.value)}>
          {artifacts.map((x) => (
            <option key={x.id} value={x.id}>
              {x.name} ({x.artifact_type})
            </option>
          ))}
        </select>
        <div className="small muted">
          adapter {a.adapter} · native <code>{a.native_dir}/{a.entry}</code>
        </div>
        <h4>Components</h4>
        <ComponentTree
          artifact={a}
          annotate={(c) => (rev?.changed_components.includes(c.id) ? <Badge tone="warn">changed</Badge> : null)}
        />
        <h4>Revisions</h4>
        {[...revs].reverse().map((r) => (
          <div key={r.id} className={`rev-row ${r.id === revId ? "selected" : ""}`} onClick={() => setRevId(r.id)}>
            <b>r{r.number}</b> {r.id === a.head_revision_id && <Badge tone="ok">head</Badge>}{" "}
            <span className="small">{r.message}</span>
            <div className="muted small">
              {r.created_at.slice(5, 19).replace("T", " ")} · changed {r.changed_components.length}
            </div>
          </div>
        ))}
      </div>
      <div className="detail-col">{rev && rev.artifact_id === a.id && <RevisionDetail a={a} rev={rev} revisions={revs} />}</div>
    </div>
  );
}
