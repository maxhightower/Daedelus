# Daedelus V1 architecture — spatial canvas and hybrid editing

V1 keeps the V0 domain (sources, bindings, artifacts, revisions, workflows, executions, adapters)
unchanged and adds one persistent concept, the **board**, plus a manual edit service. The studio
is rebuilt around a single spatial canvas. Everything here is additive: a V0 project opens in V1
after an idempotent migration, and all V0 tests and the 40-check demonstration still pass.

## Shell

Three persistent regions and a dock; no application modes.

| Region | Component | Role |
|---|---|---|
| left | `panels/ExplorerPanel` | boards, artifacts (with component trees), sources (upload/URL/text), workflows (nodes), executions; drag onto the canvas; double-click locates |
| centre | `canvas/SpatialCanvas` + `canvas/Toolbar` | the infinite board; `FocusEditor` overlays it for Level 3 |
| right | `panels/InspectorPanel` | contextual to the selection: board, item, component, source, operation, connection, frame |
| bottom | `panels/AgentDock` | collapsible agent; its target follows the selection; impact preview before running |

## Board model (`backend/daedelus/boards.py`)

```
CanvasBoard { id, project_id, name, schema_version, revision, items[], connections[],
              saved_views[], viewport, created_at, updated_at }
CanvasItem  { id, item_type: artifact_view | source | operation | note | frame | workflow,
              resource_ref: { kind, id?, workflow_id?, node_id? },
              position, size, z_index, presentation_state, group_id, collapsed, metadata }
CanvasConnection { id, connection_type: reference | execution | dependency | annotation,
              source_item_id, target_item_id, source_anchor, target_anchor,
              domain_ref: { kind: binding | workflow_edge | none, id?, workflow_id? },
              presentation_state, derived }
```

* **An item is a view of a resource, never a copy.** `resource_ref` points at an artifact, a
  source, a workflow node (`workflow_id` + `node_id`), a workflow (its frame), or nothing
  (notes, plain frames). Several items may reference the same resource; each keeps its own
  `presentation_state` (3D camera/preset, selected layer, code file, …).
* **Positions are absolute board coordinates.** `group_id` points at a frame; React Flow's
  parent-relative coordinates are computed only at render time (`toNodes`).
* **Board edits never touch project data.** Deleting an item removes a view; the resource is
  deleted only from the Explorer, with confirmation. A view whose resource disappeared is kept
  and reported in `missing_items` (rendered as a "missing resource" node).

### Derived vs stored connections

Domain relationships have exactly one source of truth — the domain model:

| Type | Meaning | Source of truth | Created by |
|---|---|---|---|
| reference | a source informs an artifact or component | `SourceBinding` | drag source → artifact/component handle, or drop a source on a view, then the relationship dialog |
| execution | data flows between workflow nodes | workflow edges (versioned) | drag `out:port` → `in:port`; saved as a new workflow version |
| dependency | informational ordering between items | the board | drag `dep-out` → `dep-in` |
| annotation | a note points at something | the board | drag a note's handle to any item |

Reference and execution connections are **derived** on every read (`derived_connections`):
`ref:{binding}:{src_item}:{tgt_item}` (target handle `comp-in:{component}` for component scope,
`ref-in` for artifact scope) and `exe:{wf}:{edge}:{src_item}:{tgt_item}`. `save_board` drops
derived connections, rejects a stored reference/execution connection without a `domain_ref`,
and drops connections whose items are gone. Project-wide bindings are shown on the source item
rather than as an edge to every view. Deleting a reference edge deletes the binding (with
confirmation); deleting an execution edge removes the workflow edge (new version).

### Persistence and migration

* Boards are stored per project in the project SQLite database (`docs` table, kind `boards`) —
  not in browser storage. `schema_version` is on every board.
* Saves use optimistic concurrency: `PUT /boards/{id}` carries `expected_revision`; a stale
  revision returns 409 and the studio reloads the saved layout instead of overwriting.
* `ensure_boards` runs when a project is opened. If the project has no board it builds the
  default board from the existing domain (an "Artifacts" frame, a "References" frame, and one
  frame per workflow containing its nodes) and records `board_migration_version=1` in the
  project's `meta` table. Re-running is a no-op (tested), and it never edits domain data.
* The studio autosaves layout 500 ms after the last change; the toolbar shows
  saving/saved/failed.

### Layout history

Undo/redo (Ctrl+Z / Ctrl+Y) stores snapshots of `items` + stored `connections` only. It never
reverts artifact revisions, bindings or workflows — those have their own revision history
(restore) and versioning.

