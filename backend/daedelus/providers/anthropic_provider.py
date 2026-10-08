"""Claude planner provider (Anthropic API).

Sends the resolved context, the unit's current state, the adapter's operation
catalogue (with JSON schemas) and the bound images to Claude and asks for a
structured plan. The plan is validated by the engine against the adapter's
schemas before anything is executed; Claude never touches files directly.

Credentials are read by the SDK from the environment (ANTHROPIC_API_KEY,
ANTHROPIC_AUTH_TOKEN or an ``ant auth login`` profile) - never from workflows.
"""

from __future__ import annotations

import base64
import io
import json
import os
from typing import Any

from ..models import PlannedOperation
from .base import Plan, PlanRequest, Provider, ProviderError

DEFAULT_MODEL = os.environ.get("DAEDELUS_CLAUDE_MODEL", "claude-opus-5-5")
MAX_IMAGES = 6

SYSTEM = """You are the planning agent inside Daedelus, a multimodal creation studio.
You receive ONE unit of work: a target (a whole artifact or one component of it), the
sources bound to it (each with a user-assigned ROLE, ASPECTS it should influence, a
STRENGTH and optional constraints), the target's current state, and the catalogue of
operations the target's application adapter supports.

Rules:
- Only use operations from the catalogue; params must satisfy the given JSON schema.
- Operations are declarative (they set values); prefer the minimum set of operations.
- Respect every active constraint. Never modify components outside the unit's subtree.
- Role semantics: drive = match closely; blend = borrow loosely at reduced weight;
  constrain = follow as rules; method = choose techniques; evaluate/context = do not apply.
- Only apply a source to the aspects it is bound for. Bindings marked applies=false are
  shown for context only.
- If a source could not be analysed (state partial/failed, e.g. a video whose content was
  not processed), do NOT invent its content; say so in the interpretation.
- For every binding id you were shown, give a one-sentence interpretation of how (or why
  not) you used it. Cite binding ids in each operation's derived_from.
- params_json must be a JSON object encoded as a string."""


def _plan_schema(op_names: list[str]) -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["operations", "interpretations", "notes"],
        "properties": {
            "operations": {"type": "array", "items": {
                "type": "object", "additionalProperties": False,
                "required": ["op", "component_id", "params_json", "derived_from", "rationale"],
                "properties": {
                    "op": {"type": "string", "enum": op_names},
                    "component_id": {"type": "string"},
                    "params_json": {"type": "string"},
                    "derived_from": {"type": "array", "items": {"type": "string"}},
                    "rationale": {"type": "string"},
                }}},
            "interpretations": {"type": "array", "items": {
                "type": "object", "additionalProperties": False,
                "required": ["binding_id", "interpretation"],
                "properties": {"binding_id": {"type": "string"},
                               "interpretation": {"type": "string"}}}},
            "notes": {"type": "array", "items": {"type": "string"}},
        },
    }


def _image_block(path: str) -> dict[str, Any] | None:
    try:
        from PIL import Image

        with Image.open(path) as im:
            im = im.convert("RGB")
            im.thumbnail((1024, 1024))
            buf = io.BytesIO()
            im.save(buf, format="PNG")
        return {"type": "image", "source": {"type": "base64", "media_type": "image/png",
                                            "data": base64.standard_b64encode(buf.getvalue())
                                            .decode("ascii")}}
    except Exception:
        return None


