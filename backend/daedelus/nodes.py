"""Workflow node type registry: typed ports and config schemas.

The visual editor renders these definitions; the engine validates graphs
against them. Adding a node type means adding a ``NodeType`` here and a
handler in ``engine.py`` - nothing else in the system needs to change.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class Port(BaseModel):
    name: str
    type: str  # sources | artifact | revision | report | files | any
    required: bool = False
    multiple: bool = False
    description: str = ""


class NodeType(BaseModel):
    type: str
    title: str
    category: str
    description: str
    inputs: list[Port] = Field(default_factory=list)
    outputs: list[Port] = Field(default_factory=list)
    config_schema: dict[str, Any] = Field(default_factory=dict)
    defaults: dict[str, Any] = Field(default_factory=dict)


RETRY_SCHEMA = {"type": "object", "properties": {
    "max_attempts": {"type": "integer", "minimum": 1, "maximum": 10},
    "backoff_seconds": {"type": "number", "minimum": 0, "maximum": 600}}}

NODE_TYPES: dict[str, NodeType] = {n.type: n for n in [
    NodeType(
        type="sources", title="Sources", category="input",
        description="Selects registered sources (all, by id, or by media type) and makes sure they "
                    "are ingested. Roles and scopes come from bindings, not from this node.",
        outputs=[Port(name="sources", type="sources")],
        config_schema={"type": "object", "properties": {
            "source_ids": {"type": "array", "items": {"type": "string"},
                           "description": "empty = every source in the project"},
            "media_types": {"type": "array", "items": {"type": "string"}},
            "reingest_failed": {"type": "boolean"}}},
        defaults={"source_ids": [], "media_types": [], "reingest_failed": False}),
    NodeType(
        type="artifact", title="Artifact", category="input",
        description="References an editable artifact in the project.",
        outputs=[Port(name="artifact", type="artifact")],
        config_schema={"type": "object", "required": ["artifact_id"], "properties": {
            "artifact_id": {"type": "string"}}},
        defaults={"artifact_id": ""}),
    NodeType(
        type="agent", title="Agent", category="operation",
        description="Resolves the bindings that apply to each target unit, asks a planner "
                    "provider for declarative operations, executes them through the artifact's "
                    "adapter, validates the result and records a revision. Units whose inputs did "
                    "not change are skipped (incremental execution).",
        inputs=[Port(name="artifact", type="artifact", required=True),
                Port(name="sources", type="sources", description="restricts usable sources"),
                Port(name="after", type="any", multiple=True,
                     description="ordering + upstream revisions passed to the planner")],
        outputs=[Port(name="revision", type="revision")],
        config_schema={"type": "object", "properties": {
            "target_component": {"type": "string", "description": "empty = artifact root"},
            "fan_out": {"type": "boolean",
                        "description": "plan separately for sub-components that have their "
                                       "own bindings"},
            "instructions": {"type": "string"},
            "provider": {"type": "string"},
            "model": {"type": "string"},
            "allowed_ops": {"type": "array", "items": {"type": "string"}},
            "require_approval": {"type": "boolean"},
            "understand": {"type": "boolean",
                           "description": "analyse applicable sources (semantic observations) "
                                          "and give the planner the context package"},
            "analysis_provider": {"type": "string",
                                  "description": "empty = same as the planner provider"},
            "analysis_model": {"type": "string"},
            "loop": {"type": "object", "description": "Evaluate -> Revise iterations",
                     "properties": {
                         "enabled": {"type": "boolean"},
                         "max_iterations": {"type": "integer", "minimum": 0, "maximum": 10},
                         "max_seconds": {"type": "number", "minimum": 10, "maximum": 7200},
                         "max_calls": {"type": "integer", "minimum": 1, "maximum": 200},
                         "max_cost_usd": {"type": "number", "minimum": 0, "maximum": 500},
                         "max_operations": {"type": "integer", "minimum": 1, "maximum": 64},
                         "evaluator": {"type": "string"},
                         "evaluator_model": {"type": "string"},
                         "criteria": {"type": "array", "items": {"type": "string"}}}},
            "execution": {"type": "string", "enum": ["local", "cloud"]},
            "validation": {"type": "array", "items": {"type": "string"}},
            "retry": RETRY_SCHEMA}},
        defaults={"target_component": "", "fan_out": True, "instructions": "", "provider": "",
                  "model": "", "allowed_ops": [], "require_approval": False,
                  "understand": False, "analysis_provider": "", "analysis_model": "",
                  "loop": {"enabled": False, "max_iterations": 3, "max_seconds": 900,
                           "max_calls": 24, "max_cost_usd": 5.0, "max_operations": 12,
                           "evaluator": "", "evaluator_model": "", "criteria": []},
                  "execution": "local", "validation": ["file_reopens"],
                  "retry": {"max_attempts": 1, "backoff_seconds": 0}}),
    NodeType(
        type="validate", title="Validate", category="validation",
        description="Runs adapter validation (file integrity, tests, JSON validity), re-checks "
                    "component preservation and reports evaluation-role metrics.",
        inputs=[Port(name="revision", type="revision", required=True, multiple=True)],
        outputs=[Port(name="report", type="report")],
        config_schema={"type": "object", "properties": {
            "checks": {"type": "array", "items": {"type": "string", "enum": [
                "file_reopens", "tests", "json_valid", "component_preservation", "evaluation"]}},
            "fail_on_error": {"type": "boolean"},
            "evaluation_max_distance": {"type": "number", "minimum": 0, "maximum": 1}}},
        defaults={"checks": ["file_reopens", "component_preservation"], "fail_on_error": True}),
    NodeType(
        type="approval", title="Approval checkpoint", category="control",
        description="Pauses execution until a user approves or rejects.",
        inputs=[Port(name="input", type="any", multiple=True)],
        outputs=[Port(name="approved", type="any")],
        config_schema={"type": "object", "properties": {"message": {"type": "string"}}},
        defaults={"message": "Review before continuing"}),
    NodeType(
        type="export", title="Export", category="output",
        description="Exports a revision to interchange formats (e.g. glb, png, obj).",
        inputs=[Port(name="revision", type="revision", required=True, multiple=True)],
        outputs=[Port(name="files", type="files")],
        config_schema={"type": "object", "properties": {
            "formats": {"type": "array", "items": {"type": "string"}}}},
        defaults={"formats": []}),
]}


def compatible(src_type: str, dst_type: str) -> bool:
    return src_type == dst_type or "any" in (src_type, dst_type)
