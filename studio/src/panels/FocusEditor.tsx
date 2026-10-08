import { useState } from "react";
import { fileUrl } from "../api";
import { codeDrafts, useHeadRevision } from "../canvas/hooks";
import { rendererFor } from "../canvas/renderers";
import { Badge, targetLabel } from "../components/common";
import { useStudio } from "../state";

/**
 * Focus = the same canvas item presented large. Identity, selection, camera and unsaved edits
 * live in the item's presentation state / shared draft store, so nothing is copied.
 */
export function FocusEditor() {
  const s = useStudio();
  const item = s.board?.items.find((i) => i.id === s.focusItemId);
  const artifact = s.artifacts.find((a) => a.id === item?.resource_ref.id);
  const revision = useHeadRevision(artifact);
  const [dockOpen, setDockOpen] = useState(true);
  const [adding, setAdding] = useState("");
  if (!item || !artifact || !s.project) return null;
  const R = rendererFor(artifact);
  const sel: string | null = item.presentation_state.selected_component ?? null;
  const bound = s.bindings.filter((b) => b.target.artifact_id === artifact.id);
  const pinned: string[] = item.presentation_state.pinned_sources ?? Array.from(new Set(bound.map((b) => b.source_id)));
  const dirty = codeDrafts.dirtyFor(artifact.id);
  const close = () => {
    if (dirty.length && !confirm(`Unsaved edits in ${dirty.join(", ")} stay as drafts (they are not committed). Leave Focus?`)) return;
    s.setFocusItem(null);
  };
  return (
    <div className="focus-overlay" data-testid="focus-editor">
      <div className="focus-head">
        <b>{artifact.name}</b>
        {item.presentation_state.label && <span className="muted"> · {item.presentation_state.label}</span>}
        {revision && <Badge tone="info">r{revision.number}</Badge>}
        {sel && <Badge tone="warn">selected: {targetLabel({ scope: "component", artifact_id: artifact.id, component_id: sel }, s.artifacts)}</Badge>}
        {dirty.length > 0 && <Badge tone="warn">unsaved: {dirty.join(", ")}</Badge>}
        <span className="spacer" />
        <button onClick={() => setDockOpen(!dockOpen)}>{dockOpen ? "Hide references" : "Show references"}</button>
        <button className="primary" onClick={close} data-testid="exit-focus">
          Exit Focus (Esc)
        </button>
      </div>
      <div className="focus-body">
        <div className="focus-main nodrag nowheel">
          <R.View
            item={item}
            artifact={artifact}
            revision={revision}
            mode="focus"
            selectedComponent={sel}
            onSelectComponent={(cid) => {
              s.patchPresentation(item.id, { selected_component: cid });
              s.select(cid ? { kind: "component", itemId: item.id, artifactId: artifact.id, componentId: cid } : { kind: "items", itemIds: [item.id] });
            }}
            patchState={(p) => s.patchPresentation(item.id, p)}
          />
        </div>
        {dockOpen && (
          <div className="focus-dock" data-testid="pinned-references">
            <div className="row">
              <b>Pinned references</b>
              <span className="spacer" />
              <select value={adding} onChange={(e) => setAdding(e.target.value)} aria-label="pin source">
                <option value="">pin a source…</option>
                {s.sources.filter((x) => !pinned.includes(x.id)).map((x) => (
                  <option key={x.id} value={x.id}>
                    {x.name}
                  </option>
                ))}
              </select>
              <button
                className="mini-btn"
                disabled={!adding}
                onClick={() => {
                  s.patchPresentation(item.id, { pinned_sources: [...pinned, adding] });
                  setAdding("");
                }}
              >
                Pin
              </button>
            </div>
            {pinned.map((sid) => {
              const src = s.sources.find((x) => x.id === sid);
              if (!src) return null;
              const rel = bound.filter((b) => b.source_id === sid);
              const forSel = rel.some((b) => b.target.component_id === sel);
              return (
                <div key={sid} className={`pin-card ${forSel ? "for-selection" : ""}`}>
                  <div className="row">
                    <b>{src.name}</b>
                    <span className="spacer" />
                    <button className="link small" onClick={() => s.patchPresentation(item.id, { pinned_sources: pinned.filter((x) => x !== sid) })}>
                      unpin
                    </button>
                  </div>
                  {src.preview_path && <img src={fileUrl(s.project!.id, src.preview_path)} alt="" />}
                  {src.media_type === "text" && <pre className="src-text">{(src.extracted.excerpt ?? "").slice(0, 300)}</pre>}
                  {src.processing.state !== "ready" && <div className="warning small">{src.processing.warnings[0] ?? src.processing.state}</div>}
                  {rel.map((b) => (
                    <div key={b.id} className="small">
                      {b.role} · {b.aspects.join(", ")} → {targetLabel(b.target, s.artifacts)} {b.target.component_id === sel && <Badge tone="warn">selected component</Badge>}
                    </div>
                  ))}
                  {!rel.length && <div className="muted small">not bound to this artifact (pinned for context)</div>}
                </div>
              );
            })}
          </div>
        )}
      </div>
    </div>
  );
}
