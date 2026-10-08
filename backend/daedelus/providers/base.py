"""Planner provider interface.

A provider turns a resolved context (which sources apply to a unit, with what
role, aspects and constraints) plus an adapter's operation catalogue into a
list of declarative operations. Providers never touch native files - adapters
execute and the engine validates.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from pydantic import BaseModel, Field

from ..adapters.base import AdapterInfo
from ..models import Artifact, Component, PlannedOperation, ResolvedContext


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


class Plan(BaseModel):
    provider: str
    model: str | None = None
    deterministic: bool = False
    operations: list[PlannedOperation] = Field(default_factory=list)
    interpretations: dict[str, str] = Field(default_factory=dict)  # binding id -> text
    notes: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    usage: dict[str, Any] = Field(default_factory=dict)


class ProviderError(RuntimeError):
    pass


class Provider(ABC):
    name = "provider"
    description = ""

    def available(self) -> tuple[bool, str]:
        return True, "ok"

    @abstractmethod
    def plan(self, req: PlanRequest) -> Plan: ...

    def ops_for(self, req: PlanRequest) -> dict[str, Any]:
        ops = {o.name: o for o in req.adapter.operations}
        if req.allowed_ops:
            ops = {k: v for k, v in ops.items() if k in req.allowed_ops}
        return ops
