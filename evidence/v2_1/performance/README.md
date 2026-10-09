# V2.1 distributed performance (V2 vs V2.1)

Host: 4 vCPU cloud container, Linux 6.18 (one cloud container; control plane, one worker, SQLite queue, CAS on local disk).
Harness: `deploy/perf_bench.py --repeat 3`. V2 ran the frozen V2 code (`cdc2597`, worktree, `--pythonpath`);
V2.1 ran this branch with the bubblewrap job sandbox (default) and again with the process profile.
Run 1 of each workload is cold (fresh workspace and cluster, empty inspect cache); runs 2-3 are warm
(same cluster, new project).

| Workload | V2 mean ± sd s (jobs) | V2 cold / warm | V2.1 bwrap mean ± sd (jobs) | V2.1 cold / warm | V2.1 process (jobs) | Gain | Queue wait per run (s) |
|---|---|---|---|---|---|---|---|
| W1_blender | 19.21 ± 0.61 (13) | 19.7 / 18.9 | 10.55 ± 0.06 (7) | 10.6 / 10.5 | 10.48 (7) | **45%** | 2.62 → 0.22 |
| W2_office | 60.00 ± 1.17 (45) | 61.3 / 59.4 | 38.15 ± 0.37 (28) | 38.5 / 38.0 | 40.01 (28) | **36%** | 8.94 → 0.26 |
| W3_code | 9.27 ± 0.24 (12) | 9.2 / 9.3 | 3.97 ± 0.54 (8) | 4.6 / 3.7 | 3.75 (8) | **57%** | 2.38 → 0.26 |
| W4_mixed | 23.53 ± 0.47 (22) | 23.7 / 23.4 | 14.03 ± 0.26 (14) | 13.9 / 14.1 | 14.26 (14) | **40%** | 4.47 → 0.26 |

W5 (cancel a running job; crash a worker mid-job and recover on another):

| | V2 | V2.1 bwrap | V2.1 process |
|---|---|---|---|
| time to cancel (s) | 21.47 | 20.53 | 21.45 |
| recovery after worker crash (s) | 22.45 | 19.6 | 20.03 |
| recovered | True | True | True |

W5 is dominated by the lease expiry (crash) and the job's own runtime (cancel); V2.1 did not change
either and its numbers are unchanged within noise. Cancellation and crash recovery still work.

**Target:** ≥20 % on the Office pipeline (W2) without weakening safety. Result: **36 % (60.0 s → 38.2 s, 45 → 28 jobs)**, sandbox on.
The bubblewrap sandbox costs nothing measurable over the process profile at these job sizes.

Where the time went (job counts by method are in the JSON files):
* fewer jobs: `inspect` jobs disappear (content-digest cache + post-inspect from mutating jobs);
  side-effect scopes are batched (one job per edit instead of one per operation);
* less queue wait: lease long-polls and job waiters wake on commit instead of polling;
* shorter job start-up: the runner imports only the adapter it needs.

Unchanged safety properties (tested by the V2 distributed suite, which passes): idempotency keys,
version-checked atomic publication, lease fencing bound to worker identity, scope preservation.
`perf_v21-profile.json` (one extra pass after the job API exposed worker phase timings and bytes) holds
the per-phase profile.
