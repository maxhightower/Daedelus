# Daedelus V2.1.1: live-test hardening and creative intelligence benchmark (report)

**Summary.**
* **Phase A is complete.** The live-verification infrastructure is now trustworthy: it can no
  longer turn an unexecuted, blocked or failed live test into a green result.
* **Phase B is prepared but BLOCKED.**
  * No provider credentials were available.
  * The live workflow cannot be dispatched until it exists on the default branch.
  * No licensed technique video was supplied.

  No real Claude or Gemini call was made, so no live-AI or creative-intelligence claim is made.
  The benchmark harness was extended and versioned. Its deterministic baseline was re-run and
  matches the preserved V2.1 results exactly on every V2.1 check.

Status vocabulary as in V2.1:
* Implemented;
* Deterministically verified;
* Integration verified;
* Live AI verified;
* Hosted cloud verified;
* Live connector verified;
* Blocked;
* Failed;
* Planned.

## 1. Branch and final SHA

| | |
|---|---|
| Working branches | `claude/blissful-clarke-sow429` (session-mandated), mirrored to `opus/daedelus-v2-1-1-live-ai-validation` |
| Starting point | `opus/daedelus-v2-1-activation-hardening` @ `7011cfd` (V2.1 application code `733c1b6`) |
| Phase A freeze | `1c23098` |
| Final | ⟨final SHA⟩ |

No merge into `main`, no pull request.

## 2. Frozen code SHA

⟨frozen application code SHA⟩. Later commits change only evidence and documentation.

## 3. Phase A outcome: complete

| Gate (handoff §21) | Result |
|---|---|
| No masked requested-test failures | **met**. No `\|\| true` and no `continue-on-error`. One runner step decides the job; the evidence upload cannot change it. Enforced by a static test |
| Missing credentials → BLOCKED, nonzero | **met**: exit 1 (test 3; local run with no credentials: 12 gates BLOCKED, exit 1) |
| Dispatch parameters cannot inject shell | **met**. Inputs only via `env:`, never in `run:` scripts; allowlist validation in a secret-less first job. 33 malicious-input cases rejected with exit 2 before any gate |
| Provider selection validated | **met**: fixed allowlist, duplicates and unknowns refused |
| Secret handling audited | **met**. Secrets only on the runner step; `redact.scrub_tree` over all evidence (test 10); recorded responses labelled |
| Environment protection documented | **met** (`docs/LIVE_AI_TESTING.md` §6). The controls themselves are **unverified**: they are account-level settings |
| Per-task and aggregate budgets enforced | **met**. Per-run budget, `CampaignBudget` across runs and the assessment pass, failed calls charged, unpriced models blocked unless approved. Limits of the dollar guarantee stated |
| Live provider failures distinguished from infrastructure failures | **met**: `failure_kind` `provider` / `evaluation` / `infrastructure`, `retriable`, and `summary.infrastructure` separate from `summary.live_evaluation` |
| Failure-injection tests pass | **met**: 49 tests, including the 12 required ones, through `live_runner.main` |
| Ordinary CI remains green | ⟨CI result⟩ |

Defects found in the V2.1 live path (details in `evidence/v2_1_1/workflow_security/README.md`):
* `|| true` masking;
* blocked runs exiting 0;
* shell interpolation of `inputs.tasks` and `inputs.video`;
* unbilled refused, truncated and malformed responses;
* timeouts counted as free;
* inaccurate cache pricing;
* a dollar cap presented as a bound for an unpriced model;
* unbounded Gemini output;
* an unbudgeted task-E analysis;
* no aggregate budget, no concurrency limit, no scrubbing.

## 4. Phase B outcome: BLOCKED (prepared)

