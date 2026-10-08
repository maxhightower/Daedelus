/**
 * Artifact-view and source-card renderer registry.
 *
 * The board never special-cases media: an item asks the registry for the renderer whose
 * `match` accepts its resource. New artifact types (spreadsheets, documents, presentations…)
 * register a renderer; unknown types fall back to an honest file card.
 */
import { lazy, Suspense, useEffect, useMemo, useState, type ComponentType, type ReactNode } from "react";
import { api, boardApi, fileUrl } from "../api";
import { Badge, Status } from "../components/common";
import { acquireGL, releaseGL, Viewer3D, type CameraState } from "../components/Viewer3D";
import { useStudio } from "../state";
import type { Artifact, CanvasItem, MediaSource, Revision } from "../types";
import { codeDrafts, draftKey, invalidateRevision, useDrafts, type Lod } from "./hooks";
import { DocView, SheetView, SlidesView } from "./office";

const CodeEditorLazy = lazy(() => import("../components/Code").then((m) => ({ default: m.CodeEditor })));
const CodeDiffLazy = lazy(() => import("../components/Code").then((m) => ({ default: m.CodeDiff })));

export type ViewMode = Lod | "active" | "focus";

export interface ArtifactRendererProps {
  item: CanvasItem;
  artifact: Artifact;
  revision: Revision | null;
  mode: ViewMode;
  selectedComponent: string | null;
  onSelectComponent: (cid: string | null) => void;
  patchState: (p: Record<string, any>) => void;
}

export interface ArtifactRenderer {
  id: string;
  label: string;
  match: (a: Artifact) => boolean;
  /** "exact": components can be picked inside the view; "list": via a list; "none": not supported */
  componentPicking: "exact" | "list" | "none";
  nativeEditing: boolean;
  View: ComponentType<ArtifactRendererProps>;
}

// ------------------------------------------------------------------ 3D (Blender)
function Model3DView({ item, artifact, revision, mode, selectedComponent, onSelectComponent, patchState }: ArtifactRendererProps) {
  const { project, bindings } = useStudio();
  const [glb, setGlb] = useState<string | null>(null);
  const [glbErr, setGlbErr] = useState<string | null>(null);
  const interactive = mode === "active" || mode === "focus";
  const wantsLive = interactive || mode === "close" || mode === "medium";
  const glKey = `${item.id}:${mode === "focus" ? "focus" : "board"}`;
  const [live, setLive] = useState(false);
  useEffect(() => {
    if (!wantsLive) {
      releaseGL(glKey);
      setLive(false);
      return;
    }
    setLive(acquireGL(glKey));
    return () => releaseGL(glKey);
  }, [wantsLive, glKey]);
  useEffect(() => {
    if (!project || !revision || !live) return;
    setGlbErr(null);
    boardApi
      .glb(project.id, revision.id)
      .then((r) => setGlb(fileUrl(project.id, r.path)))
      .catch((e) => setGlbErr(e.message));
  }, [project, revision?.id, live]);
  const marked = useMemo(
    () => bindings.filter((b) => b.target.scope === "component" && b.target.artifact_id === artifact.id).map((b) => b.target.component_id!),
    [bindings, artifact.id],
  );
  const render = revision?.previews.render;
  const preset: string = item.presentation_state.preset ?? "perspective";
  if (!live || !glb) {
    return (
      <div className="r-static">
        {render && project ? <img src={fileUrl(project.id, render)} alt="render" draggable={false} /> : <div className="muted">no preview</div>}
        {wantsLive && !live && <div className="r-note">static render — live 3D view budget reached; activate to interact</div>}
        {glbErr && <div className="r-note">{glbErr}</div>}
      </div>
    );
  }
  return (
    <div className="r-3d">
      <Viewer3D
        automationKey={`${item.id}:${mode === "focus" ? "focus" : "board"}`}
        url={glb}
        camera={item.presentation_state.camera as CameraState | undefined}
        preset={preset}
        interactive={interactive}
        selectedId={selectedComponent}
        markedIds={mode === "far" ? [] : marked}
        onPick={(cid) => onSelectComponent(cid)}
        onCameraChange={(c) => patchState({ camera: c })}
      />
      {interactive && (
        <div className="r-3d-tools nodrag">
          {["perspective", "front", "side", "top"].map((p) => (
            <button key={p} className="mini-btn" onClick={() => patchState({ preset: p, camera: null })}>
              {p}
            </button>
          ))}
          {selectedComponent && (
            <button className="mini-btn" onClick={() => onSelectComponent(artifact.components.find((c) => c.id === selectedComponent)?.parent_id ?? null)}>
              ↑ parent
            </button>
          )}
          <span className="muted small">{selectedComponent ? `selected: ${selectedComponent}` : "click geometry to select a component"}</span>
        </div>
      )}
    </div>
  );
}

