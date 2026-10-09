"""Execution targets and the remote adapter proxy.

``use_target(store, target)`` sets the execution target for the current thread/context.
``adapters.get_adapter`` consults it: for a remote target it returns a ``RemoteAdapter``
whose methods run as jobs on workers. The engine, manual edits, revisions and dependency
syncs therefore run unchanged against local or remote adapters.

Resolution rules (never a silent fallback):

- ``local``      -> this machine; errors if the adapter's tools are missing here;
- ``cloud_cpu``  -> a connected worker offering the adapter on CPU, else an error;
- ``cloud_gpu``  -> a connected GPU worker offering the adapter, else an error;
- ``automatic``  -> local when the tools are present here, otherwise a connected CPU worker,
                    otherwise an error. The choice and its reason are recorded.
"""

from __future__ import annotations

import contextlib
import contextvars
import hashlib
import json
import shutil
import tempfile
import threading
from collections import OrderedDict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..adapters.base import Adapter, AdapterError, AdapterInfo, ApplyResult, InspectResult
from ..models import PlannedOperation, ValidationReport
from . import cas
from ..budget import current as current_budget
from .models import JobRequest, JobResult
from .service import JobFailed, get_cluster

TARGETS = ("automatic", "local", "cloud_cpu", "cloud_gpu")

# V2.1 performance: inspection results are a pure function of (adapter, adapter version, entry,
# exact file contents). They are cached under the tree's content digest, so a result is never
# reused once a single byte of the artifact changes. Mutating jobs return the inspection of
# their own output, which fills the cache for the engine's next ``inspect`` (one job fewer per
# mutation).
INSPECT_CACHE_MAX = 512
_inspect_cache: "OrderedDict[tuple, dict]" = OrderedDict()
_cache_lock = threading.Lock()
cache_stats = {"hits": 0, "misses": 0, "stored": 0}


def _cache_get(key: tuple) -> dict | None:
    with _cache_lock:
        v = _inspect_cache.get(key)
        if v is not None:
            _inspect_cache.move_to_end(key)
            cache_stats["hits"] += 1
        else:
            cache_stats["misses"] += 1
        return v


def _cache_put(key: tuple, value: dict) -> None:
    with _cache_lock:
        _inspect_cache[key] = value
        _inspect_cache.move_to_end(key)
        cache_stats["stored"] += 1
        while len(_inspect_cache) > INSPECT_CACHE_MAX:
            _inspect_cache.popitem(last=False)


def clear_inspect_cache() -> None:
    with _cache_lock:
        _inspect_cache.clear()


class ExecutionUnavailable(AdapterError):
    """The requested execution target cannot run this adapter right now."""


@dataclass
class TargetContext:
    target: str
    project_id: str
    origin: dict[str, Any] = field(default_factory=dict)
    decisions: dict[str, dict[str, Any]] = field(default_factory=dict)  # adapter -> decision
    jobs: list[dict[str, Any]] = field(default_factory=list)
    on_event: Any = None
    cancelled: Any = None


_ctx: contextvars.ContextVar[TargetContext | None] = contextvars.ContextVar("dd_target",
                                                                             default=None)


def normalise(target: str | None) -> str:
    t = (target or "").strip().lower()
    return {"cloud": "cloud_cpu", "": "local"}.get(t, t)


@contextlib.contextmanager
def use_target(project_id: str, target: str | None, **kw: Any):
    t = normalise(target)
    if t not in TARGETS:
        raise ExecutionUnavailable(f"unknown execution target '{target}' (use {TARGETS})")
    tc = TargetContext(target=t, project_id=project_id, **kw)
    tok = _ctx.set(tc)
    try:
        yield tc
    finally:
        _ctx.reset(tok)


def current() -> TargetContext | None:
    return _ctx.get()


