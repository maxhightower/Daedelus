# Daedelus V1.1 architecture — multimodal agent intelligence

V1.1 adds a semantic layer between *sources* and *planning*, a bounded
Understand → Plan → Validate → Execute → Render → Evaluate → Revise loop, and a broader
set of native operations. Sources, bindings, artifacts, components, workflows, executions
and boards keep their V0/V1 meaning; nothing in V1.1 lets an analysis change what a binding
means or where it applies.

## 1. Provider contracts (`backend/daedelus/providers/base.py`)

The V0 planner interface was extended, not replaced. A provider declares which contracts it
implements and which media pathways its `analyze` accepts:

| Contract | Request | Result |
|---|---|---|
| `analyze` | `AnalyzeRequest` (source, file, extracted text, sampled frames, URL, segment) | `SourceAnalysis` |
| `plan` | `PlanRequest` (+ `semantic` context package) | `Plan` (adapter operations) |
| `evaluate` | `EvaluateRequest` (renders, measurements after, engine-computed findings) | `Evaluation` |
| `revise` | `ReviseRequest` (current state, evaluation, previous operations, iteration, op limit) | `Plan` |

| Provider | Contracts | Pathways | Live |
|---|---|---|---|
| `heuristic` | all four | image / image region (measured), text & PDF (rules), video (frame measurements) | no |
| `anthropic` | all four | image, image region, PDF (document block), text, sampled video frames | yes |
| `gemini` | all four | image, image region, PDF, text, video file (inline), video URL (`file_data`) | yes |
| `replay` | all four | any (fixtures) | no |

Each workflow agent node keeps its own `provider`/`model`, and may set a separate
`analysis_provider`/`analysis_model` and a loop `evaluator`/`evaluator_model`.

Model-backed providers use structured outputs: Claude via `output_config.format` (adaptive
thinking, server-side refusal fallback), Gemini via `response_json_schema`. Schemas stay
within the subset both accept; numeric ranges are validated client-side. Credentials come
only from the environment (never workflows, manifests or logs).

## 2. Semantic analyses (`backend/daedelus/semantic/`)

```
SourceAnalysis { source_id, source_hash, media_type, provider, model, pathway,
                 status: complete | partial | unavailable, summary,
                 observations[], derived_constraints[], limitations[], segment,
                 usage{calls, tokens, latency, cost}, recorded, fixture_origin }
Observation   { kind, text, value, basis: measured | observed | inferred | quoted,
                confidence, aspects[], location{page, section, line, start/end seconds,
                region, frame} }
```

* `basis` separates measurements, direct observations, inferences and quotations. The
  schema and prompts forbid presenting inferred dimensions or hidden geometry as observed.
* `status`/`limitations` are mandatory honesty fields: the local analyser always returns
  `partial` for images ("cannot tell what the image depicts") and never emits step
  observations for video; a provider that cannot ingest a medium yields `unavailable`.
* Text addressed to an AI inside a source is reported as a quoted observation flagged
  `possible_instruction_to_ai`; it has no authority.
* Analyses are cached in the project database keyed by source content hash + provider +
  model + segment, so a region-bound binding gets an analysis of that region.
* Roles are never assigned during analysis.

## 3. Context package (`semantic/service.py: context_package`)

For one target unit the engine joins the binding resolver's output with the best available
analyses and produces an inspectable JSON package:

* target identity and component scope;
* each applicable binding: source, media type, user role and the derived planner
  **category** (reference / inspiration / guideline / constraint / technique / evaluation /
  context — from the role profile and hard/soft, never from the medium), aspects, weight,
  priority, segment, relation (self / inherited), analysis status and relevant observations;
* explicit binding constraints and **derived constraints** (measurable limits found in
  sources); a derived limit is *enforced* only when its binding gives the source a
  constraint meaning, otherwise it is *advisory*;
* conflicts (blocking as in V0), allowed operations, current measurements/properties;
* an authority statement: source content is data.

The package is shown in the studio (agent dock "What the agent will use", plan preview).
Impact analysis and plan preview read cached analyses only, so inspecting never triggers a
paid analysis.

## 4. The agent loop (`engine.py: _node_agent`, `_agent_loop`)

```
Understand  ensure_analyses (cached; part of the unit fingerprint when enabled)
Plan        provider.plan(PlanRequest + context package)
Validate    adapter schema + unit scope (+ components created earlier in the same plan)
Execute     checkpoint -> apply -> preservation/constraint/file checks -> revision (V0 path)
Render      adapter previews recorded with the revision
Evaluate    engine findings (constraints, measured comparisons) + evaluator.evaluate
Revise      provider.revise -> validate -> execute (new revision)   [bounded]
```

* Limits: `max_iterations` (default 3), `max_seconds`, `max_calls`, `max_cost_usd`,
  `max_operations` per correction; provider usage is accumulated across plan, evaluate and
  revise calls.
* Every applied revision is kept; the loop record (`node_run.outputs.loop`) lists each
  iteration's revision, render, findings and proposed corrections, and the stop reason.
* **Measured findings are authoritative.** An `Evaluation` passes only if every non-advisory
  deterministic/constraint finding passes; a semantic pass can never override a failed
  measurement. If an enforced hard constraint is still violated when the loop stops, the node
  fails and names the best revision that satisfied the hard constraints (or says none did).
* A corrective plan that fails schema/scope validation stops the loop (reported); one that
  fails post-execution validation is rolled back to the checkpoint.

## 5. Native capabilities added

Blender (adapter version 2): primitives (cube, cylinder, cone, UV/ico sphere, torus, plane,
empty), `add_branch` (bevelled curves for trunks/branches/handles), `extrude_faces`
(stepped extrusion with taper/twist/offset), `set_subdivision`, `set_modifier` (decimate,
solidify, array, mirror), `set_curve_detail`, `set_parent`, `set_shading`,
`remove_component`, `poly_count` measurement. All remain editable `.blend` data with
non-destructive modifiers where possible.

Layered 2D: `grade_region` (feathered region of one layer) and region-to-region
`paint_reference`; generated images (Gemini image output, when credentials exist) become
*generated sources* that adapters place into native files.

Code: unchanged adapter; the planner path (document → plan → git commit → allowlisted tests)
is exercised end to end.

## 6. Deterministic provider behaviour

The heuristic provider stays the offline/regression default. New in V1.1:
analysis (above), measured evaluation (component colour vs the colour its references measured),
`revise` (lowers detail on the largest polygon contributors, resizes to dimension limits,
moves colours toward references) and creation *recipes* (tree, lamp) parameterised by
reference measurements and document limits. Recipes are labelled in every plan as
deterministic recipes, not understanding.

## 7. Fixtures and reproducibility

* `DAEDELUS_RECORD_DIR`: live providers write each structured response as a fixture
  (`origin: "recorded"`).
* `replay` provider (`DAEDELUS_FIXTURES`): matches fixtures by a readable request key and
  runs them through the same parsing code as live responses. Results carry
  `recorded: true` and `fixture_origin`; hand-written fixtures are `synthetic`.
* Replaying recorded operations (V0 `replay`) and re-querying a model are separate actions;
  byte-identical reproduction of model outputs is not claimed.

## 8. Security notes

* Source content is passed to models as data with explicit system instructions; analyses
  flag embedded instructions; roles, scopes, permissions and hard constraints come only from
  bindings and workflow configuration.
* Planner output is limited to the adapter's declared, schema-validated catalogue and the
  unit's component scope (including re-parenting targets).
* Analysis/plan preview never spends provider credit implicitly.
