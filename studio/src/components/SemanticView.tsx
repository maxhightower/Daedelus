import { useEffect, useState } from "react";
import { api, fileUrl } from "../api";
import { useStudio } from "../state";
import type { ContextEntry, ContextPackage, DerivedConstraint, Observation, SourceAnalysis, SourceLocation, Usage } from "../types";
import { Badge, Section } from "./common";

const CATEGORY_TONE: Record<string, string> = {
  reference: "info",
  inspiration: "default",
  guideline: "warn",
  constraint: "err",
  technique: "info",
  evaluation: "ok",
  context: "default",
};

export function locationText(l?: SourceLocation | null) {
  if (!l) return "";
  const parts = [];
  if (l.page != null) parts.push(`p.${l.page}`);
  if (l.section) parts.push(`§ ${l.section}`);
  if (l.line != null) parts.push(`line ${l.line}`);
  if (l.start_seconds != null) parts.push(`${l.start_seconds}s${l.end_seconds != null ? `–${l.end_seconds}s` : ""}`);
  if (l.region) parts.push(`region [${l.region.map((v) => v.toFixed(2)).join(", ")}]`);
  return parts.join(" · ");
}

export function usageText(u?: Usage | null) {
  if (!u) return "";
  const parts = [];
  if (u.calls) parts.push(`${u.calls} call(s)`);
  if (u.input_tokens != null) parts.push(`${u.input_tokens} in / ${u.output_tokens ?? "?"} out tokens`);
  if (u.latency_ms != null) parts.push(`${Math.round(u.latency_ms)} ms`);
  parts.push(u.cost_usd != null ? `$${u.cost_usd.toFixed(4)}` : "cost n/a");
  return parts.join(" · ");
}

export function ObservationRow({ o }: { o: Observation }) {
  return (
    <div className={`obs obs-${o.basis}`} data-testid="observation">
      <Badge>{o.kind}</Badge>
      <Badge tone={o.basis === "inferred" ? "warn" : o.basis === "measured" ? "ok" : "info"} title="how this was obtained">
        {o.basis}
        {o.confidence != null ? ` ${Math.round(o.confidence * 100)}%` : ""}
      </Badge>
      {o.value?.possible_instruction_to_ai && (
        <Badge tone="err" title="text addressed to an AI inside the source: reported as data, never followed">
          injected instruction (data only)
        </Badge>
      )}
      <span>{o.text}</span>
      {o.value?.hex && <span className="swatch" style={{ background: o.value.hex }} />}
      {o.location && <span className="muted small"> — {locationText(o.location)}</span>}
    </div>
  );
}

function ConstraintRow({ c }: { c: DerivedConstraint }) {
  return (
    <div className="small">
      <Badge tone={c.enforced ? "err" : "warn"}>{c.enforced ? "enforced" : "advisory"}</Badge>
      <code>
        {c.property} {c.op} {String(c.value)}
      </code>{" "}
      <span className="muted">“{c.text.slice(0, 110)}”{c.location ? ` (${locationText(c.location)})` : ""}</span>
    </div>
  );
}

export function AnalysisCard({ a }: { a: SourceAnalysis }) {
  const [all, setAll] = useState(false);
  const obs = all ? a.observations : a.observations.slice(0, 6);
  return (
    <div className="analysis-card" data-testid="analysis-card">
      <div className="row small">
        <Badge tone={a.status === "complete" ? "ok" : a.status === "partial" ? "warn" : "err"}>{a.status}</Badge>
        <b>{a.provider}</b>
        {a.model && <span className="muted">{a.model}</span>}
        <span className="muted">via {a.pathway || "?"}</span>
        {a.recorded && (
          <Badge tone="warn" title="served from a fixture - not a live model call">
            {a.fixture_origin ?? "fixture"} replay
          </Badge>
        )}
        {a.segment && <Badge title={JSON.stringify(a.segment)}>segment {a.segment.kind}</Badge>}
      </div>
      {a.summary && <div className="small">{a.summary}</div>}
      {a.limitations.map((l, i) => (
        <div key={i} className="warning small">
          {l}
        </div>
      ))}
      {obs.map((o) => (
        <ObservationRow key={o.id} o={o} />
      ))}
      {a.observations.length > 6 && (
        <button className="link small" onClick={() => setAll(!all)}>
          {all ? "show fewer" : `show all ${a.observations.length} observations`}
        </button>
      )}
      {a.derived_constraints.length > 0 && (
        <div className="small">
          <b>Measurable limits found:</b>
          {a.derived_constraints.map((c, i) => (
            <ConstraintRow key={i} c={c} />
          ))}
          <div className="muted">Whether a limit is enforced depends on the meaning you give this source, not on the analysis.</div>
        </div>
      )}
      <div className="muted small">{usageText(a.usage)}</div>
    </div>
  );
}

