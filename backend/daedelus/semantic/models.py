"""Semantic understanding contracts (provider-independent).

An analysis describes what a *source* contains. It never assigns a role or a
target - meanings and scopes stay in user-defined ``SourceBinding``s.

Every observation records how it was obtained (``basis``) and, where it makes
sense, a confidence, so measured facts, model observations and inferences are
never presented as the same thing.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from ..models import new_id, now_iso

ObservationKind = Literal[
    "object", "geometry", "proportion", "color", "material", "spatial", "composition",
    "lighting", "style", "region", "step", "operation", "state_change", "instruction",
    "requirement", "constraint", "terminology", "quote", "structure", "uncertainty", "other",
]
Basis = Literal["measured", "observed", "inferred", "quoted"]


class SourceLocation(BaseModel):
    """Where in the source an observation comes from (all optional)."""

    page: int | None = None
    section: str | None = None
    paragraph: int | None = None
    line: int | None = None
    start_seconds: float | None = None
    end_seconds: float | None = None
    region: list[float] | None = None  # normalised [x, y, w, h]
    frame: str | None = None  # project-relative frame image


class Observation(BaseModel):
    id: str = Field(default_factory=lambda: new_id("obs"))
    kind: ObservationKind
    text: str
    value: dict[str, Any] = Field(default_factory=dict)  # structured payload (numbers, colours…)
    basis: Basis = "observed"
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    aspects: list[str] = Field(default_factory=list)  # aspects this observation is relevant to
    location: SourceLocation | None = None


class DerivedConstraint(BaseModel):
    """A measurable requirement found in a source (e.g. 'max 5000 polygons').

    It is only *enforced* when the user's binding gives the source a constraint
    meaning; the analysis merely reports it with its provenance.
    """

    property: str
    op: Literal["lte", "gte", "eq", "range", "preserve"]
    value: Any = None
    text: str
    location: SourceLocation | None = None


class Usage(BaseModel):
    calls: int = 0
    input_tokens: int | None = None
    output_tokens: int | None = None
    latency_ms: float | None = None
    cost_usd: float | None = None  # None = provider/model price unknown

    def add(self, other: "Usage") -> "Usage":
        def s(a, b):
            return None if a is None and b is None else (a or 0) + (b or 0)

        return Usage(calls=self.calls + other.calls,
                     input_tokens=s(self.input_tokens, other.input_tokens),
                     output_tokens=s(self.output_tokens, other.output_tokens),
                     latency_ms=s(self.latency_ms, other.latency_ms),
                     cost_usd=s(self.cost_usd, other.cost_usd))


class SourceAnalysis(BaseModel):
    id: str = Field(default_factory=lambda: new_id("ana"))
    source_id: str
    source_hash: str | None = None
    media_type: str
    provider: str
    model: str | None = None
    pathway: str = ""  # e.g. "image", "pdf", "video_url", "video_frames", "measured"
    # complete: the provider analysed the content; partial: only some of it (or only
    # measurements); unavailable: the content could not be analysed at all
    status: Literal["complete", "partial", "unavailable"] = "complete"
    summary: str = ""
    observations: list[Observation] = Field(default_factory=list)
    derived_constraints: list[DerivedConstraint] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    segment: dict[str, Any] | None = None  # the segment analysed, if restricted
    usage: Usage = Field(default_factory=Usage)
    request_hash: str = ""
    recorded: bool = False  # True when served from a fixture, never a live call
    fixture_origin: str | None = None  # "recorded" (captured from a live call) | "synthetic"
    created_at: str = Field(default_factory=now_iso)


class Finding(BaseModel):
    criterion: str
    status: Literal["pass", "fail", "uncertain"]
    # deterministic/constraint findings are measurements; semantic ones are judgments and can
    # never turn a failed measurement into a pass
    kind: Literal["deterministic", "constraint", "semantic"]
    detail: str = ""
    component_id: str | None = None
    binding_id: str | None = None
    evidence: dict[str, Any] = Field(default_factory=dict)
    suggestion: str = ""


class Evaluation(BaseModel):
    id: str = Field(default_factory=lambda: new_id("eval"))
    provider: str
    model: str | None = None
    findings: list[Finding] = Field(default_factory=list)
    summary: str = ""
    usage: Usage = Field(default_factory=Usage)
    recorded: bool = False

    @property
    def measured_passed(self) -> bool:
        return all(f.status == "pass" for f in self.findings
                   if f.kind in ("deterministic", "constraint") and not f.evidence.get("advisory"))

    @property
    def hard_failures(self) -> list["Finding"]:
        return [f for f in self.findings if f.kind == "constraint" and f.status != "pass"]

    @property
    def semantic_passed(self) -> bool | None:
        sem = [f for f in self.findings if f.kind == "semantic"]
        if not sem:
            return None
        return all(f.status == "pass" for f in sem)

    @property
    def passed(self) -> bool:
        return self.measured_passed and self.semantic_passed is not False

    def as_dict(self) -> dict[str, Any]:
        d = self.model_dump()
        d.update(passed=self.passed, measured_passed=self.measured_passed,
                 semantic_passed=self.semantic_passed)
        return d


ROLE_CATEGORY = {
    "drive": "reference", "blend": "inspiration", "method": "technique",
    "evaluate": "evaluation", "context": "context",
}


def role_category(role_name: str, role_use: str, hard: bool) -> str:
    """Planner-facing meaning of a binding (reference / inspiration / guideline / constraint /
    technique / evaluation / context) - derived from the user's role, never from the media."""
    if role_use == "constrain":
        return "constraint" if hard or role_name in ("constraint", "instruction") else "guideline"
    return ROLE_CATEGORY.get(role_use, "context")
