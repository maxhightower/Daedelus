import { useEffect, useRef, useState } from "react";
import { api, fileUrl } from "../api";
import { BindingEditor } from "../components/BindingEditor";
import { Badge, Empty, Json, Section, Status, targetLabel } from "../components/common";
import { useStudio } from "../state";
import type { Binding, MediaSource } from "../types";

function SourceCard({ s, selected, onClick }: { s: MediaSource; selected: boolean; onClick: () => void }) {
  const { project } = useStudio();
  return (
    <div className={`source-card ${selected ? "selected" : ""}`} onClick={onClick}>
      <div className="thumb">
        {s.preview_path && project ? <img src={fileUrl(project.id, s.preview_path)} alt="" /> : <span>{s.media_type}</span>}
      </div>
      <div className="meta">
        <div className="name">{s.name}</div>
        <div className="row small">
          <Badge>{s.media_type}</Badge>
          <Status s={s.processing.state} />
          <span className="muted">{s.binding_count ?? 0} binding(s)</span>
        </div>
      </div>
    </div>
  );
}

function AddSource() {
  const { project, run, refresh } = useStudio();
  const [url, setUrl] = useState("");
  const [textName, setTextName] = useState("");
  const [text, setText] = useState("");
  const [path, setPath] = useState("");
  const fileRef = useRef<HTMLInputElement>(null);
  if (!project) return null;
  return (
    <Section title="Add sources" collapsed>
      <div className="add-source">
        <div>
          <b>Files</b> <span className="muted small">images, video, PDF, 3D (glb/gltf/obj/stl/blend), code, text…</span>
          <div className="row">
            <input ref={fileRef} type="file" multiple />
            <button
              onClick={async () => {
                const files = Array.from(fileRef.current?.files ?? []);
                if (!files.length) return;
                if (await run(api.upload(project.id, files), `${files.length} source(s) registered`)) {
                  if (fileRef.current) fileRef.current.value = "";
                  refresh();
                }
              }}
            >
              Upload
            </button>
          </div>
        </div>
        <div>
          <b>URL</b> <span className="muted small">web page, video (YouTube/Vimeo), git repository</span>
          <div className="row">
            <input value={url} onChange={(e) => setUrl(e.target.value)} placeholder="https://…" />
            <button onClick={async () => (await run(api.addUrl(project.id, url), "URL registered")) && (setUrl(""), refresh())}>
              Add
            </button>
          </div>
        </div>
        <div>
          <b>Text</b>
          <input value={textName} onChange={(e) => setTextName(e.target.value)} placeholder="name" />
          <textarea rows={3} value={text} onChange={(e) => setText(e.target.value)} placeholder={"Notes, guidelines…\nkey: value lines become machine-readable directives"} />
          <button onClick={async () => (await run(api.addText(project.id, textName || "Text", text), "Text registered")) && (setText(""), setTextName(""), refresh())}>
            Add text
          </button>
        </div>
        <div>
          <b>Local folder / repository</b>
          <div className="row">
            <input value={path} onChange={(e) => setPath(e.target.value)} placeholder="/path/to/repo" />
            <button onClick={async () => (await run(api.addPath(project.id, path), "Folder snapshot registered")) && (setPath(""), refresh())}>
              Import
            </button>
          </div>
        </div>
      </div>
    </Section>
  );
}

function Extracted({ s }: { s: MediaSource }) {
  const ex = s.extracted || {};
  const pal: { hex: string; weight: number }[] = ex.palette ?? [];
  return (
    <div className="extracted">
      {pal.length > 0 && (
        <div className="row">
          <span className="muted small">palette</span>
          {pal.map((p) => (
            <span key={p.hex} className="swatch" style={{ background: p.hex }} title={`${p.hex} ${(p.weight * 100).toFixed(0)}%`} />
          ))}
        </div>
      )}
      {ex.silhouette?.found && (
        <div className="small">
          silhouette aspect {ex.silhouette.aspect} · taper {ex.silhouette.taper} · top/bottom width {ex.silhouette.top_width}/{ex.silhouette.bottom_width}
        </div>
      )}
      {ex.directives && Object.keys(ex.directives).length > 0 && (
        <div className="small">
          directives: {Object.entries(ex.directives).map(([k, v]) => <code key={k}>{`${k}: ${v}`}</code>)}
        </div>
      )}
      {ex.title && <div className="small">title: “{ex.title}”</div>}
      {ex.understanding && (
        <div className="small muted">
          understanding: <b>{ex.understanding.kind}</b> — {ex.understanding.note ?? ex.understanding.reason}
        </div>
      )}
      <Section title="All extracted data" collapsed>
        <Json value={{ metadata: s.metadata, extracted: s.extracted }} />
      </Section>
    </div>
  );
}

