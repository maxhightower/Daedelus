"""HTTP API for the studio (FastAPI)."""

from __future__ import annotations

import hashlib
import json
import os
import threading
import traceback
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import __version__, bindings as binding_mod, ingest
from .adapters import registry as adapter_registry
from .artifacts import create_artifact, restore_revision
from .engine import Engine, validate_workflow
from .models import (
    Constraint,
    MediaType,
    ProjectSettings,
    RunStatus,
    SourceBinding,
    SourceSegment,
    TargetSelector,
    Workflow,
    now_iso,
)
from .nodes import NODE_TYPES
from .adapters import get_adapter
from .providers import providers as provider_registry
from .semantic import service as semsvc
from .store import ProjectStore, Workspace


# ---------------------------------------------------------------------------
# request bodies
# ---------------------------------------------------------------------------

class ProjectCreate(BaseModel):
    name: str
    description: str = ""


class ProjectPatch(BaseModel):
    name: str | None = None
    description: str | None = None
    settings: ProjectSettings | None = None


class UrlSource(BaseModel):
    url: str
    name: str | None = None
    media_type: MediaType | None = None


class TextSource(BaseModel):
    name: str
    text: str


class PathSource(BaseModel):
    path: str
    name: str | None = None


class TextUpdate(BaseModel):
    text: str


class BindingCreate(BaseModel):
    source_id: str
    role: str
    aspects: list[str] = Field(default_factory=list)
    target: TargetSelector = Field(default_factory=TargetSelector)
    instructions: str = ""
    priority: int = 0
    strength: float = 0.6
    constraint: str = "soft"
    constraints: list[Constraint] = Field(default_factory=list)
    allow_override: bool = False
    segment: SourceSegment | None = None
    exclusions: list[str] = Field(default_factory=list)
    enabled: bool = True


class ArtifactCreate(BaseModel):
    name: str
    adapter: str
    template: str
    params: dict[str, Any] = Field(default_factory=dict)
    artifact_type: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class ResolveBody(BaseModel):
    target: TargetSelector
    source_ids: list[str] | None = None


class ExecuteBody(BaseModel):
    mode: str = "incremental"
    nodes: list[str] | None = None
    version: int | None = None
    wait: bool = False


class DecisionBody(BaseModel):
    node_id: str
    approve: bool
    note: str = ""


class RestoreBody(BaseModel):
    revision_id: str


class AgentMessage(BaseModel):
    text: str
    target: TargetSelector = Field(default_factory=TargetSelector)
    role: str = "instruction"
    aspects: list[str] = Field(default_factory=list)
    strength: float = 0.8
    workflow_id: str | None = None


class AnalyzeBody(BaseModel):
    provider: str = "heuristic"
    model: str | None = None
    segment: SourceSegment | None = None
    force: bool = False


class GenerateBody(BaseModel):
    prompt: str
    provider: str = "gemini"
    model: str | None = None
    name: str = "Generated image"
    reference_ids: list[str] = Field(default_factory=list)


class ContextBody(BaseModel):
    artifact_id: str | None = None
    component_id: str | None = None
    source_ids: list[str] | None = None


class BoardCreate(BaseModel):
    name: str
    layout: str = "empty"  # "empty" | "default" (all project resources in frames)


class BoardSave(BaseModel):
    board: dict[str, Any]
    expected_revision: int | None = None


class PlaceBody(BaseModel):
    resource_ref: dict[str, Any]
    x: float | None = None
    y: float | None = None
    width: float | None = None
    height: float | None = None
    presentation_state: dict[str, Any] = Field(default_factory=dict)


class EditBody(BaseModel):
    operations: list[dict[str, Any]]
    message: str = ""
    base_revision_id: str | None = None


class DependencyCreate(BaseModel):
    source: dict[str, str]
    target: dict[str, str]
    target_kind: str | None = None
    options: dict[str, Any] = Field(default_factory=dict)
    note: str = ""


class SyncBody(BaseModel):
    dependency_ids: list[str] | None = None
    target_artifact_ids: list[str] | None = None
    force: bool = False


class PreviewBody(BaseModel):
    node_id: str
    all_units: bool = False


# ---------------------------------------------------------------------------
# app
# ---------------------------------------------------------------------------

