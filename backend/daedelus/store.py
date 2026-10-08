"""Persistence: one SQLite database + a filesystem tree per project.

Layout::

    <workspace>/projects/<project_id>/
        project.db
        sources/<source_id>/...          original media, previews, extracted data
        artifacts/<artifact_id>/native/  working copy of the native editable files
        artifacts/<artifact_id>/revisions/<n>/   immutable checkpoint per revision
        executions/<execution_id>/       logs, plans, resolved contexts

Domain objects are stored as validated JSON documents with a few indexed
columns; every read re-validates through the Pydantic models.
"""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
import threading
from pathlib import Path
from typing import Any, Iterable, TypeVar

from pydantic import BaseModel

from .models import (
    Artifact,
    ArtifactRevision,
    Execution,
    MediaSource,
    Project,
    ResolvedContext,
    SourceBinding,
    Workflow,
    now_iso,
)

T = TypeVar("T", bound=BaseModel)

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS project (id TEXT PRIMARY KEY, doc TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS sources (id TEXT PRIMARY KEY, created_at TEXT, doc TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS bindings (id TEXT PRIMARY KEY, source_id TEXT, target_key TEXT,
    created_at TEXT, doc TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS bindings_source ON bindings(source_id);
CREATE TABLE IF NOT EXISTS artifacts (id TEXT PRIMARY KEY, created_at TEXT, doc TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS revisions (id TEXT PRIMARY KEY, artifact_id TEXT, number INTEGER,
    doc TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS revisions_artifact ON revisions(artifact_id, number);
CREATE TABLE IF NOT EXISTS workflows (id TEXT, version INTEGER, created_at TEXT, doc TEXT NOT NULL,
    PRIMARY KEY (id, version));
CREATE TABLE IF NOT EXISTS executions (id TEXT PRIMARY KEY, workflow_id TEXT, created_at TEXT,
    doc TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS contexts (id TEXT PRIMARY KEY, execution_id TEXT, doc TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS unit_state (workflow_id TEXT, node_id TEXT, unit TEXT,
    fingerprint TEXT, revision_id TEXT, execution_id TEXT, updated_at TEXT,
    detail TEXT, PRIMARY KEY (workflow_id, node_id, unit));
CREATE TABLE IF NOT EXISTS messages (id INTEGER PRIMARY KEY AUTOINCREMENT, created_at TEXT,
    doc TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS docs (kind TEXT NOT NULL, id TEXT NOT NULL, updated_at TEXT,
    doc TEXT NOT NULL, PRIMARY KEY (kind, id));
"""

SCHEMA_VERSION = "1"


class ProjectStore:
    """Thread-safe access to one project's database and files."""

    def __init__(self, root: Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.db_path = self.root / "project.db"
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(self.db_path, check_same_thread=False, isolation_level=None)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._conn.executescript(SCHEMA)
        self._conn.execute("INSERT OR IGNORE INTO meta(key, value) VALUES ('schema_version', ?)",
                           (SCHEMA_VERSION,))

    # -- low level ---------------------------------------------------------
    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def _exec(self, sql: str, args: Iterable[Any] = ()) -> sqlite3.Cursor:
        with self._lock:
            return self._conn.execute(sql, tuple(args))

    # The connection is shared by API worker threads: rows must be fetched while holding the
    # lock, otherwise another thread's statement can reset the cursor mid-read.
    def _fetchone(self, sql: str, args: Iterable[Any] = ()) -> Any:
        with self._lock:
            return self._conn.execute(sql, tuple(args)).fetchone()

    def _fetchall(self, sql: str, args: Iterable[Any] = ()) -> list[Any]:
        with self._lock:
            return self._conn.execute(sql, tuple(args)).fetchall()

    def _one(self, model: type[T], sql: str, args: Iterable[Any] = ()) -> T | None:
        row = self._fetchone(sql, args)
        return model.model_validate_json(row[0]) if row else None

    def _many(self, model: type[T], sql: str, args: Iterable[Any] = ()) -> list[T]:
        return [model.model_validate_json(r[0]) for r in self._fetchall(sql, args)]

    def lock(self) -> threading.RLock:
        return self._lock

    # -- generic documents (boards and other presentation state) ----------------
    def put_doc(self, kind: str, doc_id: str, doc: BaseModel) -> None:
        self._exec("INSERT OR REPLACE INTO docs(kind, id, updated_at, doc) VALUES (?, ?, ?, ?)",
                   (kind, doc_id, now_iso(), doc.model_dump_json()))

    def get_doc(self, kind: str, doc_id: str, model: type[T]) -> T | None:
        return self._one(model, "SELECT doc FROM docs WHERE kind=? AND id=?", (kind, doc_id))

    def list_docs(self, kind: str, model: type[T]) -> list[T]:
        return self._many(model, "SELECT doc FROM docs WHERE kind=? ORDER BY rowid", (kind,))

    def delete_doc(self, kind: str, doc_id: str) -> None:
        self._exec("DELETE FROM docs WHERE kind=? AND id=?", (kind, doc_id))

    def get_meta(self, key: str) -> str | None:
        row = self._fetchone("SELECT value FROM meta WHERE key=?", (key,))
        return row[0] if row else None

    def set_meta(self, key: str, value: str) -> None:
        self._exec("INSERT OR REPLACE INTO meta(key, value) VALUES (?, ?)", (key, value))

    # -- paths -------------------------------------------------------------
    def abs(self, rel: str | os.PathLike[str]) -> Path:
        p = (self.root / rel).resolve()
        if self.root.resolve() not in p.parents and p != self.root.resolve():
            raise ValueError(f"path escapes project root: {rel}")
        return p

    def rel(self, path: Path) -> str:
        return Path(os.path.relpath(Path(path).resolve(), self.root.resolve())).as_posix()

    def source_dir(self, source_id: str) -> Path:
        d = self.root / "sources" / source_id
        d.mkdir(parents=True, exist_ok=True)
        return d

    def artifact_dir(self, artifact_id: str) -> Path:
        d = self.root / "artifacts" / artifact_id
        d.mkdir(parents=True, exist_ok=True)
        return d

    def execution_dir(self, execution_id: str) -> Path:
        d = self.root / "executions" / execution_id
        d.mkdir(parents=True, exist_ok=True)
        return d

    # -- project -----------------------------------------------------------
    def get_project(self) -> Project:
        p = self._one(Project, "SELECT doc FROM project LIMIT 1")
        if p is None:
            raise LookupError("project record missing")
        return p

    def save_project(self, project: Project) -> Project:
        project.updated_at = now_iso()
        self._exec("INSERT OR REPLACE INTO project(id, doc) VALUES (?, ?)",
                   (project.id, project.model_dump_json()))
        return project

    # -- sources -----------------------------------------------------------
    def save_source(self, src: MediaSource) -> MediaSource:
        src.updated_at = now_iso()
        self._exec("INSERT OR REPLACE INTO sources(id, created_at, doc) VALUES (?, ?, ?)",
                   (src.id, src.created_at, src.model_dump_json()))
        return src

    def get_source(self, source_id: str) -> MediaSource:
        s = self._one(MediaSource, "SELECT doc FROM sources WHERE id=?", (source_id,))
        if s is None:
            raise LookupError(f"source not found: {source_id}")
        return s

    def list_sources(self) -> list[MediaSource]:
        return self._many(MediaSource, "SELECT doc FROM sources ORDER BY created_at")

    def delete_source(self, source_id: str) -> None:
        with self._lock:
            self._exec("DELETE FROM bindings WHERE source_id=?", (source_id,))
            self._exec("DELETE FROM sources WHERE id=?", (source_id,))
        shutil.rmtree(self.root / "sources" / source_id, ignore_errors=True)

    # -- bindings ----------------------------------------------------------
    def save_binding(self, b: SourceBinding) -> SourceBinding:
        self.get_source(b.source_id)  # referential check
        b.updated_at = now_iso()
        self._exec(
            "INSERT OR REPLACE INTO bindings(id, source_id, target_key, created_at, doc) "
            "VALUES (?, ?, ?, ?, ?)",
            (b.id, b.source_id, b.target.key(), b.created_at, b.model_dump_json()))
        return b

    def get_binding(self, binding_id: str) -> SourceBinding:
        b = self._one(SourceBinding, "SELECT doc FROM bindings WHERE id=?", (binding_id,))
        if b is None:
            raise LookupError(f"binding not found: {binding_id}")
        return b

    def list_bindings(self, source_id: str | None = None) -> list[SourceBinding]:
        if source_id:
            return self._many(SourceBinding,
                              "SELECT doc FROM bindings WHERE source_id=? ORDER BY created_at",
                              (source_id,))
        return self._many(SourceBinding, "SELECT doc FROM bindings ORDER BY created_at")

    def delete_binding(self, binding_id: str) -> None:
        self._exec("DELETE FROM bindings WHERE id=?", (binding_id,))

    # -- artifacts ---------------------------------------------------------
    def save_artifact(self, a: Artifact) -> Artifact:
        a.updated_at = now_iso()
        self._exec("INSERT OR REPLACE INTO artifacts(id, created_at, doc) VALUES (?, ?, ?)",
                   (a.id, a.created_at, a.model_dump_json()))
        return a

    def get_artifact(self, artifact_id: str) -> Artifact:
        a = self._one(Artifact, "SELECT doc FROM artifacts WHERE id=?", (artifact_id,))
        if a is None:
            raise LookupError(f"artifact not found: {artifact_id}")
        return a

    def list_artifacts(self) -> list[Artifact]:
        return self._many(Artifact, "SELECT doc FROM artifacts ORDER BY created_at")

    # -- revisions ---------------------------------------------------------
    def save_revision(self, r: ArtifactRevision) -> ArtifactRevision:
        self._exec("INSERT OR REPLACE INTO revisions(id, artifact_id, number, doc) VALUES (?,?,?,?)",
                   (r.id, r.artifact_id, r.number, r.model_dump_json()))
        return r

    def get_revision(self, revision_id: str) -> ArtifactRevision:
        r = self._one(ArtifactRevision, "SELECT doc FROM revisions WHERE id=?", (revision_id,))
        if r is None:
            raise LookupError(f"revision not found: {revision_id}")
        return r

    def list_revisions(self, artifact_id: str) -> list[ArtifactRevision]:
        return self._many(ArtifactRevision,
                          "SELECT doc FROM revisions WHERE artifact_id=? ORDER BY number",
                          (artifact_id,))

    def next_revision_number(self, artifact_id: str) -> int:
        row = self._fetchone("SELECT MAX(number) FROM revisions WHERE artifact_id=?",
                         (artifact_id,))
        return int(row[0] or 0) + 1

    # -- workflows ---------------------------------------------------------
    def save_workflow(self, wf: Workflow, *, new_version: bool = True) -> Workflow:
        with self._lock:
            row = self._fetchone("SELECT MAX(version) FROM workflows WHERE id=?", (wf.id,))
            latest = int(row[0]) if row and row[0] is not None else 0
            if new_version or latest == 0:
                wf = wf.model_copy(update={
                    "version": latest + 1,
                    "parent_version": latest or None,
                    "created_at": now_iso(),
                })
            self._exec("INSERT OR REPLACE INTO workflows(id, version, created_at, doc) "
                       "VALUES (?, ?, ?, ?)",
                       (wf.id, wf.version, wf.created_at, wf.model_dump_json()))
            return wf

    def get_workflow(self, workflow_id: str, version: int | None = None) -> Workflow:
        if version is None:
            w = self._one(Workflow, "SELECT doc FROM workflows WHERE id=? ORDER BY version DESC "
                                    "LIMIT 1", (workflow_id,))
        else:
            w = self._one(Workflow, "SELECT doc FROM workflows WHERE id=? AND version=?",
                          (workflow_id, version))
        if w is None:
            raise LookupError(f"workflow not found: {workflow_id} v{version or 'latest'}")
        return w

    def list_workflows(self) -> list[Workflow]:
        return self._many(
            Workflow,
            "SELECT w.doc FROM workflows w JOIN (SELECT id, MAX(version) v FROM workflows "
            "GROUP BY id) m ON w.id=m.id AND w.version=m.v ORDER BY w.created_at")

    def list_workflow_versions(self, workflow_id: str) -> list[Workflow]:
        return self._many(Workflow, "SELECT doc FROM workflows WHERE id=? ORDER BY version",
                          (workflow_id,))

    # -- executions --------------------------------------------------------
    def save_execution(self, e: Execution) -> Execution:
        self._exec("INSERT OR REPLACE INTO executions(id, workflow_id, created_at, doc) "
                   "VALUES (?, ?, ?, ?)", (e.id, e.workflow_id, e.created_at, e.model_dump_json()))
        return e

    def get_execution(self, execution_id: str) -> Execution:
        e = self._one(Execution, "SELECT doc FROM executions WHERE id=?", (execution_id,))
        if e is None:
            raise LookupError(f"execution not found: {execution_id}")
        return e

    def list_executions(self, workflow_id: str | None = None) -> list[Execution]:
        if workflow_id:
            return self._many(Execution, "SELECT doc FROM executions WHERE workflow_id=? "
                                         "ORDER BY created_at DESC", (workflow_id,))
        return self._many(Execution, "SELECT doc FROM executions ORDER BY created_at DESC")

    # -- contexts ----------------------------------------------------------
    def save_context(self, ctx: ResolvedContext, execution_id: str | None = None) -> None:
        self._exec("INSERT OR REPLACE INTO contexts(id, execution_id, doc) VALUES (?, ?, ?)",
                   (ctx.id, execution_id, ctx.model_dump_json()))

    def get_context(self, context_id: str) -> ResolvedContext:
        c = self._one(ResolvedContext, "SELECT doc FROM contexts WHERE id=?", (context_id,))
        if c is None:
            raise LookupError(f"context not found: {context_id}")
        return c

    # -- incremental execution state ----------------------------------------
    def get_unit_state(self, workflow_id: str, node_id: str, unit: str) -> dict[str, Any] | None:
        row = self._fetchone("SELECT fingerprint, revision_id, execution_id, updated_at, detail FROM "
                         "unit_state WHERE workflow_id=? AND node_id=? AND unit=?",
                         (workflow_id, node_id, unit))
        if not row:
            return None
        return {"fingerprint": row[0], "revision_id": row[1], "execution_id": row[2],
                "updated_at": row[3], "detail": json.loads(row[4] or "{}")}

    def set_unit_state(self, workflow_id: str, node_id: str, unit: str, fingerprint: str,
                       revision_id: str | None, execution_id: str,
                       detail: dict[str, Any] | None = None) -> None:
        self._exec("INSERT OR REPLACE INTO unit_state VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                   (workflow_id, node_id, unit, fingerprint, revision_id, execution_id, now_iso(),
                    json.dumps(detail or {})))

    def clear_unit_state(self, workflow_id: str) -> None:
        self._exec("DELETE FROM unit_state WHERE workflow_id=?", (workflow_id,))

    # -- agent conversation --------------------------------------------------
    def add_message(self, doc: dict[str, Any]) -> dict[str, Any]:
        doc = {**doc, "created_at": doc.get("created_at") or now_iso()}
        cur = self._exec("INSERT INTO messages(created_at, doc) VALUES (?, ?)",
                         (doc["created_at"], json.dumps(doc)))
        doc["id"] = cur.lastrowid
        return doc

    def list_messages(self, limit: int = 200) -> list[dict[str, Any]]:
        rows = self._fetchall("SELECT id, doc FROM messages ORDER BY id DESC LIMIT ?",
                          (limit,))
        out = []
        for rid, doc in reversed(rows):
            d = json.loads(doc)
            d["id"] = rid
            out.append(d)
        return out


class Workspace:
    """A directory containing many projects."""

    def __init__(self, root: Path | str):
        self.root = Path(root).resolve()
        (self.root / "projects").mkdir(parents=True, exist_ok=True)
        self._stores: dict[str, ProjectStore] = {}
        self._lock = threading.Lock()

    def project_root(self, project_id: str) -> Path:
        if not project_id.replace("_", "").isalnum():
            raise ValueError("invalid project id")
        return self.root / "projects" / project_id

    def create_project(self, name: str, description: str = "") -> tuple[Project, ProjectStore]:
        project = Project(name=name, description=description)
        store = self.open(project.id, create=True)
        store.save_project(project)
        return project, store

    def open(self, project_id: str, *, create: bool = False) -> ProjectStore:
        with self._lock:
            if project_id in self._stores:
                return self._stores[project_id]
            root = self.project_root(project_id)
            if not create and not (root / "project.db").exists():
                raise LookupError(f"project not found: {project_id}")
            store = ProjectStore(root)
            self._stores[project_id] = store
            return store

    def list_projects(self) -> list[Project]:
        out = []
        for d in sorted((self.root / "projects").iterdir()):
            if (d / "project.db").exists():
                try:
                    out.append(self.open(d.name).get_project())
                except Exception:  # corrupt project dirs are skipped, not fatal
                    continue
        return sorted(out, key=lambda p: p.created_at)

    def close(self) -> None:
        with self._lock:
            for s in self._stores.values():
                s.close()
            self._stores.clear()
