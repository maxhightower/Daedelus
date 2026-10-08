"""HTTP surface of the cluster: worker protocol + user-facing job and event endpoints."""

from __future__ import annotations

import asyncio
import json
import os
import tempfile
import time
from pathlib import Path

from fastapi import APIRouter, Header, HTTPException, Request
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel, Field

from ..models import new_id
from .cas import BlobError, sha256_file, valid_sha
from .models import JobResult, WorkerInfo
from .queue import LeaseLost
from .service import get_cluster

MAX_BLOB = int(os.environ.get("DAEDELUS_MAX_BLOB_MB", "2048")) * 1024 * 1024


class RegisterBody(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    capabilities: list[str] = Field(default_factory=lambda: ["cpu"])
    adapters: list[str] = Field(default_factory=list)
    version: str = ""
    host: str = ""


class LeaseBody(BaseModel):
    worker_id: str
    wait_s: float = 10


class HeartbeatBody(BaseModel):
    progress: float | None = None
    message: str | None = None


class FailBody(BaseModel):
    error: str
    retryable: bool = False
    cancelled: bool = False


class MissingBody(BaseModel):
    shas: list[str]


def _cluster():
    cl = get_cluster()
    if cl is None:
        raise HTTPException(503, "cluster not configured")
    return cl


def _worker(request: Request, authorization: str | None):
    cl = _cluster()
    if not cl.workers_enabled:
        raise HTTPException(403, "worker endpoints are disabled (DAEDELUS_WORKER_TOKENS unset)")
    tok = (authorization or "").removeprefix("Bearer ").strip()
    if not cl.check_worker_token(tok):
        cl.audit.record("worker_auth_failed", "denied", actor=request.client.host
                        if request.client else "?", target=request.url.path)
        raise HTTPException(401, "invalid worker token")
    return cl


def _lease(cl, request: Request, job_id: str, lease_token: str | None):
    try:
        return cl.queue.check_lease(job_id, lease_token or "")
    except LeaseLost as exc:
        cl.audit.record("lease_rejected", "denied", target=job_id, path=request.url.path,
                        reason=str(exc))
        raise HTTPException(409, str(exc))


def build_router() -> APIRouter:
    r = APIRouter()

    # ------------------------------------------------------------------ worker protocol
    @r.post("/api/cluster/workers")
    def register(body: RegisterBody, request: Request, authorization: str | None = Header(None)):
        cl = _worker(request, authorization)
        caps = [c for c in body.capabilities if c in ("cpu", "gpu")] or ["cpu"]
        w = cl.queue.register_worker(WorkerInfo(id=new_id("wkr"), name=body.name,
                                                capabilities=caps, adapters=body.adapters,
                                                version=body.version, host=body.host))
        cl.audit.record("worker_registered", actor=w.id, target=w.name, capabilities=caps,
                        adapters=body.adapters, peer=request.client.host if request.client else "")
        return w.model_dump()

    @r.post("/api/cluster/lease")
    def lease(body: LeaseBody, request: Request, authorization: str | None = Header(None)):
        cl = _worker(request, authorization)
        w = next((x for x in cl.queue.workers() if x.id == body.worker_id), None)
        if w is None:
            raise HTTPException(404, "unknown worker; register again")
        t_end = time.time() + max(0.0, min(body.wait_s, 25.0))
        while True:
            got = cl.queue.lease(w)
            if got is not None:
                cl.audit.record("job_leased", actor=w.id, target=got.job.id,
                                attempt=got.attempt)
                return got.model_dump()
            if time.time() >= t_end:
                return Response(status_code=204)
            time.sleep(0.25)

    @r.get("/api/cluster/jobs/{job_id}/blobs/{sha}")
    def get_blob(job_id: str, sha: str, request: Request,
                 authorization: str | None = Header(None),
                 x_lease_token: str | None = Header(None)):
        cl = _worker(request, authorization)
        job = _lease(cl, request, job_id, x_lease_token)
        allowed = job.native.blobs() | (job.before.blobs() if job.before else set()) | \
            set(job.files.values())
        if sha not in allowed:  # scope: a lease grants exactly this job's inputs
            cl.audit.record("blob_scope_denied", "denied", target=job_id, sha=sha)
            raise HTTPException(403, "blob not part of this job")
        try:
            return Response(cl.blobs.read(sha), media_type="application/octet-stream")
        except BlobError as exc:
            raise HTTPException(404, str(exc))

    @r.post("/api/cluster/jobs/{job_id}/missing")
    def missing(job_id: str, body: MissingBody, request: Request,
                authorization: str | None = Header(None),
                x_lease_token: str | None = Header(None)):
        cl = _worker(request, authorization)
        _lease(cl, request, job_id, x_lease_token)
        return {"missing": [s for s in body.shas if valid_sha(s) and not cl.blobs.has(s)]}

    @r.put("/api/cluster/jobs/{job_id}/blobs/{sha}")
    async def put_blob(job_id: str, sha: str, request: Request,
                       authorization: str | None = Header(None),
                       x_lease_token: str | None = Header(None)):
        cl = _worker(request, authorization)
        _lease(cl, request, job_id, x_lease_token)
        if not valid_sha(sha):
            raise HTTPException(400, "invalid blob id")
        fd, tmp = tempfile.mkstemp(prefix=".in_", dir=cl.blobs.root)
        size = 0
        try:
            with os.fdopen(fd, "wb") as f:
                async for chunk in request.stream():
                    size += len(chunk)
                    if size > MAX_BLOB:
                        raise HTTPException(413, "blob too large")
                    f.write(chunk)
            got = sha256_file(Path(tmp))
            if got != sha:
                cl.audit.record("blob_hash_mismatch", "rejected", target=job_id, expected=sha,
                                got=got)
                raise HTTPException(422, f"hash mismatch: expected {sha}, got {got}")
            dst = cl.blobs.path(sha)
            dst.parent.mkdir(parents=True, exist_ok=True)
            os.replace(tmp, dst)
            return {"stored": sha, "size": size}
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)

    @r.post("/api/cluster/jobs/{job_id}/heartbeat")
    def heartbeat(job_id: str, body: HeartbeatBody, request: Request,
                  authorization: str | None = Header(None),
                  x_lease_token: str | None = Header(None)):
        cl = _worker(request, authorization)
        try:
            return cl.queue.heartbeat(job_id, x_lease_token or "", body.progress, body.message)
        except LeaseLost as exc:
            raise HTTPException(409, str(exc))

    @r.post("/api/cluster/jobs/{job_id}/complete")
    async def complete(job_id: str, request: Request, authorization: str | None = Header(None),
                       x_lease_token: str | None = Header(None)):
        cl = _worker(request, authorization)
        res = JobResult.model_validate_json(await request.body())
        for m in (res.output, res.artifacts):
            if m is not None:
                miss = [f.sha256 for f in m.files.values() if not cl.blobs.has(f.sha256)]
                if miss:
                    raise HTTPException(400, f"result references missing blobs: {miss[:3]}")
        try:
            st = cl.queue.complete(job_id, x_lease_token or "", res)
        except LeaseLost as exc:
            cl.audit.record("late_result_discarded", "rejected", target=job_id,
                            worker=res.worker_id, reason=str(exc))
            raise HTTPException(409, str(exc))
        cl.audit.record("job_completed", st.state, actor=res.worker_id or "", target=job_id,
                        seconds=res.seconds)
        return st.model_dump()

    @r.post("/api/cluster/jobs/{job_id}/fail")
    def fail(job_id: str, body: FailBody, request: Request,
             authorization: str | None = Header(None),
             x_lease_token: str | None = Header(None)):
        cl = _worker(request, authorization)
        try:
            st = cl.queue.fail(job_id, x_lease_token or "", body.error[:4000],
                               retryable=body.retryable, cancelled=body.cancelled)
        except LeaseLost as exc:
            raise HTTPException(409, str(exc))
        cl.audit.record("job_failed_report", st.state, target=job_id, error=body.error[:500])
        return st.model_dump()

    # ------------------------------------------------------------------ user-facing
    @r.get("/api/cluster/status")
    def status():
        cl = get_cluster()
        if cl is None:
            return {"configured": False, "workers_enabled": False, "workers": [], "jobs": {}}
        return {"configured": True, **cl.summary()}

    @r.get("/api/cluster/audit")
    def audit(n: int = 200):
        return _cluster().audit.tail(min(n, 2000))

    @r.get("/api/projects/{pid}/jobs")
    def jobs(pid: str, limit: int = 100):
        return [s.model_dump() for s in _cluster().queue.list(project_id=pid, limit=limit)]

    @r.get("/api/projects/{pid}/jobs/{job_id}")
    def job(pid: str, job_id: str):
        cl = _cluster()
        try:
            st = cl.queue.status(job_id)
        except KeyError:
            raise HTTPException(404, "job not found")
        if st.project_id != pid:
            raise HTTPException(404, "job not found")
        res = cl.queue.result(job_id)
        req = cl.queue.request(job_id)
        return {"status": st.model_dump(), "request": {
            "adapter": req.adapter, "method": req.method, "requires": req.requires,
            "files": len(req.native.files), "base_digest": req.base_digest, "origin": req.origin},
            "result": None if res is None else {"ok": res.ok, "error": res.error,
                                                "seconds": res.seconds,
                                                "worker_id": res.worker_id,
                                                "logs": res.logs[-2000:]},
            "publication": cl.queue.publication(job_id),
            "events": cl.queue.events(job_id=job_id)}

    @r.post("/api/projects/{pid}/jobs/{job_id}/cancel")
    def cancel(pid: str, job_id: str):
        cl = _cluster()
        st = cl.queue.status(job_id)
        if st.project_id != pid:
            raise HTTPException(404, "job not found")
        st = cl.queue.cancel(job_id)
        cl.audit.record("job_cancel_requested", target=job_id, by="user")
        return st.model_dump()

    @r.get("/api/projects/{pid}/events")
    async def events(pid: str, request: Request, since: int | None = None,
                     last_event_id: str | None = Header(None)):
        """Server-sent events: executions, jobs and revisions of one project (resumable)."""
        cl = _cluster()
        start = since if since is not None else int(last_event_id or 0) if (
            last_event_id or "").isdigit() else None
        if start is None:  # new subscriber: only events from now on
            tail = cl.queue.events(project_id=pid, since=0, limit=1_000_000)
            start = tail[-1]["seq"] if tail else 0

        async def gen():
            seq = start
            yield f"event: hello\ndata: {json.dumps({'seq': seq})}\n\n"
            last_ping = time.time()
            while not await request.is_disconnected():
                evs = cl.queue.events(project_id=pid, since=seq, limit=200)
                for ev in evs:
                    seq = ev["seq"]
                    yield f"id: {seq}\nevent: {ev['kind']}\ndata: {json.dumps(ev)}\n\n"
                if not evs:
                    if time.time() - last_ping > 15:
                        yield ": ping\n\n"
                        last_ping = time.time()
                    await asyncio.sleep(0.25)

        return StreamingResponse(gen(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    return r
