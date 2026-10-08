# V1.2 demonstration

Duration 44.2 s.

## Research analysis and presentation pipeline

Status: **PASSED** - verification type: integration

- [x] pipeline workflow succeeded
- [x] node build_wb succeeded
- [x] node build_doc succeeded
- [x] node build_deck succeeded
- [x] node deps succeeded
- [x] node validate succeeded
- [x] workbook hierarchy: sheets, table, named ranges, charts
- [x] summary values are live formulas in the native file
- [x] formula results (LibreOffice recalculation) match decade means computed independently from the CSV
- [x] two native Excel charts in the workbook package
- [x] report hierarchy: title, sections, paragraphs, results table, figure, references
- [x] report table carries the workbook's calculated values
- [x] results sentence filled from the named range
- [x] report body paragraphs are quoted from the research notes (no generated prose)
- [x] 4 slides built from editable objects (text frames, native chart, shapes; no slide is a picture)
- [x] slide chart data comes from the workbook (data-driven, editable)
- [x] theme from the visual guide applied (title colour and font)
- [x] 5 component dependencies, all synced after the run
- [x] native validation of CO2 analysis (workbook.xlsx)
- [x] native validation of CO2 report (document.docx)
- [x] native validation of CO2 briefing (presentation.pptx)
- [x] data range edit is a checked revision of the workbook
- [x] exactly the dependents of the changed range went stale
- [x] update created one revision per dependent artifact
- [x] report: only the table, figure and results sentence changed
- [x] deck: only the data chart changed (findings text untouched)
- [x] updated sentence shows the recalculated 2020s mean
- [x] all dependencies synced again

Notes:
- deterministic recipe 'analysis workbook' (heuristic provider): cleaning, formulas and charts follow a fixed template parameterised by the bound data
- value column: 'CO2 (ppm)', 67 clean rows, 8 decades
- The data change is a demonstration edit (+10.0 ppm on 2020-2025), not NOAA data; it exists only to show selective dependency updates.

Connectors:
- Microsoft 365 (Graph): blocked: DAEDELUS_MSGRAPH_TOKEN is not set (fixture-tested (no live account))
- Google Workspace (Drive): blocked: DAEDELUS_GOOGLE_TOKEN is not set (fixture-tested (no live account))
- LibreOffice (local conversion): available (integration-tested with LibreOffice)
