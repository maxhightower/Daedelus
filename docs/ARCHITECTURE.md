# Daedelus architecture (V0)

Daedelus is a configurable graph of **sources**, **meanings**, **operations**, **artifacts** and
their **relationships**. No medium, provider or pipeline is primary; the demonstration fixtures
(a table, a background, a manifest) are data passed through generic interfaces.

```
               ┌──────────── studio (React, React Flow, Three.js, Monaco; Tauri shell) ────────────┐
               │  workspace · source library · binding inspector · workflow editor · artifacts ·   │
               │  executions · agent conversation                                                  │
               └───────────────────────────────▲───────────────────────────────────────────────────┘
                                               │ HTTP (FastAPI)  — same API in browser and desktop
┌──────────────────────────────────────────────┴───────────────────────────────────────────────────┐
│ backend (Python)                                                                                  │
│                                                                                                   │
│  ingest.py ── MediaSource ──┐            bindings.py                     engine.py                │
│  (what a source IS:         │   SourceBinding (what it MEANS + WHERE)    Workflow graph →         │
│   text, image, video,       ├──► resolve(target) → ResolvedContext ───► units → fingerprints →    │
│   video URL, PDF, 3D,       │    inherited / own / descendant,          provider.plan() →         │
│   code, audio, url)         │    cascading aspects, overrides,          adapter.apply() →         │
│                             │    constraints, conflicts                  validate → revision      │
│  store.py: SQLite + files per project (sources/, artifacts/<id>/native|revisions/, executions/)   │
│                                                                                                   │
│  providers/: heuristic (deterministic, local) · anthropic (Claude, structured output)           │
│  adapters/:  blender (headless worker process) · layered2d (OpenRaster) · code (git)             │
└───────────────────────────────────────────────────────────────────────────────────────────────────┘
```

## Separation of concerns

| Concept | Module | Knows about |
|---|---|---|
| `MediaSource` | `models.py`, `ingest.py` | media type, locator, hash, metadata, measurements, previews, processing state, provenance. **Never** roles or targets. |
| `SourceBinding` | `models.py`, `bindings.py` | one meaning of one source: `role`, `aspects`, `target` (project / artifact / component), `strength`, `priority`, hard/soft, structured `constraints`, `segment`, `exclusions`. A source may have many bindings. |
| `RoleProfile` | `models.py` (project settings) | how a role is used by planners: `drive`, `blend`, `constrain`, `method`, `evaluate`, `context`, with a weight. Roles are user-definable; unknown roles are treated as context and flagged. |
| `Artifact` / `Component` | `models.py`, `artifacts.py` | stable component ids persisted in native files (Blender custom property, OpenRaster layer attribute, file path / Python symbol). |
| `Workflow` | `models.py`, `nodes.py`, `engine.py` | versioned graph of typed nodes and edges; exportable/importable JSON. |
| `Execution` | `models.py`, `engine.py` | workflow version, inputs snapshot, per-node and per-unit status, fingerprints, plans, logs, approvals, outputs. |

## Binding resolution (`bindings.resolve`)

For a target unit the resolver walks the scope path `project → artifact → ancestors → component`:

* **own** (`self`): bindings anchored at the unit (artifact-scoped bindings are "own" for the root component).
* **inherited**: bindings on ancestors; only *cascading* aspects (style, colour, palette, material,
  lighting, mood, texture, conventions) flow down. Geometry-like aspects apply at their anchor only.
* **descendant**: bindings below the unit are shown for inspection but applied at their own unit.

Overrides: a more specific binding overrides an inherited one per aspect when the inherited one is soft
(and priorities allow), or when it is hard **and** `allow_override` is set **and** the specific one has a
higher priority. Otherwise competing hard bindings produce an **unresolved conflict**; incompatible
hard constraints (`eq 0.9` vs `preserve`) also conflict. Unresolved conflicts block execution of the
unit with an explicit message — nothing is silently discarded. Soft constraints yield to hard ones and
the resolution is recorded.

## Incremental, scoped execution (`engine.py`)

