"""Core domain model for Daedelus.

The model deliberately keeps four concepts separate:

* ``MediaSource``   - *what* a piece of input is (media type, locator, extracted content).
* ``SourceBinding`` - *what it means* (role, aspects) and *where it applies* (target scope).
* ``Artifact``      - an editable creative output with a stable component hierarchy.
* ``Workflow``      - a versioned graph that turns bindings + artifacts into new revisions.

Nothing in ingestion knows about roles, and nothing in the engine knows about
particular media or particular demonstration fixtures.
"""

from __future__ import annotations

import datetime as _dt
import uuid
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


def now_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="milliseconds")


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


# ---------------------------------------------------------------------------
# Sources
# ---------------------------------------------------------------------------


class MediaType(str, Enum):
    text = "text"
    image = "image"
    video = "video"
    video_url = "video_url"
    document = "document"
    model3d = "model3d"
    code = "code"
    audio = "audio"
    url = "url"
    file = "file"
    spreadsheet = "spreadsheet"
    presentation = "presentation"


class ProcessingState(str, Enum):
    pending = "pending"
    processing = "processing"
    ready = "ready"
    partial = "partial"  # some extraction succeeded, some did not (see warnings)
    failed = "failed"
    unsupported = "unsupported"


class Locator(_Model):
    kind: Literal["file", "url", "repo", "inline", "path"]
    path: str | None = None  # project-relative path for stored files
    url: str | None = None
    original_path: str | None = None  # for kind=path / repo: where it was imported from


class Provenance(_Model):
    origin: Literal["upload", "url", "path", "inline", "generated", "agent_message"]
    original_name: str | None = None
    url: str | None = None
    imported_at: str = Field(default_factory=now_iso)
    imported_by: str | None = None
    notes: str | None = None


class Processing(_Model):
    state: ProcessingState = ProcessingState.pending
    extractor: str | None = None
    extractor_version: str | None = None
    error: str | None = None
    warnings: list[str] = Field(default_factory=list)
    updated_at: str = Field(default_factory=now_iso)


class MediaSource(_Model):
    id: str = Field(default_factory=lambda: new_id("src"))
    name: str
    media_type: MediaType
    mime_type: str | None = None
    locator: Locator
    content_hash: str | None = None
    size_bytes: int | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    extracted: dict[str, Any] = Field(default_factory=dict)
    preview_path: str | None = None
    processing: Processing = Field(default_factory=Processing)
    provenance: Provenance
    tags: list[str] = Field(default_factory=list)
    created_at: str = Field(default_factory=now_iso)
    updated_at: str = Field(default_factory=now_iso)


# ---------------------------------------------------------------------------
# Targets and bindings
# ---------------------------------------------------------------------------


class TargetSelector(_Model):
    """Hierarchical scope: project > artifact > component (> sub-component...).

    Components are addressed by *stable* component ids that adapters persist in
    the native file (Blender custom properties, OpenRaster layer attributes,
    file paths / symbol paths for code), so selectors survive revisions.
    """

    scope: Literal["project", "artifact", "component"] = "project"
    artifact_id: str | None = None
    component_id: str | None = None

    @field_validator("artifact_id")
    @classmethod
    def _artifact_required(cls, v, info):
        scope = info.data.get("scope")
        if scope in ("artifact", "component") and not v:
            raise ValueError(f"artifact_id is required for scope={scope}")
        return v

    def key(self) -> str:
        if self.scope == "project":
            return "project"
        if self.scope == "artifact":
            return f"artifact:{self.artifact_id}"
        return f"artifact:{self.artifact_id}#{self.component_id}"

    @classmethod
    def parse(cls, key: str) -> "TargetSelector":
        if key == "project":
            return cls(scope="project")
        if not key.startswith("artifact:"):
            raise ValueError(f"invalid target key: {key}")
        rest = key[len("artifact:"):]
        if "#" in rest:
            aid, cid = rest.split("#", 1)
            return cls(scope="component", artifact_id=aid, component_id=cid)
        return cls(scope="artifact", artifact_id=rest)


class SourceSegment(_Model):
    """Optional sub-range of a source a binding refers to."""

    kind: Literal["time", "page", "region", "lines"]
    start: float | None = None  # seconds (time) / first line (lines)
    end: float | None = None
    page: int | None = None
    region: list[float] | None = None  # normalised [x, y, w, h]
    note: str | None = None


