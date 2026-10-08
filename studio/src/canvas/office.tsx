/**
 * V1.2 Office renderers: spreadsheet grid, document flow and slide views.
 *
 * Every view is drawn from the adapter's structured preview of the *native* file (cells with
 * formulas and calculated values, document blocks with stable ids, slide shapes with boxes)
 * plus LibreOffice page renders. Edits are declarative operations sent through the same
 * checked edit endpoint as every other artifact: each one becomes a revision or is rejected.
 */
import { useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { boardApi, fileUrl, officeApi, type OfficeView } from "../api";
import { Badge } from "../components/common";
import { useStudio } from "../state";
import type { Artifact } from "../types";
import { invalidateRevision } from "./hooks";
import type { ArtifactRendererProps } from "./renderers";

const viewCache = new Map<string, OfficeView>();

function useOfficeView(artifact: Artifact, revisionId: string | undefined) {
  const { project } = useStudio();
  const [view, setView] = useState<OfficeView | null>(revisionId ? viewCache.get(revisionId) ?? null : null);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => {
    if (!project || !revisionId) return;
    const hit = viewCache.get(revisionId);
    if (hit) return setView(hit);
    let live = true;
    officeApi
      .view(project.id, artifact.id, revisionId)
      .then((v) => {
        viewCache.set(revisionId, v);
        if (live) setView(v);
      })
      .catch((e) => live && setErr(e.message));
    return () => {
      live = false;
    };
  }, [project, artifact.id, revisionId]);
  return { view, err };
}

function useEdit(artifact: Artifact) {
  const { project, run, refresh } = useStudio();
  return async (ops: any[], label: string) => {
    if (!project) return false;
    const r = await run(boardApi.edit(project.id, artifact.id, ops, label, artifact.head_revision_id), `New revision: ${label}`);
    if (r) {
      invalidateRevision(r.id);
      await refresh();
      return true;
    }
    return false;
  };
}

function PagesStrip({ pages, max = 12 }: { pages: string[]; max?: number }) {
  const { project } = useStudio();
  if (!project) return null;
  return (
    <div className="office-pages">
      {pages.slice(0, max).map((p, i) => (
        <img key={p} src={fileUrl(project.id, p)} alt={`page ${i + 1}`} draggable={false} />
      ))}
      {!pages.length && <div className="muted small">no rendered pages (LibreOffice unavailable)</div>}
    </div>
  );
}

const interactiveMode = (m: string) => m === "active" || m === "focus";

// ------------------------------------------------------------------ chart drawing
/** Small SVG drawing of a chart from its calculated cell values (labelled as such). */
export function MiniChart({ chart, width = 280, height = 150 }: { chart: any; width?: number; height?: number }) {
  if (chart.error) return <div className="warning small">chart {chart.id}: {chart.error}</div>;
  const names = Object.keys(chart.series ?? {});
  const vals: number[] = names.flatMap((n) => (chart.series[n] as any[]).filter((v) => typeof v === "number"));
  if (!vals.length) return <div className="muted small">chart {chart.id}: no calculated values</div>;
  const max = Math.max(...vals, 0);
  const min = Math.min(...vals, 0);
  const n = Math.max(...names.map((k) => chart.series[k].length));
  const pad = 18;
  const x = (i: number) => pad + ((width - 2 * pad) * (i + 0.5)) / n;
  const y = (v: number) => height - pad - ((height - 2 * pad) * (v - min)) / (max - min || 1);
  const colors = ["#e07a1f", "#3b6ea8", "#6a9f3b", "#9b59b6"];
  return (
    <svg width={width} height={height} className="mini-chart" role="img" aria-label={chart.title ?? chart.id}>
      <text x={width / 2} y={12} textAnchor="middle" fontSize="11">
        {chart.title ?? chart.id}
      </text>
      <line x1={pad} y1={y(0)} x2={width - pad} y2={y(0)} stroke="#999" />
      {names.map((nm, si) =>
        chart.type === "line" || chart.type === "scatter" ? (
          <polyline
            key={nm}
            fill="none"
            stroke={colors[si % 4]}
            strokeWidth={1.5}
            points={(chart.series[nm] as any[]).map((v, i) => (typeof v === "number" ? `${x(i)},${y(v)}` : "")).join(" ")}
          />
        ) : (
          (chart.series[nm] as any[]).map((v, i) =>
            typeof v === "number" ? (
              <rect
                key={`${nm}${i}`}
                x={x(i) - ((width - 2 * pad) / n) * 0.4 + (si * ((width - 2 * pad) / n) * 0.8) / names.length}
                width={(((width - 2 * pad) / n) * 0.8) / names.length}
                y={Math.min(y(v), y(0))}
                height={Math.abs(y(0) - y(v))}
                fill={colors[si % 4]}
              />
            ) : null,
          )
        ),
      )}
      {(chart.categories ?? []).map((c: string, i: number) =>
        n <= 12 || i % Math.ceil(n / 8) === 0 ? (
          <text key={i} x={x(i)} y={height - 4} fontSize="8" textAnchor="middle">
            {c}
          </text>
        ) : null,
      )}
    </svg>
  );
}

