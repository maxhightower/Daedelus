# Live AI and connector testing (V2.1.1)

This document describes how Daedelus runs **real** model and connector tests: what the workflow
guarantees, what the repository owner must configure on GitHub, and how to read the results.
Ordinary CI never calls an external model; everything here is opt-in and manual.

## 1. Pieces

| Piece | Path | Role |
|---|---|---|
| Workflow | `.github/workflows/live.yml` | manual dispatch, two jobs: input validation without secrets, then live gates in the protected environment |
| Runner | `backend/daedelus/live_runner.py` (`daedelus live-run --from-env`) | validates inputs, orders gates, classifies outcomes, writes the manifest, scrubs secrets, sets the exit code |
| Benchmark | `backend/daedelus/bench_v21.py` | creative tasks A–E (see `docs/CREATIVE_BENCHMARK.md`) |
| Budgets | `backend/daedelus/budget.py` | per-run `ExecutionBudget`, campaign-wide `CampaignBudget` |
| Scrubbing | `backend/daedelus/redact.py` | removes secret values and credential shapes from all evidence |
| Tests | `backend/tests/test_live_runner.py` | the 12 Phase A cases, through the real entry point |

## 2. Outcomes

Every known gate appears in `live_results.json`. A gate is `claude.smoke`, `claude.bench.A` …
`claude.bench.E`, the same for `gemini`, `connector.google` or `connector.msgraph`.

| Outcome | Meaning | Exit |
|---|---|---|
| PASS | the live operation ran and met every assertion | 0 only if *all* requested gates pass |
| FAIL | it ran but an assertion failed, an unexpected error occurred, or the output was invalid. `failure_kind`: `evaluation` (creative checks failed), `provider` (API error, refusal, rate limit, malformed output; `retriable` set for 429/timeouts) or `infrastructure` (harness crash) | 1 |
| BLOCKED | a credential, account, video, price or budget authorisation was missing. `failure_kind`: `credentials`, `pricing`, `budget`, `prerequisite` (its smoke test did not pass) | 1 |
| NOT RUN | not requested | — |

`summary.infrastructure` says whether the runner itself completed (`completed`, or
`input_rejected` with exit 2). `summary.live_evaluation` is the aggregate of the requested
gates. A green infrastructure job with a BLOCKED evaluation is **not** a live verification.

Rules that make a false green impossible:
* missing credentials → BLOCKED → exit 1;
* a benchmark run that made **no live model call** → FAIL;
* any **recorded (replayed) response** in a live gate → FAIL;
* the benchmark for a provider starts only after its smoke test passed;
* the evidence upload runs `if: always()` but cannot change the job result. No `|| true`, no
  `continue-on-error`.

## 3. Inputs (all validated before anything runs)

| Input | Accepted | Refused (examples) |
|---|---|---|
| `providers` | `none`, `claude`, `gemini`, `claude,gemini`, `all` | unknown names, duplicates, anything with spaces or shell characters |
| `stages` | `smoke`, `bench`, `smoke,bench` | — |
| `tasks` | 1–5 distinct letters from `ABCDE` | `A;rm -rf /`, `A B`, `$(id)`, `AA`, `F`, lower case |
| `video_url` | `https://` on an approved host, default port, a YouTube URL must carry a video id, and it must pass the SSRF policy (no private, loopback or metadata addresses). Approved hosts: `youtube.com`, `youtu.be`, `upload.wikimedia.org`, plus `DAEDELUS_LIVE_VIDEO_HOSTS` | `http://`, other hosts, look-alike hosts, credentials, ports, whitespace, `$`, backquotes, quotes, newlines |
| `connectors` | `none`, `google`, `msgraph`, `google,msgraph` | — |
| `allow_unpriced` | boolean | — |
| `max_model_calls` | 1–200 | — |
| `max_cost_usd` | 0.01–25 | — |

Inputs reach the runner only as environment variables (`LIVE_*`). No `${{ inputs.* }}`
expression appears in a `run:` script; a static test enforces this. A rejected request writes
`live_results.json` with `infrastructure: input_rejected` and exits 2. No credential is read and
no call is made.