| Gate | Status | Why |
|---|---|---|
| Real Claude smoke test | **Blocked** | no `ANTHROPIC_API_KEY`; workflow not dispatchable (not on `main`) |
| Real Gemini smoke test | **Blocked** | no Gemini key; no authoritative price (would need `allow_unpriced`) |
| Image semantic understanding | **Blocked** | as above |
| Real structured plans execute | **Blocked** | as above (the deterministic planner's plans do execute: B and C pass) |
| Real editable Blender outputs | **Blocked** for live; deterministic B produces reopening `.blend` revisions; deterministic A produces no geometry |
| Scoped image/model modification | **Blocked** for live; deterministic B and C preserve unrelated components exactly |
| Document-guided code execution | **Blocked** for live; the deterministic planner cannot write code (D: `no_operations_planned`) |
| Video understanding | **Blocked** (no credentials, no licensed video supplied) |
| Single-pass vs iterative compared | **Blocked** for live; harness reports improved / unchanged / regressed per task |
| Independent evaluation evidence | **Blocked** for live; the separate `Engine.assess` pass is implemented and tested with a live double |
| Actual API usage and costs | none: **0 calls, $0** |
| Blocked capabilities identified | **yes** (this table, register N6, N7, N15, N16) |

What was built for Phase B (Implemented, Deterministically verified):
* **Benchmark v2.1.1.** New checks are tagged `since: 2.1.1`:
  * A: GLB mesh;
  * B: revision recorded and `.blend` reopens;
  * C: still layered;
  * D: 8 held-out specification cases (they catch a test-gaming patch) and file scope;
  * E: procedural steps.

  `passed_v21_checks` keeps V2.1 verdicts comparable.
* **Separate model assessment** (`Engine.assess`): labelled `authoritative: false` with its
  independence (cross-provider when two providers are requested). It never fills a check or the
  rubric, and is budgeted inside the campaign.
* **Automatic failure classification:** the earliest failing stage and category, labelled
  heuristic.
* **Provider comparison** (`bench_compare`): n = 1 per cell, stated; coverage gaps shown as
  BLOCKED; no ranking.

## 5. CI links

⟨CI table⟩

## 6. Regression results

⟨regression table⟩

## 7. Actual provider calls made

**None.** Every live gate is BLOCKED; the campaign used 0 calls. All provider behaviour in this
report comes from deterministic tests with live doubles, and is labelled as such.

## 8. Task-by-task results (deterministic baseline; live columns BLOCKED)

| Task | Deterministic v2.1.1 (all checks) | V2.1 checks (then → now) | Automatic failure analysis | Claude | Gemini |
|---|---|---|---|---|---|
| A | 3/7 (single and iterative) | 3/6 → 3/6 | `inappropriate_operation_choice`: planned `set_taper` and `set_material` on an empty artifact, no geometry-creating op | Blocked | Blocked |
| B | 5/5 | 3/3 → 3/3 | — | Blocked | Blocked |
| C | 5/5 | 4/4 → 4/4 | — | Blocked | Blocked |
| D | 1/5 | 1/3 → 1/3 | `no_operations_planned` | Blocked | Blocked |
| E | blocked (no video) | blocked → blocked | — | Blocked | Blocked |

Evidence: `evidence/v2_1_1/regression/bench_deterministic/` (with `OLD_VS_NEW.md`) and
`evidence/v2_1_1/comparison/`.

## 9. Native output evidence

Deterministic renders of every revision of A and B:
`evidence/v2_1_1/regression/bench_deterministic/*.png`. The native files remain in the run
workspace and are validated by the `file_reopens`, zip/`stack.xml`, GLB and layer checks. No
live outputs exist.

## 10. Single-pass vs iterative

Deterministic: identical in every task (unchanged). The heuristic's correction loop does not
change outcomes on these tasks, as expected of a non-AI planner. Live comparison: **Blocked**.

## 11. Usage and total cost

0 model calls, $0. The workflow's per-campaign bound when it does run:
* known-price cap (default $5) plus at most one call (≤ $1.68 for Claude Opus 5.5 at the
  configured limits);
* for an unpriced model, only the call and token caps (`live_results.json → cost_bounds`).

## 12. Security findings

The V2.1 live workflow had real defects, all fixed and tested:
* shell injection from dispatch inputs;
* failure masking;
* blocked runs reported as success;
* unaccounted spend on failed calls;
* missing evidence scrubbing.

Remaining:
* GitHub environment protection is unverified (N15);
* provider-side spend limits are the only hard monetary stop;
* a dispatcher with write access could still edit the workflow on an allowed branch. Required
  reviewers on the environment are the control for that.

## 13. Blocked gates

* Live Claude (N6), live Gemini (N7) and the live benchmark (N16): credentials, and the
  workflow not dispatchable from `main`.
* Gemini cost bound: no price.
* Video (task E): no licensed video.
* Environment protection (N15): account-level, unverified.
* Live connectors (N8, N12): unchanged from V2.1.

## 14. Recommendations for creative performance (for V3)

These come from the deterministic evidence and the harness design. They are **not** from live
model results, which do not exist yet.

1. **Run the live campaign first** (`docs/LIVE_AI_TESTING.md` §7). Every recommendation about
   model quality waits on it.
2. **Task A's failure mode is planning, not execution.** The deterministic planner chose
   modification operations on an empty artifact. Whether a live model picks `add_primitive`
   with the right size, or needs richer creation operations (profiles, lathe, bevel) to reach a
   recognisable mug within 600 polygons, is exactly what the live run should show, through
   `no_operations_planned` versus `incorrect_parameter_selection`. Change the catalogue only as
   a separate, versioned experiment.
3. **Task D's held-out cases are the meaningful signal for code work.** Visible tests alone
   would reward test-gaming patches.
4. **Record a current Gemini price** so Gemini runs get a real dollar bound.
5. **Keep cross-provider assessment** (each provider assessing the other's output) and add
   human scoring of the rubric's empty dimensions before drawing quality conclusions; n = 1 per
   cell is not enough to rank providers.
