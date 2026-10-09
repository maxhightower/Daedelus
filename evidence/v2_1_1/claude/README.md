# Live Claude benchmark: BLOCKED

No live Claude call was made in V2.1.1.

* **Credentials:** this development container has no `ANTHROPIC_API_KEY`
  (`ANTHROPIC_AUTH_TOKEN` / profile). The handoff forbids using any other, unauthorised
  credential. The session's own model access is not an approved benchmark credential and was
  not used.
* **Workflow:** `live.yml` cannot be dispatched yet. GitHub dispatches only workflows present on
  the default branch, `main` has no workflows, and merging into `main` is not allowed in this
  work. Whether the `daedelus-live` environment exists, holds secrets or is protected cannot be
  seen from here.
* **Recorded outcome:** `../smoke_tests/live_results.json`: `claude.smoke` and
  `claude.bench.A`–`E` are **BLOCKED** (`credentials`), exit code 1.

## To unblock (owner)

1. Complete `docs/LIVE_AI_TESTING.md` §6:
   * put `live.yml` on `main`;
   * create the `daedelus-live` environment with required reviewers and branch policy
     `main` and `opus/*`;
   * add `ANTHROPIC_API_KEY` as an environment secret;
   * set a spend limit in the Anthropic Console.
2. Dispatch `providers=claude, stages=smoke`. Inspect `live_results.json`: expect about 3 calls
   and a few cents.
3. Dispatch `stages=smoke,bench, tasks=C, max_model_calls=20, max_cost_usd=2` (one task), then
   `tasks=ABCD` with an approved campaign cap. Default campaign: 60 calls / $5 known-price, with
   overshoot of at most one call (≤ $1.68 for Opus 5.5). See the `cost_bounds` section of the
   manifest.
4. Re-run `python -m daedelus.bench_compare` with the downloaded `bench_report.json` to fill
   `../comparison/`.