/** Source inspector section: run an analysis with a chosen provider and browse the results. */
export function SourceUnderstanding({ sourceId }: { sourceId: string }) {
  const s = useStudio();
  const [list, setList] = useState<SourceAnalysis[]>([]);
  const [prov, setProv] = useState("heuristic");
  const [busy, setBusy] = useState(false);
  const pid = s.project?.id;
  useEffect(() => {
    if (pid) api.analyses(pid, sourceId).then(setList).catch(() => setList([]));
  }, [pid, sourceId]);
  if (!pid) return null;
  const provs = (s.health?.providers ?? []).filter((p) => !p.contracts || p.contracts.includes("analyze"));
  const chosen = provs.find((p) => p.name === prov);
  return (
    <Section title={`Understanding (${list.length} analysis${list.length === 1 ? "" : "es"})`}>
      <div className="row small">
        <select value={prov} onChange={(e) => setProv(e.target.value)} aria-label="analysis provider">
          {provs.map((p) => (
            <option key={p.name} value={p.name} disabled={!p.available}>
              {p.name}
              {p.live ? " (live model)" : ""}
              {p.available ? "" : " — unavailable"}
            </option>
          ))}
        </select>
        <button
          disabled={busy || !chosen?.available}
          data-testid="analyze-source"
          onClick={async () => {
            setBusy(true);
            const r = await s.run(api.analyze(pid, sourceId, { provider: prov, force: true }), "Analysis recorded");
            setBusy(false);
            if (r) setList(await api.analyses(pid, sourceId));
          }}
        >
          {busy ? "Analysing…" : "Analyse"}
        </button>
        {chosen && !chosen.available && <span className="muted">{chosen.detail}</span>}
      </div>
      {chosen?.pathways && <div className="muted small">pathways: {chosen.pathways.join(", ")}</div>}
      {list
        .slice()
        .reverse()
        .map((a) => (
          <AnalysisCard key={a.id} a={a} />
        ))}
      {!list.length && <div className="muted small">Not analysed yet. Analyses describe what a source contains; its meaning comes from the bindings below.</div>}
    </Section>
  );
}

function EntryCard({ e }: { e: ContextEntry }) {
  const [open, setOpen] = useState(false);
  return (
    <div className={`ctx-entry ${e.applies ? "" : "inactive"}`} data-testid="context-entry">
      <div className="row small">
        <Badge tone={CATEGORY_TONE[e.category] ?? "default"}>{e.category}</Badge>
        <b>{e.source}</b>
        <span className="muted">
          {e.media_type} · {e.role} · {e.relation} · weight {e.weight.toFixed(2)}
        </span>
        {!e.applies && <Badge tone="warn">not applied here</Badge>}
        {e.segment && <Badge title={JSON.stringify(e.segment)}>{e.segment.kind}{e.segment.region ? ` [${e.segment.region.map((v: number) => v.toFixed(2)).join(",")}]` : ""}</Badge>}
      </div>
      {e.aspects.length > 0 && <div className="muted small">aspects: {e.aspects.join(", ")}</div>}
      {e.analysis ? (
        <div className="small">
          analysis: <Badge tone={e.analysis.status === "complete" ? "ok" : "warn"}>{e.analysis.status}</Badge> {e.analysis.provider}
          {e.analysis.recorded && <Badge tone="warn">fixture</Badge>} — {e.analysis.summary}{" "}
          {e.observations.length > 0 && (
            <button className="link" onClick={() => setOpen(!open)}>
              {open ? "hide" : `${e.observations.length} observation(s)`}
            </button>
          )}
        </div>
      ) : (
        <div className="warning small">not analysed yet — the planner would only see the binding and measured summary</div>
      )}
      {open && e.observations.map((o) => <ObservationRow key={o.id} o={o} />)}
      {e.derived_constraints.map((c, i) => (
        <ConstraintRow key={i} c={c} />
      ))}
    </div>
  );
}

