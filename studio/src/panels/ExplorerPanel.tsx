import { useEffect, useRef, useState, type ReactNode } from "react";
import { api, boardApi, fileUrl } from "../api";
import { Badge, Status, short } from "../components/common";
import { useStudio } from "../state";
import type { Artifact, Component, ResourceRef } from "../types";

function Group({ title, count, children, actions, open: initial = true, testid }: { title: string; count?: number; children: ReactNode; actions?: ReactNode; open?: boolean; testid?: string }) {
  const [open, setOpen] = useState(initial);
  return (
    <div className="ex-group" data-testid={testid}>
      <div className="ex-group-head">
        <button className="link" onClick={() => setOpen(!open)}>
          {open ? "▾" : "▸"} {title}
          {count !== undefined && <span className="muted"> {count}</span>}
        </button>
        <span className="spacer" />
        {actions}
      </div>
      {open && <div className="ex-group-body">{children}</div>}
    </div>
  );
}

function Row({
  label,
  sub,
  icon,
  dragRef,
  selected,
  onClick,
  onDouble,
  depth = 0,
  right,
  testid,
}: {
  label: ReactNode;
  sub?: ReactNode;
  icon?: ReactNode;
  dragRef?: ResourceRef;
  selected?: boolean;
  onClick?: () => void;
  onDouble?: () => void;
  depth?: number;
  right?: ReactNode;
  testid?: string;
}) {
  return (
    <div
      className={`ex-row ${selected ? "selected" : ""}`}
      style={{ paddingLeft: 6 + depth * 12 }}
      draggable={!!dragRef}
      onDragStart={(e) => {
        if (!dragRef) return;
        e.dataTransfer.setData("application/daedelus-ref", JSON.stringify(dragRef));
        e.dataTransfer.effectAllowed = "copy";
      }}
      onClick={onClick}
      onDoubleClick={onDouble}
      title={dragRef ? "drag onto the canvas · double-click to locate" : undefined}
      data-testid={testid}
    >
      {icon && <span className="ex-icon">{icon}</span>}
      <span className="ex-label">{label}</span>
      {sub && <span className="ex-sub">{sub}</span>}
      {right}
    </div>
  );
}

function AddSources() {
  const s = useStudio();
  const [mode, setMode] = useState<"" | "url" | "text">("");
  const [val, setVal] = useState("");
  const [name, setName] = useState("");
  const file = useRef<HTMLInputElement>(null);
  if (!s.project) return null;
  const placeAll = async (ids: string[]) => {
    for (const id of ids) await s.placeResource({ kind: "source", id });
  };
  return (
    <div className="ex-add">
      <button className="mini-btn" onClick={() => file.current?.click()} title="Upload files (images, video, PDF, DOCX, XLSX/CSV, PPTX, 3D, code…)">
        + files
      </button>
      <button className="mini-btn" onClick={() => setMode(mode === "url" ? "" : "url")}>+ URL</button>
      <button className="mini-btn" onClick={() => setMode(mode === "text" ? "" : "text")}>+ text</button>
      <input
        ref={file}
        type="file"
        multiple
        hidden
        data-testid="upload-input"
        onChange={async (e) => {
          const files = Array.from(e.target.files ?? []);
          if (!files.length) return;
          const r = await s.run(api.upload(s.project!.id, files), `${files.length} source(s) registered`);
          e.target.value = "";
          if (r) {
            await s.refresh();
            await placeAll(r.map((x) => x.id));
          }
        }}
      />
      {mode && (
        <div className="ex-add-form">
          {mode === "text" && <input placeholder="name" value={name} onChange={(e) => setName(e.target.value)} />}
          {mode === "url" ? (
            <input placeholder="https://… (web page, YouTube/Vimeo, git)" value={val} onChange={(e) => setVal(e.target.value)} />
          ) : (
            <textarea rows={3} placeholder="key: value lines become directives" value={val} onChange={(e) => setVal(e.target.value)} />
          )}
          <button
            className="mini-btn primary"
            onClick={async () => {
              const r = mode === "url" ? await s.run(api.addUrl(s.project!.id, val), "URL registered") : await s.run(api.addText(s.project!.id, name || "Note", val), "Text registered");
              if (r) {
                setVal("");
                setName("");
                setMode("");
                await s.refresh();
                await placeAll([r.id]);
              }
            }}
          >
            Add
          </button>
        </div>
      )}
    </div>
  );
}

