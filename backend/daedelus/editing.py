"""User-triggered (in-place / Focus) artifact edits.

Direct manipulation in the studio never writes files itself: it submits
declarative adapter operations here. They pass the same safeguards as workflow
execution - catalogue + schema validation, conflict check, checkpoint, apply,
rollback on failure, scope/preservation check, active constraint check and
adapter validation - and produce an ordinary artifact revision.
"""

from __future__ import annotations

import shutil
import threading
from typing import Any

from . import bindings as binding_mod
from .adapters import AdapterError, get_adapter
from .artifacts import native_path, record_revision, restore_tree, snapshot
from .models import (
    ArtifactRevision,
    OperationResult,
    PlannedOperation,
    TargetSelector,
    ValidationReport,
    new_id,
)
from .store import ProjectStore


class EditRejected(Exception):
    """The edit was refused or rolled back; nothing changed."""

    def __init__(self, message: str, report: dict[str, Any] | None = None):
        super().__init__(message)
        self.report = report or {}


def apply_manual_edit(store: ProjectStore, artifact_id: str, operations: list[PlannedOperation],
                      *, message: str = "", base_revision_id: str | None = None,
                      lock: threading.RLock | None = None) -> ArtifactRevision:
    lock = lock or store.lock()
    with lock:
        return _apply(store, artifact_id, operations, message, base_revision_id)


def _apply(store: ProjectStore, artifact_id: str, ops: list[PlannedOperation], message: str,
           base_revision_id: str | None) -> ArtifactRevision:
    from .engine import Engine  # constraint checking is shared with the engine

    art = store.get_artifact(artifact_id)
    if base_revision_id and art.head_revision_id != base_revision_id:
        raise EditRejected("the artifact changed since this view was loaded "
                           f"(head is {art.head_revision_id}); refresh and retry")
    if not ops:
        raise EditRejected("no operations")
    adapter = get_adapter(art.adapter)
    ok, why = adapter.check_environment()
    if not ok:
        raise EditRejected(f"adapter '{art.adapter}' unavailable: {why}")
    errors = adapter.validate_operations(ops, art.components)
    if errors:
        raise EditRejected("invalid operations: " + "; ".join(errors))

    root = art.root_id()
    targets: list[str] = []
    for op in ops:
        spec = adapter.op_spec(op.op)
        if spec and spec.target_kinds == ["new"]:
            t = op.params.get("parent") or root
        else:
            t = op.component_id or root
        if t and t not in targets:
            targets.append(t)
    scope: set[str] = set()
    contexts = []
    for t in targets:
        scope.update([t] + art.descendants(t))
        ctx = binding_mod.resolve(store, TargetSelector(scope="component", artifact_id=art.id,
                                                        component_id=t), artifact=art)
        if ctx.blocking_conflicts:
            raise EditRejected(f"unresolved conflicts on {t}: " +
                               "; ".join(c.detail for c in ctx.blocking_conflicts))
        store.save_context(ctx)
        contexts.append({"unit": ctx.target.key(), "component_id": t, "context_id": ctx.id})

    native = native_path(store, art)
    before = adapter.inspect(native, art.entry)
    ckpt = store.artifact_dir(art.id) / "checkpoints" / new_id("manual")
    snapshot(native, ckpt)
    try:
        try:
            res = adapter.apply(native, art.entry, ops, {"message": message or "Manual edit",
                                                         "source_paths": _image_paths(store)})
        except AdapterError as exc:
            res = None
            err = str(exc)
        if res is None or not res.ok:
            restore_tree(ckpt, native)
            adapter.after_restore(native, art.entry, "Rollback manual edit")
            raise EditRejected(f"operation failed, rolled back: "
                               f"{err if res is None else res.error}")
        after = adapter.inspect(native, art.entry)
        rep = ValidationReport()
        scope.update(c for c in after.states if c not in before.states)
        changed = sorted(c for c in after.states if before.states.get(c) != after.states.get(c))
        outside = [c for c in changed if c not in scope and c not in after.aggregates]
        rep.add("component_preservation", not outside,
                f"changed: {changed}; outside edited scope: {outside or 'none'}",
                changed=changed, outside=outside, scope=sorted(scope),
                preserved=sorted(c for c in before.states if c not in changed))
        for passed, msg in Engine(store)._check_constraints(art, adapter, contexts, before, after):
            rep.add("constraint", passed, msg)
        for c in adapter.validate(native, art.entry, ["file_reopens"], {}).checks:
            rep.add(c.name, c.passed, c.detail, **c.data)
        if not rep.passed:
            restore_tree(ckpt, native)
            adapter.after_restore(native, art.entry, "Rollback manual edit")
            raise EditRejected("edit rejected and rolled back: " + " | ".join(
                f"{c.name}: {c.detail}" for c in rep.checks if not c.passed), rep.model_dump())
        rev = record_revision(
            store, art, insp=after, message=message or f"Manual edit: {', '.join(o.op for o in ops)}",
            operations=ops, units=[c["unit"] for c in contexts], changed_components=changed,
            validation=rep, origin="manual",
            operation_results=[OperationResult(**{k: r[k] for k in (
                "index", "op", "component_id", "status", "detail")}) for r in res.results])
        return rev
    finally:
        shutil.rmtree(ckpt, ignore_errors=True)


def _image_paths(store: ProjectStore) -> dict[str, str]:
    return {s.id: str(store.abs(s.locator.path)) for s in store.list_sources()
            if s.locator.path and s.media_type.value == "image"}


def ensure_glb_with_ids(store: ProjectStore, revision_id: str) -> str | None:
    """Return a GLB preview whose glTF node extras carry ``daedelus_id`` for component picking.

    Revisions recorded before V1 exported GLBs without extras; they are re-exported from the
    revision's own native snapshot on first use (the snapshot itself is never modified)."""
    rev = store.get_revision(revision_id)
    if "glb_ids" in rev.previews:
        return rev.previews["glb_ids"]
    art = store.get_artifact(rev.artifact_id)
    adapter = get_adapter(art.adapter)
    if "glb" not in adapter.info().export_formats:
        return None
    snap = store.abs(rev.snapshot_dir)
    out_dir = store.abs(rev.snapshot_dir).parent / "previews"
    tmp = store.artifact_dir(art.id) / "checkpoints" / new_id("glbx")
    try:
        shutil.copytree(snap, tmp)  # export works on a copy: snapshots stay byte-identical
        path = adapter.export(tmp, art.entry, "glb", out_dir / "_ids")
        final = out_dir / "model_ids.glb"
        shutil.move(str(path), final)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
        shutil.rmtree(out_dir / "_ids", ignore_errors=True)
    rev.previews["glb_ids"] = store.rel(final)
    store.save_revision(rev)
    return rev.previews["glb_ids"]
