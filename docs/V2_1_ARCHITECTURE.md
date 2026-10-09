# Daedelus V2.1: activation and cloud hardening (architecture)

V2.1 keeps every V0–V2 abstraction unchanged. Source, binding (meaning and scope), artifact,
component, workflow, execution, board, and the execution target all remain independent
concepts. It changes how the pieces are **secured**, how model providers are **bounded and
audited**, and how remote work is **measured and shortened**. It adds harnesses for the
things that need the outside world: live models, hosted machines, live connectors.

```
                         HTTPS (TLS edge / built-in TLS / tunnel)
 studio ──session cookie+CSRF / bearer+tickets──▶ control plane (hosted profile)
                                                  ├ AuthMiddleware: tokens · sessions · tickets
                                                  ├ HttpsOnly (X-Forwarded-Proto), security headers
                                                  ├ engine ── ExecutionBudget (calls · cost · tokens · time)
                                                  │            └ BudgetedProvider ─▶ Claude / Gemini / heuristic / replay
                                                  ├ RemoteAdapter ── inspect cache (content digest)
                                                  ├ queue (SQLite WAL) ── leases bound to worker identity
                                                  └ CredentialStore (per-worker, hashed, revocable)
                  HTTPS, cert verified ▲
                                       │ ddw1.<id>.<secret>
                              worker (hosted profile)
                              ├ sandbox self-test → registered isolation report
                              └ job ─▶ bubblewrap: no network · ro root · private dir · own PID ns
```

## 1. Security layer

| Concern | Mechanism | Module |
|---|---|---|
| Deployment rules | `DAEDELUS_PROFILE=hosted`: TLS or TLS proxy, tokens required, no join-token enrolment, no path sources, Secure cookies, code jobs only in a verified sandbox, workers refuse plain HTTP | `profile.py`, `cli.py`, `worker.py` |
| Worker identity | per-worker credentials, adapter/capability restrictions, join tokens only enrol, leases bound to the authenticated id, revocation revokes leases, rotation with grace | `distributed/identity.py`, `queue.py`, `router.py` |
| Browser auth | HttpOnly SameSite=Strict session cookie, CSRF token + Origin check, short-lived project-scoped tickets, query tokens refused | `sessions.py`, `security.py`, `studio/src/api.ts`, `main.tsx` |
| Transport | built-in TLS / proxy mode, HTTPS-only behind proxies, HSTS and security headers, verified certificates on workers, optional mTLS | `cli.py`, `security.py`, `worker.py` |
| Job isolation | env allowlist, private dirs, rlimits; bubblewrap profile verified at start; hardened containers with seccomp for user namespaces | `distributed/sandbox.py`, `deploy/compose.hardened.yml`, `deploy/seccomp/` |
| Network input | SSRF with connect-time pinning (no DNS TOCTOU), numeric/IPv6 normalisation, manifest validation and mode masking, symlink-safe repository snapshots | `netsafe.py`, `distributed/cas.py`, `ingest.py` |

See [SECURITY_MODEL.md](SECURITY_MODEL.md) for the threat model and residual risks.

## 2. Model providers: compatibility, errors, budgets

**Compatibility audit, 2026-10.** Each item was checked against the provider's documentation.

**Anthropic:**
* default `claude-opus-5-5`;
* `thinking: {type: "adaptive"}` (a thinking budget returns 400 on this model);
* explicit `output_config.effort` (the model's default is `medium`, so it is set rather than
  assumed);
* structured output via `output_config.format`;
* server-side refusal fallbacks (`fallbacks: "default"`, beta
  `server-side-fallback-2026-07-01`; disable with `DAEDELUS_CLAUDE_FALLBACKS=off`);
* refusal and `max_tokens` stop reasons handled; 16k non-streaming output limit;
* image limits respected (≤1024 px, ≤6 images); PDFs over 23 MB refused before upload.

**Gemini:**
* `gemini-2.5-flash` (the V1.1 default) shuts down on 2026-10-16, so the default is the
  stable `gemini-3.8-flash`;
* videos over 20 MB go through the Files API, with a bounded ACTIVE wait and deletion after
  the call;
* YouTube URLs are sent as `file_data`;
* `response_json_schema` for structured output;
* SDK retry options and timeout come from the budget.

Model ids, effort, output tokens and fallbacks are all environment-configurable
(`DAEDELUS_CLAUDE_*`, `DAEDELUS_GEMINI_*`).

**Typed errors.** `ProviderTimeout`, `ProviderRateLimited` (with `retry_after`; also
Anthropic 529) and `ProviderOverBudget`. A failed call fails the node without touching the
artifact.

