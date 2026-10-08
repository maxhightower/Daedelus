# Daedelus V1.2 — Universal Document & Office Authoring: freeze report

## 1. Branch and code SHA

* Branch: `opus/daedelus-v1-2-universal-authoring`, based on the frozen V1.1 branch head
  `08a78aa` (V1.1 code `b05c1c5`).
* **Frozen code SHA: `71037b1`.** All evidence below was produced from exactly this commit.
  Later commits on the branch contain only this report, documentation and evidence.
* Not merged. No pull request.

## 2. Summary of completed functionality

* **Native, editable `.xlsx` / `.docx` / `.pptx` artifacts.** Each has a component hierarchy
  with stable ids, integrated with targets and bindings:
  * Workbook → sheet → table / named range / chart.
  * Document → section → paragraph / table / figure.
  * Presentation → slide → title / text / shape / image / table / chart.
* **Office operations.**
  * Spreadsheet: 12 operations.
  * Document: 11 operations.
  * Presentation: 14 operations.

  All run through checkpoint, rollback, scope-preservation, constraint and reopen checks.
  Content the adapter cannot round-trip is refused, never destroyed. This covers macros, OLE
  embeddings, external links, pivots, and xlsx charts that Daedelus did not create.
* **Formulas handled honestly.**
  * Formulas stay formulas in the native file.
  * LibreOffice recalculates a copy of the workbook.
  * Values that were not calculated are reported as such.
  * Row insertion is refused when references would break.
* **Cross-artifact component dependencies.**
  * Six route kinds; staleness is based on calculated values.
  * Selective updates: one checked revision per dependent artifact.
  * A `dependencies` workflow node, API endpoints, and board connections that are marked when
    stale.
* **Studio.**
  * Spreadsheet grid, document and slide views on the infinite canvas.
  * In-place and Focus editing: cells, paragraphs, slide text.
  * LibreOffice print previews.
  * Dependency panel (*Update dependents*) and a component link form.
* **Connector contracts.** Microsoft Graph and Google Drive (fixture-tested) and local
  LibreOffice ODF conversion. Office artifacts can also be imported from stored sources.
* **Integrated pipeline.** The *Research Analysis and Presentation Pipeline* (`daedelus
  demo-v12`) and a browser walkthrough.

Architecture: [V1_2_ARCHITECTURE.md](V1_2_ARCHITECTURE.md).

## 3. Implementation commits

| Commit | Content |
|---|---|
| `bf69e94` | Phase 1: Office adapters, dependencies, recipes, dependencies node and API, CSV comment handling and truncation flag, scope side effects, cross-sheet insert refusal |
| `71037b1` | Phase 2: studio Office views, dependency panel and connections, connectors, `demo-v12`, browser walkthrough, CI with LibreOffice |
| (this) | Freeze report, architecture, audit and register updates, evidence |

## 4. Demonstration: Research Analysis and Presentation Pipeline

Evidence: [`evidence/v1_2`](../evidence/v1_2). Result: **passed (28/28 checks)**.
Verification type: integration, using real openpyxl / python-docx / python-pptx /
LibreOffice and the deterministic local provider.

**Inputs**

* The NOAA GML Mauna Loa CO₂ annual means. This is real data, and the file is kept verbatim
  with its usage note.
* Research notes and a visual guide, both written for the demonstration.

**What one workflow produced**

* **Workbook.**
  * Cleaned data: 67 rows, 0 dropped.
  * `Change` formulas, named ranges, an Excel table, decade `AVERAGEIFS` formulas, and two
    native charts.
  * The LibreOffice-recalculated decade means **equal values computed independently from the
    CSV in Python**: 315.98 / 320.29 / … / 420.37 ppm.
* **Report.**
  * Sections quoted from the notes.
  * The results table and results sentence were filled from the workbook's named range, and
    the figure was rendered from the workbook chart.
  * References.
* **Deck.**
  * Four slides built from editable objects: text frames, a native chart and shapes. No slide
    is a picture.
  * Theme from the guide (Georgia, `#0b3d5c`, accent `#e07a1f`).
  * The chart data comes from the workbook, and the findings text comes from the report's
    discussion section.

**Selective update**

The data range was changed with a **labelled demonstration edit**: +10 ppm on 2020–2025.
This is synthetic and is not NOAA data. Afterwards:

* Exactly 4 of the 5 dependencies became stale.
* *Update dependents* changed only `results_table`, `fig_decades` and `results_text` in the
  report, and only `deck_chart` in the deck. This was checked state by state.

Timing: build 27.7 s; update 12.2 s.

Native files are in `before/` and `after/`, with LibreOffice page renders. Further evidence
is in `plans.json`, `dependencies_*.json`, `update_report.json` and `validation.json`.

## 5. Tests and walkthroughs (all at `71037b1`)

| Suite | Result |
|---|---|
| Backend pytest (real Blender 4.5.14, `DAEDELUS_REQUIRE_LIBREOFFICE=1`) | **114 passed, 4 skipped.** The 4 skips are the opt-in live-AI gates, and each prints its reason. |
| New V1.2 tests | `test_office.py` 20 passed; `test_connectors.py` 8 passed |
| V0 demonstration | 40/40 |
| V1.1 demonstrations | A, B, C, D passed; E blocked (no credentials), as at V1.1 |
| V1.2 demonstration | 28/28 |
| Studio unit tests (vitest) | 14/14 (3 new) |
| V1 spatial walkthrough (regression) | 60/60 |
| V1.1 semantic walkthrough (regression) | 19/19 |
| **V1.2 office walkthrough (new)** | **23/23** |