// ------------------------------------------------------------------ layered 2D (OpenRaster)
function LayeredView({ artifact, revision, mode, selectedComponent, onSelectComponent }: ArtifactRendererProps) {
  const { project, run, refresh } = useStudio();
  const [opacity, setOpacity] = useState<number | null>(null);
  if (!project || !revision) return <div className="muted">loading…</div>;
  const comp = revision.previews.composite;
  const layers = artifact.components.filter((c) => c.kind === "layer");
  const showLayers = mode !== "far";
  const sel = layers.find((l) => l.id === selectedComponent);
  const apply = async (params: Record<string, any>, label: string) => {
    if (!sel) return;
    const r = await run(boardApi.edit(project.id, artifact.id, [{ op: "set_layer_props", component_id: sel.id, params }], `${label} (${sel.name})`, artifact.head_revision_id), `New revision: ${label}`);
    if (r) {
      invalidateRevision(r.id);
      setOpacity(null);
      refresh();
    }
  };
  return (
    <div className="r-2d">
      {comp && <img className="r-2d-main" src={fileUrl(project.id, sel && revision.previews[`layer:${sel.id}`] && mode === "focus" ? revision.previews[`layer:${sel.id}`] : comp)} alt="composite" draggable={false} />}
      {showLayers && (
        <div className="r-layers nodrag">
          {layers.map((l) => (
            <button
              key={l.id}
              className={`layer-chip ${l.id === selectedComponent ? "on" : ""}`}
              disabled={mode === "medium" || mode === "close"}
              onClick={() => onSelectComponent(l.id === selectedComponent ? null : l.id)}
              title={mode === "active" || mode === "focus" ? "select layer" : "activate the view to select layers"}
            >
              {revision.previews[`layer:${l.id}`] && <img src={fileUrl(project.id, revision.previews[`layer:${l.id}`])} alt="" className="checker" />}
              <span>{l.name}</span>
            </button>
          ))}
        </div>
      )}
      {(mode === "active" || mode === "focus") && sel && (
        <div className="r-2d-edit nodrag">
          <b>{sel.name}</b>
          <label className="inline">
            opacity
            <input type="range" min={0} max={1} step={0.05} value={opacity ?? 1} onChange={(e) => setOpacity(+e.target.value)} />
          </label>
          <button className="mini-btn" disabled={opacity === null} onClick={() => apply({ opacity }, `Layer opacity ${opacity}`)}>
            Apply
          </button>
          <button className="mini-btn" onClick={() => apply({ visible: false }, "Hide layer")}>
            hide
          </button>
          <button className="mini-btn" onClick={() => apply({ visible: true }, "Show layer")}>
            show
          </button>
        </div>
      )}
    </div>
  );
}

// ------------------------------------------------------------------ code (git)
function CodeView({ item, artifact, revision, mode, onSelectComponent, patchState }: ArtifactRendererProps) {
  const { project, run, refresh } = useStudio();
  const drafts = useDrafts();
  const files = artifact.components.filter((c) => c.kind === "file").map((c) => c.name);
  const path: string = item.presentation_state.file ?? files.find((f) => f.endsWith(".json")) ?? files[0] ?? "";
  const key = draftKey(artifact.id, path);
  const draft = drafts[key];
  const [text, setText] = useState<string | null>(null);
  const [review, setReview] = useState(false);
  const editing = mode === "active" || mode === "focus";
  useEffect(() => {
    if (!project || !revision || !path) return;
    api.revisionFile(project.id, revision.id, path).then((r) => setText(r.text ?? "(binary file)"));
  }, [project, revision?.id, path]);
  const dirty = !!draft && draft.text !== draft.original;
  const commit = async () => {
    if (!project || !draft) return;
    const r = await run(
      boardApi.edit(project.id, artifact.id, [{ op: "write_file", component_id: `file:${path}`, params: { path, content: draft.text } }], `Edit ${path}`, draft.baseRevision),
      `Committed ${path} as a new revision`,
    );
    if (r) {
      codeDrafts.discard(key);
      setReview(false);
      refresh();
    }
  };
  if (mode === "far") return <div className="r-card">{files.length} files · {revision?.vcs_commit?.slice(0, 8) ?? ""}</div>;
  return (
    <div className="r-code">
      <div className="r-code-files nodrag">
        {files.map((f) => (
          <button
            key={f}
            className={`file-chip ${f === path ? "on" : ""} ${codeDrafts.dirtyFor(artifact.id).includes(f) ? "dirty" : ""}`}
            disabled={!editing}
            onClick={() => {
              patchState({ file: f });
              onSelectComponent(`file:${f}`);
            }}
          >
            {f}
          </button>
        ))}
      </div>
      {!editing && <pre className="r-code-pre">{(text ?? "").split("\n").slice(0, mode === "close" ? 40 : 14).join("\n")}</pre>}
      {editing && text !== null && (
        <div className="r-code-edit nodrag nowheel">
          <Suspense fallback={<div className="muted">loading editor…</div>}>
            {review && draft ? (
              <CodeDiffLazy path={path} before={draft.original} after={draft.text} height="100%" />
            ) : (
              <CodeEditorLazy
                path={`${artifact.id}/${path}`}
                value={draft?.text ?? text}
                onChange={(v) => codeDrafts.set(key, { original: draft?.original ?? text, text: v, baseRevision: draft?.baseRevision ?? artifact.head_revision_id ?? null })}
                height="100%"
              />
            )}
          </Suspense>
          <div className="r-code-bar">
            {dirty ? <Badge tone="warn">unsaved changes</Badge> : <span className="muted small">no unsaved changes</span>}
            <button className="mini-btn" disabled={!dirty} onClick={() => setReview(!review)}>
              {review ? "Back to editor" : "Review diff"}
            </button>
            <button className="mini-btn" disabled={!dirty} onClick={() => codeDrafts.discard(key)}>
              Discard
            </button>
            <button className="mini-btn primary" disabled={!dirty} onClick={commit}>
              Commit revision
            </button>
          </div>
        </div>
      )}
    </div>
  );
}