class Constraint(_Model):
    """A structured, checkable constraint carried by a binding.

    ``property`` is a measurable quantity understood by the target's adapter
    (e.g. ``height``, ``width``, ``layer_count``, ``file:<path>``).
    """

    property: str
    op: Literal["preserve", "eq", "lte", "gte", "range", "forbid_change"]
    value: Any = None
    tolerance: float = 1e-3
    description: str | None = None


class SourceBinding(_Model):
    """Assigns a meaning (role + aspects) and a scope (target) to one source.

    One source may have many bindings; a target may receive many bindings.
    """

    id: str = Field(default_factory=lambda: new_id("bind"))
    source_id: str
    role: str  # user-defined; interpreted through RoleProfile
    aspects: list[str] = Field(default_factory=list)  # what the source is meant to influence
    target: TargetSelector = Field(default_factory=TargetSelector)
    instructions: str = ""
    priority: int = 0
    strength: float = Field(default=0.6, ge=0.0, le=1.0)
    constraint: Literal["soft", "hard"] = "soft"
    constraints: list[Constraint] = Field(default_factory=list)
    allow_override: bool = False  # may a more specific binding override this one?
    segment: SourceSegment | None = None
    exclusions: list[str] = Field(default_factory=list)  # component ids excluded from the scope
    enabled: bool = True
    created_at: str = Field(default_factory=now_iso)
    updated_at: str = Field(default_factory=now_iso)


class RoleProfile(_Model):
    """How the planner should treat sources bound with a given role.

    ``use``:
      * drive     - features are applied at full binding strength (references)
      * blend     - features are blended in at reduced weight (inspiration)
      * constrain - only directives/constraints are applied (guidelines, constraints)
      * method    - selects techniques / operation variants (techniques, tutorials)
      * evaluate  - not used for planning; used by validation (benchmarks, criteria)
      * context   - presented to the agent but never applied automatically
    """

    name: str
    description: str = ""
    use: Literal["drive", "blend", "constrain", "method", "evaluate", "context"] = "context"
    weight: float = Field(default=1.0, ge=0.0, le=2.0)
    default_aspects: list[str] = Field(default_factory=list)
    builtin: bool = False


BUILTIN_ROLES: list[RoleProfile] = [
    RoleProfile(name="reference", use="drive", weight=1.0, builtin=True,
                description="Match the source closely for the bound aspects."),
    RoleProfile(name="inspiration", use="blend", weight=0.5, builtin=True,
                description="Borrow qualities loosely; blended at reduced weight."),
    RoleProfile(name="guideline", use="constrain", weight=1.0, builtin=True,
                description="Rules/directives to follow (style guides, conventions)."),
    RoleProfile(name="constraint", use="constrain", weight=1.0, builtin=True,
                description="Hard requirements the result must satisfy."),
    RoleProfile(name="technique", use="method", weight=1.0, builtin=True,
                description="How to perform an operation (procedures, tutorials)."),
    RoleProfile(name="example", use="blend", weight=0.35, builtin=True,
                description="An example of acceptable output; weak influence."),
    RoleProfile(name="evaluation", use="evaluate", weight=1.0, builtin=True,
                description="Benchmark / acceptance criteria used during validation."),
    RoleProfile(name="instruction", use="constrain", weight=1.0, builtin=True,
                description="Direct user instruction (e.g. from the agent conversation)."),
    RoleProfile(name="context", use="context", weight=0.0, builtin=True,
                description="Background information only; never applied automatically."),
]

# Aspects whose influence cascades from an ancestor scope to its descendants
# (like inherited CSS properties). Geometry-like aspects apply at the anchor.
CASCADING_ASPECTS = {"style", "color", "palette", "material", "lighting", "mood", "texture",
                     "convention", "code_style"}


# ---------------------------------------------------------------------------
# Artifacts
# ---------------------------------------------------------------------------


class Component(_Model):
    id: str  # stable, persisted in the native representation
    name: str
    kind: str  # object, group, mesh, layer, file, symbol, region ...
    parent_id: str | None = None
    native_ref: str | None = None  # e.g. blender object name, layer src, file path
    metadata: dict[str, Any] = Field(default_factory=dict)


class ValidationCheck(_Model):
    name: str
    passed: bool
    detail: str = ""
    data: dict[str, Any] = Field(default_factory=dict)


class ValidationReport(_Model):
    passed: bool = True
    checks: list[ValidationCheck] = Field(default_factory=list)

    def add(self, name: str, passed: bool, detail: str = "", **data: Any) -> None:
        self.checks.append(ValidationCheck(name=name, passed=passed, detail=detail, data=data))
        if not passed:
            self.passed = False