export function SourcesPanel() {
  const { project, sources, artifacts, focus, setFocus, run, refresh } = useStudio();
  const selId = focus.source ?? sources[0]?.id;
  const [detail, setDetail] = useState<MediaSource | null>(null);
  const [editing, setEditing] = useState<string | "new" | null>(null);
  const [textEdit, setTextEdit] = useState<string | null>(null);
  useEffect(() => {
    if (project && selId) api.source(project.id, selId).then(setDetail).catch(() => setDetail(null));
    else setDetail(null);
  }, [project, selId, sources]);
  if (!project) return <Empty>Select or create a project.</Empty>;
  return (
    <div className="split">
      <div className="list-col">
        <AddSource />
        {sources.length === 0 && <Empty>No sources yet.</Empty>}
        {sources.map((s) => (
          <SourceCard key={s.id} s={s} selected={s.id === selId} onClick={() => (setFocus("source", s.id), setEditing(null))} />
        ))}
      </div>
      <div className="detail-col">
        {detail && (
          <>
            <h2>{detail.name}</h2>
            <div className="row">
              <Badge>{detail.media_type}</Badge>
              <Status s={detail.processing.state} />
              <span className="muted small">
                {detail.processing.extractor} · origin {detail.provenance.origin}
                {detail.provenance.url && <> · <a href={detail.provenance.url} target="_blank" rel="noreferrer">{detail.provenance.url}</a></>}
              </span>
            </div>
            {detail.processing.error && <div className="error-box">{detail.processing.error}</div>}
            {detail.processing.warnings.map((w, i) => (
              <div key={i} className="warning">
                {w}
              </div>
            ))}
            <div className="source-detail">
              {detail.preview_path && <img className="preview" src={fileUrl(project.id, detail.preview_path)} alt="preview" />}
              <Extracted s={detail} />
            </div>
            {detail.media_type === "text" && (
              <Section title="Edit text (creates new content hash; dependent units become stale)" collapsed>
                <textarea
                  rows={8}
                  value={textEdit ?? detail.extracted.text ?? ""}
                  onChange={(e) => setTextEdit(e.target.value)}
                />
                <button
                  className="primary"
                  disabled={textEdit === null}
                  onClick={async () => {
                    if (await run(api.updateText(project.id, detail.id, textEdit ?? ""), "Text updated")) {
                      setTextEdit(null);
                      refresh();
                    }
                  }}
                >
                  Save text
                </button>
              </Section>
            )}
            <h3>
              Meanings &amp; scopes ({detail.bindings?.length ?? 0})
              <button className="small-btn" onClick={() => setEditing("new")}>
                + assign meaning
              </button>
            </h3>
            <p className="muted small">
              The same media can serve different purposes for different targets. Each binding is independent.
            </p>
            {editing === "new" && <BindingEditor sourceId={detail.id} onDone={() => setEditing(null)} />}
            {(detail.bindings ?? []).map((b: Binding) => (
              <div key={b.id} className="binding-card">
                <div className="row" onClick={() => setEditing(editing === b.id ? null : b.id)}>
                  <b>{b.role}</b>
                  <span>→ {targetLabel(b.target, artifacts)}</span>
                  <span className="muted">{b.aspects.join(", ")}</span>
                  <Badge tone={b.constraint === "hard" ? "warn" : "default"}>{b.constraint}</Badge>
                  <span className="muted small">strength {b.strength}</span>
                  {!b.enabled && <Badge>disabled</Badge>}
                  {b.constraints.map((c, i) => (
                    <Badge key={i} tone="info">
                      {c.property} {c.op} {c.value ?? ""}
                    </Badge>
                  ))}
                </div>
                {editing === b.id && <BindingEditor sourceId={detail.id} binding={b} onDone={() => setEditing(null)} />}
              </div>
            ))}
            <div className="row end">
              <button
                className="danger"
                onClick={async () => {
                  if (!confirm(`Delete source “${detail.name}” and its bindings?`)) return;
                  if (await run(api.deleteSource(project.id, detail.id), "Source deleted")) {
                    setFocus("source", undefined);
                    refresh();
                  }
                }}
              >
                Delete source
              </button>
              <button onClick={async () => (await run(api.reingest(project.id, detail.id), "Re-ingested")) && refresh()}>Re-ingest</button>
            </div>
          </>
        )}
      </div>
    </div>
  );
}
