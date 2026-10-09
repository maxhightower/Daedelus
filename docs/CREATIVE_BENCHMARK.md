# Creative benchmark (v2.1.1)

**Question:** can real multimodal models turn unfamiliar references and creative instructions
into successful, editable artifact modifications through Daedelus's existing operation system?

The benchmark lives in `backend/daedelus/bench_v21.py`, the same harness as V2.1, extended and
versioned rather than replaced. It runs through `daedelus bench-v21` (any provider) or as the
`bench` stage of `daedelus live-run` (live providers; see `docs/LIVE_AI_TESTING.md`).

## 1. Tasks

All inputs are licensed assets in `backend/daedelus/demo_assets/v21` (`ATTRIBUTION.json`).
Input hashes are recorded per run. The source code contains no object-specific recipes, only
sources, bindings, instructions, constraints and checks.

| Task | Objective | Inputs and bindings | Checks (`2.1.1` = added in this version) |
|---|---|---|---|
| A | image-guided 3D creation | CC0 coffee photo (region: the cup; reference: geometry, shape, proportion), project-authored stylized illustration (inspiration: style, colour), written spec as a **hard** constraint (height 0.1 m, ≤ 600 polygons) → an empty Blender artifact | execution finished; native object with geometry created; `.blend` reopens; render produced; polygon budget met (measured, > 0); height met (measured); GLB export with mesh geometry (2.1.1) |
| B | component-specific 3D modification | 5-component launch scene; public-domain launch photo bound **only** to `body` (colour, material) | execution finished; bound component changed; unrelated components preserved exactly (component-state digests); new revision recorded (2.1.1); native `.blend` reopens (2.1.1) |
| C | layered 2D modification | 3-layer OpenRaster poster; CC0 cat photo bound **only** to `subject` | execution finished; bound layer changed; other layers preserved exactly; valid `.ora` (zip + `stack.xml`); still layered with the same layers (2.1.1) |
| D | document-guided coding | 2-file repository with a stub and 2 visible tests; written requirement as a hard constraint | schema-valid plan; real git diff of `textutil.py`; allowlisted tests pass; **held-out specification cases pass** (8 cases derived from the requirement, never shown to the model) (2.1.1); only the permitted file changed (2.1.1) |
| E | video-guided technique | a real video (file or approved HTTPS URL) bound as a technique to a Blender component | the model analysed video content (not metadata); timestamped observations; procedural steps identified (2.1.1); a validated operation resulted. BLOCKED without a video or a provider that can read video |

A valid file with meaningless geometry does not pass task A: geometry must exist, be measured
within the budget, and export as a non-empty mesh. Task D's held-out cases separate a correct
implementation from a patch that only satisfies the two visible tests (tested with a
deliberately test-gaming patch in `tests/test_bench_v211.py`).

## 2. Modes

Every runnable task runs twice, on **fresh, equivalent projects**, with the same inputs and
criteria:
* **single:** one planning pass, no evaluation loop;
* **iterative:** plan → execute → evaluate → revise, at most 3 corrections, within the same
  per-run budget.

The engine keeps every revision and names the best valid one; a failed correction never
replaces the last valid revision. The comparison reports `improved` / `unchanged` / `regressed`
per task. An iterative run that passes fewer checks than the single pass is reported as a
**regression**.

## 3. Evaluation categories

| Category | What | Authority |
|---|---|---|
| Deterministic | file reopens, geometry exists, measured polygons and height, component-state digests, layer list, git diff scope, test results, held-out cases | **authoritative** for measurable requirements; the only input to PASS/FAIL |
| Model-based assessment | after each live run, a **separate** evaluation pass (`Engine.assess`) over the final revision: relevance to the bound references, style, plausibility and completeness | recorded as `model_assessment` with `authoritative: false` and its independence: `cross-provider` when another provider was requested, otherwise "same provider, separate call (not independent)". Never counted as a check, never fills the rubric |
| Human review | renders of every revision (`<task>_<mode>_rev<n>.png`), before/after per run | the rubric dimensions `relevance`, `structural_fidelity` and `usability` stay **empty** until a person scores them |

The rubric dimensions `constraint_adherence` and `preservation` are filled only from
measurements.

## 4. Failure investigation

Each failed run gets an automatic `failure_analysis`: the earliest failing stage
(understanding, planning, execution or validation), a category and the evidence. Categories:
* source understanding failure;
* inappropriate source interpretation;
* wrong component selected;
* invalid operation schema;
* insufficient adapter capabilities;
* incorrect parameter selection;
* overly restrictive constraints;
* Blender execution failure;
* native artifact validation failure;
* visual quality failure;
* provider refusal;
* API timeout or rate limit;
* budget exhaustion.

It is a heuristic, labelled as such, and a starting point for review. Example: in the
deterministic baseline, task A fails at planning with "insufficient adapter capabilities"
because nothing was planned. That points at the planner, here the heuristic, not at Blender.

The operation catalogue is **not** changed during a comparison. Improvements to it must be
measured as a separate, versioned experiment.

## 5. Versioning and baseline preservation

* `BENCH_VERSION = "2.1.1"`. V2.1 checks keep their names and meaning. New checks are tagged
  `since: "2.1.1"`, and each run reports both `passed` (all checks) and `passed_v21_checks`
  (V2.1 checks only) for old-versus-new comparison.
* Preserved V2.1 deterministic baseline:
  * A 3/6 (no geometry; the polygon check was corrected during V2.1 so a 0-polygon result no
    longer passes);
  * B and C pass;
  * D fails;
  * E blocked.

  The v2.1.1 re-run and the explicit comparison are in
  `evidence/v2_1_1/regression/bench_deterministic/`.
* Live and deterministic results are never mixed. A live gate in which any run made no live
  call, or used a recorded response, is a FAIL.

## 6. Provider comparison

`python -m daedelus.bench_compare --out <dir> label=report.json …` writes `comparison.json`
and `comparison.md`. Per task, mode and provider it reports:
* checks, and checks restricted to the V2.1 set;
* planned operations and invalid units;
* live calls, cost (or unknown) and runtime;
* the loop's stop reason, the iteration effect and the failure category;
* the assessment status.

Each cell is a single run (n = 1). The report says so and declares no winner. A provider
without a capability, such as video, is shown as BLOCKED for that task, not forced into
equivalence.

## 7. Budgets

* Per run: 24 calls, $2 known-price, 600k tokens, 900 s, 3 corrections.
* The separate assessment: at most 3 calls, $0.50.
* Everything is inside the campaign budget of `live-run`.

See `docs/LIVE_AI_TESTING.md` §4 for what the dollar figures do and do not guarantee.