def decide(local: Adapter, tc: TargetContext) -> dict[str, Any]:
    if local.name in tc.decisions:
        return tc.decisions[local.name]
    cl = get_cluster()
    ok, why = local.check_environment()
    t = tc.target
    if t == "local":
        d = {"requested": t, "resolved": "local", "reason": "local execution selected"}
    elif t == "automatic":
        if ok:
            d = {"requested": t, "resolved": "local", "reason": "tools available on this machine"}
        elif cl and cl.can_run(local.name, "cpu"):
            d = {"requested": t, "resolved": "cloud_cpu",
                 "reason": f"local tools unavailable ({why}); a CPU worker is connected"}
        else:
            raise ExecutionUnavailable(
                f"automatic execution: '{local.name}' is unavailable locally ({why}) and no "
                "connected worker offers it")
    else:
        cap = "gpu" if t == "cloud_gpu" else "cpu"
        if cl is None or not cl.workers_enabled:
            raise ExecutionUnavailable(f"{t} requested but this control plane has no worker "
                                       "pool configured (DAEDELUS_WORKER_TOKENS unset); not "
                                       "falling back to local execution")
        w = cl.can_run(local.name, cap)
        if w is None:
            raise ExecutionUnavailable(f"{t} requested but no connected {cap.upper()} worker "
                                       f"offers adapter '{local.name}'; not falling back to "
                                       "local execution")
        d = {"requested": t, "resolved": t, "reason": f"worker '{w.name}' offers it"}
    tc.decisions[local.name] = d
    return d


def wrap(local: Adapter) -> Adapter:
    tc = current()
    if tc is None or tc.target == "local":
        return local
    d = decide(local, tc)
    if d["resolved"] == "local":
        return local
    return RemoteAdapter(local, tc, "gpu" if d["resolved"] == "cloud_gpu" else "cpu")