## 4. Budgets and what the dollar cap really guarantees

**Per run** (one task in one mode): 24 model calls, $2 known-price cost, 600,000 tokens, 900 s,
3 corrections, 180 s per call, 2 SDK retries. **Campaign** (all requested gates together,
defaults in `budget.CAMPAIGN_DEFAULTS`): 60 calls, $5, 1,500,000 tokens, 3600 s. Each run's
limits are the smaller of its own and what the campaign has left. A run that the campaign
cannot cover is not started (BLOCKED, `budget`).

Audit of enforcement (V2.1.1):

| Item | Status |
|---|---|
| Pre-call check | every live contract call goes through `BudgetedProvider.before_call` (calls, known cost, tokens, time) and is **refused before it is sent** once a limit is reached |
| Post-call accounting | usage is charged from each **billed response**, recorded by the provider the moment it arrives (`llm_common.bill`). **Fixed in V2.1.1:** refusals, truncated (`max_tokens`) replies, malformed JSON and schema-invalid plans used to escape the accounting |
| Retries | SDK retries inside one logical call are not visible individually. A timed-out call with no response is charged as one call of **unknown** cost (the server may have billed it). 429s are not charged (not billed) |
| Cache tokens | charged at the documented per-model cache-read price (Opus 5.5 $0.20/MTok, Fable 5.1 $0.25/MTok; undocumented cache prices are charged at the full input price). Cache writes at 1.25× input. **Fixed in V2.1.1:** a flat 0.1× multiplier was used |
| Price lookup | `semantic/service.py` `PRICES` (Anthropic documentation, 2026-10), including Haiku 5.5's long-prompt tier. A model not in the table has **unknown** price |
| Unknown price | the dollar cap cannot be enforced, so the runner BLOCKS such a provider unless `allow_unpriced=true`. Then the run is bounded only by the call and token caps, and the manifest shows `cost_complete: false` and `unpriced_calls`. **Gemini is unpriced today**: no authoritative current Gemini price is recorded |
| Output size | Claude `max_tokens` 16,000; Gemini `max_output_tokens` 16,000 (**added in V2.1.1**: Gemini output was unbounded) |
| Wall clock | per-call SDK timeout (180 s; 120 s in smoke tests), per-run 900 s, campaign `max_seconds`, job `timeout-minutes: 100` |
| Cancellation | per request: the SDK timeout aborts the HTTP request. Per run: budget refusal before the next call. Per job: GitHub's job timeout or a manual cancel stops the runner. Calls are sequential, so at most one request is in flight |

**The $5 is not an absolute guarantee.** Spending stops *before* a call once known spend
reaches the cap, but the last call can overshoot by up to one call. `live_results.json →
cost_bounds` states the bound: for Claude Opus 5.5 it is ≤ $1.68 (3 attempts × (60k input + 16k
output) at $4/$20 per MTok), so the campaign worst case is cap + $1.68. For an unpriced model
there is **no** monetary bound, only the call and token caps.

## 5. Secrets and evidence hygiene

* Secrets live only in the GitHub environment `daedelus-live` and are passed only to the single
  `Live gates` step. The validation job has none; setup steps have none.
* Before the evidence is uploaded, `redact.scrub_tree` removes from every text file:
  * the exact values of `ANTHROPIC_API_KEY`, `GEMINI_API_KEY`, connector tokens, Daedelus and
    GitHub tokens and AWS secrets;
  * credential shapes: `sk-ant-…`, `AIza…`, `ya29.…`, Google refresh tokens, GitHub tokens,
    `ddw1.` worker credentials, JWTs, `Authorization:`/`Bearer`/`Basic`, `x-api-key`, cookies,
    `dd_session`, and secret-named URL parameters (`key=`, `token=`, `sig=`, `ticket=`, …).

  A test plants a secret in a provider error and checks no file contains it.
* Recorded responses (`DAEDELUS_RECORD_DIR`) carry `origin: "recorded"`, a note that replaying
  them is not a live call, and `recorded_at`. A recorded response used inside a live gate makes
  that gate FAIL.