The V1.2 walkthrough builds the pipeline through the API, then uses the studio UI only. It
checks:

* the formula and calculated value in the grid;
* named-range highlighting;
* an in-place cell edit, after which exactly four dependents are stale, with stale
  connections drawn on the canvas;
* *Update dependents*, after which only the dependents changed;
* a paragraph edit in Focus that changed only that paragraph and made the deck's text
  dependency stale;
* LibreOffice print preview;
* the native slide chart data, and a slide text edit that changed only that text frame;
* that no page errors occurred.

Logs: `evidence/v1_2/regression/`.

## 6. CI

* CI (backend with Blender and LibreOffice, V0 / V1.1 / V1.2 demonstrations, studio, all
  walkthroughs, perf): [run 37836874690](https://github.com/maxhightower/Daedelus/actions/runs/37836874690),
  **success** on `71037b1`. The CI sets `DAEDELUS_REQUIRE_LIBREOFFICE=1`, so LibreOffice
  tests cannot skip.
* Windows desktop build: [run 37836874558](https://github.com/maxhightower/Daedelus/actions/runs/37836874558),
  **success**. The installers were built but not installed (register N1).

## 7. Screenshots (real studio, real backend)

All are in `docs/screenshots/v1_2/`:

| File | Shows |
|---|---|
| `v12_01` | Board with the three Office views and dependency connections |
| `v12_02` | Spreadsheet in place: formula bar, highlighted named range, chart |
| `v12_03` | Stale dependency connections after a cell edit |
| `v12_04` | Dependency panel |
| `v12_05` | After *Update dependents* |
| `v12_06` | Document in Focus |
| `v12_07` | Document print preview |
| `v12_08` | Slide chart data |
| `v12_09` | Slide text edit in Focus |

## 8. Freeze status (13 items)

| # | Item | Status |
|---|---|---|
| 1 | XLSX adapter (hierarchy, operations, formulas, charts, lifecycle) | Implemented; deterministically verified; integration verified (LibreOffice) |
| 2 | DOCX adapter (sections, paragraphs, tables, figures, styles, page layout) | Implemented; deterministically verified; integration verified |
| 3 | PPTX adapter (slides, text, shapes, images, tables, native charts, theme) | Implemented; deterministically verified; integration verified |
| 4 | Formula calculation | Integration verified (LibreOffice recalculation equals independently computed values). Excel calculation: **Planned / open (N11)**. |
| 5 | Stable component identity, missing / ambiguous target detection | Deterministically verified |
| 6 | No silent loss of macros / embedded objects | Deterministically verified (refusal; file byte-identical) |
| 7 | Canvas renderers with in-place and Focus editing | Integration verified (browser walkthrough 23/23) |
| 8 | Cross-artifact dependencies and selective updates | Deterministically verified; integration verified |
| 9 | Previews and exports through LibreOffice | Integration verified |
| 10 | Microsoft Graph connector | Implemented; contract verified with **synthetic** fixtures; live: **Blocked** (N8, N12) |
| 11 | Google Workspace connector | Implemented; contract verified with **synthetic** fixtures; live: **Blocked** (N8, N12) |
| 12 | Opening outputs in Microsoft Office | **Blocked / open (N5)**: no Microsoft Office; LibreOffice and the libraries reopen the files |
| 13 | AI-written narrative / live semantic authoring of Office content | **Blocked** (no credentials); deterministic recipes only, labelled as such |

Live AI verified: none. Cloud verified: not applicable to V1.2; no V1.2 feature claims cloud
execution.

## 9. Known limitations

* Report and deck prose comes only from quoted source paragraphs and fixed templates. No
  narrative is written without a live model.
* Text edits in slides rewrite the text frame's runs. Theme fonts and colours are reapplied;
  run-level formatting that was set by hand is not preserved.
* xlsx charts are always rebuilt from Daedelus specs. Workbooks containing foreign charts are
  read-only (edits are refused).
* The studio grid shows up to 200 rows × 26 columns per sheet, and says so when there are
  more.
* The Drive publish check-then-write leaves a short race window. Google-native files cannot
  be published back into.
* Previews and recalculation require LibreOffice. Without it, values are reported as not
  calculated.

## 10. Deviations from the handoff

* "Change a source-data range" is shown as an in-place edit of the workbook's data range
  rather than as a new upstream file. The edit is a labelled synthetic change (+10 ppm,
  2020–2025), because no genuinely revised NOAA file exists. +1 ppm was tried first: the
  re-rendered figure was pixel-identical, so the figure dependency correctly produced no
  state change. The shift was increased to make the figure update visible, instead of
  relaxing the check.
* Connector fixtures are hand-written from the documented response shapes. They are not
  recordings.

## 11. Dependency audit and native validation

* [DEPENDENCY_AUDIT.md](DEPENDENCY_AUDIT.md) is updated: et_xmlfile, LibreOffice 24.2,
  poppler 24.02, and the NOAA data terms (no SPDX licence stated; the owner should confirm).
* [NATIVE_VALIDATION_REGISTER.md](NATIVE_VALIDATION_REGISTER.md) adds N11 (Excel calculation)
  and N12 (live publish conflicts). N5 and N8 remain open / blocked.

## 12. Recommendations

1. Open the `after/` files in Excel, Word and PowerPoint, and record N5 and N11.
2. With a Microsoft 365 or Google test account, run import → edit → publish → conflict
   (N12).
3. With credentials, add an LLM authoring recipe for narrative sections, keeping the quoting
   recipe as the offline fallback.