**Usage and provenance.** Each live call records:
* input, output and cache-read tokens;
* cost from known list prices only, with cache writes ×1.25 and reads ×0.1. An unknown price
  is reported as **unknown**, never as zero (`Usage.unpriced_calls`, `cost_label`);
* the serving model, the fallback source and the request id.

**Execution budgets** (`budget.py`). A workflow's `parameters.budget` bounds:
* model calls, cost, tokens and wall time;
* corrective iterations, per-call timeout and retries;
* remote jobs: `job_timeout_s` becomes each job's deadline, `job_memory_mb` lowers the
  worker's address-space limit for that job (a job can never raise it), and
  `max_concurrent_jobs` bounds the execution's jobs in flight at once.

`get_provider()` returns a `BudgetedProvider` during an execution: a call is **refused
before it is made** when a limit is reached. Usage is charged after each call and recorded
on `Execution.budget`. After a control-plane restart the counts resume.

**Plan reuse after a restart.** Plans recorded before an interruption are reused when the
unit's fingerprint is unchanged. The model is not called twice, so cost is not paid twice and
a second, different plan cannot appear.

**Preflight** (`preflight.py`, `GET …/workflows/{wid}/preflight`). Before a run, the studio
shows:
* where each node executes: this machine, or a container or hosted worker, with its
  isolation;
* which provider and model are called, whether they are live and configured, and whether the
  price is known;
* the budget limits and a spend bound.

When live models or remote workers are involved, the user confirms.

## 3. Creative benchmark (`bench_v21.py`)

Tasks A–E use unfamiliar, licensed references:
* real CC0 and public-domain photographs (coffee cup, rocket launch, cat, brick);
* a project-authored stylized illustration;
* written specifications.

There are no object-specific recipes. Each task runs as a single pass and as an
Evaluate→Revise loop (at most 3 corrections) under a fixed budget. It records:
* provider and model, source hashes, bindings and scopes, observations;
* operations and validation decisions;
* renders, revisions, evaluations, latency, tokens and cost.

A five-dimension rubric is scored by measurement where possible (constraint adherence,
preservation). The other dimensions are left for human review and are never filled in from a
model's self-assessment. Live runs report BLOCKED without credentials.

## 4. Distributed execution performance

Measured with `deploy/perf_bench.py` (results in `V2_1_REPORT.md`):

* **Inspect cache** (`remote.py`): inspections are a pure function of (adapter, version,
  entry, exact contents) and are cached under the tree digest. A result is never served once
  any byte changes; tested.
* **Post-inspect:** `create`, `apply` and `after_restore` jobs return the inspection of their
  output, which fills the cache. This removes one job per mutation.
* **Batched side-effect scopes:** one job per edit instead of one per operation. It is used
  only when every live worker advertises the `batch_scope` feature; older workers get
  per-operation jobs.
* **Lazy adapter import** in the job runner: only the adapter the job needs is imported.
* **Event-driven queue:** lease long-polls and job waiters wake on commit (a condition
  variable) instead of sleeping in 0.1–0.25 s steps.
* **Observability:**
  * workers report per-phase seconds (fetch, run, runner start-up and method, upload),
    bytes in and out, and their isolation profile;
  * node runs record cache hits, bytes, deployment and isolation;
  * the Inspector shows them.

Unchanged safety properties: idempotency keys, version-checked atomic publication, lease
fencing and scope preservation. The whole V2 distributed suite passes unchanged except for
two assertions that expected `inspect` jobs to exist.

## 5. Connectors

* **Graph:** `If-Match` with the eTag (item version), 412 → conflict, 250 MB simple-upload
  limit.
* **Drive:** no atomic precondition exists, so publication is conservative:
  1. a version check;
  2. pin the head revision as a backup;
  3. upload with `keepRevisionForever`;
  4. re-read the revision list to detect a write in the race window.
  A detected race keeps all revisions and raises `ConnectorConflict(written=True)`, which is
  recorded on the artifact. It is never reported as success.
* Discovery, creation, deletion and revision history support the opt-in live harness
  (`connectors_live.py`). The harness touches only files it creates in a designated folder.

## 6. Deployment

* Compose topologies: development, hardened, and TLS edge.
* Two-machine hosted recipe: GitHub-hosted runners plus a quick tunnel, credentials sealed
  to a key generated on the worker host, and phases published as a commit status.
* Opt-in live workflow with a protected environment.

See [CLOUD_DEPLOYMENT.md](CLOUD_DEPLOYMENT.md).