class PlannedOperation(_Model):
    op: str
    component_id: str | None = None
    params: dict[str, Any] = Field(default_factory=dict)
    derived_from: list[str] = Field(default_factory=list)  # binding ids the planner says it used
    rationale: str = ""


class AttributionRecord(_Model):
    """What was presented to the planner and how the planner says it used it.

    This is a *declared* attribution, not a causal measurement.
    """

    binding_id: str
    source_id: str
    source_name: str
    role: str
    aspects: list[str]
    anchor: str  # target key the binding is attached to
    unit: str  # unit (target key) it was presented for
    presented: bool = True
    applied: bool = False
    operations: list[int] = Field(default_factory=list)  # indices into revision operations
    interpretation: str = ""


class OperationResult(_Model):
    index: int
    op: str
    component_id: str | None = None
    status: Literal["applied", "failed", "skipped"]
    detail: str = ""


class ArtifactRevision(_Model):
    id: str = Field(default_factory=lambda: new_id("rev"))
    artifact_id: str
    number: int
    parent_revision_id: str | None = None
    created_at: str = Field(default_factory=now_iso)
    message: str = ""
    execution_id: str | None = None
    node_id: str | None = None
    units: list[str] = Field(default_factory=list)  # unit keys executed into this revision
    snapshot_dir: str  # project-relative directory with the native files at this revision
    previews: dict[str, str] = Field(default_factory=dict)  # name -> project-relative path
    component_states: dict[str, str] = Field(default_factory=dict)  # component id -> state hash
    measurements: dict[str, Any] = Field(default_factory=dict)
    operations: list[PlannedOperation] = Field(default_factory=list)
    operation_results: list[OperationResult] = Field(default_factory=list)
    attribution: list[AttributionRecord] = Field(default_factory=list)
    changed_components: list[str] = Field(default_factory=list)
    validation: ValidationReport | None = None
    diff: str | None = None  # unified diff for text/code artifacts
    vcs_commit: str | None = None
    origin: Literal["create", "workflow", "manual", "restore", "dependency"] | None = None


class Artifact(_Model):
    id: str = Field(default_factory=lambda: new_id("art"))
    name: str
    artifact_type: str  # model3d, image2d, code ...
    adapter: str
    native_dir: str  # project-relative working directory holding native files
    entry: str  # main native file relative to native_dir (e.g. model.blend, image.ora, '.')
    components: list[Component] = Field(default_factory=list)
    head_revision_id: str | None = None
    dependencies: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: str = Field(default_factory=now_iso)
    updated_at: str = Field(default_factory=now_iso)

    def component(self, cid: str) -> Component | None:
        return next((c for c in self.components if c.id == cid), None)

    def root_id(self) -> str | None:
        roots = [c for c in self.components if c.parent_id is None]
        return roots[0].id if roots else None

    def ancestors(self, cid: str) -> list[str]:
        """Component ids from the root down to (excluding) ``cid``."""
        chain: list[str] = []
        cur = self.component(cid)
        seen = set()
        while cur is not None and cur.parent_id and cur.parent_id not in seen:
            seen.add(cur.parent_id)
            chain.append(cur.parent_id)
            cur = self.component(cur.parent_id)
        return list(reversed(chain))

    def descendants(self, cid: str) -> list[str]:
        out: list[str] = []
        frontier = [cid]
        while frontier:
            nxt = []
            for f in frontier:
                kids = [c.id for c in self.components if c.parent_id == f]
                out.extend(kids)
                nxt.extend(kids)
            frontier = nxt
        return out


# ---------------------------------------------------------------------------
# Workflows
# ---------------------------------------------------------------------------


class RetryPolicy(_Model):
    max_attempts: int = Field(default=1, ge=1, le=10)
    backoff_seconds: float = Field(default=0.0, ge=0.0, le=600.0)


class WorkflowNode(_Model):
    id: str
    type: str
    label: str = ""
    config: dict[str, Any] = Field(default_factory=dict)
    position: dict[str, float] = Field(default_factory=lambda: {"x": 0.0, "y": 0.0})


class WorkflowEdge(_Model):
    id: str
    source: str
    source_port: str
    target: str
    target_port: str


class Workflow(_Model):
    id: str = Field(default_factory=lambda: new_id("wf"))
    name: str
    version: int = 1
    description: str = ""
    nodes: list[WorkflowNode] = Field(default_factory=list)
    edges: list[WorkflowEdge] = Field(default_factory=list)
    parameters: dict[str, Any] = Field(default_factory=dict)
    created_at: str = Field(default_factory=now_iso)
    parent_version: int | None = None
    schema_version: int = 1


