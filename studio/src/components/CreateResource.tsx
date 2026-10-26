import { useEffect, useRef, useState } from "react";
import { api } from "../api";
import { useStudio } from "../state";
import type { ResourceRef } from "../types";

const starters = [
  { adapter: "blender", label: "3D scene", template: "empty" },
  { adapter: "layered2d", label: "Layered image", template: "blank" },
  { adapter: "code", label: "Code project", template: "files" },
  { adapter: "document", label: "Document", template: "blank" },
  { adapter: "spreadsheet", label: "Spreadsheet", template: "blank" },
  { adapter: "presentation", label: "Presentation", template: "blank" },
];

/** Creation and board placement are separate: a placement failure must not create duplicates. */
export function CreateResource({ kind, onClose }: { kind: "artifact" | "workflow"; onClose: () => void }) {
  const s = useStudio();
  const [name, setName] = useState("");
  const [adapter, setAdapter] = useState("document");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const pending = useRef(false);
  const mounted = useRef(true);
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; }; }, []);
  const starter = starters.find((x) => x.adapter === adapter)!;
  const info = s.health?.adapters.find((x) => x.name === adapter);
  const available = !!info?.available && info.templates.includes(starter.template);
  const unavailable = !s.health ? "Loading available tools…" : info?.unavailable_reason || "This artifact type is unavailable on this server.";

  return <form className="ex-add-form" aria-label={`New ${kind}`} onSubmit={async (e) => {
    e.preventDefault();
    if (pending.current || !s.project || !name.trim() || (kind === "artifact" && !available)) return;
    pending.current = true;
    setBusy(true);
    setError(null);
    let created = false;
    try {
      const pid = s.project.id;
      const ref: ResourceRef = kind === "artifact"
        ? { kind, id: (await api.createArtifact(pid, { name: name.trim(), adapter, template: starter.template, params: {} })).artifact.id }
        : { kind, id: (await api.createWorkflow(pid, { name: name.trim(), nodes: [], edges: [] })).id };
      created = true;
      // A project change unmounts this form. Do not place old project resources on the new board.
      if (!mounted.current) return;
      await s.refresh();
      if (!mounted.current) return;
      const item = await s.placeResource(ref);
      if (!mounted.current) return;
      if (item) s.select({ kind: "items", itemIds: [item.id] });
      else s.select({ kind: "resource", ref });
      s.notify(item ? `${name.trim()} created on this board` : `${name.trim()} created. Drag it from Explorer to place it on a board.`);
      onClose();
    } catch (err) {
      if (!mounted.current) return;
      const message = err instanceof Error ? err.message : String(err);
      if (created) {
        s.setError(`Created successfully, but could not update the board: ${message}. Find it in Explorer after refreshing.`);
        onClose();
      } else setError(message);
    } finally {
      pending.current = false;
      if (mounted.current) setBusy(false);
    }
  }}>
    <label>Name<input autoFocus aria-label={`${kind} name`} value={name} disabled={busy} onChange={(e) => setName(e.target.value)} required /></label>
    {kind === "artifact" && <>
      <label>Type<select aria-label="Artifact type" value={adapter} disabled={busy} onChange={(e) => setAdapter(e.target.value)}>
        {starters.map((x) => <option key={x.adapter} value={x.adapter}>{x.label}</option>)}
      </select></label>
      {!available && <p className="muted small" role="status">{unavailable}</p>}
      <p className="muted small">Start with an editable blank artifact, then use Focus or the agent dock to build it.</p>
    </>}
    {kind === "workflow" && <p className="muted small">Select the new workflow on the board, then use + Operation to add steps.</p>}
    {error && <p role="alert">{error}</p>}
    <div className="actions">
      <button className="mini-btn primary" type="submit" disabled={busy || !name.trim() || (kind === "artifact" && !available)}>{busy ? "Creating…" : "Create"}</button>
      <button className="mini-btn" type="button" disabled={busy} onClick={onClose}>Cancel</button>
    </div>
  </form>;
}