/** What the agent would receive for one target: inspectable before anything runs. */
export function ContextPackageView({ pkg, onAnalyse }: { pkg: ContextPackage; onAnalyse?: () => void }) {
  return (
    <div className="ctx-package" data-testid="context-package">
      <div className="small">
        <b>{pkg.target.component_name ?? pkg.target.artifact_name ?? "Project"}</b> <span className="muted">{pkg.unit}</span>
      </div>
      {pkg.missing_analyses.length > 0 && (
        <div className="warning small">
          {pkg.missing_analyses.length} applicable source(s) not analysed.{" "}
          {onAnalyse && (
            <button className="link" onClick={onAnalyse} data-testid="analyse-missing">
              Analyse (local, measurement-based)
            </button>
          )}
        </div>
      )}
      {pkg.entries.map((e) => (
        <EntryCard key={e.binding_id} e={e} />
      ))}
      {!pkg.entries.length && <div className="muted small">No sources are bound to this target or its ancestors.</div>}
      {pkg.conflicts.length > 0 && <div className="error-box small">{pkg.conflicts.length} conflict(s) — affected operations will be blocked.</div>}
      <div className="muted small">{pkg.authority}</div>
    </div>
  );
}

/** Evaluate -> Revise loop record of one agent node run. */
export function LoopView({ loop }: { loop: any }) {
  const s = useStudio();
  if (!loop) return null;
  return (
    <div className="loop-view" data-testid="loop-view">
      <div className="row small">
        <Badge tone={loop.status === "achieved" ? "ok" : loop.status === "running" ? "info" : "warn"}>{loop.status}</Badge>
        <span>{loop.stop_reason}</span>
        <span className="muted">
          {loop.iterations_run ?? 0} correction(s) · evaluator {loop.evaluator} · {usageText(loop.usage)}
          {loop.seconds != null ? ` · ${loop.seconds}s` : ""}
        </span>
      </div>
      {(loop.iterations ?? []).map((it: any) => (
        <div key={it.iteration} className="loop-iter">
          <div className="row small">
            <b>iteration {it.iteration}</b> <span className="muted">revision r{it.revision_number}</span>
            <Badge tone={it.passed ? "ok" : "warn"}>{it.passed ? "passed" : "issues"}</Badge>
          </div>
          {it.render && s.project && <img className="loop-thumb" src={fileUrl(s.project.id, it.render)} alt={`render r${it.revision_number}`} />}
          {it.units.flatMap((u: any) => u.evaluation.findings).map((f: any, i: number) => (
            <div key={i} className="small">
              <Badge tone={f.status === "pass" ? "ok" : f.status === "fail" ? "err" : "warn"}>{f.status}</Badge>
              <Badge>{f.kind}</Badge> {f.criterion} <span className="muted">{f.detail}</span>
            </div>
          ))}
          {(it.revisions ?? []).map((r: any, i: number) => (
            <div key={i} className="small">
              → correction: {r.plan.operations.map((o: any) => `${o.op}(${o.component_id ?? ""})`).join(", ") || "none"}
              {r.errors.length > 0 && <span className="error-box"> rejected: {r.errors.join("; ")}</span>}
            </div>
          ))}
          {it.apply_error && <div className="error-box small">{it.apply_error}</div>}
        </div>
      ))}
    </div>
  );
}
