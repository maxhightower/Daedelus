"""Artifact lifecycle: creation, snapshots/checkpoints, restore, file access."""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

from .adapters import get_adapter
from .models import Artifact, ArtifactRevision, PlannedOperation
from .store import ProjectStore

SNAPSHOT_IGNORE = shutil.ignore_patterns(".git", "__pycache__", ".pytest_cache", "*.blend1",
                                         "*.tmp")


def native_path(store: ProjectStore, a: Artifact) -> Path:
    return store.abs(a.native_dir)


def snapshot(src: Path, dst: Path) -> None:
    if dst.exists():
        shutil.rmtree(dst)
    shutil.copytree(src, dst, ignore=SNAPSHOT_IGNORE)


def restore_tree(snapshot_dir: Path, native: Path) -> None:
    """Make ``native`` match ``snapshot_dir`` (keeping VCS metadata such as .git)."""
    for p in sorted(native.rglob("*"), reverse=True):
        rel = p.relative_to(native)
        if rel.parts and rel.parts[0] == ".git":
            continue
        if not (snapshot_dir / rel).exists():
            if p.is_dir():
                shutil.rmtree(p, ignore_errors=True)
            else:
                p.unlink()
    for p in snapshot_dir.rglob("*"):
        rel = p.relative_to(snapshot_dir)
        dst = native / rel
        if p.is_dir():
            dst.mkdir(parents=True, exist_ok=True)
        else:
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(p, dst)


def _baseline(inspect_res) -> dict[str, dict[str, Any]]:
    out = {}
    for c in inspect_res.components:
        out[c.id] = {**inspect_res.properties.get(c.id, {}),
                     **{k: v for k, v in inspect_res.measurements.get(c.id, {}).items()
                        if k not in inspect_res.properties.get(c.id, {})}}
    return out


def record_revision(store: ProjectStore, a: Artifact, *, message: str,
                    operations: list[PlannedOperation] | None = None, insp: Any = None,
                    **fields: Any) -> ArtifactRevision:
    adapter = get_adapter(a.adapter)
    native = native_path(store, a)
    number = store.next_revision_number(a.id)
    rev_dir = store.artifact_dir(a.id) / "revisions" / f"{number:04d}"
    snapshot(native, rev_dir / "native")
    insp = insp or adapter.inspect(native, a.entry)
    previews = {}
    try:
        for name, p in adapter.preview(native, a.entry, rev_dir / "previews").items():
            previews[name] = store.rel(p)
    except Exception as exc:  # previews are evidence, not a precondition; report the failure
        fields.setdefault("measurements", {})["preview_error"] = f"{type(exc).__name__}: {exc}"
    parent = a.head_revision_id
    diff = None
    if parent:
        prev = store.get_revision(parent)
        diff = adapter.diff(store.abs(prev.snapshot_dir), rev_dir / "native", a.entry)
    rev = ArtifactRevision(
        artifact_id=a.id, number=number, parent_revision_id=parent, message=message,
        snapshot_dir=store.rel(rev_dir / "native"), previews=previews,
        component_states=insp.states,
        measurements={**insp.measurements, **fields.pop("measurements", {})},
        operations=operations or [], diff=diff or None,
        vcs_commit=insp.properties.get("repo", {}).get("head"), **fields)
    store.save_revision(rev)
    a.components = insp.components
    a.head_revision_id = rev.id
    store.save_artifact(a)
    return rev


def create_artifact(store: ProjectStore, *, name: str, adapter: str, template: str,
                    params: dict[str, Any] | None = None, artifact_type: str | None = None,
                    metadata: dict[str, Any] | None = None) -> tuple[Artifact, ArtifactRevision]:
    ad = get_adapter(adapter)
    ok, reason = ad.check_environment()
    if not ok:
        raise RuntimeError(f"adapter '{adapter}' unavailable: {reason}")
    info = ad.info()
    a = Artifact(name=name, artifact_type=artifact_type or info.artifact_types[0], adapter=adapter,
                 native_dir="", entry="", metadata=dict(metadata or {}))
    native = store.artifact_dir(a.id) / "native"
    a.native_dir = store.rel(native)
    a.entry = ad.create(native, template, params or {})
    insp = ad.inspect(native, a.entry)
    a.components = insp.components
    a.metadata["baseline"] = _baseline(insp)
    a.metadata["template"] = template
    store.save_artifact(a)
    rev = record_revision(store, a, message=f"Created from template '{template}'")
    return store.get_artifact(a.id), rev


def restore_revision(store: ProjectStore, artifact_id: str, revision_id: str) -> ArtifactRevision:
    a = store.get_artifact(artifact_id)
    target = store.get_revision(revision_id)
    if target.artifact_id != a.id:
        raise ValueError("revision belongs to another artifact")
    native = native_path(store, a)
    restore_tree(store.abs(target.snapshot_dir), native)
    get_adapter(a.adapter).after_restore(native, a.entry, f"Restore revision {target.number}")
    return record_revision(store, a, message=f"Restored revision {target.number}")