function ArtifactFiles({ a }: { a: Artifact }) {
  const { project } = useStudio();
  const [files, setFiles] = useState<string[] | null>(null);
  useEffect(() => {
    if (project && a.head_revision_id) api.revisionFiles(project.id, a.head_revision_id).then(setFiles).catch(() => setFiles([]));
  }, [project, a.head_revision_id]);
  if (!files) return <div className="muted small ex-row">…</div>;
  return (
    <>
      {files.map((f) => (
        <Row key={f} depth={2} icon="·" label={<span className="mono small">{f}</span>} />
      ))}
    </>
  );
}

export function ExplorerPanel() {
  const s = useStudio();
  const [q, setQ] = useState("");
  const [expanded, setExpanded] = useState<Record<string, boolean>>({});
  if (!s.project) return null;
  const pid = s.project.id;
  const match = (t: string) => !q || t.toLowerCase().includes(q.toLowerCase());
  const locate = (ref: ResourceRef) => {
    const items = s.itemsFor(ref);
    if (items.length) s.locate(items[0].id);
    else s.notify("Not on this board — drag it onto the canvas.");
  };
  const selRefKey = (() => {
    const sel = s.selection;
    if (sel.kind === "items" && sel.itemIds.length === 1) {
      const it = s.board?.items.find((i) => i.id === sel.itemIds[0]);
      return it ? `${it.resource_ref.kind}:${it.resource_ref.id ?? it.resource_ref.node_id}` : "";
    }
    if (sel.kind === "component") return `component:${sel.artifactId}:${sel.componentId}`;
    if (sel.kind === "resource") return `${sel.ref.kind}:${sel.ref.id}`;
    return "";
  })();

  const compRows = (a: Artifact, parent: string | null, depth: number): ReactNode[] =>
    a.components
      .filter((c: Component) => (c.parent_id ?? null) === parent)
      .flatMap((c) => [
        match(c.name) || match(a.name) ? (
          <Row
            key={c.id}
            depth={depth}
            icon={<span className="kind">{c.kind}</span>}
            label={c.name}
            selected={selRefKey === `component:${a.id}:${c.id}`}
            onClick={() => {
              const view = s.itemsFor({ kind: "artifact", id: a.id }).find((i) => i.item_type === "artifact_view");
              if (view) s.patchPresentation(view.id, { selected_component: c.id });
              s.select({ kind: "component", itemId: view?.id ?? null, artifactId: a.id, componentId: c.id });
            }}
            right={s.bindings.some((b) => b.target.component_id === c.id && b.target.artifact_id === a.id) ? <Badge tone="info">ref</Badge> : undefined}
            testid={`ex-comp-${c.id}`}
          />
        ) : null,
        ...compRows(a, c.id, depth + 1),
      ]);

  const wfExecs = Object.values(s.executions);
  return (
    <aside className="explorer" data-testid="explorer">
      <div className="ex-search">
        <input placeholder="Search project…" value={q} onChange={(e) => setQ(e.target.value)} aria-label="search" />
      </div>
      <div className="ex-scroll">
        <Group
          title="Boards"
          count={s.boards.length}
          testid="ex-boards"
          actions={
            <button
              className="mini-btn"
              onClick={async () => {
                const name = prompt("Board name", `Board ${s.boards.length + 1}`);
                if (!name) return;
                const b = await s.run(boardApi.create(pid, name), "Board created");
                if (b) await s.openBoard(b.id);
              }}
            >
              + board
            </button>
          }
        >
          {s.boards.map((b) => (
            <Row
              key={b.id}
              icon="▦"
              label={b.name}
              selected={s.board?.id === b.id}
              onClick={() => s.board?.id !== b.id && s.openBoard(b.id)}
              right={
                s.boards.length > 1 && s.board?.id !== b.id ? (
                  <button
                    className="link small"
                    onClick={async (e) => {
                      e.stopPropagation();
                      if (confirm(`Delete board “${b.name}”? (Only the layout; project data is kept.)`) && (await s.run(boardApi.remove(pid, b.id), "Board deleted")))
                        s.openBoard(s.board!.id);
                    }}
                  >
                    ✕
                  </button>
                ) : undefined
              }
            />
          ))}
          {(s.board?.saved_views ?? []).map((v) => (
            <Row key={v.id} depth={1} icon="◎" label={v.name} onClick={() => s.notify("Use Views ▾ in the canvas toolbar to jump to a saved view")} />
          ))}
        </Group>
        <Group title="Artifacts" count={s.artifacts.length} testid="ex-artifacts">
          {s.artifacts
            .filter((a) => match(a.name) || a.components.some((c) => match(c.name)))
            .map((a) => (
              <div key={a.id}>
                <Row
                  icon={({ blender: "◆", layered2d: "▣", spreadsheet: "▦", document: "¶", presentation: "▭" } as Record<string, string>)[a.adapter] ?? "{ }"}
                  label={a.name}
                  sub={<span className="muted">{s.itemsFor({ kind: "artifact", id: a.id }).length} view(s)</span>}
                  dragRef={{ kind: "artifact", id: a.id }}
                  selected={selRefKey === `artifact:${a.id}`}
                  onClick={() => {
                    setExpanded({ ...expanded, [a.id]: !expanded[a.id] });
                    const it = s.itemsFor({ kind: "artifact", id: a.id })[0];
                    s.select(it ? { kind: "items", itemIds: [it.id] } : { kind: "resource", ref: { kind: "artifact", id: a.id } });
                  }}
                  onDouble={() => locate({ kind: "artifact", id: a.id })}
                  testid={`ex-artifact-${a.name}`}
                />
                {(expanded[a.id] || !!q) && (
                  <>
                    {compRows(a, null, 1)}
                    <Row depth={1} icon="▸" label={<span className="muted small">files</span>} onClick={() => setExpanded({ ...expanded, [`f:${a.id}`]: !expanded[`f:${a.id}`] })} />
                    {expanded[`f:${a.id}`] && <ArtifactFiles a={a} />}
                  </>
                )}
              </div>
            ))}
        </Group>
        <Group title="Sources" count={s.sources.length} actions={<AddSources />} testid="ex-sources">
          {s.sources
            .filter((x) => match(x.name) || match(x.media_type))
            .map((x) => (
              <Row
                key={x.id}
                icon={x.preview_path ? <img className="ex-thumb" src={fileUrl(pid, x.preview_path)} alt="" /> : "◉"}
                label={x.name}
                sub={
                  <>
                    <span className="muted">{x.media_type}</span>
                    {x.processing.state !== "ready" && <Status s={x.processing.state} />}
                  </>
                }
                dragRef={{ kind: "source", id: x.id }}
                selected={selRefKey === `source:${x.id}`}
                onClick={() => {
                  const it = s.itemsFor({ kind: "source", id: x.id })[0];
                  s.select(it ? { kind: "items", itemIds: [it.id] } : { kind: "resource", ref: { kind: "source", id: x.id } });
                }}
                onDouble={() => locate({ kind: "source", id: x.id })}
                testid={`ex-source-${x.name}`}
              />
            ))}
        </Group>
        <Group title="Workflows" count={s.workflows.length} testid="ex-workflows">
          {s.workflows
            .filter((w) => match(w.name) || w.nodes.some((n) => match(n.label)))
            .map((w) => (
              <div key={w.id}>
                <Row
                  icon="⚙"
                  label={w.name}
                  sub={<span className="muted">v{w.version}</span>}
                  right={s.executions[w.id] ? <Status s={s.executions[w.id].status} /> : undefined}
                  onClick={() => s.select({ kind: "resource", ref: { kind: "workflow", id: w.id } })}
                  onDouble={() => {
                    const f = s.board?.items.find((i) => i.item_type === "workflow" && i.resource_ref.id === w.id);
                    if (f) s.locate(f.id);
                  }}
                />
                {w.nodes.map((n) => (
                  <Row
                    key={n.id}
                    depth={1}
                    icon="·"
                    label={n.label || n.type}
                    sub={<span className="muted">{n.type}</span>}
                    dragRef={{ kind: "workflow_node", workflow_id: w.id, node_id: n.id }}
                    onDouble={() => locate({ kind: "workflow_node", workflow_id: w.id, node_id: n.id })}
                  />
                ))}
              </div>
            ))}
        </Group>
        <Group title="Executions" count={wfExecs.length} open={false} testid="ex-executions">
          <ExecutionList />
        </Group>
      </div>
    </aside>
  );
}

function ExecutionList() {
  const s = useStudio();
  const [list, setList] = useState<any[]>([]);
  useEffect(() => {
    if (s.project) api.executions(s.project.id).then((l) => setList(l.slice(0, 25)));
  }, [s.project, s.executions]);
  return (
    <>
      {list.map((e) => (
        <Row
          key={e.id}
          icon={<Status s={e.status} />}
          label={s.workflows.find((w) => w.id === e.workflow_id)?.name ?? "workflow"}
          sub={<span className="muted">{e.created_at.slice(11, 19)} · {short(e.id)}</span>}
          onClick={() => s.select({ kind: "resource", ref: { kind: "execution", id: e.id } })}
        />
      ))}
    </>
  );
}
