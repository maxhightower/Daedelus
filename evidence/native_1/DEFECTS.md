# NATIVE-1 defect log (working file; summarised in docs/NATIVE_1_REPORT.md)

Baseline build: GitHub Actions run 37916449085, commit `4f4c5f9` (V2.1.1 app code `43ce9d6`
plus the hosted-harness correction), NSIS `Daedelus Studio_0.1.0_x64-setup.exe`
SHA-256 `f6b3b736b3f9950141312b090837965a0a2f607f10f8b81257bd39dcd351e07e`.
Machine: Windows 11 Home 26200, RTX 3070, WebView2 155.0.4283.45, Blender 5.1.2 / 4.4.3,
Microsoft 365 apps 16.0.20430, no LibreOffice.

## D-001 HTML5 drag-and-drop does nothing inside the packaged WebView2 window

* Severity: **high**. Explorer → canvas drag is the only way to place an existing artifact
  on a board ("'Table' is not on this board. Drag it from the explorer."); the same gesture
  creates source→artifact/component bindings (Interaction help text). Without it a packaged
  Windows user cannot put an API- or workflow-created artifact on the board, nor bind a
  reference by dragging.
* Layer: packaging (Tauri window configuration) — suspected.
* Reproduction (baseline): install NSIS build, create project, register sources with
  "+ files", create an artifact (no UI path exists, see G-001), then drag the artifact row
  from the Explorer onto the canvas. Tried an instant drag and a slow drag with 6
  intermediate pointer moves: no drop, no notification, board unchanged.
* Hypothesis: Tauri v2 windows default `dragDropEnabled: true`, under which the Windows
  shell intercepts OLE drag/drop for native file-drop events and WebView2 never sees HTML5
  dragstart/drop. `tauri.conf.json` does not set it, and the frontend never listens for
  Tauri drag-drop events. Verification: A/B rebuild of the shell with only that setting changed.

## D-002 Non-cp1252 text in any bound source makes every agent node fail on Windows

* Severity: **high**. Agent planning crashes with
  `UnicodeEncodeError: 'charmap' codec can't encode character '→'` as soon as a bound
  guideline contains a character outside Windows-1252 (→, ≤, CO₂, CJK, emoji...).
* Layer: backend. `engine.py:840` writes `PlanRequest.model_dump_json()` (raw Unicode) with
  `Path.write_text()` and no encoding; Python 3.12 on Windows defaults to the ANSI code page
  outside UTF-8 mode. Same pattern in `scenarios.py:314` (demo report; fails
  `test_multimodal_demo_scenarios` natively), `engine.py:1468` (replay read),
  distributed `worker.py`/`runjob.py` job files, Office `.md`/`.csv` exports, repository
  ingest (`ingest.py:679-688`, silent mis-decoding of UTF-8 source files).
* Reproduction (baseline, packaged sidecar): `tools/native_validation/repro_unicode.py`.
  Unicode guideline: execution `failed`, `model_agent` and `paint_agent` fail with the
  error above, `code_agent`/`validate`/`export` blocked
  (`workflows/d002_unicode_baseline_packaged.json`). ASCII control: all agents succeed
  (`workflows/d002_ascii_control_packaged.json`).
* Why CI missed it: Windows CI has no Blender, so the demo scenario test is skipped, and no
  Windows test exercises a non-ASCII plan request.

## D-003 Packaged app runs code-artifact tests with the Microsoft Store `python` stub

* Severity: **medium**. Validation of code artifacts fails with
  "`python -m pytest -q` exited 9009 / Python was not found; run without arguments to install
  from the Microsoft Store" although Python 3.12 with pytest is installed (`py -3.12`).
* Layer: backend (interpreter discovery for frozen builds). `codefiles.py:82-90` returns the
  first of `which("python3")`, `which("python")`, `which("py")`. On a default Windows 11 install
  the first two are App Execution Alias stubs under `%LOCALAPPDATA%\Microsoft\WindowsApps`.
* Reproduction (baseline, packaged sidecar): ASCII control run above; `validate` reports
  `Scene manifest: tests=FAIL` with the Store message (`workflows/d002_ascii_control_packaged.json`).
* Why CI missed it: GitHub's windows-latest image has a real `python` on PATH ahead of the
  WindowsApps aliases.

## D-004 A second Daedelus instance can run against the same workspace

* Severity: **medium** (data-integrity risk). Launching Daedelus Studio while it is already
  running starts a second shell and a second sidecar on the same
  `%APPDATA%\org.daedelus.studio\workspace` (observed: two `daedelus-studio.exe`, each with its
  own `daedelus-server.exe` pair). Both serve and autosave the same boards and project
  databases; board revisions from one window are not seen by the other until reload.
* Layer: packaging/desktop shell (no single-instance guard). Observed by accident when a
  close click did not register before the next launch. Not exploited further, to avoid
  corrupting the test workspace.

## D-005 Grouping inside a frame, then moving the outer frame, scrambles the layout

* Severity: **medium** (layout corruption; items end up hidden under others). Shared
  frontend, not WebView2-specific.
* Reproduction (baseline packaged app, demo project): shift-select two cards inside the
  *References* frame, click **+ Frame**, then drag the *References* frame by its header.
  Result: *References* and its six cards move, the new frame and its two cards stay behind,
  overlapping other cards, and the persisted board records *References* (1130×560) as a
  child of the new frame (569×300) (`board_items.py` dump in the report).
* Causes: (1) `Toolbar.addFrame` creates the frame at top level (`group_id: null`) even when
  its members share a parent frame, so it is not moved with that parent; (2)
  `onNodeDragStop` regrouping chooses the smallest frame containing the dragged item's
  centre without requiring it to be larger than the item, so a large frame can be adopted
  by a small frame it overlaps.

## Gaps (not defects in the native layer)

* **G-001** No Studio UI path creates a new artifact (`api.createArtifact` is never called) or
  a second workflow; artifacts come only from the demo builder, the API, Office
  `from_source` (API only) and connectors. Shared frontend gap, not WebView2-specific.

## Harness notes (not product defects)

* First project-name attempt saved "Untitled project": my first click only activated the
  window, so the pasted text never reached the input. Reproduced correctly on retry
  (name typed, verified visible, saved as "N1 Lifecycle Test").
* `tests/test_security_v21.py::test_repository_symlinks_cannot_pull_in_outside_files` fails
  natively with WinError 1314: creating symlinks needs Developer Mode or elevation, which
  this account lacks and GitHub's runner has. Test-environment precondition, not a product
  defect; the test should skip when symlink creation is not permitted.
