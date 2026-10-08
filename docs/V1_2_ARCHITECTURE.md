# Daedelus V1.2 — Universal document & office authoring: architecture

V1.2 adds three native document artifact types (workbooks, documents and presentations), links
between components of different artifacts, and connector contracts. These additions fit the
existing abstractions:

* An Office file is an **Artifact** with a native entry (`workbook.xlsx`, `document.docx`,
  `presentation.pptx`), **Components** with stable ids, **Revisions** with checkpoints, and
  **Bindings** that attach sources by media type, purpose and target independently.
* Authoring goes through the same **Workflow / Execution** engine and the same checked
  manual-edit path as Blender, OpenRaster and code artifacts.
* Office files show up as views on the infinite **Board**. There is no separate mode screen.

## 1. Adapters (`backend/daedelus/adapters/office/`)

| Adapter | Library | Component hierarchy | Native identity |
|---|---|---|---|
| `spreadsheet` | openpyxl 3.1 | workbook → sheet → table / named range / chart | sheet title, table name, defined name; chart specs |
| `document` | python-docx 1.2 | document → section (by heading level) → paragraph / table / figure | `dd_<id>` bookmarks in the body |
| `presentation` | python-pptx 1.0 | presentation → slide → title / text / shape / image / table / chart | native `slide_id` / `shape_id` plus aliases |

A sidecar file, `native/.daedelus/ids.json`, maps component ids to native locators and stores
specs, such as chart definitions and the deck theme. Each adapter's `_reconcile` step matches
the sidecar against the file every time it is opened:

* An element that exists in the file but not in the sidecar is listed as **unregistered**. It
  gets a derived id and is never dropped.
* An id whose element has disappeared is reported as **missing**.
* A target that is ambiguous or missing makes the operation fail with the component named.

Shared properties of the three adapters:

* **Lifecycle.** Each edit goes through the following steps:
  1. A checkpoint snapshot is taken.
  2. The adapter applies the declarative operations.
  3. The result is inspected.
  4. Preservation is checked: only components in the edit's scope may change state.
  5. Active constraints are checked.
  6. The file is reopened as a validation step.
  7. The edit is recorded as a revision.

  If any step fails, the snapshot is restored and nothing is half-written.
* **Scope side effects are declared, not tolerated.** `side_effect_scope()` lets an operation
  name the components it legitimately changes. For example, a sheet rename rewrites formulas
  on the sheets that reference it, and deleting a child component restructures its container.
  Any other change outside the scope rejects the edit.
* **Unsupported content is refused, never destroyed.** If a package contains parts the
  adapter cannot round-trip, every edit is refused with the reason, and the file is left
  byte-identical. Such parts include VBA macros, OLE embeddings, external links, pivot
  caches, and charts that Daedelus did not create in xlsx (openpyxl drops those on save).
* **Formulas.** openpyxl stores formulas but never evaluates them, so the adapter treats the
  file and its values separately:
  * After each save, LibreOffice recalculates a **copy** of the workbook, using a throw-away
    profile. The resulting values are cached in the sidecar, keyed by the file hash.
  * Values from that cache are flagged `calc: true`.
  * Without LibreOffice, values are reported as *not calculated*. Formulas are never
    flattened to values.
  * `insert_rows` is refused when any formula, defined name, table or chart refers to the
    affected rows, including references from other sheets. openpyxl would not rewrite those
    references.
* **Charts.** xlsx charts are rebuilt from Daedelus specs on every save. pptx charts are
  native chart parts with an embedded workbook, edited with `update_chart_data`. Neither is
  ever a picture.
* **Previews.**
  * LibreOffice converts a copy of the file to PDF, and pdftoppm turns the PDF into page
    PNGs.
  * Each adapter also writes a structured view (`grid.json` or `structure.json`), which the
    studio draws.
  * Charts in `grid.json` carry their calculated categories and series.
* **Export / diff / validate.**
  * Export formats are xlsx/csv/pdf, docx/pdf/md and pptx/pdf.
  * Diffs are computed per cell, per block and per shape.
  * Validation checks that the file reopens, that formulas are preserved, that component
    identity holds, that LibreOffice can open the file, that values were recalculated, and
    (for decks) that slide objects are still editable.
* **Import.** The `import` template copies an existing OOXML file in unchanged. The API does
  not accept it with an arbitrary path. It is reachable only through
  `POST /artifacts/from_source`, which uses a stored source and converts ODF through
  LibreOffice, or through a connector download.

## 2. Cross-artifact component dependencies (`backend/daedelus/dependencies.py`)

An `ArtifactDependency` links a **source end** to a **target end**. Each end is an artifact
id plus a component id.

Allowed routes:

| Source component | Target component | How the target is updated |
|---|---|---|
| range / table | table | values become table rows |
| range / table | chart | values become chart categories and series |
| chart | figure / image | the chart is rendered to PNG and becomes the figure or picture |
| range | paragraph / text / title | values fill a text template (`{B9:.1f}`) |
| section / paragraph | text / paragraph | the paragraphs become text |

Validation and update rules:

* **When a link is created**, it is rejected if:
  * the route is not in the table above;
  * either component is missing (unless `options.create` asks for the target to be created
    on first sync);
  * both ends are in the same artifact;
  * it would create a cycle between artifacts.
* **Staleness.** The source end has a signature: the component states plus the `values_hash`
  of its subtree. For ranges, tables and charts that hash covers *calculated* values. A
  formula edit that changes no result therefore leaves dependents synced, and a data edit
  that changes a result makes them stale.