* Artifacts: 14-day retention. Benchmark workspaces (native files, copies of inputs) are
  excluded from the upload; renders and reports are kept.
* Inputs sent to providers are the licensed benchmark assets (`demo_assets/v21`, CC0, public
  domain or project-authored; see `ATTRIBUTION.json`) and, for task E, a video URL the
  dispatcher asserts is licensed for this use. Do not supply private media.

## 6. GitHub configuration the owner must do

These controls are **account-level settings**. The repository cannot set them and this project
could not verify them. Until they are done, treat the environment as **unprotected**. The
`environment:` line in YAML only names the environment; it protects nothing by itself.

1. **Put the workflow on the default branch.** GitHub dispatches only workflows whose file
   exists on the default branch (`main` currently has no workflows). Add
   `.github/workflows/live.yml` to `main` through a reviewed change. A dispatch can then select
   an `opus/*` branch to test that branch's code.
2. **Create the environment** `daedelus-live` (Settings → Environments):
   * **Required reviewers:** at least one trusted person. Every live run then waits for approval
     after validation. Enable "Prevent self-review" if more than one maintainer exists.
   * **Deployment branches and tags:** "Selected branches and tags" → `main` and `opus/*` only.
     The workflow also refuses other refs, as defence in depth.
   * **Wait timer:** optional.
   * **Environment secrets** (only those you have): `ANTHROPIC_API_KEY`, `GEMINI_API_KEY`,
     `DAEDELUS_GOOGLE_TOKEN`, `DAEDELUS_LIVE_GOOGLE_FOLDER`, `DAEDELUS_LIVE_GOOGLE_NATIVE_ID`
     (optional), `DAEDELUS_MSGRAPH_TOKEN`, `DAEDELUS_LIVE_MSGRAPH_FOLDER`. Put them in the
     environment, **not** in repository secrets.
3. **Actions settings** (Settings → Actions → General):
   * "Fork pull request workflows": do not send secrets or write tokens to fork PRs (the
     default).
   * Workflow permissions: read repository contents. The workflow asks only for
     `contents: read`.
4. **Provider-side limits** (recommended, the only hard monetary stop):
   * a spend limit on the Anthropic Console workspace that owns the key;
   * a budget alert or quota on the Google project that owns the Gemini key;
   * dedicated keys for this repository, rotated after the campaign.
5. **Connector test accounts:** a dedicated test tenant or account and a dedicated folder. Use
   least-privilege scopes (`docs/CLOUD_DEPLOYMENT.md` §7). The harness touches only files it
   creates, deletes them, and FAILs if it cannot verify the deletion.

Workflow-level controls (verifiable in the file and enforced by a test):
* trigger `workflow_dispatch` only;
* `permissions: contents: read`;
* `concurrency: live-verification`, `cancel-in-progress: false`: one live run at a time, queued,
  never killed halfway;
* timeouts 10 min (validate) and 100 min (live);
* checkout without persisted credentials;
* artifact retention 14 days.

## 7. Running a campaign

Recommended order (handoff §17), each step inspected before the next:

1. `providers=claude, stages=smoke`: about 3 calls, a few cents.
2. `providers=claude, stages=smoke,bench, tasks=C, max_model_calls=20, max_cost_usd=2`: one
   bounded task.
3. `tasks=ABCD` with a campaign cap you approve. Expected scale: 8 runs × at most 24 calls,
   capped by the campaign (default 60 calls / $5).
4. `providers=gemini, allow_unpriced=true` with a deliberately small `max_model_calls`. The cost
   is unknown until a Gemini price is recorded.
5. Task E only with a `video_url` of a licensed technique video.

Results: the `live-evidence` artifact (`live_results.json`, `live_results.md`, per-provider
smoke and benchmark reports, renders) and the job summary.

Local equivalent (same code path; never uses credentials it was not given):

```
LIVE_PROVIDERS=claude LIVE_STAGES=smoke daedelus live-run --from-env --out evidence-live
```