def build_prompt(req: PlanRequest) -> tuple[list[dict[str, Any]], list[str]]:
    ops = {o.name: o for o in req.adapter.operations}
    if req.allowed_ops:
        ops = {k: v for k, v in ops.items() if k in req.allowed_ops}
    unit_ids = [req.component.id] if req.component else []
    if req.component:
        unit_ids += req.artifact.descendants(req.component.id)
    payload = {
        "unit": req.unit,
        "artifact": {"id": req.artifact.id, "name": req.artifact.name,
                     "type": req.artifact.artifact_type, "adapter": req.artifact.adapter},
        "unit_component": req.component.model_dump() if req.component else None,
        "components_in_scope": [c.model_dump() for c in req.artifact.components
                                if not unit_ids or c.id in unit_ids],
        "current_properties": {k: v for k, v in req.properties.items()
                               if not unit_ids or k in unit_ids},
        "current_measurements": {k: v for k, v in req.measurements.items()
                                 if not unit_ids or k in unit_ids},
        "bindings": [{
            "binding_id": e.binding.id, "source": e.source_name, "media_type": e.media_type,
            "source_state": e.processing_state, "role": e.binding.role, "role_use": e.role_use,
            "aspects": e.cascading_aspects, "relation": e.relation, "anchor": e.anchor,
            "applies": e.applies, "weight": e.effective_weight,
            "constraint": e.binding.constraint, "instructions": e.binding.instructions,
            "notes": e.notes, "measurements": e.summary,
        } for e in req.context.entries if e.relation != "descendant"],
        "constraints": req.context.constraints,
        "conflicts": [c.model_dump() for c in req.context.conflicts],
        "node_instructions": req.instructions,
        "upstream_revisions": req.upstream,
        "operations": [{"name": o.name, "description": o.description, "aspects": o.aspects,
                        "target_kinds": o.target_kinds, "params_schema": o.params_schema,
                        "applies_to_subtree": o.subtree} for o in ops.values()],
    }
    content: list[dict[str, Any]] = []
    images = 0
    for e in req.context.entries:
        path = req.source_files.get(e.binding.source_id)
        if path and e.applies and e.media_type == "image" and images < MAX_IMAGES:
            blk = _image_block(path)
            if blk:
                content.append({"type": "text", "text": f"Image for binding {e.binding.id} "
                                                        f"('{e.source_name}'):"})
                content.append(blk)
                images += 1
    content.append({"type": "text", "text": "Plan this unit of work. Context:\n" +
                    json.dumps(payload, indent=1, default=str)})
    return [{"role": "user", "content": content}], list(ops)


class AnthropicProvider(Provider):
    name = "anthropic"
    description = "Claude (Anthropic API) plans operations from the resolved context and images."

    def available(self) -> tuple[bool, str]:
        try:
            import anthropic  # noqa: F401
        except ImportError:
            return False, "anthropic SDK not installed (pip install 'daedelus[anthropic]')"
        if not (os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN")
                or os.environ.get("ANTHROPIC_PROFILE")):
            return False, ("no Anthropic credentials detected in the environment "
                           "(ANTHROPIC_API_KEY / ANTHROPIC_AUTH_TOKEN / ANTHROPIC_PROFILE)")
        return True, "credentials detected"

    def plan(self, req: PlanRequest, client: Any = None) -> Plan:
        import anthropic

        messages, op_names = build_prompt(req)
        if not op_names:
            return Plan(provider=self.name, model=req.model or DEFAULT_MODEL,
                        notes=["no operations allowed for this unit"])
        model = req.model or DEFAULT_MODEL
        client = client or anthropic.Anthropic()
        try:
            resp = client.beta.messages.create(
                model=model,
                max_tokens=16000,
                system=SYSTEM,
                messages=messages,
                thinking={"type": "adaptive"},
                output_config={"effort": "medium",
                               "format": {"type": "json_schema",
                                          "schema": _plan_schema(op_names)}},
                betas=["server-side-fallback-2026-07-01"],
                fallbacks="default",
            )
        except anthropic.APIConnectionError as exc:
            raise ProviderError(f"Anthropic API unreachable: {exc}") from exc
        except anthropic.APIStatusError as exc:
            raise ProviderError(f"Anthropic API error {exc.status_code}: {exc.message}") from exc
        if resp.stop_reason == "refusal":
            cat = getattr(getattr(resp, "stop_details", None), "category", None)
            raise ProviderError(f"model declined to plan this unit (refusal, category={cat})")
        if resp.stop_reason == "max_tokens":
            raise ProviderError("plan truncated (max_tokens reached)")
        text = next((b.text for b in resp.content if getattr(b, "type", "") == "text"), None)
        if not text:
            raise ProviderError("model returned no plan")
        try:
            data = json.loads(text)
            ops = []
            for o in data["operations"]:
                params = json.loads(o["params_json"]) if o["params_json"].strip() else {}
                if not isinstance(params, dict):
                    raise ValueError("params_json is not an object")
                ops.append(PlannedOperation(op=o["op"], component_id=o["component_id"] or None,
                                            params=params, derived_from=o["derived_from"],
                                            rationale=o["rationale"]))
        except (ValueError, KeyError, TypeError) as exc:
            raise ProviderError(f"malformed plan from model: {exc}") from exc
        usage = getattr(resp, "usage", None)
        return Plan(provider=self.name, model=getattr(resp, "model", model), deterministic=False,
                    operations=ops,
                    interpretations={i["binding_id"]: i["interpretation"]
                                     for i in data.get("interpretations", [])},
                    notes=data.get("notes", []),
                    usage={"input_tokens": getattr(usage, "input_tokens", None),
                           "output_tokens": getattr(usage, "output_tokens", None)})