// ------------------------------------------------------------------ honest fallback
function FileCardView({ artifact, revision }: ArtifactRendererProps) {
  return (
    <div className="r-card">
      <div>
        <b>{artifact.artifact_type}</b> via adapter <code>{artifact.adapter}</code>
      </div>
      <div className="muted small">
        native: <code>{artifact.native_dir}/{artifact.entry}</code>
      </div>
      <div className="muted small">rev {revision?.number ?? "?"} · no embedded editor for this artifact type</div>
    </div>
  );
}

export const ARTIFACT_RENDERERS: ArtifactRenderer[] = [
  { id: "model3d", label: "3D viewport", match: (a) => a.adapter === "blender", componentPicking: "exact", nativeEditing: true, View: Model3DView },
  { id: "layered2d", label: "Layered image", match: (a) => a.adapter === "layered2d", componentPicking: "list", nativeEditing: true, View: LayeredView },
  { id: "code", label: "Code / files", match: (a) => a.adapter === "code", componentPicking: "list", nativeEditing: true, View: CodeView },
  { id: "sheet", label: "Spreadsheet grid", match: (a) => a.adapter === "spreadsheet", componentPicking: "exact", nativeEditing: true, View: SheetView },
  { id: "doc", label: "Document", match: (a) => a.adapter === "document", componentPicking: "exact", nativeEditing: true, View: DocView },
  { id: "slides", label: "Slides", match: (a) => a.adapter === "presentation", componentPicking: "exact", nativeEditing: true, View: SlidesView },
  { id: "file", label: "File card", match: () => true, componentPicking: "none", nativeEditing: false, View: FileCardView },
];
export const rendererFor = (a: Artifact) => ARTIFACT_RENDERERS.find((r) => r.match(a))!;

// ------------------------------------------------------------------ source cards
export function SourceBody({ s, lod }: { s: MediaSource; lod: Lod }) {
  const { project } = useStudio();
  if (!project) return null;
  const ex = s.extracted ?? {};
  const pv = s.preview_path ? <img className="src-preview" src={fileUrl(project.id, s.preview_path)} alt="" draggable={false} /> : null;
  let body: ReactNode = null;
  if (lod === "far") return pv ?? <div className="src-kind">{s.media_type}</div>;
  switch (s.media_type) {
    case "image":
      body = (
        <>
          {pv}
          <div className="swatches">
            {(ex.palette ?? []).slice(0, 5).map((p: any) => (
              <span key={p.hex} className="swatch" style={{ background: p.hex }} title={p.hex} />
            ))}
          </div>
        </>
      );
      break;
    case "video_url":
      body = (
        <>
          {pv}
          <div className="small">{ex.title ?? s.locator.url}</div>
        </>
      );
      break;
    case "text":
    case "document":
      body = (
        <>
          {pv}
          {(ex.headings ?? []).length > 0 && <div className="small"><b>{ex.headings[0]}</b></div>}
          <pre className="src-text">{(ex.excerpt ?? ex.text ?? "").slice(0, lod === "close" ? 600 : 220)}</pre>
        </>
      );
      break;
    case "spreadsheet": {
      const sh = ex.sheets?.[0];
      body = sh ? (
        <div className="src-table">
          <div className="small muted">
            {sh.name} · {sh.row_count}×{sh.col_count}
          </div>
          <table>
            <tbody>
              {sh.rows.slice(0, lod === "close" ? 12 : 5).map((r: string[], i: number) => (
                <tr key={i}>
                  {r.slice(0, 6).map((c, j) => (
                    <td key={j}>{c}</td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null;
      break;
    }
    case "presentation":
      body = (
        <ol className="src-slides">
          {(ex.slides ?? []).slice(0, 8).map((sl: any) => (
            <li key={sl.index}>{sl.title || "(untitled)"}</li>
          ))}
        </ol>
      );
      break;
    default:
      body = pv ?? <div className="src-kind">{s.media_type}</div>;
  }
  return (
    <>
      {body}
      {s.processing.state !== "ready" && (
        <div className={`src-state state-${s.processing.state}`} title={s.processing.warnings.join("\n") || s.processing.error || ""}>
          <Status s={s.processing.state} /> {s.processing.error ?? s.processing.warnings[0] ?? ""}
        </div>
      )}
    </>
  );
}
