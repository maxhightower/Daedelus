"""Application adapter interface.

An adapter wraps a real application or file format (Blender, a layered raster
format, a git repository ...). The engine only talks to this interface; it
never assumes anything about a particular medium.

Operations are *declarative* ("set taper factor to 0.3", "fill layer with
these colours") so re-running a plan converges to the same state - a
requirement for incremental execution and reproducibility.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from ..models import Component, PlannedOperation, ValidationReport


class OperationSpec(BaseModel):
    name: str
    description: str
    family: str  # semantic family used by planners (proportions, material, shape, palette_fill...)
    aspects: list[str]  # aspects of a binding this operation can express
    target_kinds: list[str]  # component kinds this op accepts ("*" = any)
    params_schema: dict[str, Any]  # JSON schema of params (excluding component_id)
    subtree: bool = False  # applies to the component and all its descendants


class AdapterInfo(BaseModel):
    name: str
    version: str
    description: str
    artifact_types: list[str]
    native_formats: list[str]
    preview_formats: list[str]
    export_formats: list[str]
    scopes: list[str]  # project / artifact / component kinds addressable
    operations: list[OperationSpec]
    measurements: list[str]  # properties usable in constraints
    validation: list[str]
    environment: dict[str, Any] = Field(default_factory=dict)
    available: bool = True
    unavailable_reason: str | None = None
    templates: list[str] = Field(default_factory=list)


class ApplyResult(BaseModel):
    ok: bool
    results: list[dict[str, Any]] = Field(default_factory=list)  # per-op {index, status, detail}
    logs: str = ""
    error: str | None = None


class InspectResult(BaseModel):
    components: list[Component]
    states: dict[str, str]  # component id -> deterministic state hash
    measurements: dict[str, dict[str, Any]] = Field(default_factory=dict)  # comp id -> props
    properties: dict[str, dict[str, Any]] = Field(default_factory=dict)  # comp id -> current params
    # components whose state hash summarises their descendants (e.g. a repository root); a change
    # to them is implied by changes inside the executed scope and is not a preservation violation
    aggregates: list[str] = Field(default_factory=list)


class AdapterError(RuntimeError):
    pass


class Adapter(ABC):
    name: str = "adapter"
    version: str = "0"

    @abstractmethod
    def info(self) -> AdapterInfo: ...

    def check_environment(self) -> tuple[bool, str]:
        return True, "ok"

    @abstractmethod
    def create(self, native_dir: Path, template: str, params: dict[str, Any]) -> str:
        """Create a new native artifact in ``native_dir``; return the entry file name."""

    @abstractmethod
    def inspect(self, native_dir: Path, entry: str) -> InspectResult: ...

    @abstractmethod
    def apply(self, native_dir: Path, entry: str, operations: list[PlannedOperation],
              context: dict[str, Any]) -> ApplyResult: ...

    @abstractmethod
    def preview(self, native_dir: Path, entry: str, out_dir: Path) -> dict[str, Path]: ...

    def export(self, native_dir: Path, entry: str, fmt: str, out_dir: Path) -> Path:
        raise AdapterError(f"{self.name} cannot export {fmt}")

    def diff(self, before_dir: Path, after_dir: Path, entry: str) -> str | None:
        return None

    def after_restore(self, native_dir: Path, entry: str, message: str) -> None:
        """Called after native files were restored from a snapshot (rollback, restore, replay)."""

    def validate(self, native_dir: Path, entry: str, checks: list[str],
                 context: dict[str, Any]) -> ValidationReport:
        return ValidationReport()

    # -- helpers -----------------------------------------------------------
    def op_spec(self, name: str) -> OperationSpec | None:
        return next((o for o in self.info().operations if o.name == name), None)

    def side_effect_scope(self, native_dir: Path, entry: str, op: PlannedOperation) -> list[str]:
        """Components an operation legitimately changes besides its target (e.g. formulas on
        other sheets rewritten by a sheet rename). They are added to the edit scope and
        reported; everything else must stay unchanged."""
        return []

    def created_kind(self, op: PlannedOperation) -> str:
        """Component kind created by a ``new`` operation ('*' = unknown/any)."""
        return "*"

    def validate_operations(self, ops: list[PlannedOperation],
                            components: list[Component]) -> list[str]:
        """Static checks of a plan against this adapter's catalogue. Components created by
        earlier operations of the same plan may be targeted by later ones."""
        errors = []
        comp_ids = {c.id: c for c in components}
        for i, op in enumerate(ops):
            spec = self.op_spec(op.op)
            if spec is None:
                errors.append(f"op {i}: unknown operation '{op.op}' for adapter {self.name}")
                continue
            if spec.target_kinds == ["new"] and isinstance(op.params.get("id"), str):
                new_id = op.params["id"]
                if new_id not in comp_ids:
                    comp_ids[new_id] = Component(id=new_id, name=new_id,
                                                 kind=self.created_kind(op),
                                                 parent_id=op.params.get("parent"))
            if op.component_id is not None:
                comp = comp_ids.get(op.component_id)
                if comp is None and spec.target_kinds != ["new"]:
                    errors.append(f"op {i} ({op.op}): unknown component '{op.component_id}'")
                elif comp is not None and "*" not in spec.target_kinds and \
                        comp.kind != "*" and comp.kind not in spec.target_kinds:
                    errors.append(f"op {i} ({op.op}): component kind '{comp.kind}' not in "
                                  f"{spec.target_kinds}")
            errors.extend(f"op {i} ({op.op}): {e}" for e in
                          check_schema(op.params, spec.params_schema))
        return errors


def check_schema(value: Any, schema: dict[str, Any], path: str = "params") -> list[str]:
    """Minimal JSON-schema subset validator (type, required, enum, min/max, items)."""
    errs: list[str] = []
    t = schema.get("type")
    types = {"object": dict, "array": list, "string": str, "boolean": bool,
             "number": (int, float), "integer": int}
    if t and t in types:
        ok = isinstance(value, types[t]) and not (t in ("number", "integer") and
                                                    isinstance(value, bool))
        if not ok:
            return [f"{path}: expected {t}, got {type(value).__name__}"]
    if "enum" in schema and value not in schema["enum"]:
        errs.append(f"{path}: {value!r} not in {schema['enum']}")
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if "minimum" in schema and value < schema["minimum"]:
            errs.append(f"{path}: {value} < minimum {schema['minimum']}")
        if "maximum" in schema and value > schema["maximum"]:
            errs.append(f"{path}: {value} > maximum {schema['maximum']}")
    if t == "string" and "pattern" in schema:
        import re

        if not re.fullmatch(schema["pattern"], value):
            errs.append(f"{path}: {value!r} does not match {schema['pattern']}")
    if t == "object":
        for req in schema.get("required", []):
            if req not in value:
                errs.append(f"{path}.{req}: required")
        props = schema.get("properties", {})
        for k, v in value.items():
            if k in props:
                errs.extend(check_schema(v, props[k], f"{path}.{k}"))
            elif schema.get("additionalProperties") is False:
                errs.append(f"{path}.{k}: unexpected property")
    if t == "array":
        if "minItems" in schema and len(value) < schema["minItems"]:
            errs.append(f"{path}: fewer than {schema['minItems']} items")
        if "maxItems" in schema and len(value) > schema["maxItems"]:
            errs.append(f"{path}: more than {schema['maxItems']} items")
        if "items" in schema:
            for i, v in enumerate(value):
                errs.extend(check_schema(v, schema["items"], f"{path}[{i}]"))
    return errs


HEX_COLOR = {"type": "string", "pattern": "#[0-9a-fA-F]{6}"}
