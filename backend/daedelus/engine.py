"""Workflow engine: graph validation, topological execution, incremental units,
checkpoints, retries, approval pauses, revisions, impact analysis and replay.

Execution of an ``agent`` node, per target *unit* (a component that has its own
bindings, plus the node's target root):

1. resolve the bindings that apply to the unit (``bindings.resolve``)
2. fingerprint everything that can influence the unit's result
3. skip the unit if the fingerprint and the unit's subtree state are unchanged
4. plan declarative operations with a provider; check them against the
   adapter catalogue and the unit's scope
5. checkpoint the native files, apply, inspect, and verify that components
   outside the executed units are untouched and constraints hold - otherwise
   roll back to the checkpoint
6. record a revision with operations, attribution and validation evidence
"""

from __future__ import annotations

import hashlib
import json
import shutil
import threading
import time
import traceback
from pathlib import Path
from typing import Any

from . import bindings as binding_mod
from . import features, ingest
from .adapters import AdapterError, get_adapter
from .adapters.base import check_schema
from .artifacts import native_path, record_revision, restore_tree, snapshot
from .models import (
    Artifact,
    AttributionRecord,
    Execution,
    NodeRun,
    OperationResult,
    PlannedOperation,
    ProcessingState,
    ResolvedContext,
    RunStatus,
    TargetSelector,
    UnitRun,
    ValidationReport,
    Workflow,
    WorkflowNode,
    now_iso,
)
from .nodes import NODE_TYPES, compatible
from .providers import PlanRequest, ProviderError, get_provider, providers
from .store import ProjectStore

MEASURABLE_OPS = {"preserve", "forbid_change", "eq", "lte", "gte", "range"}


def _h(obj: Any) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, default=str).encode()).hexdigest()


class ExecutionPaused(Exception):
    pass


class NodeFailure(Exception):
    pass


# ---------------------------------------------------------------------------
# validation
# ---------------------------------------------------------------------------

def topo_order(wf: Workflow) -> list[str]:
    ids = [n.id for n in wf.nodes]
    indeg = {i: 0 for i in ids}
    succ: dict[str, list[str]] = {i: [] for i in ids}
    for e in wf.edges:
        if e.source in indeg and e.target in indeg:
            indeg[e.target] += 1
            succ[e.source].append(e.target)
    order, ready = [], [i for i in ids if indeg[i] == 0]
    while ready:
        n = ready.pop(0)
        order.append(n)
        for m in succ[n]:
            indeg[m] -= 1
            if indeg[m] == 0:
                ready.append(m)
    if len(order) != len(ids):
        raise ValueError("workflow graph contains a cycle")
    return order


def validate_workflow(wf: Workflow, store: ProjectStore | None = None) -> list[dict[str, str]]:
    issues: list[dict[str, str]] = []

    def err(node: str | None, msg: str, level: str = "error") -> None:
        issues.append({"node_id": node or "", "level": level, "message": msg})

    seen = set()
    nodes = {}
    for n in wf.nodes:
        if n.id in seen:
            err(n.id, f"duplicate node id '{n.id}'")
        seen.add(n.id)
        nodes[n.id] = n
        nt = NODE_TYPES.get(n.type)
        if nt is None:
            err(n.id, f"unknown node type '{n.type}'")
            continue
        cfg = {**nt.defaults, **n.config}
        for e in check_schema(cfg, nt.config_schema, "config"):
            err(n.id, e)
    for e in wf.edges:
        s, t = nodes.get(e.source), nodes.get(e.target)
        if s is None or t is None:
            err(None, f"edge {e.id} references a missing node")
            continue
        st, tt = NODE_TYPES.get(s.type), NODE_TYPES.get(t.type)
        if not st or not tt:
            continue
        sp = next((p for p in st.outputs if p.name == e.source_port), None)
        tp = next((p for p in tt.inputs if p.name == e.target_port), None)
        if sp is None:
            err(s.id, f"edge {e.id}: node has no output port '{e.source_port}'")
        if tp is None:
            err(t.id, f"edge {e.id}: node has no input port '{e.target_port}'")
        if sp and tp and not compatible(sp.type, tp.type):
            err(t.id, f"edge {e.id}: {sp.type} output cannot connect to {tp.type} input "
                      f"'{tp.name}'")
    for n in wf.nodes:
        nt = NODE_TYPES.get(n.type)
        if not nt:
            continue
        for p in nt.inputs:
            incoming = [e for e in wf.edges if e.target == n.id and e.target_port == p.name]
            if p.required and not incoming:
                err(n.id, f"required input '{p.name}' is not connected")
            if not p.multiple and len(incoming) > 1:
                err(n.id, f"input '{p.name}' accepts a single connection")
    try:
        topo_order(wf)
    except ValueError as exc:
        err(None, str(exc))
    if store is not None:
        known_providers = providers()
        for n in wf.nodes:
            cfg = {**NODE_TYPES[n.type].defaults, **n.config} if n.type in NODE_TYPES else n.config
            if n.type == "artifact":
                try:
                    store.get_artifact(cfg.get("artifact_id", ""))
                except LookupError:
                    err(n.id, f"artifact '{cfg.get('artifact_id')}' does not exist")
            if n.type == "sources":
                for sid in cfg.get("source_ids", []):
                    try:
                        store.get_source(sid)
                    except LookupError:
                        err(n.id, f"source '{sid}' does not exist")
            if n.type == "agent":
                prov = cfg.get("provider") or store.get_project().settings.default_provider
                if prov not in known_providers:
                    err(n.id, f"unknown provider '{prov}'")
                else:
                    ok, why = known_providers[prov].available()
                    if not ok:
                        err(n.id, f"provider '{prov}' unavailable: {why}", "warning")
                if cfg.get("execution") == "cloud":
                    err(n.id, "cloud execution selected but no cloud workers are configured "
                              "in this installation")
                art = _agent_artifact(wf, n, store)
                if art is not None:
                    ad = get_adapter(art.adapter)
                    ok, why = ad.check_environment()
                    if not ok:
                        err(n.id, f"adapter '{art.adapter}' unavailable: {why}", "warning")
                    tc = cfg.get("target_component")
                    if tc and art.component(tc) is None:
                        err(n.id, f"target component '{tc}' not found in '{art.name}'")
                    names = {o.name for o in ad.info().operations}
                    for op in cfg.get("allowed_ops", []):
                        if op not in names:
                            err(n.id, f"operation '{op}' not offered by adapter '{art.adapter}'")
    return issues