* **Status** is one of `synced`, `stale`, `never_synced`, `missing_source`, `missing_target`
  or `needs_recalc`.
* **Sync.**
  1. Each stale dependency produces operations.
  2. The operations are grouped per target artifact.
  3. Each group is applied through `apply_manual_edit(origin="dependency")`, which runs the
     same checkpoint / scope / validation pipeline as a manual edit.

  A target artifact therefore gets **one checked revision**, and components that are not
  dependents keep identical states. The demonstration and the browser walkthrough compare
  every state to confirm this.
* **Workflow node.** The `dependencies` node declares links from its configuration and syncs
  them; it runs after the agents that built the artifacts.
* **API.** `GET/POST/DELETE /dependencies` and `POST /dependencies/sync`.
* **Board.** Each dependency is drawn between the two artifact views as a `dependency`
  connection (`domain_ref.kind = artifact_dependency`). Stale connections are marked. Status
  is cached by dependency set and artifact heads.

## 3. Authoring without a live model (`providers/office_recipes.py`)

The deterministic provider has three fixed recipes. They are selected by the instructions
and the adapter, and each is labelled in its plan notes.

* **Analysis workbook.** The recipe:
  * cleans the bound CSV / XLSX rows, dropping missing, non-numeric and negative values and
    counting the drops;
  * writes a data sheet with a `Change` formula column;
  * adds a named range `Dataset`, an Excel table, decade `AVERAGEIFS` formulas, named ranges
    `SummaryValues` and `DecadeMeans`, and line and column charts.

  It refuses truncated extracts.
* **Report.** Sections are arranged from paragraphs **quoted** from the bound document. The
  results paragraph is a placeholder that a dependency fills. No prose is generated.
* **Deck.** The theme (colours, fonts, accent) comes from the bound design guide. The recipe
  builds title, data, findings and sources slides. The data chart and the findings text are
  filled by dependencies.

Bound source contents reach planners as `PlanRequest.source_extracts`: bounded text and sheet
rows. They are untrusted data, in line with the prompt-injection rules from V1.1. Narrative
writing needs a live model, which is blocked here.

## 4. Connectors (`backend/daedelus/connectors.py`)

Every connector implements the `OfficeConnector` contract: `status()`, `get()`, `download()`
and `upload(expected_version)`. `import_remote` and `publish_remote` link an artifact to a
remote file in `artifact.metadata.remote`.

| Connector | Hosts | Conflict detection | Notes |
|---|---|---|---|
| Microsoft Graph (drive items) | `graph.microsoft.com`; downloads may redirect only to `*.sharepoint.com`, `*.1drv.com`, `*.svc.ms`, and the bearer token is not forwarded there | `If-Match: <eTag>`, so Graph answers 412 atomically | |
| Google Drive v3 | `www.googleapis.com` | the version is compared before upload (check-then-write) | Drive has no precondition header for media updates, so a short race window remains (register N12). Google-native Docs, Sheets and Slides are **exported** to OOXML on download; publishing back into them is refused. |
| LibreOffice (local) | none | none | ODF ↔ OOXML conversion on copies |

Rules shared by the remote connectors:

* Remote ids must match `^[A-Za-z0-9!_\-.]{1,256}$` before they are put into a URL.
* Only HTTPS is allowed, and no arbitrary URL is ever fetched.
* Tokens come from `DAEDELUS_MSGRAPH_TOKEN` and `DAEDELUS_GOOGLE_TOKEN` and are never stored.
  Without them the status is `blocked`.

**Verification:** the contracts are tested with synthetic fixtures through an httpx mock
transport (`tests/test_connectors.py`). No live account was called.

## 5. Studio (`studio/src/canvas/office.tsx`, `components/DependencyPanel.tsx`)

The three Office renderers register in the artifact renderer registry. Each has a far, medium
and close level of detail on the board, an editable in-place mode, and Focus mode. The same
board item and the same presentation state (sheet, slide, selection) are used throughout.

* **Spreadsheet grid.**
  * Sheet tabs.
  * Named-range, table and chart chips; selecting one highlights its cells.
  * A formula bar that shows the native formula and the calculated value. Applying an edit
    sends `set_cells` to the sheet.
  * Charts drawn from calculated values, labelled as such.
  * Print preview with the LibreOffice pages.
* **Document.** The block flow (headings, paragraphs, tables, figures) has stable
  `data-component` ids. A selected paragraph or heading can be edited and applied as a
  revision.
* **Slides.** The LibreOffice slide render is overlaid with shape hit areas, positioned from
  native shape boxes in a measured frame. The view has a slide strip, text editing for text
  shapes, and a data view for charts.
* **Dependency panel.** It appears in the artifact inspector, the component inspector and the
  dependency connection inspector. It lists *Feeds* and *Uses* with their status, offers
  *Update dependents*, *Update from sources*, per-link Update and Unlink, and a *Link
  component* form that offers only the routes the backend allows.
* **Freshness.** When an artifact head changes, the board's derived connections are
  refetched so that staleness stays current.

## 6. Security notes

* LibreOffice always runs on copies, with a temporary user profile, and only through the
  fixed conversion filters.
* The `import` template cannot be reached with a client-supplied path. Clients also cannot
  inject `metadata.remote` when creating an artifact.
* Connector hosts are allowlisted, ids are validated, and redirects are allowed only to
  content hosts, without credentials.
* Source contents are data. Recipes quote them and never execute them.