# ---------------------------------------------------------------------------
# Execution
# ---------------------------------------------------------------------------


class RunStatus(str, Enum):
    pending = "pending"
    running = "running"
    waiting_approval = "waiting_approval"
    succeeded = "succeeded"
    failed = "failed"
    skipped = "skipped"  # up to date (incremental) or not selected
    cancelled = "cancelled"
    blocked = "blocked"  # an upstream failure or unresolved conflict prevents running


class UnitRun(_Model):
    unit: str  # target key
    status: RunStatus = RunStatus.pending
    fingerprint: str | None = None
    previous_fingerprint: str | None = None
    reason: str = ""
    context_id: str | None = None
    plan: dict[str, Any] | None = None
    error: str | None = None


class NodeRun(_Model):
    node_id: str
    node_type: str
    status: RunStatus = RunStatus.pending
    attempts: int = 0
    started_at: str | None = None
    finished_at: str | None = None
    units: list[UnitRun] = Field(default_factory=list)
    outputs: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None
    logs: list[str] = Field(default_factory=list)
    approval: dict[str, Any] | None = None


class Execution(_Model):
    id: str = Field(default_factory=lambda: new_id("exe"))
    workflow_id: str
    workflow_version: int
    mode: Literal["incremental", "full", "replay"] = "incremental"
    status: RunStatus = RunStatus.pending
    created_at: str = Field(default_factory=now_iso)
    started_at: str | None = None
    finished_at: str | None = None
    node_runs: list[NodeRun] = Field(default_factory=list)
    inputs_snapshot: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None
    selected_nodes: list[str] | None = None
    replay_of: str | None = None
    # V2.1: limits applied to this execution and what it consumed (budget.py)
    budget: dict[str, Any] = Field(default_factory=dict)

    def run(self, node_id: str) -> NodeRun:
        return next(r for r in self.node_runs if r.node_id == node_id)


class ResolvedEntry(_Model):
    binding: SourceBinding
    source_name: str
    media_type: str
    source_hash: str | None
    processing_state: str
    anchor: str
    relation: Literal["self", "inherited", "descendant"]
    cascading_aspects: list[str]
    applies: bool  # whether the planner may apply it at this unit
    role_use: str
    effective_weight: float
    overridden_by: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)
    summary: dict[str, Any] = Field(default_factory=dict)  # compact extracted features


class Conflict(_Model):
    property: str
    bindings: list[str]
    detail: str
    resolved: bool = False
    resolution: str = ""


class ResolvedContext(_Model):
    id: str = Field(default_factory=lambda: new_id("ctx"))
    target: TargetSelector
    target_path: list[str] = Field(default_factory=list)  # project, artifact, ancestors..., self
    entries: list[ResolvedEntry] = Field(default_factory=list)
    constraints: list[dict[str, Any]] = Field(default_factory=list)
    conflicts: list[Conflict] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    created_at: str = Field(default_factory=now_iso)

    @property
    def blocking_conflicts(self) -> list[Conflict]:
        return [c for c in self.conflicts if not c.resolved]


# ---------------------------------------------------------------------------
# Project
# ---------------------------------------------------------------------------


class ProjectSettings(_Model):
    roles: list[RoleProfile] = Field(default_factory=lambda: [r.model_copy() for r in BUILTIN_ROLES])
    allowed_commands: list[str] = Field(default_factory=lambda: ["python -m pytest"])
    default_provider: str = "heuristic"
    default_model: str | None = None
    allow_network_fetch: bool = True
    # V2: where adapter work runs by default (automatic | local | cloud_cpu | cloud_gpu);
    # nodes may override. Remote targets never fall back to local silently.
    execution_target: str = "local"


class Project(_Model):
    id: str = Field(default_factory=lambda: new_id("prj"))
    name: str
    description: str = ""
    created_at: str = Field(default_factory=now_iso)
    updated_at: str = Field(default_factory=now_iso)
    settings: ProjectSettings = Field(default_factory=ProjectSettings)

    def role(self, name: str) -> RoleProfile:
        for r in self.settings.roles:
            if r.name == name:
                return r
        # Unknown, user-typed roles are presented as context until a profile is defined.
        return RoleProfile(name=name, use="context", weight=0.0,
                           description="(no profile defined - presented as context only)")