def _agent_artifact(wf: Workflow, node: WorkflowNode, store: ProjectStore) -> Artifact | None:
    e = next((e for e in wf.edges if e.target == node.id and e.target_port == "artifact"), None)
    if e is None:
        return None
    src = next((n for n in wf.nodes if n.id == e.source), None)
    if src is None or src.type != "artifact":
        return None
    try:
        return store.get_artifact(src.config.get("artifact_id", ""))
    except LookupError:
        return None


# ---------------------------------------------------------------------------
# engine
# ---------------------------------------------------------------------------

class Engine:
    def __init__(self, store: ProjectStore):
        self.store = store
        self._cancel: set[str] = set()
        self._run_lock = threading.RLock()

    # -- public API ---------------------------------------------------------
    def start(self, workflow_id: str, *, version: int | None = None, mode: str = "incremental",
              nodes: list[str] | None = None) -> Execution:
        wf = self.store.get_workflow(workflow_id, version)
        issues = [i for i in validate_workflow(wf, self.store) if i["level"] == "error"]
        ex = Execution(workflow_id=wf.id, workflow_version=wf.version, mode=mode,  # type: ignore
                       selected_nodes=nodes)
        ex.node_runs = [NodeRun(node_id=n.id, node_type=n.type) for n in wf.nodes]
        if issues:
            ex.status = RunStatus.failed
            ex.error = "workflow invalid: " + "; ".join(
                f"[{i['node_id'] or 'graph'}] {i['message']}" for i in issues)
            ex.finished_at = now_iso()
        self.store.save_execution(ex)
        return ex

    def run(self, execution_id: str) -> Execution:
        """Run (or resume) an execution synchronously until done or paused.

        Executions of one project are serialised: they edit the same native files."""
        with self._run_lock:
            return self._run(execution_id)

    def _run(self, execution_id: str) -> Execution:
        ex = self.store.get_execution(execution_id)
        if ex.status in (RunStatus.failed, RunStatus.succeeded, RunStatus.cancelled):
            return ex
        wf = self.store.get_workflow(ex.workflow_id, ex.workflow_version)
        ex.status = RunStatus.running
        ex.started_at = ex.started_at or now_iso()
        if not ex.inputs_snapshot:
            ex.inputs_snapshot = self._inputs_snapshot(wf)
        self.store.save_execution(ex)
        order = topo_order(wf)
        nodes = {n.id: n for n in wf.nodes}
        try:
            for nid in order:
                if ex.id in self._cancel:
                    raise NodeFailure("cancelled")
                nr = ex.run(nid)
                if nr.status in (RunStatus.succeeded, RunStatus.skipped, RunStatus.failed,
                                 RunStatus.blocked):
                    continue
                if ex.selected_nodes is not None and nid not in ex.selected_nodes \
                        and nr.status == RunStatus.pending:
                    upstream_needed = self._needed_by_selection(wf, ex.selected_nodes)
                    if nid not in upstream_needed:
                        nr.status = RunStatus.skipped
                        nr.logs.append("not selected for this execution")
                        self.store.save_execution(ex)
                        continue
                ups = self._upstream(wf, nid)
                bad = [u for u in ups if ex.run(u).status in (RunStatus.failed, RunStatus.blocked)]
                if bad:
                    nr.status = RunStatus.blocked
                    nr.error = f"blocked by failed upstream node(s): {bad}"
                    self.store.save_execution(ex)
                    continue
                self._run_node(ex, wf, nodes[nid])
        except ExecutionPaused:
            ex.status = RunStatus.waiting_approval
            self.store.save_execution(ex)
            return ex
        except NodeFailure as exc:
            if str(exc) == "cancelled":
                ex.status = RunStatus.cancelled
                for r in ex.node_runs:
                    if r.status in (RunStatus.pending, RunStatus.running):
                        r.status = RunStatus.cancelled
        statuses = {r.status for r in ex.node_runs}
        if ex.status != RunStatus.cancelled:
            ex.status = RunStatus.failed if statuses & {RunStatus.failed, RunStatus.blocked} \
                else RunStatus.succeeded
            failed = [r for r in ex.node_runs if r.status == RunStatus.failed]
            if failed:
                ex.error = "; ".join(f"{r.node_id}: {r.error}" for r in failed)
        ex.finished_at = now_iso()
        self.store.save_execution(ex)
        return ex

    def execute(self, workflow_id: str, **kw: Any) -> Execution:
        ex = self.start(workflow_id, **kw)
        return self.run(ex.id) if ex.status == RunStatus.pending else ex

    def decide(self, execution_id: str, node_id: str, approve: bool, note: str = "") -> Execution:
        ex = self.store.get_execution(execution_id)
        nr = ex.run(node_id)
        if nr.status != RunStatus.waiting_approval:
            raise ValueError(f"node {node_id} is not waiting for approval")
        nr.approval = {**(nr.approval or {}), "decision": "approved" if approve else "rejected",
                       "note": note, "decided_at": now_iso()}
        if approve:
            nr.status = RunStatus.pending
            nr.logs.append(f"approved by user{': ' + note if note else ''}")
        else:
            nr.status = RunStatus.failed
            nr.error = f"rejected by user{': ' + note if note else ''}"
        ex.status = RunStatus.running
        self.store.save_execution(ex)
        return self.run(execution_id)

    def cancel(self, execution_id: str) -> None:
        self._cancel.add(execution_id)

    # -- helpers ------------------------------------------------------------
    def _upstream(self, wf: Workflow, nid: str) -> list[str]:
        return [e.source for e in wf.edges if e.target == nid]

    def _needed_by_selection(self, wf: Workflow, selected: list[str]) -> set[str]:
        need, frontier = set(selected), list(selected)
        while frontier:
            n = frontier.pop()
            for u in self._upstream(wf, n):
                if u not in need:
                    need.add(u)
                    frontier.append(u)
        return need

    def _inputs(self, ex: Execution, wf: Workflow, nid: str, port: str) -> list[Any]:
        out = []
        for e in wf.edges:
            if e.target == nid and e.target_port == port:
                val = ex.run(e.source).outputs.get(e.source_port)
                if val is not None:
                    out.append(val)
        return out

    def _inputs_snapshot(self, wf: Workflow) -> dict[str, Any]:
        project = self.store.get_project()
        return {
            "workflow": wf.model_dump(),
            "sources": {s.id: {"name": s.name, "media_type": s.media_type.value,
                               "content_hash": s.content_hash, "state": s.processing.state.value,
                               "extractor": s.processing.extractor,
                               "extractor_version": s.processing.extractor_version}
                        for s in self.store.list_sources()},
            "bindings": [b.model_dump() for b in self.store.list_bindings()],
            "roles": [r.model_dump() for r in project.settings.roles],
            "artifact_heads": {a.id: a.head_revision_id for a in self.store.list_artifacts()},
        }

    def _log(self, ex: Execution, nr: NodeRun, msg: str) -> None:
        nr.logs.append(f"{now_iso()} {msg}")
        self.store.save_execution(ex)

    def _run_node(self, ex: Execution, wf: Workflow, node: WorkflowNode) -> None:
        nr = ex.run(node.id)
        nt = NODE_TYPES[node.type]
        cfg = {**nt.defaults, **node.config}
        nr.status = RunStatus.running
        nr.started_at = nr.started_at or now_iso()
        self.store.save_execution(ex)
        try:
            handler = getattr(self, f"_node_{node.type}")
            handler(ex, wf, node, nr, cfg)
            if nr.status == RunStatus.running:
                nr.status = RunStatus.succeeded
        except ExecutionPaused:
            nr.status = RunStatus.waiting_approval
            self.store.save_execution(ex)
            raise
        except NodeFailure as exc:
            if str(exc) == "cancelled":
                raise
            nr.status = RunStatus.failed
            nr.error = str(exc)
        except Exception as exc:  # unexpected errors are reported verbatim, never hidden
            nr.status = RunStatus.failed
            nr.error = f"{type(exc).__name__}: {exc}"
            nr.logs.append(traceback.format_exc()[-3000:])
        nr.finished_at = now_iso()
        self.store.save_execution(ex)

    # -- node handlers ------------------------------------------------------
    def _node_sources(self, ex, wf, node, nr, cfg):
        ids = set(cfg.get("source_ids") or [])
        mts = set(cfg.get("media_types") or [])
        chosen = []
        for s in self.store.list_sources():
            if ids and s.id not in ids:
                continue
            if mts and s.media_type.value not in mts:
                continue
            if s.processing.state in (ProcessingState.pending,) or (
                    cfg.get("reingest_failed") and s.processing.state == ProcessingState.failed):
                s = ingest.ingest(self.store, s)
                nr.logs.append(f"ingested '{s.name}': {s.processing.state.value}")
            if s.processing.state in (ProcessingState.failed, ProcessingState.unsupported):
                nr.logs.append(f"WARNING source '{s.name}' is {s.processing.state.value}: "
                               f"{s.processing.error}")
            elif s.processing.state == ProcessingState.partial:
                nr.logs.append(f"note: source '{s.name}' partially processed: "
                               f"{'; '.join(s.processing.warnings)}")
            chosen.append(s.id)
        nr.outputs = {"sources": chosen}

    def _node_artifact(self, ex, wf, node, nr, cfg):
        a = self.store.get_artifact(cfg["artifact_id"])
        nr.outputs = {"artifact": {"artifact_id": a.id, "revision_id": a.head_revision_id}}

    def _node_approval(self, ex, wf, node, nr, cfg):
        if (nr.approval or {}).get("decision") == "approved":
            nr.outputs = {"approved": self._inputs(ex, wf, node.id, "input")}
            return
        nr.approval = {"message": cfg.get("message", ""), "requested_at": now_iso(),
                       "inputs": self._inputs(ex, wf, node.id, "input")}
        raise ExecutionPaused()

    def _node_export(self, ex, wf, node, nr, cfg):
        files = []
        for rv in self._inputs(ex, wf, node.id, "revision"):
            a = self.store.get_artifact(rv["artifact_id"])
            adapter = get_adapter(a.adapter)
            out_dir = self.store.root / "exports" / ex.id / a.id
            offered = adapter.info().export_formats
            for fmt in cfg.get("formats", []):
                if fmt not in offered:
                    nr.logs.append(f"{a.name}: format '{fmt}' not offered by adapter "
                                   f"'{a.adapter}' (offers {offered}) - skipped")
                    continue
                try:
                    p = adapter.export(native_path(self.store, a), a.entry, fmt, out_dir)
                    files.append({"artifact_id": a.id, "format": fmt, "path": self.store.rel(p)})
                    nr.logs.append(f"exported {a.name} -> {fmt}")
                except AdapterError as exc:
                    nr.logs.append(f"export {a.name} -> {fmt} not supported: {exc}")
        nr.outputs = {"files": files}

    def _node_validate(self, ex, wf, node, nr, cfg):
        checks = cfg.get("checks", [])
        project = self.store.get_project()
        reports = []
        all_ok = True
        for rv in self._inputs(ex, wf, node.id, "revision"):
            a = self.store.get_artifact(rv["artifact_id"])
            adapter = get_adapter(a.adapter)
            native = native_path(self.store, a)
            ctx = {"test_command": a.metadata.get("test_command"),
                   "allowed_commands": project.settings.allowed_commands,
                   "env": {"DAEDELUS_PROJECT_ROOT": str(self.store.root)}}
            rep = adapter.validate(native, a.entry, [c for c in checks if c in
                                                     ("file_reopens", "tests", "json_valid")], ctx)
            rev = self.store.get_revision(rv["revision_id"]) if rv.get("revision_id") else None
            if "component_preservation" in checks:
                if rev and rev.validation:
                    pres = next((c for c in rev.validation.checks
                                 if c.name == "component_preservation"), None)
                    if pres:
                        rep.add("component_preservation", pres.passed, pres.detail, **pres.data)
                    else:
                        rep.add("component_preservation", True, "no unit executed in this revision")
                else:
                    rep.add("component_preservation", True,
                            "no new revision in this execution (all units up to date)")
            if "evaluation" in checks:
                self._evaluation(a, rev, rep, cfg)
            reports.append({"artifact_id": a.id, "artifact": a.name,
                            "revision_id": rv.get("revision_id"), "report": rep.model_dump()})
            all_ok = all_ok and rep.passed
            nr.logs.append(f"{a.name}: " + ", ".join(
                f"{c.name}={'pass' if c.passed else 'FAIL'}" for c in rep.checks))
        nr.outputs = {"report": {"passed": all_ok, "artifacts": reports}}
        if not all_ok and cfg.get("fail_on_error", True):
            failed = [f"{r['artifact']}:{c['name']}" for r in reports
                      for c in r["report"]["checks"] if not c["passed"]]
            raise NodeFailure(f"validation failed: {failed}")

    def _evaluation(self, a: Artifact, rev, rep: ValidationReport, cfg) -> None:
        """Compare the artifact preview against evaluation-role sources (measured palette)."""
        ctx = binding_mod.resolve(self.store, TargetSelector(scope="artifact", artifact_id=a.id))
        evals = [e for e in ctx.entries if e.role_use == "evaluate"]
        if not evals:
            rep.add("evaluation", True, "no evaluation-role sources bound to this artifact")
            return
        preview = None
        if rev:
            for key in ("render", "composite"):
                if key in rev.previews:
                    preview = self.store.abs(rev.previews[key])
        if preview is None or not Path(preview).exists():
            rep.add("evaluation", False, "no raster preview available to evaluate")
            return
        pf = features.image_features(preview)
        limit = cfg.get("evaluation_max_distance")
        for e in evals:
            ref = e.summary.get("dominant")
            if not ref:
                rep.add("evaluation", False, f"'{e.source_name}' has no measurable colour")
                continue
            d = min(features.color_distance(p["hex"], ref) for p in pf["palette"])
            ok = True if limit is None else d <= float(limit)
            rep.add("evaluation", ok, f"closest palette distance to '{e.source_name}' dominant "
                    f"{ref}: {d:.3f}" + ("" if limit is None else f" (limit {limit})"),
                    distance=round(d, 4), reference=ref)

    # -- the agent node -------------------------------------------------------
    def _units(self, art: Artifact, target_cid: str, fan_out: bool,
               allowed_sources: set[str] | None) -> list[str]:
        units = [target_cid]
        if fan_out:
            anchored = set()
            for b in self.store.list_bindings():
                if not b.enabled or b.target.scope != "component" or \
                        b.target.artifact_id != art.id:
                    continue
                if allowed_sources is not None and b.source_id not in allowed_sources:
                    continue
                anchored.add(b.target.component_id)
            desc = art.descendants(target_cid)
            units += sorted([c for c in desc if c in anchored],
                            key=lambda c: (len(art.ancestors(c)), desc.index(c)))
        return units

    def _fingerprint_inputs(self, ctx: ResolvedContext, cfg: dict[str, Any], adapter_name: str,
                            adapter_version: str, provider: str, upstream: list[dict[str, Any]],
                            ancestors: list[str]) -> dict[str, str]:
        parts: dict[str, str] = {}
        project = self.store.get_project()
        for e in ctx.entries:
            if e.relation == "descendant":
                continue  # applied at (and fingerprinted by) its own, deeper unit
            b = e.binding.model_dump(exclude={"created_at", "updated_at"})
            role = project.role(e.binding.role).model_dump()
            parts[f"binding:{e.binding.id}"] = _h({"binding": b, "source": e.source_hash,
                                                   "state": e.processing_state, "role": role,
                                                   "relation": e.relation, "applies": e.applies,
                                                   "aspects": e.cascading_aspects,
                                                   "summary": e.summary})
        parts["constraints"] = _h(ctx.constraints)
        parts["conflicts"] = _h([c.model_dump() for c in ctx.conflicts])
        parts["node_config"] = _h({k: cfg.get(k) for k in ("instructions", "allowed_ops", "model",
                                                           "fan_out", "target_component")})
        parts["planner"] = _h([provider, adapter_name, adapter_version])
        parts["upstream"] = _h([(u["artifact_id"], u["revision_id"]) for u in upstream])
        parts["ancestors"] = _h(ancestors)
        return parts

    @staticmethod
    def _diff_parts(old: dict[str, str], new: dict[str, str], ctx: ResolvedContext) -> list[str]:
        names = {f"binding:{e.binding.id}": f"binding '{e.source_name}' ({e.binding.role})"
                 for e in ctx.entries}
        reasons = []
        for k in sorted(set(old) | set(new)):
            label = names.get(k, k)
            if k not in old:
                reasons.append(f"{label} added")
            elif k not in new:
                reasons.append(f"{k.replace('binding:', 'binding ')} removed")
            elif old[k] != new[k]:
                reasons.append(f"{label} changed")
        return reasons

    def plan_units(self, wf: Workflow, node: WorkflowNode, cfg: dict[str, Any],
                   art: Artifact, insp, *, allowed_sources: set[str] | None,
                   upstream: list[dict[str, Any]], mode: str) -> list[dict[str, Any]]:
        """Resolve + fingerprint every unit (no planning yet). Used by execution and impact."""
        adapter = get_adapter(art.adapter)
        provider = cfg.get("provider") or self.store.get_project().settings.default_provider
        target = cfg.get("target_component") or art.root_id()
        if not target or art.component(target) is None:
            raise NodeFailure(f"target component '{target}' not found in '{art.name}'")
        units = self._units(art, target, bool(cfg.get("fan_out", True)), allowed_sources)
        sources = {s.id: s for s in self.store.list_sources()}
        all_bindings = self.store.list_bindings()
        out = []
        fps: dict[str, str] = {}
        for u in units:
            sel = TargetSelector(scope="component", artifact_id=art.id, component_id=u)
            ctx = binding_mod.resolve(self.store, sel, source_ids=allowed_sources,
                                      bindings=all_bindings, sources=sources, artifact=art)
            ancestors = [fps[a] for a in art.ancestors(u) if a in fps]
            parts = self._fingerprint_inputs(ctx, cfg, adapter.name, adapter.version, provider,
                                             upstream, ancestors)
            fp = _h(parts)
            fps[u] = fp
            key = sel.key()
            prev = self.store.get_unit_state(wf.id, node.id, key)
            subtree = [u] + art.descendants(u)
            cur_states = {c: insp.states.get(c) for c in subtree}
            reasons: list[str] = []
            if mode == "full":
                reasons.append("full execution requested")
            elif prev is None:
                reasons.append("never executed")
            else:
                if prev["fingerprint"] != fp:
                    reasons += self._diff_parts(prev["detail"].get("parts", {}), parts, ctx) or \
                        ["inputs changed"]
                if prev["detail"].get("states") and prev["detail"]["states"] != cur_states:
                    reasons.append("artifact changed outside this node since its last run")
            out.append({"unit": key, "component_id": u, "ctx": ctx, "fingerprint": fp,
                        "parts": parts, "prev": prev, "stale": bool(reasons), "reasons": reasons,
                        "subtree": subtree})
        return out

    def _node_agent(self, ex, wf, node, nr, cfg):
        if cfg.get("execution") == "cloud":
            raise NodeFailure("cloud execution requested but no cloud workers are configured")
        art_in = self._inputs(ex, wf, node.id, "artifact")
        if not art_in:
            raise NodeFailure("no artifact input")
        art = self.store.get_artifact(art_in[0]["artifact_id"])
        adapter = get_adapter(art.adapter)
        ok, why = adapter.check_environment()
        if not ok:
            raise NodeFailure(f"adapter '{art.adapter}' unavailable: {why}")
        src_in = self._inputs(ex, wf, node.id, "sources")
        allowed = set(src_in[0]) if src_in else None
        upstream = self._upstream_revisions(ex, wf, node.id)
        provider_name = cfg.get("provider") or self.store.get_project().settings.default_provider
        provider = get_provider(provider_name)
        native = native_path(self.store, art)

        approval = nr.approval or {}
        if approval.get("decision") == "approved" and approval.get("pending"):
            pending = approval["pending"]
            planned = [(u, PlannedOperation(**o)) for u, o in pending["ops"]]
            unit_infos = pending["units"]
            self._log(ex, nr, "applying approved plan")
        else:
            insp = adapter.inspect(native, art.entry)
            if [c.id for c in insp.components] != [c.id for c in art.components]:
                art.components = insp.components
                self.store.save_artifact(art)
            infos = self.plan_units(wf, node, cfg, art, insp, allowed_sources=allowed,
                                    upstream=upstream, mode=ex.mode)
            nr.units = []
            for info in infos:
                self.store.save_context(info["ctx"], ex.id)
                nr.units.append(UnitRun(
                    unit=info["unit"], fingerprint=info["fingerprint"],
                    previous_fingerprint=(info["prev"] or {}).get("fingerprint"),
                    context_id=info["ctx"].id,
                    status=RunStatus.pending if info["stale"] else RunStatus.skipped,
                    reason="; ".join(info["reasons"]) if info["stale"] else "up to date"))
            self.store.save_execution(ex)
            stale = [i for i in infos if i["stale"]]
            if not stale:
                self._refresh_states(wf, node, infos, insp, ex.id)
                nr.outputs = {"revision": {"artifact_id": art.id,
                                           "revision_id": art.head_revision_id, "changed": False}}
                self._log(ex, nr, f"all {len(infos)} unit(s) up to date - nothing executed")
                return
            blocked = [i for i in stale if i["ctx"].blocking_conflicts]
            if blocked:
                for i in blocked:
                    ur = next(u for u in nr.units if u.unit == i["unit"])
                    ur.status = RunStatus.blocked
                    ur.error = "; ".join(c.detail for c in i["ctx"].blocking_conflicts)
                raise NodeFailure("unresolved hard-constraint conflicts: " + " | ".join(
                    f"{i['unit']}: " + "; ".join(c.detail for c in i["ctx"].blocking_conflicts)
                    for i in blocked))
            planned = []
            unit_infos = []
            source_files = self._source_files()
            for info in stale:
                ur = next(u for u in nr.units if u.unit == info["unit"])
                comp = art.component(info["component_id"])
                req = PlanRequest(
                    unit=info["unit"], artifact=art, component=comp, context=info["ctx"],
                    adapter=adapter.info(), allowed_ops=cfg.get("allowed_ops") or None,
                    instructions=cfg.get("instructions", ""),
                    properties={c: insp.properties.get(c, {}) for c in info["subtree"]},
                    measurements={c: insp.measurements.get(c, {}) for c in info["subtree"]},
                    baseline={c: art.metadata.get("baseline", {}).get(c, {})
                              for c in info["subtree"]},
                    upstream=upstream, source_files=source_files,
                    model=cfg.get("model") or None)
                (self.store.execution_dir(ex.id) / f"plan_request_{node.id}_"
                 f"{_h(info['unit'])[:10]}.json").write_text(req.model_dump_json(indent=1))
                try:
                    plan = provider.plan(req)
                except ProviderError as exc:
                    ur.status = RunStatus.failed
                    ur.error = str(exc)
                    raise NodeFailure(f"planner '{provider_name}' failed for {info['unit']}: {exc}")
                errors = adapter.validate_operations(plan.operations, art.components)
                allowed_scope = set(info["subtree"])
                for i, op in enumerate(plan.operations):
                    spec = adapter.op_spec(op.op)
                    if spec and spec.target_kinds == ["new"]:
                        parent = op.params.get("parent")
                        if parent and parent not in allowed_scope:
                            errors.append(f"op {i} creates a component outside the unit scope")
                    elif op.component_id and op.component_id not in allowed_scope:
                        errors.append(f"op {i} ({op.op}) targets '{op.component_id}', outside "
                                      f"unit {info['unit']}")
                ur.plan = plan.model_dump()
                if errors:
                    ur.status = RunStatus.failed
                    ur.error = "; ".join(errors)
                    raise NodeFailure(f"invalid plan for {info['unit']}: {'; '.join(errors)}")
                for op in plan.operations:
                    planned.append((info["unit"], op))
                unit_infos.append({"unit": info["unit"], "component_id": info["component_id"],
                                   "fingerprint": info["fingerprint"], "parts": info["parts"],
                                   "subtree": info["subtree"], "context_id": info["ctx"].id,
                                   "interpretations": plan.interpretations,
                                   "provider": plan.provider, "model": plan.model})
                self._log(ex, nr, f"planned {len(plan.operations)} op(s) for {info['unit']} "
                                  f"via {plan.provider}")
            if cfg.get("require_approval"):
                nr.approval = {"requested_at": now_iso(), "message": "Review planned operations",
                               "pending": {"ops": [(u, o.model_dump()) for u, o in planned],
                                           "units": unit_infos}}
                raise ExecutionPaused()

        self._apply_units(ex, wf, node, nr, cfg, art, adapter, planned, unit_infos)

    def _source_files(self) -> dict[str, str]:
        out = {}
        for s in self.store.list_sources():
            if s.locator.path and s.media_type.value in ("image",):
                out[s.id] = str(self.store.abs(s.locator.path))
        return out

    def _upstream_revisions(self, ex, wf, nid) -> list[dict[str, Any]]:
        out = []
        for val in self._inputs(ex, wf, nid, "after"):
            vals = val if isinstance(val, list) else [val]
            for v in vals:
                if isinstance(v, dict) and v.get("revision_id"):
                    rev = self.store.get_revision(v["revision_id"])
                    a = self.store.get_artifact(rev.artifact_id)
                    root = a.root_id()
                    out.append({"artifact_id": a.id, "artifact_name": a.name,
                                "artifact_type": a.artifact_type, "revision_id": rev.id,
                                "revision_number": rev.number,
                                "native": f"{a.native_dir}/{a.entry}".rstrip("/."),
                                "previews": rev.previews,
                                "measurements": rev.measurements.get(root or "", {})})
        return out

    def _refresh_states(self, wf, node, infos, insp, execution_id) -> None:
        for info in infos:
            prev = info["prev"]
            if prev is None or info["stale"]:
                continue
            states = {c: insp.states.get(c) for c in info["subtree"]}
            self.store.set_unit_state(wf.id, node.id, info["unit"], prev["fingerprint"],
                                      prev["revision_id"], prev["execution_id"],
                                      {**prev["detail"], "states": states})

    def _check_constraints(self, art, adapter, unit_infos, before, after) -> list[tuple[bool, str]]:
        out = []
        for ui in unit_infos:
            ctx = self.store.get_context(ui["context_id"])
            cid = ui["component_id"]
            for c in ctx.constraints:
                if not c.get("active", True):
                    continue
                con = c["constraint"]
                prop = con["property"]
                b = before.measurements.get(cid, {}).get(prop)
                a = after.measurements.get(cid, {}).get(prop)
                tol = float(con.get("tolerance") or 1e-3)
                label = f"{ui['unit']} {prop} {con['op']} {con.get('value', '')} ({c['source']})"
                if a is None:
                    out.append((False, f"{label}: property not measurable by adapter "
                                       f"'{adapter.name}'"))
                    continue
                if con["op"] in ("preserve", "forbid_change"):
                    ok = b is not None and abs(float(a) - float(b)) <= tol
                    out.append((ok, f"{label}: before {b}, after {a}"))
                elif con["op"] == "eq":
                    out.append((abs(float(a) - float(con["value"])) <= tol, f"{label}: {a}"))
                elif con["op"] == "lte":
                    out.append((float(a) <= float(con["value"]) + tol, f"{label}: {a}"))
                elif con["op"] == "gte":
                    out.append((float(a) >= float(con["value"]) - tol, f"{label}: {a}"))
                elif con["op"] == "range":
                    lo, hi = con["value"]
                    out.append((float(lo) - tol <= float(a) <= float(hi) + tol, f"{label}: {a}"))
        return out

    def _apply_units(self, ex, wf, node, nr, cfg, art, adapter, planned, unit_infos) -> None:
        native = native_path(self.store, art)
        before = adapter.inspect(native, art.entry)
        ops = [op for _, op in planned]
        retry = cfg.get("retry") or {}
        attempts = int(retry.get("max_attempts", 1))
        backoff = float(retry.get("backoff_seconds", 0))
        ckpt = self.store.artifact_dir(art.id) / "checkpoints" / f"{ex.id}_{node.id}"
        snapshot(native, ckpt)
        self._log(ex, nr, f"checkpoint saved ({self.store.rel(ckpt)})")
        sources = {s.id: s for s in self.store.list_sources()}
        apply_ctx = {"source_paths": self._source_files(),
                     "message": f"{wf.name} / {node.label or node.id}: "
                                f"{', '.join(u['unit'].split('#')[-1] for u in unit_infos)}"}
        result = None
        last_error = ""
        for attempt in range(1, attempts + 1):
            nr.attempts = attempt
            try:
                result = adapter.apply(native, art.entry, ops, apply_ctx)
                if result.ok:
                    break
                last_error = result.error or "operation failed"
            except AdapterError as exc:
                last_error = str(exc)
                result = None
            restore_tree(ckpt, native)
            adapter.after_restore(native, art.entry, "Rollback to checkpoint")
            self._log(ex, nr, f"attempt {attempt}/{attempts} failed: {last_error} - rolled back "
                              f"to checkpoint")
            if attempt < attempts and backoff:
                time.sleep(backoff)
        if result is None or not result.ok:
            for ui in unit_infos:
                ur = next(u for u in nr.units if u.unit == ui["unit"])
                ur.status = RunStatus.failed
                ur.error = last_error
            raise NodeFailure(f"operations failed after {attempts} attempt(s): {last_error}")

        after = adapter.inspect(native, art.entry)
        rep = ValidationReport()
        scope = set()
        for ui in unit_infos:
            scope.update(ui["subtree"])
        new_components = [c for c in after.states if c not in before.states]
        scope.update(new_components)
        changed = sorted(c for c in after.states if before.states.get(c) != after.states.get(c))
        outside = [c for c in changed if c not in scope and c not in after.aggregates]
        rep.add("component_preservation", not outside,
                f"changed: {changed}; outside executed scope: {outside or 'none'}",
                changed=changed, outside=outside, scope=sorted(scope),
                preserved=sorted(c for c in before.states if c not in changed))
        for ok, msg in self._check_constraints(art, adapter, unit_infos, before, after):
            rep.add("constraint", ok, msg)
        vchecks = [c for c in cfg.get("validation", ["file_reopens"]) if c != "tests"]
        if vchecks:
            v = adapter.validate(native, art.entry, vchecks, {})
            for c in v.checks:
                rep.add(c.name, c.passed, c.detail, **c.data)
        op_results = [OperationResult(**{k: r[k] for k in ("index", "op", "component_id",
                                                             "status", "detail")})
                      for r in result.results]
        if not rep.passed:
            restore_tree(ckpt, native)
            adapter.after_restore(native, art.entry, "Rollback to checkpoint")
            failed = [f"{c.name}: {c.detail}" for c in rep.checks if not c.passed]
            for ui in unit_infos:
                ur = next(u for u in nr.units if u.unit == ui["unit"])
                ur.status = RunStatus.failed
                ur.error = "validation failed"
            nr.outputs = {"validation": rep.model_dump()}
            raise NodeFailure("post-execution validation failed, rolled back to checkpoint: "
                              + " | ".join(failed))

        attribution: list[AttributionRecord] = []
        for ui in unit_infos:
            ctx = self.store.get_context(ui["context_id"])
            for e in ctx.entries:
                if e.relation == "descendant":
                    continue
                idxs = [i for i, (u, op) in enumerate(planned)
                        if u == ui["unit"] and e.binding.id in op.derived_from]
                attribution.append(AttributionRecord(
                    binding_id=e.binding.id, source_id=e.binding.source_id,
                    source_name=sources.get(e.binding.source_id).name
                    if e.binding.source_id in sources else e.source_name,
                    role=e.binding.role, aspects=e.cascading_aspects, anchor=e.anchor,
                    unit=ui["unit"], presented=True, applied=bool(idxs), operations=idxs,
                    interpretation=ui["interpretations"].get(e.binding.id, "")))
        rev = record_revision(
            self.store, art, insp=after,
            message=apply_ctx["message"], operations=ops, execution_id=ex.id, node_id=node.id,
            units=[u["unit"] for u in unit_infos], operation_results=op_results,
            attribution=attribution, changed_components=changed, validation=rep)
        shutil.rmtree(ckpt, ignore_errors=True)
        art = self.store.get_artifact(art.id)
        # record fingerprints for executed units, refresh observed state for the rest
        for ui in unit_infos:
            states = {c: after.states.get(c) for c in ui["subtree"]}
            self.store.set_unit_state(wf.id, node.id, ui["unit"], ui["fingerprint"], rev.id, ex.id,
                                      {"parts": ui["parts"], "states": states,
                                       "provider": ui["provider"], "model": ui["model"]})
            ur = next(u for u in nr.units if u.unit == ui["unit"])
            ur.status = RunStatus.succeeded
        for ur in nr.units:
            if ur.status == RunStatus.skipped:
                prev = self.store.get_unit_state(wf.id, node.id, ur.unit)
                if prev:
                    cid = ur.unit.split("#", 1)[1]
                    sub = [cid] + art.descendants(cid)
                    self.store.set_unit_state(
                        wf.id, node.id, ur.unit, prev["fingerprint"], prev["revision_id"],
                        prev["execution_id"],
                        {**prev["detail"], "states": {c: after.states.get(c) for c in sub}})
        nr.outputs = {"revision": {"artifact_id": art.id, "revision_id": rev.id, "changed": True,
                                   "revision_number": rev.number}}
        self._log(ex, nr, f"revision {rev.number} of '{art.name}' recorded; changed {changed}")

    # -- impact analysis ------------------------------------------------------
    def impact(self, workflow_id: str) -> dict[str, Any]:
        """Which units would run if the workflow were executed now, and why."""
        wf = self.store.get_workflow(workflow_id)
        out = {"workflow_id": wf.id, "version": wf.version, "nodes": [], "affected_artifacts": []}
        affected = set()
        for node in wf.nodes:
            if node.type != "agent":
                continue
            cfg = {**NODE_TYPES["agent"].defaults, **node.config}
            art = _agent_artifact(wf, node, self.store)
            if art is None:
                continue
            adapter = get_adapter(art.adapter)
            ok, why = adapter.check_environment()
            if not ok:
                out["nodes"].append({"node_id": node.id, "artifact_id": art.id,
                                     "error": f"adapter unavailable: {why}"})
                continue
            insp = adapter.inspect(native_path(self.store, art), art.entry)
            allowed = self._static_allowed(wf, node)
            upstream = self._current_upstream(wf, node)
            infos = self.plan_units(wf, node, cfg, art, insp, allowed_sources=allowed,
                                    upstream=upstream, mode="incremental")
            stale = [i for i in infos if i["stale"]]
            if stale:
                affected.add(art.id)
            out["nodes"].append({
                "node_id": node.id, "label": node.label, "artifact_id": art.id,
                "artifact": art.name,
                "units": [{"unit": i["unit"], "stale": i["stale"], "reasons": i["reasons"],
                           "conflicts": [c.model_dump() for c in i["ctx"].blocking_conflicts]}
                          for i in infos]})
        out["affected_artifacts"] = sorted(affected)
        return out


    def _static_allowed(self, wf: Workflow, node: WorkflowNode) -> set[str] | None:
        src_edge = next((e for e in wf.edges if e.target == node.id
                         and e.target_port == "sources"), None)
        if not src_edge:
            return None
        sn = next(n for n in wf.nodes if n.id == src_edge.source)
        scfg = {**NODE_TYPES["sources"].defaults, **sn.config}
        return {s.id for s in self.store.list_sources()
                if (not scfg["source_ids"] or s.id in scfg["source_ids"]) and
                (not scfg["media_types"] or s.media_type.value in scfg["media_types"])}

    def _current_upstream(self, wf: Workflow, node: WorkflowNode) -> list[dict[str, Any]]:
        """Upstream revisions as they stand now (heads of upstream agents' artifacts)."""
        fake = Execution(workflow_id=wf.id, workflow_version=wf.version)
        fake.node_runs = []
        for e in wf.edges:
            if e.target == node.id and e.target_port == "after":
                up = next(n for n in wf.nodes if n.id == e.source)
                ua = _agent_artifact(wf, up, self.store) if up.type == "agent" else None
                if ua and ua.head_revision_id:
                    fake.node_runs.append(NodeRun(node_id=up.id, node_type=up.type, outputs={
                        e.source_port: {"artifact_id": ua.id, "revision_id": ua.head_revision_id}}))
        if not fake.node_runs:
            return []
        return self._upstream_revisions(fake, wf, node.id)

    def preview_node(self, workflow_id: str, node_id: str, *, all_units: bool = False
                     ) -> dict[str, Any]:
        """Dry run: resolve and plan an agent node's units without applying anything."""
        wf = self.store.get_workflow(workflow_id)
        node = next((n for n in wf.nodes if n.id == node_id), None)
        if node is None or node.type != "agent":
            raise ValueError(f"{node_id} is not an agent node")
        cfg = {**NODE_TYPES["agent"].defaults, **node.config}
        art = _agent_artifact(wf, node, self.store)
        if art is None:
            raise ValueError("agent node has no artifact input")
        adapter = get_adapter(art.adapter)
        ok, why = adapter.check_environment()
        if not ok:
            raise RuntimeError(f"adapter unavailable: {why}")
        insp = adapter.inspect(native_path(self.store, art), art.entry)
        upstream = self._current_upstream(wf, node)
        infos = self.plan_units(wf, node, cfg, art, insp, allowed_sources=self._static_allowed(
            wf, node), upstream=upstream, mode="full" if all_units else "incremental")
        provider_name = cfg.get("provider") or self.store.get_project().settings.default_provider
        provider = get_provider(provider_name)
        out = []
        for info in infos:
            item: dict[str, Any] = {"unit": info["unit"], "stale": info["stale"],
                                    "reasons": info["reasons"],
                                    "context": info["ctx"].model_dump()}
            if info["stale"] or all_units:
                req = PlanRequest(
                    unit=info["unit"], artifact=art, component=art.component(info["component_id"]),
                    context=info["ctx"], adapter=adapter.info(),
                    allowed_ops=cfg.get("allowed_ops") or None,
                    instructions=cfg.get("instructions", ""),
                    properties={c: insp.properties.get(c, {}) for c in info["subtree"]},
                    measurements={c: insp.measurements.get(c, {}) for c in info["subtree"]},
                    baseline={c: art.metadata.get("baseline", {}).get(c, {})
                              for c in info["subtree"]},
                    upstream=upstream, source_files=self._source_files(),
                    model=cfg.get("model") or None)
                try:
                    plan = provider.plan(req)
                    item["plan"] = plan.model_dump()
                    item["errors"] = adapter.validate_operations(plan.operations, art.components)
                except ProviderError as exc:
                    item["error"] = str(exc)
            out.append(item)
        return {"node_id": node_id, "artifact_id": art.id, "provider": provider_name,
                "units": out}

    # -- reproduction -------------------------------------------------------
    def replay(self, execution_id: str) -> dict[str, Any]:
        """Re-plan from saved plan requests and re-apply saved operations to the saved parent
        revision; compare operations and resulting component states."""
        ex = self.store.get_execution(execution_id)
        results = []
        for nr in ex.node_runs:
            rv = (nr.outputs or {}).get("revision")
            if nr.node_type != "agent" or not rv or not rv.get("changed"):
                continue
            rev = self.store.get_revision(rv["revision_id"])
            art = self.store.get_artifact(rev.artifact_id)
            adapter = get_adapter(art.adapter)
            parent = self.store.get_revision(rev.parent_revision_id) if rev.parent_revision_id \
                else None
            entry = {"node_id": nr.node_id, "artifact": art.name, "revision": rev.number}
            if parent is None:
                entry.update(reproducible=False, detail="no parent revision snapshot")
                results.append(entry)
                continue
            # 1) re-plan with the saved requests (deterministic providers only)
            replanned: list[PlannedOperation] = []
            deterministic = True
            for ur in nr.units:
                if ur.status != RunStatus.succeeded or not ur.plan:
                    continue
                pfile = self.store.execution_dir(ex.id) / \
                    f"plan_request_{nr.node_id}_{_h(ur.unit)[:10]}.json"
                prov = get_provider(ur.plan["provider"])
                if not ur.plan.get("deterministic") or not pfile.exists():
                    deterministic = False
                    continue
                req = PlanRequest.model_validate_json(pfile.read_text())
                replanned += prov.plan(req).operations
            ops_match = None
            if deterministic:
                ops_match = [o.model_dump() for o in replanned] == \
                            [o.model_dump() for o in rev.operations]
            # 2) re-apply recorded operations onto the parent snapshot in a scratch copy
            scratch = self.store.root / "replay" / f"{ex.id}_{rev.id}"
            if scratch.exists():
                shutil.rmtree(scratch)
            shutil.copytree(native_path(self.store, art), scratch)  # keeps VCS metadata
            restore_tree(self.store.abs(parent.snapshot_dir), scratch)
            adapter.after_restore(scratch, art.entry, "Replay base")
            res = adapter.apply(scratch, art.entry, rev.operations,
                                {"source_paths": self._source_files(), "message": "replay"})
            if not res.ok:
                entry.update(reproducible=False, detail=f"re-apply failed: {res.error}")
            else:
                insp = adapter.inspect(scratch, art.entry)
                mism = sorted(c for c in set(insp.states) | set(rev.component_states)
                              if insp.states.get(c) != rev.component_states.get(c))
                entry.update(reproducible=not mism and ops_match is not False,
                             state_mismatches=mism, operations_match=ops_match,
                             detail="component states identical" if not mism else
                             f"state mismatch in {mism}")
            shutil.rmtree(scratch, ignore_errors=True)
            results.append(entry)
        return {"execution_id": ex.id, "results": results,
                "reproducible": bool(results) and all(r.get("reproducible") for r in results)}