### Saved views

`saved_views[]` stores named viewports (Views ▾). Fit (Shift+1) and Selection (Shift+2) compute
bounds from board items rather than from rendered nodes, so they work with lazy mounting.

## Rendering

### Canvas library

React Flow (`@xyflow/react` 12, MIT) is used as the presentation layer only: it receives nodes
and edges computed from the board and reports gestures back; it owns no state that is not in
the board. It was already a V0 dependency, and its typed handles map directly onto typed
anchors (`ref-in`, `comp-in:{id}`, `in:{port}`, …), it supports parent/child grouping for frames,
viewport APIs, and viewport culling (`onlyRenderVisibleElements`).

### Renderer registry (`canvas/renderers.tsx`)

`ARTIFACT_RENDERERS` maps artifact types to a renderer with a declared component-picking
capability (`exact` / `list` / `none`): `model3d` → Three.js viewport with raycast picking,
`layered2d` → layer view with layer chips, `code` → Monaco editor/diff, anything else → file
listing. Sources render per media type (image + palette, text, PDF/document headings,
spreadsheet table, slides, video/URL status). Adding a media type means adding a renderer.

### Level of detail and lazy mounting

* `far` (< 38 % zoom): title, type, status only. `medium` (< 85 %): static preview.
  `close`: live, interactive content.
* Off-screen nodes are not mounted. Live 3D viewports are capped by a WebGL budget
  (`MAX_LIVE = 6`); further views show the static render until a slot frees.
* The Three.js viewer renders on demand (no idle loop).

### 3D component picking

The Blender worker exports glTF with `export_extras=True`, so each object's `daedelus_id`
custom property arrives as `userData.daedelus_id`. A click without drag raycasts and walks up
to the nearest ancestor carrying an id — the same persistent id used by bindings, scopes and
incremental units. Revisions created before V1 get an id-carrying GLB re-exported on demand
from the revision snapshot (`ensure_glb_with_ids`), never from the live file.

## Hybrid editing

| Level | Entry | What it does | Exit |
|---|---|---|---|
| 1 canvas | default | select, move, resize, connect, pan/zoom | — |
| 2 in place | double-click or Enter on a view | the view's body becomes interactive (orbit/pick, layer edit, Monaco); events are isolated with `nodrag nowheel nopan` | Escape / Done |
| 3 Focus | ⤢ or Shift+Enter | the item fills the canvas area with a pinned-references dock; board viewport is saved | Escape restores the exact board position and zoom |

Escape always exits one level. Inactive bodies are `pointer-events: none` so canvas gestures
cannot be stolen by content.

### Manual edit service (`backend/daedelus/editing.py`)

`POST /artifacts/{id}/edit` with adapter operations runs the same safety path as an agent:
validate operations against the adapter schema → compute scope (targets + descendants) →
checkpoint → apply → preservation check outside scope, constraint check, native file reopen →
record a revision with `origin = "manual"`; any failure rolls back to the checkpoint. A
`base_revision_id` guards against editing a stale view (409). In-place edits implemented:
3D material/roughness and leg taper, 2D layer opacity/visibility/grade, code file edits
(Monaco, diff review, commit).

### Revision sync

All views of an artifact show its head revision. The studio polls artifact heads (3 s) and
invalidates the revision cache when a head changes; cameras and other presentation state are
per view and survive the update.

## Workflows on the board

A workflow is a frame (`item_type: workflow`) containing one operation item per node. The
board is a view of the workflow: adding an operation, connecting ports, or changing a node's
configuration in the Inspector saves a **new workflow version** through the existing API.
Validate, impact preview, run, run all and approvals use the existing engine; operation nodes
show `idle / waiting / running / succeeded / failed / blocked / waiting_approval / skipped` and
per-unit status. Executions never move or reset board items.

## Office formats

Spreadsheets (`.csv .tsv .xlsx .xlsm .ods`), documents (`.docx .odt`, plus PDF) and
presentations (`.pptx .odp`) are imported as sources and previewed (table, headings/text,
slide titles) by reading their XML directly (`office.py`); they are marked view-only (`partial`)
because editing them is not part of V1.

## Mapping summary

| User action | Domain effect |
|---|---|
| move/resize/group/duplicate/delete item | board only (undoable) |
| connect source → component | `SourceBinding` (scope component) |
| connect port → port | workflow edge, new workflow version |
| + Operation | workflow node, new workflow version |
| in-place / Focus edit | artifact revision (`origin: manual`) via adapter |
| orbit a 3D view | that item's `presentation_state.camera` only |
| run / approve | existing execution engine |