// ------------------------------------------------------------------ spreadsheet
export const colName = (i: number) => {
  let s = "";
  i += 1;
  while (i > 0) {
    const m = (i - 1) % 26;
    s = String.fromCharCode(65 + m) + s;
    i = Math.floor((i - 1) / 26);
  }
  return s;
};
export const parseRef = (ref: string) => {
  const m = /^\$?([A-Z]+)\$?(\d+)(?::\$?([A-Z]+)\$?(\d+))?$/.exec(ref.replace(/^.*!/, ""));
  if (!m) return null;
  const col = (s: string) => s.split("").reduce((a, ch) => a * 26 + ch.charCodeAt(0) - 64, 0) - 1;
  return { c1: col(m[1]), r1: +m[2] - 1, c2: col(m[3] ?? m[1]), r2: +(m[4] ?? m[2]) - 1 };
};
const fmtCell = (v: any) => (typeof v === "number" ? (Number.isInteger(v) ? String(v) : v.toFixed(Math.abs(v) >= 100 ? 2 : 3)) : v == null ? "" : String(v));
export const parseInput = (s: string): any => {
  if (s.startsWith("=")) return s;
  if (s.trim() !== "" && !isNaN(Number(s))) return Number(s);
  return s;
};

export function SheetView({ item, artifact, revision, mode, selectedComponent, onSelectComponent, patchState }: ArtifactRendererProps) {
  const { project } = useStudio();
  const { view, err } = useOfficeView(artifact, revision?.id);
  const edit = useEdit(artifact);
  const [cell, setCell] = useState<{ r: number; c: number } | null>(null);
  const [input, setInput] = useState("");
  const [pages, setPages] = useState(false);
  const interactive = interactiveMode(mode);
  const sheets = artifact.components.filter((c) => c.kind === "sheet");
  const sheetTitle: string = item.presentation_state.sheet ?? view?.view.sheets?.[0]?.title ?? "";
  const sheetComp = sheets.find((s) => s.name === sheetTitle);
  const grid = view?.view.sheets?.find((s: any) => s.title === sheetTitle);
  const named = artifact.components.filter((c) => ["range", "table", "chart"].includes(c.kind) && c.parent_id === sheetComp?.id);
  const selComp = artifact.components.find((c) => c.id === selectedComponent);
  const hi = selComp?.metadata?.ref && selComp.metadata.title === sheetTitle ? parseRef(selComp.metadata.ref) : null;
  const charts = (view?.view.charts ?? []).filter((c: any) => c.sheet === sheetTitle);
  if (err) return <div className="warning small">{err}</div>;
  if (!view || !project) return <div className="muted">loading…</div>;
  if (mode === "far")
    return view.pages[0] ? <img className="office-thumb" src={fileUrl(project.id, view.pages[0])} alt="" draggable={false} /> : <div className="r-card">{sheets.length} sheets</div>;
  const maxRows = mode === "medium" ? 10 : mode === "close" ? 24 : 200;
  const sel = cell && grid?.rows?.[cell.r]?.[cell.c];
  const addr = cell ? `${colName(cell.c)}${cell.r + 1}` : "";
  const apply = async () => {
    if (!cell || !sheetComp) return;
    if (await edit([{ op: "set_cells", component_id: sheetComp.id, params: { cells: { [addr]: parseInput(input) } } }], `${sheetTitle}!${addr} = ${input}`)) setCell(null);
  };
  return (
    <div className="r-office r-sheet" data-testid="sheet-view">
      <div className="office-tabs nodrag">
        {sheets.map((s) => (
          <button key={s.id} className={`tab-chip ${s.name === sheetTitle ? "on" : ""}`} onClick={() => patchState({ sheet: s.name })}>
            {s.name}
          </button>
        ))}
        <span className="spacer" />
        {!view.view.calculated && <Badge tone="warn">formulas not calculated</Badge>}
        <button className="mini-btn" onClick={() => setPages(!pages)}>
          {pages ? "Grid" : "Print preview"}
        </button>
      </div>
      {named.length > 0 && mode !== "medium" && (
        <div className="office-chips nodrag">
          {named.map((c) => (
            <button key={c.id} className={`layer-chip ${c.id === selectedComponent ? "on" : ""}`} onClick={() => onSelectComponent(c.id === selectedComponent ? null : c.id)} title={`${c.kind} ${c.native_ref ?? ""}`}>
              <span className="muted small">{c.kind}</span> {c.name}
            </button>
          ))}
        </div>
      )}
      {pages ? (
        <PagesStrip pages={view.pages} />
      ) : (
        <>
          {interactive && (
            <div className="formula-bar nodrag" data-testid="formula-bar">
              <code>{addr || "—"}</code>
              <input
                value={input}
                disabled={!cell}
                placeholder={cell ? "value or =formula" : "select a cell"}
                onChange={(e) => setInput(e.target.value)}
                onKeyDown={(e) => e.key === "Enter" && apply()}
                aria-label="cell input"
              />
              <button className="mini-btn primary" disabled={!cell} onClick={apply}>
                Apply
              </button>
              {sel && "f" in sel && <span className="muted small">{sel.calc ? `= ${fmtCell(sel.v)}` : "not calculated"}</span>}
            </div>
          )}
          <div className={`grid-wrap ${interactive ? "nodrag nowheel" : ""}`}>
            <table className="sheet-grid">
              <thead>
                <tr>
                  <th />
                  {(grid?.rows?.[0] ?? []).map((_: any, j: number) => (
                    <th key={j}>{colName(j)}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {(grid?.rows ?? []).slice(0, maxRows).map((row: any[], i: number) => (
                  <tr key={i}>
                    <th>{i + 1}</th>
                    {row.map((c, j) => {
                      const inHi = hi && i >= hi.r1 && i <= hi.r2 && j >= hi.c1 && j <= hi.c2;
                      const on = cell?.r === i && cell?.c === j;
                      return (
                        <td
                          key={j}
                          className={`${c && "f" in c ? "formula" : ""} ${c?.b ? "bold" : ""} ${inHi ? "hi" : ""} ${on ? "on" : ""} ${typeof c?.v === "number" ? "num" : ""}`}
                          title={c && "f" in c ? c.f : undefined}
                          onClick={() => {
                            if (!interactive) return;
                            setCell({ r: i, c: j });
                            setInput(c ? ("f" in c ? c.f : fmtCell(c.v)) : "");
                          }}
                          data-cell={`${colName(j)}${i + 1}`}
                        >
                          {c && "f" in c && !c.calc ? "…" : fmtCell(c?.v)}
                        </td>
                      );
                    })}
                  </tr>
                ))}
              </tbody>
            </table>
            {grid && grid.max_row > Math.min(maxRows, grid.rows.length) && (
              <div className="muted small">
                showing {Math.min(maxRows, grid.rows.length)} of {grid.max_row} rows
              </div>
            )}
          </div>
          {charts.length > 0 && mode !== "medium" && (
            <div className="office-charts nodrag">
              {charts.map((ch: any) => (
                <button key={ch.id} className={`chart-card ${ch.id === selectedComponent ? "on" : ""}`} onClick={() => onSelectComponent(ch.id)}>
                  <MiniChart chart={ch} />
                </button>
              ))}
              <div className="muted small">charts drawn from calculated cell values · native charts in Print preview</div>
            </div>
          )}
        </>
      )}
    </div>
  );
}

// ------------------------------------------------------------------ document
export function DocView({ artifact, revision, mode, selectedComponent, onSelectComponent }: ArtifactRendererProps) {
  const { project } = useStudio();
  const { view, err } = useOfficeView(artifact, revision?.id);
  const edit = useEdit(artifact);
  const [draft, setDraft] = useState<string | null>(null);
  const [pages, setPages] = useState(false);
  const interactive = interactiveMode(mode);
  const blocks: any[] = view?.view.blocks ?? [];
  const sel = blocks.find((b) => b.id === selectedComponent);
  const imgDir = revision?.previews.structure?.replace(/[^/]+$/, "") ?? "";
  useEffect(() => setDraft(null), [selectedComponent, revision?.id]);
  if (err) return <div className="warning small">{err}</div>;
  if (!view || !project) return <div className="muted">loading…</div>;
  if (mode === "far")
    return view.pages[0] ? <img className="office-thumb" src={fileUrl(project.id, view.pages[0])} alt="" draggable={false} /> : <div className="r-card">{blocks.length} blocks</div>;
  const shown = mode === "medium" ? blocks.slice(0, 8) : blocks;
  const save = async () => {
    if (!sel || draft === null) return;
    if (await edit([{ op: "set_text", component_id: sel.id, params: { text: draft } }], `Edit text of ${sel.id}`)) setDraft(null);
  };
  return (
    <div className="r-office r-doc" data-testid="doc-view">
      <div className="office-tabs nodrag">
        <b className="small">{blocks.filter((b) => b.kind === "section").length} sections</b>
        {view.view.issues?.length > 0 && <Badge tone="warn">{view.view.issues.length} issue(s)</Badge>}
        <span className="spacer" />
        <button className="mini-btn" onClick={() => setPages(!pages)}>
          {pages ? "Structure" : "Print preview"}
        </button>
      </div>
      {pages ? (
        <PagesStrip pages={view.pages} />
      ) : (
        <div className={`doc-flow ${interactive ? "nodrag nowheel" : ""}`}>
          {shown.map((b) => {
            const on = b.id === selectedComponent;
            const cls = `doc-block kind-${b.kind} ${on ? "on" : ""} ${b.unregistered ? "unreg" : ""}`;
            const pick = () => interactive && onSelectComponent(on ? null : b.id);
            if (b.kind === "section") {
              const H = (`h${Math.min(4, Math.max(1, (b.level ?? 1) + 1))}`) as any;
              return (
                <H key={b.id} className={cls} onClick={pick} data-component={b.id}>
                  {b.text}
                </H>
              );
            }
            if (b.kind === "table")
              return (
                <table key={b.id} className={`${cls} doc-table`} onClick={pick} data-component={b.id}>
                  <tbody>
                    {(b.rows ?? []).map((r: string[], i: number) => (
                      <tr key={i}>
                        {r.map((c, j) => (i === 0 ? <th key={j}>{c}</th> : <td key={j}>{c}</td>))}
                      </tr>
                    ))}
                  </tbody>
                </table>
              );
            if (b.kind === "figure")
              return (
                <div key={b.id} className={cls} onClick={pick} data-component={b.id}>
                  {b.image ? <img src={fileUrl(project.id, imgDir + b.image)} alt={b.id} draggable={false} /> : <div className="muted small">figure {b.id}</div>}
                </div>
              );
            return (
              <p key={b.id} className={cls} onClick={pick} data-component={b.id}>
                {b.text}
              </p>
            );
          })}
          {mode === "medium" && blocks.length > shown.length && <div className="muted small">… {blocks.length - shown.length} more blocks</div>}
        </div>
      )}
      {interactive && sel && ["paragraph", "section"].includes(sel.kind) && (
        <div className="office-edit nodrag" data-testid="doc-edit">
          <b className="small">{sel.id}</b>
          <textarea value={draft ?? sel.text} onChange={(e) => setDraft(e.target.value)} rows={3} aria-label="block text" />
          <button className="mini-btn primary" disabled={draft === null || draft === sel.text} onClick={save}>
            Apply as revision
          </button>
        </div>
      )}
    </div>
  );
}

// ------------------------------------------------------------------ presentation
/** Size of the largest box with aspect `ar` that fits the element (for shape overlays). */
function useFit(ar: number, ready: boolean) {
  const ref = useRef<HTMLDivElement>(null);
  const [size, setSize] = useState({ w: 0, h: 0 });
  useLayoutEffect(() => {
    const el = ref.current;
    if (!el || !ready) return;
    const fit = () => {
      const W = el.clientWidth;
      const H = el.clientHeight;
      const w = Math.min(W, H * ar);
      setSize({ w, h: w / ar });
    };
    fit();
    const ro = new ResizeObserver(fit);
    ro.observe(el);
    return () => ro.disconnect();
  }, [ar, ready]);
  return { ref, size };
}

export function SlidesView({ item, artifact, revision, mode, selectedComponent, onSelectComponent, patchState }: ArtifactRendererProps) {
  const { project } = useStudio();
  const { view, err } = useOfficeView(artifact, revision?.id);
  const edit = useEdit(artifact);
  const [draft, setDraft] = useState<string | null>(null);
  const interactive = interactiveMode(mode);
  const slides: any[] = view?.view.slides ?? [];
  const shapeSlide = useMemo(() => {
    const m: Record<string, number> = {};
    slides.forEach((s, i) => s.shapes.forEach((sh: any) => (m[sh.id] = i)));
    slides.forEach((s, i) => (m[s.id] = i));
    return m;
  }, [slides]);
  const idx: number = Math.min(slides.length - 1, Math.max(0, selectedComponent && selectedComponent in shapeSlide ? shapeSlide[selectedComponent] : item.presentation_state.slide ?? 0));
  const slide = slides[idx];
  const shape = slide?.shapes.find((s: any) => s.id === selectedComponent);
  const [ar, setAr] = useState(4 / 3);
  const { ref, size } = useFit(ar, !!view && !err && mode !== "far");
  useEffect(() => setDraft(null), [selectedComponent, revision?.id]);
  if (err) return <div className="warning small">{err}</div>;
  if (!view || !project) return <div className="muted">loading…</div>;
  const img = view.pages[idx];
  if (mode === "far") return img ? <img className="office-thumb" src={fileUrl(project.id, view.pages[0])} alt="" draggable={false} /> : <div className="r-card">{slides.length} slides</div>;
  const save = async () => {
    if (!shape || draft === null) return;
    if (await edit([{ op: "set_text", component_id: shape.id, params: { paragraphs: draft.split("\n") } }], `Edit text of ${shape.id}`)) setDraft(null);
  };
  return (
    <div className="r-office r-slides" data-testid="slides-view">
      <div className="slide-main" ref={ref}>
        <div className="slide-frame" style={{ width: size.w, height: size.h }}>
          {img ? (
            <img
              src={fileUrl(project.id, img)}
              alt={`slide ${idx + 1}`}
              draggable={false}
              onLoad={(e) => {
                const t = e.currentTarget;
                if (t.naturalWidth && t.naturalHeight) setAr(t.naturalWidth / t.naturalHeight);
              }}
            />
          ) : (
            <div className="muted">slide {idx + 1} (no render)</div>
          )}
          {interactive &&
            slide?.shapes.map((sh: any) => (
              <button
                key={sh.id}
                className={`shape-hit nodrag ${sh.id === selectedComponent ? "on" : ""}`}
                style={{ left: `${sh.box[0] * 100}%`, top: `${sh.box[1] * 100}%`, width: `${sh.box[2] * 100}%`, height: `${sh.box[3] * 100}%` }}
                title={`${sh.kind} ${sh.id}`}
                onClick={() => onSelectComponent(sh.id === selectedComponent ? null : sh.id)}
                data-component={sh.id}
              />
            ))}
        </div>
      </div>
      {mode !== "medium" && (
        <div className="slide-strip nodrag">
          {slides.map((s, i) => (
            <button
              key={s.id}
              className={`slide-thumb ${i === idx ? "on" : ""}`}
              onClick={() => {
                patchState({ slide: i });
                onSelectComponent(s.id);
              }}
              title={s.title}
            >
              {view.pages[i] ? <img src={fileUrl(project.id, view.pages[i])} alt="" draggable={false} /> : <span>{i + 1}</span>}
            </button>
          ))}
        </div>
      )}
      {interactive && shape && (
        <div className="office-edit nodrag" data-testid="slide-edit">
          <b className="small">
            {shape.kind} · {shape.id}
          </b>
          {"text" in shape && shape.kind !== "chart" ? (
            <>
              <textarea value={draft ?? shape.text} onChange={(e) => setDraft(e.target.value)} rows={4} aria-label="shape text" />
              <button className="mini-btn primary" disabled={draft === null || draft === shape.text} onClick={save}>
                Apply as revision
              </button>
            </>
          ) : shape.kind === "chart" ? (
            <MiniChart chart={{ ...shape, type: /LINE/.test(shape.chart_type ?? "") ? "line" : "column" }} />
          ) : (
            <span className="muted small">no text</span>
          )}
        </div>
      )}
    </div>
  );
}