An `agent` node targets an artifact (or a component). With `fan_out`, every component that has its own
bindings becomes an additional *unit*. For each unit the engine fingerprints everything that can
influence the result: binding definitions, source content hashes and extracted measurements, role
profiles, constraints, conflicts, node configuration, planner/adapter versions, upstream revisions and
ancestor-unit fingerprints. A unit runs only when its fingerprint changed or its subtree was modified
outside the node (detected from component state hashes). This is what makes "change the leg reference →
only legs re-run" a property of the engine rather than of the demo.

Execution of stale units: plan (provider) → static checks (catalogue, JSON schema, unit scope) →
optional approval pause → checkpoint native files → apply (retry with rollback) → inspect → verify that
only components inside the executed units changed (`component_preservation`) and constraints hold →
adapter validation → record revision (snapshot, previews, diff, operations, results, attribution,
validation) → store fingerprints. Any failure restores the checkpoint and is reported verbatim.

Reproduction (`Engine.replay`): re-plans every executed unit from the saved plan request (deterministic
providers) and re-applies the recorded operations to the parent revision snapshot in a scratch copy,
comparing operations and component state hashes.

## Adapters

`Adapter` declares artifact types, native/preview/export formats, operations (with families, aspects,
target kinds and JSON schemas), measurable properties and validation capabilities, and implements
`create / inspect / apply / preview / export / diff / validate`. Operations are declarative so re-running
converges. Current adapters:

* **blender** — runs `blender --background` with `adapters/blender_worker.py`; components are objects
  with a `daedelus_id` property; non-destructive modifiers (taper, bevel), Principled materials,
  proportional scaling; Cycles CPU preview render + GLB export; deterministic state hashes of evaluated
  geometry, transforms, modifiers and materials.
* **layered2d** — OpenRaster (`.ora`, opens in Krita/GIMP/MyPaint); layers are components; content ops
  set a layer's base pixels, `color_grade` is stored non-destructively and re-rendered.
* **code** — a git repository; files and top-level Python symbols are components; each applied plan is a
  commit; unified diffs; tests run only if the command is in the project's `allowed_commands`.

## Planner providers

* **heuristic** (default, deterministic): maps measured source features onto operation *families*
  (`proportions`, `shape`, `material`, `palette_fill`, `color_grade`, `reference_paint`,
  `structured_update`) according to role use and aspects, blending from the artifact's baseline. It
  reads `key: value` directives from text. It does not understand images semantically and says so.
* **anthropic** (Claude): sends the resolved context, unit state, operation catalogue and bound images,
  and receives a JSON-schema-constrained plan (structured outputs; server-side refusal fallback enabled).
  Plans are validated exactly like heuristic plans. Requires credentials in the environment; not
  exercised live in this repository's CI (tested against a fake client).

## Desktop packaging

`studio/src-tauri` is a Tauri 2 shell. On start it launches the PyInstaller-frozen backend
(`daedelus-server`, built by `backend/packaging/build_sidecar.py`) as a sidecar on a free localhost port
with a workspace in the app-data directory, and exposes the URL to the webview via the `backend_url`
command. The same backend serves the built studio for browser use (`daedelus serve`).

## Prior art reviewed (not incorporated)

* **DCC-MCP** (`dcc-mcp-core`, `dcc-mcp-blender`; PyPI metadata: MIT; the Blender Extensions ZIP is
  GPL-3.0-or-later): MCP servers embedded in DCC hosts. Compatible in principle as a future
  *transport* behind a Daedelus adapter (e.g. live sessions instead of headless batch). Not used in V0
  because the headless worker gives deterministic, testable batch execution in CI without a running
  DCC; API stability/reliability not yet evaluated.
* **Blender Agent Bridge** (`blender-bridge` on PyPI, GPL-3.0-or-later): MCP bridge for Blender. Its GPL
  license means it should only ever be used as a separate process, not linked into Daedelus.
* **Blender Agent Studio**: no canonical repository could be identified (only a skill listing under
  `ifBars/blender-agent-studio`); not evaluated.