class RemoteAdapter(Adapter):
    """Adapter whose filesystem-touching methods run as jobs on workers."""

    def __init__(self, local: Adapter, tc: TargetContext, capability: str):
        self.local = local
        self.name = local.name
        self.version = getattr(local, "version", "")
        self.tc = tc
        self.capability = capability

    # catalogue / static checks are pure Python and identical on both sides
    def info(self) -> AdapterInfo:
        return self.local.info()

    def check_environment(self) -> tuple[bool, str]:
        cl = get_cluster()
        w = cl.can_run(self.name, self.capability) if cl else None
        return (w is not None, f"remote worker '{w.name}'" if w else "no connected worker")

    def op_spec(self, name):
        return self.local.op_spec(name)

    def created_kind(self, op):
        return self.local.created_kind(op)

    def validate_operations(self, ops, components):
        return self.local.validate_operations(ops, components)

    # ------------------------------------------------------------------ job plumbing
    def _run(self, method: str, native_dir: Path | None, entry: str, args: dict[str, Any],
             files: dict[str, str] | None = None, before: Path | None = None) -> JobResult:
        cl = get_cluster()
        if cl is None:
            raise ExecutionUnavailable("no cluster configured")
        native = cas.snapshot(native_dir, cl.blobs) if native_dir else cas.Manifest()
        before_m = cas.snapshot(before, cl.blobs) if before else None
        fblobs = {k: cl.blobs.put_file(Path(p)) for k, p in (files or {}).items()
                  if p and Path(p).is_file()}
        bud = current_budget()
        key = None
        if self.tc.origin.get("execution_id"):
            # deterministic key: a resumed execution (control-plane restart) resubmits the same
            # job and receives the already-computed result instead of running it again
            key = hashlib.sha256(json.dumps({
                "o": [self.tc.origin.get("execution_id"), self.tc.origin.get("node_id")],
                "m": method, "a": self.name, "n": native.digest(), "args": args,
                "f": fblobs, "b": before_m.digest() if before_m else None, "e": entry,
                "r": self.capability}, sort_keys=True, default=str).encode()).hexdigest()
        req = JobRequest(project_id=self.tc.project_id, artifact_id=self.tc.origin.get(
                             "artifact_id"), adapter=self.name, method=method, entry=entry,
                         native=native, before=before_m, args=args, files=fblobs,
                         requires=self.capability, base_digest=native.digest(),
                         origin=self.tc.origin, idempotency_key=key,
                         **(bud.job_settings() if bud else {}))
        slot = bud.job_slot() if bud else contextlib.nullcontext()
        with slot:
            return self._submit_and_wait(cl, req, method, entry, native_dir)

    def _submit_and_wait(self, cl, req: JobRequest, method: str, entry: str,
                         native_dir: Path | None) -> JobResult:
        st0 = cl.submit(req)
        if st0.id != req.id:  # idempotent hit: continue with the existing job
            req = cl.queue.request(st0.id)
        rec = {"job_id": req.id, "method": method, "adapter": self.name,
               "requires": self.capability}
        self.tc.jobs.append(rec)
        try:
            res = cl.wait(req.id, on_event=self.tc.on_event, cancelled=self.tc.cancelled)
        except JobFailed as exc:
            rec.update(state=exc.status.state, error=exc.status.error,
                       worker_id=exc.status.worker_id, attempts=exc.status.attempt)
            if exc.result is not None and exc.result.value.get("adapter_error"):
                raise AdapterError(exc.result.error or "remote adapter error") from None
            raise AdapterError(f"remote {method} {exc.status.state}: {exc.status.error}") \
                from None
        st = cl.queue.status(req.id)
        w = next((x for x in cl.queue.workers() if x.id == res.worker_id), None)
        rec.update(state="succeeded", worker_id=res.worker_id, attempts=st.attempt,
                   seconds=res.seconds, bytes_in=res.bytes_in, bytes_out=res.bytes_out,
                   isolation=res.isolation or None,
                   deployment=(w.deployment or "process") if w else None,
                   worker_name=w.name if w else None)
        if method in ("create", "apply", "after_restore") and native_dir is not None:
            self._publish(req, res, Path(native_dir))
            post = res.value.get("_post_inspect") if isinstance(res.value, dict) else None
            if post and res.output is not None:
                ent = res.value.get("entry", entry) if method == "create" else entry
                _cache_put(self._ckey(ent, res.output.digest()), post)
        return res

    def _ckey(self, entry: str, digest: str) -> tuple:
        return (self.name, self.version, entry, digest)

    def _workers_support(self, feature: str) -> bool:
        cl = get_cluster()
        ws = [w for w in (cl.live_workers() if cl else []) if self.name in w.adapters and
              self.capability in w.capabilities]
        return bool(ws) and all(feature in (w.features or []) for w in ws)

    def _publish(self, req: JobRequest, res: JobResult, native_dir: Path) -> None:
        """Atomic, idempotent publication with optimistic version check.

        current == output -> already published (no-op); current == base -> swap the output in;
        anything else -> version conflict (someone changed the files meanwhile)."""
        cl = get_cluster()
        if res.output is None:
            raise AdapterError(f"job {req.id} returned no native output")
        current_digest = cas.snapshot(native_dir).digest() if native_dir.exists() else \
            cas.Manifest().digest()
        if current_digest == res.output.digest():
            return
        if current_digest != req.base_digest:
            detail = (f"native files changed while job {req.id} ran (version conflict); "
                      "result not published")
            cl.queue.mark_conflict(req.id, detail)
            cl.audit.record("publication_conflict", "rejected", target=req.id,
                            project=req.project_id, artifact=req.artifact_id)
            raise AdapterError(detail)
        missing = [f.sha256 for f in res.output.files.values() if not cl.blobs.has(f.sha256)]
        if missing:
            raise AdapterError(f"job {req.id}: output blobs missing: {missing[:3]}")
        cas.swap_in(res.output, cl.blobs, native_dir)
        again = cl.queue.publication(req.id) is not None
        cl.queue.set_publication(req.id, {"digest": res.output.digest(),
                                          "files": len(res.output.files), "republished": again})
        cl.audit.record("republished" if again else "published", target=req.id,
                        project=req.project_id, artifact=req.artifact_id,
                        digest=res.output.digest())

    def _materialize_into(self, m: cas.Manifest | None, out_dir: Path) -> None:
        if m is None:
            return
        cl = get_cluster()
        out_dir.mkdir(parents=True, exist_ok=True)
        tmp = Path(tempfile.mkdtemp(prefix=".rx_", dir=out_dir))
        shutil.rmtree(tmp)
        cas.materialize(m, cl.blobs, tmp)
        for p in tmp.rglob("*"):
            if p.is_file():
                dst = out_dir / p.relative_to(tmp)
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(p), dst)
        shutil.rmtree(tmp, ignore_errors=True)

    # ------------------------------------------------------------------ adapter API
    def create(self, native_dir: Path, template: str, params: dict[str, Any]) -> str:
        files = {}
        if template == "import" and params.get("path"):
            files["import"] = params["path"]
            params = {**params, "path": None}
        res = self._run("create", native_dir, "", {"template": template, "params": params,
                                                   "post_inspect": True}, files)
        return res.value["entry"]

    def inspect(self, native_dir: Path, entry: str) -> InspectResult:
        key = self._ckey(entry, cas.snapshot(native_dir).digest() if Path(native_dir).exists()
                         else cas.Manifest().digest())
        hit = _cache_get(key)
        if hit is not None:
            self.tc.jobs.append({"method": "inspect", "adapter": self.name, "cached": True,
                                 "state": "cache_hit", "digest": key[3][:12]})
            return InspectResult(**hit)
        val = self._run("inspect", native_dir, entry, {}).value
        _cache_put(key, val)
        return InspectResult(**val)

    def apply(self, native_dir: Path, entry: str, operations: list[PlannedOperation],
              context: dict[str, Any]) -> ApplyResult:
        files: dict[str, str] = {}
        for k, p in (context.get("source_paths") or {}).items():
            files[f"source:{k}"] = p
        for k, p in (context.get("files") or {}).items():
            files[f"file:{k}"] = p
        args = {"operations": [o.model_dump() for o in operations],
                "message": context.get("message", ""), "post_inspect": True}
        val = dict(self._run("apply", native_dir, entry, args, files).value)
        val.pop("_post_inspect", None)
        return ApplyResult(**val)

    def preview(self, native_dir: Path, entry: str, out_dir: Path) -> dict[str, Path]:
        res = self._run("preview", native_dir, entry, {})
        self._materialize_into(res.artifacts, Path(out_dir))
        return {k: Path(out_dir) / v for k, v in res.value.get("files", {}).items()}

    def export(self, native_dir: Path, entry: str, fmt: str, out_dir: Path) -> Path:
        res = self._run("export", native_dir, entry, {"fmt": fmt})
        self._materialize_into(res.artifacts, Path(out_dir))
        return Path(out_dir) / res.value["path"]

    def diff(self, before_dir: Path, after_dir: Path, entry: str) -> str | None:
        return self._run("diff", after_dir, entry, {}, before=before_dir).value.get("diff")

    def after_restore(self, native_dir: Path, entry: str, message: str) -> None:
        self._run("after_restore", native_dir, entry, {"message": message, "post_inspect": True})

    def validate(self, native_dir: Path, entry: str, checks: list[str],
                 context: dict[str, Any]) -> ValidationReport:
        args = {"checks": checks, "allowed_commands": context.get("allowed_commands") or [],
                "test_command": context.get("test_command")}
        return ValidationReport(**self._run("validate", native_dir, entry, args).value)

    def side_effect_scope(self, native_dir: Path, entry: str, op: PlannedOperation) -> list[str]:
        return self.side_effect_scopes(native_dir, entry, [op])[0]

    def side_effect_scopes(self, native_dir: Path, entry: str,
                           ops: list[PlannedOperation]) -> list[list[str]]:
        """All operations' side-effect scopes in ONE job (V2.1 workers); per-operation jobs
        for workers that do not advertise the feature."""
        if len(ops) > 1 and self._workers_support("batch_scope"):
            v = self._run("side_effect_scope", native_dir, entry,
                          {"ops": [o.model_dump() for o in ops]}).value
            return [list(x) for x in v.get("ids_per_op", [])]
        return [list(self._run("side_effect_scope", native_dir, entry,
                               {"op": o.model_dump()}).value.get("ids", [])) for o in ops]