def create_app(workspace_root: str | Path | None = None,
               studio_dist: str | Path | None = None) -> FastAPI:
    root = Path(workspace_root or os.environ.get("DAEDELUS_WORKSPACE",
                                                 Path.home() / ".daedelus" / "workspace"))
    ws = Workspace(root)
    engines: dict[str, Engine] = {}
    threads: dict[str, threading.Thread] = {}
    lock = threading.Lock()

    app = FastAPI(title="Daedelus", version=__version__)
    app.state.workspace = ws
    app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"],
                       allow_headers=["*"])

    def store_for(pid: str) -> ProjectStore:
        try:
            return ws.open(pid)
        except (LookupError, ValueError):
            raise HTTPException(404, f"project not found: {pid}")

    def engine_for(pid: str) -> Engine:
        with lock:
            if pid not in engines:
                engines[pid] = Engine(store_for(pid))
            return engines[pid]

    @app.exception_handler(LookupError)
    async def _not_found(_: Request, exc: LookupError):
        return JSONResponse(status_code=404, content={"detail": str(exc)})

    @app.exception_handler(ValueError)
    async def _bad(_: Request, exc: ValueError):
        return JSONResponse(status_code=400, content={"detail": str(exc)})

    # -- system ---------------------------------------------------------------
    @app.get("/api/health")
    def health():
        adapters = []
        for a in adapter_registry().values():
            adapters.append(a.info().model_dump())
        provs = []
        for p in provider_registry().values():
            ok, why = p.available()
            provs.append(p.describe())
        return {"ok": True, "version": __version__, "workspace": str(ws.root),
                "adapters": adapters, "providers": provs}

    @app.get("/api/node-types")
    def node_types():
        return [n.model_dump() for n in NODE_TYPES.values()]

    # -- projects -------------------------------------------------------------
    @app.get("/api/projects")
    def list_projects():
        return [p.model_dump() for p in ws.list_projects()]

    @app.post("/api/projects")
    def create_project(body: ProjectCreate):
        p, _ = ws.create_project(body.name, body.description)
        return p.model_dump()

    @app.post("/api/projects/demo")
    def create_demo(include_video: bool = True):
        from .demo import create_demo_project

        p, store, info = create_demo_project(ws, ws.root / "demo_media" / now_iso()[:19]
                                             .replace(":", "-"), include_video=include_video)
        return {"project": p.model_dump(), **info}

    @app.get("/api/projects/{pid}")
    def get_project(pid: str):
        return store_for(pid).get_project().model_dump()

    @app.patch("/api/projects/{pid}")
    def patch_project(pid: str, body: ProjectPatch):
        st = store_for(pid)
        p = st.get_project()
        if body.name is not None:
            p.name = body.name
        if body.description is not None:
            p.description = body.description
        if body.settings is not None:
            p.settings = body.settings
        return st.save_project(p).model_dump()

    @app.get("/api/projects/{pid}/files/{path:path}")
    def project_file(pid: str, path: str):
        st = store_for(pid)
        p = st.abs(path)
        if not p.is_file():
            raise HTTPException(404, "file not found")
        return FileResponse(p)

    # -- sources --------------------------------------------------------------
    @app.get("/api/projects/{pid}/sources")
    def list_sources(pid: str):
        st = store_for(pid)
        bs = st.list_bindings()
        out = []
        for s in st.list_sources():
            d = s.model_dump()
            d["binding_count"] = sum(1 for b in bs if b.source_id == s.id)
            d.get("extracted", {}).pop("text", None)
            d.get("extracted", {}).pop("pages", None)
            out.append(d)
        return out

    @app.post("/api/projects/{pid}/sources/upload")
    async def upload_sources(pid: str, files: list[UploadFile] = File(...),
                             media_type: str | None = Form(None)):
        st = store_for(pid)
        out = []
        for f in files:
            data = await f.read()
            mt = MediaType(media_type) if media_type else None
            src = await _in_thread(ingest.register_bytes, st, data, f.filename or "upload",
                                   media_type=mt)
            out.append(src.model_dump())
        return out

    @app.post("/api/projects/{pid}/sources/url")
    async def add_url(pid: str, body: UrlSource):
        st = store_for(pid)
        return (await _in_thread(ingest.register_url, st, body.url, name=body.name,
                                 media_type=body.media_type)).model_dump()

    @app.post("/api/projects/{pid}/sources/text")
    def add_text(pid: str, body: TextSource):
        return ingest.register_text(store_for(pid), body.name, body.text).model_dump()

    @app.post("/api/projects/{pid}/sources/path")
    async def add_path(pid: str, body: PathSource):
        st = store_for(pid)
        p = Path(body.path).expanduser()
        if not p.exists():
            raise HTTPException(400, f"path does not exist: {p}")
        return (await _in_thread(ingest.register_file, st, p, name=body.name)).model_dump()

    @app.get("/api/projects/{pid}/sources/{sid}")
    def get_source(pid: str, sid: str):
        st = store_for(pid)
        d = st.get_source(sid).model_dump()
        d["bindings"] = [b.model_dump() for b in st.list_bindings(sid)]
        return d

    @app.put("/api/projects/{pid}/sources/{sid}/text")
    def update_text(pid: str, sid: str, body: TextUpdate):
        """Edit a text source in place (new content hash, re-ingested, provenance noted)."""
        st = store_for(pid)
        src = st.get_source(sid)
        if src.media_type != MediaType.text or not src.locator.path:
            raise HTTPException(400, "only stored text sources can be edited")
        p = st.abs(src.locator.path)
        old_hash = src.content_hash
        data = body.text.encode("utf-8")
        p.write_bytes(data)
        src.content_hash = hashlib.sha256(data).hexdigest()
        src.size_bytes = len(data)
        src.provenance.notes = ((src.provenance.notes or "") +
                                f"\n{now_iso()} edited (previous sha256 {old_hash[:12] if old_hash else '?'})"
                                ).strip()
        st.save_source(src)
        return ingest.ingest(st, src).model_dump()

    @app.post("/api/projects/{pid}/sources/{sid}/reingest")
    async def reingest(pid: str, sid: str):
        st = store_for(pid)
        return (await _in_thread(ingest.ingest, st, st.get_source(sid))).model_dump()

    @app.delete("/api/projects/{pid}/sources/{sid}")
    def delete_source(pid: str, sid: str):
        store_for(pid).delete_source(sid)
        return {"deleted": sid}

    # -- bindings -------------------------------------------------------------
    @app.get("/api/projects/{pid}/bindings")
    def list_bindings(pid: str, source_id: str | None = None):
        return [b.model_dump() for b in store_for(pid).list_bindings(source_id)]

    @app.post("/api/projects/{pid}/bindings")
    def create_binding(pid: str, body: BindingCreate):
        st = store_for(pid)
        _check_target(st, body.target)
        b = SourceBinding(**body.model_dump())
        return st.save_binding(b).model_dump()

    @app.patch("/api/projects/{pid}/bindings/{bid}")
    def patch_binding(pid: str, bid: str, body: dict[str, Any]):
        st = store_for(pid)
        b = st.get_binding(bid)
        data = b.model_dump()
        for k, v in body.items():
            if k in ("id", "created_at", "updated_at"):
                continue
            if k not in data:
                raise HTTPException(400, f"unknown binding field: {k}")
            data[k] = v
        nb = SourceBinding.model_validate(data)
        _check_target(st, nb.target)
        return st.save_binding(nb).model_dump()

    @app.delete("/api/projects/{pid}/bindings/{bid}")
    def delete_binding(pid: str, bid: str):
        store_for(pid).delete_binding(bid)
        return {"deleted": bid}

    @app.get("/api/projects/{pid}/bindings/{bid}/scope")
    def binding_scope(pid: str, bid: str):
        """Artifacts/components that fall inside the binding's scope (potentially affected)."""
        st = store_for(pid)
        b = st.get_binding(bid)
        out = []
        for a in st.list_artifacts():
            if b.target.scope == "project" or b.target.artifact_id == a.id:
                comps = [c.id for c in a.components]
                if b.target.scope == "component":
                    comps = [b.target.component_id] + a.descendants(b.target.component_id or "")
                comps = [c for c in comps if c not in b.exclusions]
                out.append({"artifact_id": a.id, "artifact": a.name, "components": comps})
        return {"binding_id": bid, "target": b.target.key(), "affected": out}

    def _check_target(st: ProjectStore, t: TargetSelector) -> None:
        if t.scope in ("artifact", "component"):
            a = st.get_artifact(t.artifact_id or "")
            if t.scope == "component" and a.component(t.component_id or "") is None:
                raise HTTPException(400, f"component {t.component_id} not in {a.name}")

    # -- roles ----------------------------------------------------------------
    @app.get("/api/projects/{pid}/roles")
    def roles(pid: str):
        return [r.model_dump() for r in store_for(pid).get_project().settings.roles]

    # -- artifacts ------------------------------------------------------------
    @app.get("/api/projects/{pid}/artifacts")
    def list_artifacts(pid: str):
        return [a.model_dump() for a in store_for(pid).list_artifacts()]

    @app.post("/api/projects/{pid}/artifacts")
    async def new_artifact(pid: str, body: ArtifactCreate):
        st = store_for(pid)
        try:
            a, rev = await _in_thread(create_artifact, st, name=body.name, adapter=body.adapter,
                                      template=body.template, params=body.params,
                                      artifact_type=body.artifact_type, metadata=body.metadata)
        except RuntimeError as exc:
            raise HTTPException(400, str(exc))
        return {"artifact": a.model_dump(), "revision": rev.model_dump()}

    @app.get("/api/projects/{pid}/artifacts/{aid}")
    def get_artifact(pid: str, aid: str):
        return store_for(pid).get_artifact(aid).model_dump()

    @app.get("/api/projects/{pid}/artifacts/{aid}/revisions")
    def revisions(pid: str, aid: str):
        return [r.model_dump(exclude={"diff"}) | {"has_diff": bool(r.diff)}
                for r in store_for(pid).list_revisions(aid)]

    @app.get("/api/projects/{pid}/revisions/{rid}")
    def revision(pid: str, rid: str):
        return store_for(pid).get_revision(rid).model_dump()

    @app.get("/api/projects/{pid}/revisions/{rid}/files")
    def revision_files(pid: str, rid: str, path: str | None = None):
        st = store_for(pid)
        rev = st.get_revision(rid)
        snap = st.abs(rev.snapshot_dir)
        if path is None:
            return [p.relative_to(snap).as_posix() for p in sorted(snap.rglob("*"))
                    if p.is_file()]
        p = (snap / path).resolve()
        if snap.resolve() not in p.parents or not p.is_file():
            raise HTTPException(404, "file not in revision")
        try:
            return {"path": path, "text": p.read_text(encoding="utf-8")}
        except UnicodeDecodeError:
            return {"path": path, "binary": True, "size": p.stat().st_size}

    @app.post("/api/projects/{pid}/artifacts/{aid}/restore")
    async def restore(pid: str, aid: str, body: RestoreBody):
        st = store_for(pid)
        return (await _in_thread(restore_revision, st, aid, body.revision_id)).model_dump()

    # -- context / impact -----------------------------------------------------
    @app.post("/api/projects/{pid}/resolve")
    def resolve(pid: str, body: ResolveBody):
        st = store_for(pid)
        ctx = binding_mod.resolve(st, body.target, source_ids=body.source_ids)
        return ctx.model_dump()

    # -- semantic understanding --------------------------------------------------
    @app.get("/api/providers")
    def list_providers():
        return [p.describe() for p in provider_registry().values()]

    @app.post("/api/projects/{pid}/sources/{sid}/analyze")
    async def analyze_source(pid: str, sid: str, body: AnalyzeBody):
        from .providers.base import ProviderError as PErr

        st = store_for(pid)
        try:
            src = st.get_source(sid)
        except KeyError:
            raise HTTPException(404, "source not found")
        try:
            ana = await _in_thread(semsvc.analyze_source, st, src, body.provider,
                                   body.model or None, body.segment, body.force)
        except PErr as exc:
            raise HTTPException(422, str(exc))
        return ana.model_dump()

    @app.post("/api/projects/{pid}/sources/generate")
    async def generate_source(pid: str, body: GenerateBody):
        """Generate an image with a live provider and register it as a *generated* source."""
        from .providers import get_provider
        from .providers.base import ProviderError as PErr

        st = store_for(pid)
        prov = get_provider(body.provider)
        ok, why = prov.available()
        if not ok or not hasattr(prov, "generate_image"):
            raise HTTPException(422, f"image generation unavailable ({body.provider}): {why}")
        refs = [str(st.abs(st.get_source(r).locator.path)) for r in body.reference_ids]
        try:
            data, mime, usage = await _in_thread(prov.generate_image, body.prompt, refs,
                                                 body.model or None)
        except PErr as exc:
            raise HTTPException(422, str(exc))
        ext = ".jpg" if "jpeg" in mime else ".png"
        src = ingest.register_bytes(st, data, f"generated{ext}", name=body.name,
                                    origin="generated")
        src.provenance.notes = json.dumps({"provider": body.provider, "prompt": body.prompt,
                                           "references": body.reference_ids,
                                           "usage": usage.model_dump()})
        st.save_source(src)
        return src.model_dump()

    @app.get("/api/projects/{pid}/sources/{sid}/analyses")
    def source_analyses(pid: str, sid: str):
        return [a.model_dump() for a in semsvc.analyses_for(store_for(pid), sid)]

    @app.post("/api/projects/{pid}/agent/context")
    def agent_context(pid: str, body: ContextBody):
        """The context package an agent would receive for a target (cached analyses only:
        inspecting never triggers a provider call)."""
        st = store_for(pid)
        art = st.get_artifact(body.artifact_id) if body.artifact_id else None
        cid = body.component_id or (art.root_id() if art else None)
        sel = TargetSelector(scope="component" if art else "project",
                             artifact_id=art.id if art else None, component_id=cid)
        ctx = binding_mod.resolve(st, sel, source_ids=body.source_ids, artifact=art)
        props, meas, ops = {}, {}, []
        if art is not None:
            ad = get_adapter(art.adapter)
            ops = [o.name for o in ad.info().operations]
            rev = st.get_revision(art.head_revision_id) if art.head_revision_id else None
            if rev is not None:
                meas = {c: m for c, m in rev.measurements.items()
                        if c == cid or c in art.descendants(cid)}
        return semsvc.context_package(st, ctx, art, properties=props, measurements=meas,
                                      operations=ops)

    # -- workflows ------------------------------------------------------------
    @app.get("/api/projects/{pid}/workflows")
    def list_workflows(pid: str):
        return [w.model_dump() for w in store_for(pid).list_workflows()]

    @app.post("/api/projects/{pid}/workflows")
    def create_workflow(pid: str, body: dict[str, Any]):
        body = {k: v for k, v in body.items() if k not in ("version", "created_at",
                                                            "parent_version")}
        wf = Workflow.model_validate(body)
        return store_for(pid).save_workflow(wf).model_dump()

    @app.post("/api/projects/{pid}/workflows/validate")
    def validate(pid: str, body: dict[str, Any]):
        wf = Workflow.model_validate({**body, "id": body.get("id") or "wf_tmp"})
        return validate_workflow(wf, store_for(pid))

    @app.post("/api/projects/{pid}/workflows/import")
    def import_workflow(pid: str, body: dict[str, Any]):
        st = store_for(pid)
        doc = body.get("workflow", body)
        wf = Workflow.model_validate({k: v for k, v in doc.items()
                                      if k not in ("version", "created_at", "parent_version")})
        try:
            st.get_workflow(wf.id)
            wf = wf.model_copy(update={"id": Workflow(name=wf.name).id})
        except LookupError:
            pass
        saved = st.save_workflow(wf)
        return {"workflow": saved.model_dump(), "issues": validate_workflow(saved, st)}

    @app.get("/api/projects/{pid}/workflows/{wid}")
    def get_workflow(pid: str, wid: str, version: int | None = None):
        return store_for(pid).get_workflow(wid, version).model_dump()

    @app.get("/api/projects/{pid}/workflows/{wid}/versions")
    def versions(pid: str, wid: str):
        return [{"version": w.version, "created_at": w.created_at, "nodes": len(w.nodes),
                 "edges": len(w.edges)} for w in store_for(pid).list_workflow_versions(wid)]

    @app.put("/api/projects/{pid}/workflows/{wid}")
    def save_workflow(pid: str, wid: str, body: dict[str, Any]):
        st = store_for(pid)
        st.get_workflow(wid)
        wf = Workflow.model_validate({**{k: v for k, v in body.items()
                                         if k not in ("version", "created_at",
                                                      "parent_version")}, "id": wid})
        saved = st.save_workflow(wf)
        return {"workflow": saved.model_dump(), "issues": validate_workflow(saved, st)}

    @app.get("/api/projects/{pid}/workflows/{wid}/export")
    def export_workflow(pid: str, wid: str, version: int | None = None):
        wf = store_for(pid).get_workflow(wid, version)
        return {"format": "daedelus.workflow", "format_version": 1, "workflow": wf.model_dump()}

    @app.get("/api/projects/{pid}/workflows/{wid}/impact")
    async def impact(pid: str, wid: str):
        return await _in_thread(engine_for(pid).impact, wid)

    @app.post("/api/projects/{pid}/workflows/{wid}/preview")
    async def preview(pid: str, wid: str, body: PreviewBody):
        return await _in_thread(engine_for(pid).preview_node, wid, body.node_id,
                                all_units=body.all_units)

    @app.post("/api/projects/{pid}/workflows/{wid}/execute")
    def execute(pid: str, wid: str, body: ExecuteBody):
        eng = engine_for(pid)
        ex = eng.start(wid, version=body.version, mode=body.mode, nodes=body.nodes)
        if ex.status != RunStatus.pending:
            return ex.model_dump()
        _spawn(pid, ex.id, eng.run, ex.id)
        if body.wait:
            threads[ex.id].join()
        return eng.store.get_execution(ex.id).model_dump()

    def _spawn(pid: str, key: str, fn, *args) -> None:
        def target():
            try:
                fn(*args)
            except Exception:  # surfaced through execution records
                st = store_for(pid)
                ex = st.get_execution(key)
                ex.status = RunStatus.failed
                ex.error = traceback.format_exc()[-2000:]
                ex.finished_at = now_iso()
                st.save_execution(ex)
        t = threading.Thread(target=target, daemon=True, name=f"exec-{key}")
        threads[key] = t
        t.start()

    # -- executions -----------------------------------------------------------
    @app.get("/api/projects/{pid}/executions")
    def list_executions(pid: str, workflow_id: str | None = None):
        return [_exec_summary(e) for e in store_for(pid).list_executions(workflow_id)]

    def _exec_summary(e) -> dict[str, Any]:
        return {"id": e.id, "workflow_id": e.workflow_id, "workflow_version": e.workflow_version,
                "mode": e.mode, "status": e.status.value, "created_at": e.created_at,
                "finished_at": e.finished_at, "error": e.error,
                "nodes": {r.node_id: r.status.value for r in e.node_runs}}

    @app.get("/api/projects/{pid}/executions/{eid}")
    def get_execution(pid: str, eid: str):
        d = store_for(pid).get_execution(eid).model_dump()
        d.pop("inputs_snapshot", None)
        return d

    @app.get("/api/projects/{pid}/executions/{eid}/inputs")
    def execution_inputs(pid: str, eid: str):
        return store_for(pid).get_execution(eid).inputs_snapshot

    @app.get("/api/projects/{pid}/contexts/{cid}")
    def get_context(pid: str, cid: str):
        return store_for(pid).get_context(cid).model_dump()

    @app.post("/api/projects/{pid}/executions/{eid}/decide")
    def decide(pid: str, eid: str, body: DecisionBody):
        eng = engine_for(pid)
        ex = eng.store.get_execution(eid)
        if ex.run(body.node_id).status != RunStatus.waiting_approval:
            raise HTTPException(400, "node is not waiting for approval")
        _spawn(pid, eid, eng.decide, eid, body.node_id, body.approve, body.note)
        return {"accepted": True}

    @app.post("/api/projects/{pid}/executions/{eid}/cancel")
    def cancel(pid: str, eid: str):
        engine_for(pid).cancel(eid)
        return {"cancelling": eid}

    @app.post("/api/projects/{pid}/executions/{eid}/replay")
    async def replay(pid: str, eid: str):
        return await _in_thread(engine_for(pid).replay, eid)

    # -- agent conversation ----------------------------------------------------
    @app.get("/api/projects/{pid}/agent/messages")
    def messages(pid: str):
        return store_for(pid).list_messages()

    @app.post("/api/projects/{pid}/agent/messages")
    async def agent_message(pid: str, body: AgentMessage):
        """Record a user direction as an instruction source bound to the chosen target, then
        report which workflow units it makes stale. Nothing is executed until the user runs
        the workflow."""
        st = store_for(pid)
        _check_target(st, body.target)
        st.add_message({"role": "user", "text": body.text, "target": body.target.key()})
        src = ingest.register_text(st, f"Instruction: {body.text[:40]}", body.text,
                                   origin="agent_message")
        b = st.save_binding(SourceBinding(source_id=src.id, role=body.role, aspects=body.aspects,
                                          target=body.target, strength=body.strength,
                                          instructions=""))
        directives = src.extracted.get("directives", {})
        colors = src.extracted.get("colors", [])
        lines = [f"Recorded as source '{src.name}' with role '{b.role}' on {b.target.key()}."]
        if directives or colors:
            lines.append("Understood directives: " + ", ".join(
                [f"{k}={v}" for k, v in directives.items()] + [f"colour {c}" for c in colors]))
        else:
            lines.append("No machine-readable directives found (use 'key: value' lines, e.g. "
                         "'taper: 0.3'). The text is still available to AI planner providers.")
        imp = None
        if body.workflow_id:
            imp = await _in_thread(engine_for(pid).impact, body.workflow_id)
            stale = [u["unit"] for n in imp["nodes"] for u in n.get("units", []) if u["stale"]]
            lines.append(f"Units that will re-run: {stale or 'none'}")
        reply = st.add_message({"role": "assistant", "text": "\n".join(lines),
                                "source_id": src.id, "binding_id": b.id})
        return {"source": src.model_dump(), "binding": b.model_dump(), "reply": reply,
                "impact": imp}

    # -- spatial boards -------------------------------------------------------------
    from . import boards as board_mod
    from .editing import EditRejected, apply_manual_edit, ensure_glb_with_ids
    from .models import PlannedOperation

    @app.get("/api/projects/{pid}/boards")
    def list_boards(pid: str):
        st = store_for(pid)
        return [{"id": b.id, "name": b.name, "revision": b.revision, "items": len(b.items),
                 "updated_at": b.updated_at} for b in board_mod.ensure_boards(st)]

    @app.post("/api/projects/{pid}/boards")
    def create_board(pid: str, body: BoardCreate):
        st = store_for(pid)
        board_mod.ensure_boards(st)
        b = (board_mod.default_board(st, body.name) if body.layout == "default" else
             board_mod.CanvasBoard(project_id=st.get_project().id, name=body.name))
        return board_mod.board_view(st, board_mod.save_board(st, b))

    @app.get("/api/projects/{pid}/boards/{bid}")
    def get_board(pid: str, bid: str):
        st = store_for(pid)
        return board_mod.board_view(st, board_mod.get_board(st, bid))

    @app.put("/api/projects/{pid}/boards/{bid}")
    def save_board(pid: str, bid: str, body: BoardSave):
        st = store_for(pid)
        board_mod.get_board(st, bid)
        doc = {k: v for k, v in body.board.items() if k != "missing_items"}
        b = board_mod.CanvasBoard.model_validate({**doc, "id": bid})
        try:
            saved = board_mod.save_board(st, b, expected_revision=body.expected_revision)
        except board_mod.BoardConflict as exc:
            raise HTTPException(409, str(exc))
        return board_mod.board_view(st, saved)

    @app.patch("/api/projects/{pid}/boards/{bid}")
    def rename_board(pid: str, bid: str, body: dict[str, Any]):
        st = store_for(pid)
        b = board_mod.get_board(st, bid)
        if "name" in body:
            b.name = str(body["name"]).strip() or b.name
        return board_mod.board_view(st, board_mod.save_board(st, b))

    @app.delete("/api/projects/{pid}/boards/{bid}")
    def delete_board(pid: str, bid: str):
        st = store_for(pid)
        if len(board_mod.list_boards(st)) <= 1:
            raise HTTPException(400, "a project keeps at least one board")
        board_mod.delete_board(st, bid)
        return {"deleted": bid}

    @app.post("/api/projects/{pid}/boards/{bid}/place")
    def place(pid: str, bid: str, body: PlaceBody):
        st = store_for(pid)
        b = board_mod.get_board(st, bid)
        kw: dict[str, Any] = {"presentation_state": body.presentation_state}
        if body.width and body.height:
            kw["size"] = board_mod.Size(width=body.width, height=body.height)
        item = board_mod.place_resource(st, b, board_mod.ResourceRef(**body.resource_ref),
                                        body.x, body.y, **kw)
        view = board_mod.board_view(st, board_mod.save_board(st, b))
        return {"item": item.model_dump(), "board": view}

    # -- in-place artifact editing -------------------------------------------------
    @app.post("/api/projects/{pid}/artifacts/{aid}/edit")
    async def edit_artifact(pid: str, aid: str, body: EditBody):
        st = store_for(pid)
        ops = [PlannedOperation(**o) for o in body.operations]
        try:
            rev = await _in_thread(apply_manual_edit, st, aid, ops, message=body.message,
                                   base_revision_id=body.base_revision_id,
                                   lock=engine_for(pid)._run_lock)
        except EditRejected as exc:
            return JSONResponse(status_code=409 if "changed since" in str(exc) else 422,
                                content={"detail": str(exc), "report": exc.report})
        return rev.model_dump()

    @app.get("/api/projects/{pid}/revisions/{rid}/glb")
    async def revision_glb(pid: str, rid: str):
        st = store_for(pid)
        path = await _in_thread(ensure_glb_with_ids, st, rid)
        if not path:
            raise HTTPException(404, "this artifact has no 3D representation")
        return {"path": path}

    # -- cross-artifact component dependencies (V1.2) -----------------------------
    from . import dependencies as dep_mod

    @app.get("/api/projects/{pid}/dependencies")
    async def list_deps(pid: str, artifact_id: str | None = None):
        st = store_for(pid)
        rows = await _in_thread(dep_mod.all_status, st)
        if artifact_id:
            rows = [r for r in rows if artifact_id in (r["source"]["artifact_id"],
                                                       r["target"]["artifact_id"])]
        return rows

    @app.post("/api/projects/{pid}/dependencies")
    def create_dep(pid: str, body: DependencyCreate):
        st = store_for(pid)
        try:
            d = dep_mod.add_dependency(st, dep_mod.End(**body.source), dep_mod.End(**body.target),
                                       options=body.options, target_kind=body.target_kind,
                                       note=body.note)
        except (ValueError, LookupError, TypeError) as exc:
            raise HTTPException(422, str(exc))
        return dep_mod.status(st, d)

    @app.delete("/api/projects/{pid}/dependencies/{did}")
    def delete_dep(pid: str, did: str):
        dep_mod.delete_dependency(store_for(pid), did)
        return {"deleted": did}

    @app.post("/api/projects/{pid}/dependencies/sync")
    async def sync_deps(pid: str, body: SyncBody):
        st = store_for(pid)
        rep = await _in_thread(dep_mod.sync, st, dependency_ids=body.dependency_ids,
                               target_artifact_ids=body.target_artifact_ids, force=body.force,
                               lock=engine_for(pid)._run_lock)
        rep["status"] = await _in_thread(dep_mod.all_status, st)
        return rep

    @app.get("/api/projects/{pid}/artifacts/{aid}/office")
    def office_view(pid: str, aid: str, revision_id: str | None = None):
        """Structured view of an Office artifact revision (grid / document / slides)."""
        st = store_for(pid)
        a = st.get_artifact(aid)
        if a.adapter not in ("spreadsheet", "document", "presentation"):
            raise HTTPException(400, f"'{a.name}' is not an Office artifact")
        rev = st.get_revision(revision_id or a.head_revision_id)
        key = "grid" if a.adapter == "spreadsheet" else "structure"
        rel = rev.previews.get(key)
        if not rel or not st.abs(rel).exists():
            raise HTTPException(404, f"no {key} preview for revision {rev.number}")
        pages = list((k, v) for k, v in rev.previews.items()
                       if k == "render" or k.startswith(("page", "slide")))
        return {"artifact_id": a.id, "adapter": a.adapter, "revision_id": rev.id,
                "revision_number": rev.number, "view": json.loads(st.abs(rel).read_text()),
                "pages": [v for _, v in sorted(pages, key=lambda kv: (
                    kv[0] != "render", int("".join(ch for ch in kv[0] if ch.isdigit()) or 0)))],
                "pdf": rev.previews.get("pdf")}

    @app.get("/api/projects/{pid}/artifacts/{aid}/operations")
    def artifact_operations(pid: str, aid: str):
        a = store_for(pid).get_artifact(aid)
        info = adapter_registry()[a.adapter].info()
        return [o.model_dump() for o in info.operations]

    # -- static studio ----------------------------------------------------------
    dist = Path(studio_dist or os.environ.get("DAEDELUS_STUDIO_DIST", "") or
                Path(__file__).resolve().parents[2] / "studio" / "dist")
    if dist.is_dir() and (dist / "index.html").exists():
        app.mount("/", StaticFiles(directory=dist, html=True), name="studio")

    return app


async def _in_thread(fn, *args, **kw):
    import anyio

    return await anyio.to_thread.run_sync(lambda: fn(*args, **kw))
