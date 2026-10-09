"""Typed contracts between the control plane and workers (V2).

A job asks a worker to run ONE adapter method on a snapshot of an artifact's native files.
Files travel as content-addressed blobs (sha256) described by a ``Manifest``; nothing else
(no paths, no commands) crosses the boundary.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from ..models import new_id, now_iso

ExecutionTarget = Literal["automatic", "local", "cloud_cpu", "cloud_gpu"]
Capability = Literal["cpu", "gpu"]
JobMethod = Literal["create", "inspect", "apply", "preview", "validate", "export", "diff",
                    "after_restore", "side_effect_scope"]
# methods whose output replaces the artifact's native files (published atomically)
MUTATING = {"create", "apply", "after_restore"}
JobState = Literal["queued", "leased", "running", "succeeded", "failed", "cancelled",
                   "timed_out", "conflict"]
TERMINAL = {"succeeded", "failed", "cancelled", "timed_out", "conflict"}


class FileEntry(BaseModel):
    sha256: str
    size: int
    mode: int = 0o644


class Manifest(BaseModel):
    """Exact content of a directory: relative POSIX path -> blob."""
    files: dict[str, FileEntry] = Field(default_factory=dict)

    def digest(self) -> str:
        import hashlib
        import json
        return hashlib.sha256(json.dumps({k: v.sha256 for k, v in sorted(self.files.items())},
                                         sort_keys=True).encode()).hexdigest()

    def blobs(self) -> set[str]:
        return {f.sha256 for f in self.files.values()}


class JobRequest(BaseModel):
    id: str = Field(default_factory=lambda: new_id("job"))
    project_id: str
    artifact_id: str | None = None
    adapter: str
    method: JobMethod
    entry: str = ""
    native: Manifest = Field(default_factory=Manifest)  # snapshot the method runs on
    before: Manifest | None = None  # second tree (diff)
    args: dict[str, Any] = Field(default_factory=dict)  # JSON-only method arguments
    files: dict[str, str] = Field(default_factory=dict)  # context file key -> blob sha
    requires: Capability = "cpu"
    timeout_s: float = 900
    max_attempts: int = 3
    idempotency_key: str | None = None
    # optimistic concurrency: the native manifest digest the job was planned against
    base_digest: str | None = None
    created_at: str = Field(default_factory=now_iso)
    origin: dict[str, Any] = Field(default_factory=dict)  # execution / node / user (audit)


class JobResult(BaseModel):
    ok: bool
    output: Manifest | None = None  # new native tree (mutating methods)
    artifacts: Manifest | None = None  # preview / export files
    value: dict[str, Any] = Field(default_factory=dict)  # serialised return value
    error: str | None = None
    logs: str = ""
    seconds: float = 0.0
    worker_id: str | None = None
    # V2.1 observability: per-phase seconds (fetch / run / upload / runner.*), bytes moved
    timings: dict[str, float] = Field(default_factory=dict)
    bytes_in: int = 0
    bytes_out: int = 0
    isolation: str = ""


class JobStatus(BaseModel):
    id: str
    state: JobState
    attempt: int = 0
    max_attempts: int = 3
    worker_id: str | None = None
    lease_expires_at: float | None = None
    progress: float = 0.0
    message: str = ""
    error: str | None = None
    created_at: str = ""
    updated_at: str = ""
    method: str = ""
    adapter: str = ""
    project_id: str = ""
    artifact_id: str | None = None
    requires: str = "cpu"


class WorkerInfo(BaseModel):
    id: str
    name: str
    capabilities: list[Capability] = Field(default_factory=lambda: ["cpu"])
    adapters: list[str] = Field(default_factory=list)
    version: str = ""
    last_seen: float = 0.0
    registered_at: str = Field(default_factory=now_iso)
    host: str = ""
    draining: bool = False
    credential_kind: str = ""  # provisioned | enrolled (V2.1 per-worker identity)
    isolation: dict[str, Any] = Field(default_factory=dict)  # job sandbox self-test
    deployment: str = ""  # process | container | hosted (declared by the operator)
    peer: str = ""  # network address the worker registered from


class Lease(BaseModel):
    job: JobRequest
    lease_token: str
    lease_expires_at: float
    attempt: int
