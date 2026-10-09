"""Provider interface: semantic analysis, planning, evaluation and revision.

A provider
- analyses a source into structured observations (``analyze``),
- turns a resolved context (which sources apply to a unit, with what role,
  aspects and constraints) plus an adapter's operation catalogue into a list
  of declarative operations (``plan``),
- critiques a result against the references and constraints (``evaluate``),
- proposes a bounded corrective plan from that critique (``revise``).

Providers never touch native files - adapters execute and the engine validates.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from pydantic import BaseModel, Field

from ..adapters.base import AdapterInfo
from ..models import (Artifact, Component, MediaSource, PlannedOperation, ResolvedContext,
                      SourceSegment)
from ..semantic.models import Evaluation, Finding, SourceAnalysis, Usage


class PlanRequest(BaseModel):
    unit: str
    artifact: Artifact
    component: Component | None
    context: ResolvedContext
    adapter: AdapterInfo
    allowed_ops: list[str] | None = None
    instructions: str = ""
    # current state from adapter.inspect for the unit's component (and its subtree)
    properties: dict[str, dict[str, Any]] = Field(default_factory=dict)
    measurements: dict[str, dict[str, Any]] = Field(default_factory=dict)
    # state captured when the artifact was created; planners blend from here so
    # re-planning with the same inputs yields the same operations
    baseline: dict[str, dict[str, Any]] = Field(default_factory=dict)
    upstream: list[dict[str, Any]] = Field(default_factory=list)
    source_files: dict[str, str] = Field(default_factory=dict)  # source id -> abs path (images)
    model: str | None = None
    # semantic context package (see semantic.context): role categories, observations relevant
    # to this unit, derived constraints. Empty when no analyses exist.
    semantic: dict[str, Any] = Field(default_factory=dict)
    # extracted contents of bound sources (text, sheet rows) - untrusted data, never instructions
    source_extracts: dict[str, dict[str, Any]] = Field(default_factory=dict)


class AnalyzeRequest(BaseModel):
    source: MediaSource
    file_path: str | None = None  # absolute path of the stored source file, if any
    text: str | None = None  # extracted text (documents / text sources)
    frames: list[dict[str, Any]] = Field(default_factory=list)  # [{time, path(abs)}] for video
    url: str | None = None  # remote media URL (e.g. a video URL) where the provider can ingest it
    segment: SourceSegment | None = None
    focus: list[str] = Field(default_factory=list)  # aspects of interest (hint only)
    model: str | None = None


class EvaluateRequest(BaseModel):
    plan_request: PlanRequest
    previews: dict[str, str] = Field(default_factory=dict)  # name -> abs path (renders)
    measurements_after: dict[str, dict[str, Any]] = Field(default_factory=dict)
    deterministic: list[Finding] = Field(default_factory=list)  # engine-computed findings
    criteria: list[str] = Field(default_factory=list)  # extra user criteria
    model: str | None = None
    iteration: int = 0  # 0 = first result, n = after the n-th correction


class ReviseRequest(BaseModel):
    plan_request: PlanRequest  # with the *current* state of the artifact
    evaluation: Evaluation
    previous_operations: list[PlannedOperation] = Field(default_factory=list)
    iteration: int = 1
    max_operations: int = 12


class Plan(BaseModel):
    provider: str
    model: str | None = None
    deterministic: bool = False
    operations: list[PlannedOperation] = Field(default_factory=list)
    interpretations: dict[str, str] = Field(default_factory=dict)  # binding id -> text
    notes: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    usage: dict[str, Any] = Field(default_factory=dict)
    recorded: bool = False  # served from a fixture (never a live model call)


def usage_from(usage: Usage | None) -> dict[str, Any]:
    return usage.model_dump() if usage else {}


class ProviderError(RuntimeError):
    pass


class NotSupported(ProviderError):
    """The provider does not implement this contract (or this media pathway)."""


class ProviderTimeout(ProviderError):
    """The model API did not answer within the call timeout (after the SDK's retries)."""


class ProviderRateLimited(ProviderError):
    """The model API refused the call for rate/quota reasons (after the SDK's retries)."""

    def __init__(self, msg: str, retry_after: float | None = None):
        super().__init__(msg)
        self.retry_after = retry_after


class ProviderOverBudget(ProviderError):
    """The execution budget does not allow this call; nothing was sent to the provider."""


class Provider(ABC):
    name = "provider"
    description = ""

    def available(self) -> tuple[bool, str]:
        return True, "ok"

    # contracts this provider implements (subset of analyze/plan/evaluate/revise)
    contracts: tuple[str, ...] = ("plan",)
    # media pathways analyze() supports, e.g. ("image", "text", "pdf", "video_url")
    pathways: tuple[str, ...] = ()
    live: bool = False  # True when calls reach a remote model

    def describe(self) -> dict[str, Any]:
        ok, why = self.available()
        return {"name": self.name, "description": self.description, "available": ok,
                "detail": why, "contracts": list(self.contracts),
                "pathways": list(self.pathways), "live": self.live}

    @abstractmethod
    def plan(self, req: PlanRequest) -> Plan: ...

    def analyze(self, req: AnalyzeRequest) -> SourceAnalysis:
        raise NotSupported(f"provider '{self.name}' does not analyse sources")

    def evaluate(self, req: EvaluateRequest) -> Evaluation:
        """Default: no semantic judgement - the engine's deterministic findings stand alone."""
        return Evaluation(provider=self.name, findings=list(req.deterministic),
                          summary="deterministic checks only (no semantic evaluation)")

    def revise(self, req: ReviseRequest) -> Plan:
        """Default: re-plan with the failed findings appended to the instructions."""
        failed = [f for f in req.evaluation.findings if f.status != "pass"]
        extra = "\n".join(f"- [{f.kind}] {f.criterion}: {f.detail} {f.suggestion}".rstrip()
                           for f in failed)
        pr = req.plan_request.model_copy(deep=True)
        pr.instructions = (pr.instructions + "\n\nCorrect these issues from the previous "
                           f"attempt (iteration {req.iteration}):\n" + extra).strip()
        return self.plan(pr)

    def ops_for(self, req: PlanRequest) -> dict[str, Any]:
        ops = {o.name: o for o in req.adapter.operations}
        if req.allowed_ops:
            ops = {k: v for k, v in ops.items() if k in req.allowed_ops}
        return ops
