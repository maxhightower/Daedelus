# Daedelus V2 — Distributed Creative Execution: freeze report

## 1. Branch and code SHA

* Branch: `opus/daedelus-v2-distributed-execution`, based on the frozen V1.2 branch head
  `93b5d31` (V1.2 code `71037b1`).
* **Frozen code SHA: `923ca4f`.** Later commits on the branch contain only this report,
  documentation and evidence.
* The container end-to-end evidence was produced from images built at `e925ab2`. That
  commit differs from `923ca4f` only in `studio/e2e/office.e2e.mjs`, a test-timing fix
  (`git diff --stat e925ab2 923ca4f`: 1 file). Application, worker and deployment code is
  identical.
* Not merged. No pull request.

## 2. Summary of completed functionality

* **Planes.**
  * Control plane: API, auth, engine, durable job queue, content-addressed blob store,
    audit log and SSE event stream.
  * Worker plane: `daedelus worker` processes that run one adapter method per job, each in a
    killable child process.
  * Artifact storage: sha256 blobs and manifests.
* **Typed contracts:** `JobRequest` / `JobStatus` / `JobResult` / `Manifest`. No paths,
  commands or URLs cross the boundary.
* **Durable queue** (SQLite WAL):
  * leasing with per-attempt tokens (only their hashes are stored) and heartbeats;
  * deadlines and timeouts, retry policy, and cancellation of queued and running jobs;
  * idempotency keys;
  * reaping of expired leases on start and periodically;
  * a durable event log.
* **Publication** of remote results is atomic (rename-swap), idempotent and version-checked.
  A conflict is refused and audited.
* **Restart recovery.** Executions interrupted by a control-plane restart resume. Their
  in-flight jobs are reused through deterministic idempotency keys, not recomputed.
* **Execution targets:** Automatic / Local / Cloud CPU / Cloud GPU, per project and per node.
  * A remote target never silently falls back to local.
  * Each decision, its reason, and every job with its worker are recorded on the node run.
* **Security:**
  * API tokens with project scopes;
  * a loopback bind by default, with a non-loopback bind refused unless tokens are set;
  * Host-header allowlist (DNS rebinding) and CORS restricted;
  * worker tokens, and blob access limited to the leased job's own inputs;
  * upload hash verification, and manifest path-traversal and symlink refusal;
  * worker and project command allowlists;
  * SSRF guard on all URL ingestion, checked on every redirect hop;
  * Blender `--disable-autoexec`;
  * an fsynced audit log.
* **Studio.**
  * One event stream per project replaces the V1 polling loops.
  * Execution settings: target choice, per-adapter availability, workers, and jobs with
    progress and cancel.
  * Each node run shows where it executed.
* **Containers.**
  * The control-plane image contains no Blender and no LibreOffice.
  * Blender, Office and code worker images.
  * Compose with an `edge` network bound to loopback and an internal `jobs` network.
  * A CI cluster job.
* **Docs:** [V2_ARCHITECTURE.md](V2_ARCHITECTURE.md),
  [SECURITY_MODEL.md](SECURITY_MODEL.md), [CLOUD_DEPLOYMENT.md](CLOUD_DEPLOYMENT.md).

## 3. Implementation commits

| Commit | Content |
|---|---|
| `aebe1da` | Phase 1: contracts, CAS, queue, service, worker, remote adapter, targets, publication, recovery, security, SSE, studio execution UI, images, compose, container e2e, CI job, docs |
| `db57233` | Worker retries transient control-plane errors; revoked leases abandon cleanly; execution walkthrough in CI |
| `6b4b4b8` | Control image owns its data volume (found by the first container run); e2e reports compose failures |
| `fd074ec` | Artifact outputs can feed revision inputs (found by S4); slow worker removed after the S7 restart |
| `e925ab2` | Portable process-tree kill (found by the Windows CI run) |
| `923ca4f` | Office walkthrough waits for the highlight instead of racing the render (found in CI) |
| (this) | Freeze report and evidence |

## 4. Container end-to-end (`deploy/cluster_e2e.py`, evidence in [`evidence/v2/cluster`](../evidence/v2/cluster))

**Result: passed, 42/42 checks, 645 s.**

Scope: one Docker host (this cloud container), real images, the control plane and the
Blender, Office and code workers as separate containers on separate networks.

| Scenario | What was verified |
|---|---|
| S1 Topology | The control container has no `blender` or `soffice`. The `jobs` network is internal, and a worker cannot open a connection to 1.1.1.1. The published port binds to 127.0.0.1. All three worker kinds registered. |
| S2 Blender worker | Create a model, edit it remotely (cylinder and cone) and render. The PNG render was produced on the Blender worker and served by the control plane. Components exist after publication. |
| S3 Office workers | The V1.2 research pipeline ran entirely on workers (45 jobs, 42 s). Every agent node resolved to `cloud_cpu`. Formulas were recalculated by LibreOffice inside the Office worker. Dependencies synced. |
| S4 Code worker | Allowlisted `python -m pytest` ran on the code worker and passed. A command allowed by the project but not by the worker was refused, and the marker file it would have written does not exist. |
| S5 Worker killed mid-job | A slow worker leased a job and was `docker kill`ed. The lease expired, the job was retried (attempt 2) on another worker and succeeded 27 s after the kill. History: `queued, leased, retry, leased, succeeded, published`. |
| S6 Network partition | The worker holding the job was disconnected from `jobs`. The job was retried elsewhere and completed. After reconnection, the partitioned worker's late result was rejected (409) and audited, and exactly one result was published. |
| S7 Control-plane restart | `docker compose restart control` while an apply job ran on a worker. The control plane came back, the queue and volume were intact, and the execution resumed and succeeded (79 s after the restart). The in-flight apply job finished on its worker and was **reused, not recomputed**. The resumption was logged and audited. |
| S8 Security | API without a token: 401. Bad worker token: 401. Foreign Host header: 400. URL ingestion of `169.254.169.254`, `control:8765` and `127.0.0.1:8765` was refused. Authentication failures were audited. |

During development the first full run scored 40/42: both S4 checks failed because my S4
workflow was invalid. That led to the `artifact → revision` edge fix. The final run is the
one above.

## 5. Tests and walkthroughs (code `923ca4f`; the cluster run is from `e925ab2`)

| Suite | Result |
|---|---|
| Backend pytest (Blender 4.5.14, `DAEDELUS_REQUIRE_LIBREOFFICE=1`) | **144 passed, 4 skipped.** The 4 skips are the opt-in live-AI gates. |
| New V2 tests (`test_distributed.py`) | 30 passed. Queue semantics. Real server plus real worker processes: crash → retry, corrupt upload rejected, duplicate completion idempotent, version conflict, cancellation, SSE, blob scoping, API tokens and scopes, trusted hosts, public bind refused, GPU flag gated, SSRF (11 URL classes plus a redirect to metadata). |
| V0 demonstration | 40/40 |
| V1.1 demonstrations | A, B, C, D passed; E blocked (no credentials) |
| V1.2 demonstration | 28/28 |
| Studio unit tests | 14/14 |
| V1 spatial walkthrough | 60/60 |
| V1.1 semantic walkthrough | 19/19 |
| V1.2 office walkthrough | 23/23 |
| **V2 execution walkthrough (new)** | **15/15.** Target chosen in the UI. Live worker and job list over SSE. Node run shows target, reason and worker. Cloud GPU flagged and refused with no fallback. A running job was cancelled from the UI and created nothing. No API request during 10 s idle (polling removed). |

Logs: `evidence/v2/regression/`.

## 6. CI

* CI on `923ca4f`: see §11 (filled in after the run).
* Windows desktop build on `e925ab2`:
  [run 37849484795](https://github.com/maxhightower/Daedelus/actions/runs/37849484795),
  **success**. Its first run on `6b4b4b8` had failed: the POSIX-only `os.killpg` was used in
  the worker and its tests. That is fixed in `e925ab2`.

## 7. Screenshots (`docs/screenshots/v2/`)

| File | Shows |
|---|---|
| `v2_01` | Execution settings: target, per-adapter availability, connected worker, live jobs |
| `v2_02` | Run view showing where a node executed (`cloud_cpu`, worker, 7 jobs) |
| `v2_03` | Cloud GPU unavailable: warning, run refused without fallback |
| `v2_04` | Job running on a slow worker |
| `v2_05` | Job cancelled from the UI |

`evidence/v2/cluster/blender_render.png` is the render produced by the containerised
Blender worker.

## 8. Metrics (local containers on one host; not cloud performance)

| Metric | Value |
|---|---|
| Blender create + edit + render, remote | 16.1 s |
| V1.2 research pipeline, fully remote | 42.4 s, 45 jobs (local: about 18–28 s) |
| Recovery after a worker kill (lease 20 s) | 27.0 s |
| Execution completion after a control-plane restart | 79.1 s |
| Remote edit overhead | about 0.5–1.5 s per job: lease poll plus a child Python process |
| Image sizes (uncompressed) | control 844 MB; office 1.29 GB; code 753 MB; Blender 2.61 GB |

## 9. Known limitations

* **One host.** All container tests ran on one Docker host. A hosted multi-machine
  deployment, real TLS termination and cross-region latency are unverified (register N9).
* **No GPU.** No GPU worker exists. `cloud_gpu` is verified only as "explicitly refused when
  unavailable", and workers refuse to advertise GPU without operator verification (N10).
* **Per-job overhead.** Every adapter call is a job, including the frequent `inspect` calls,
  each started in a fresh child process. Batching and warm workers are future work.
* **Credentials.** A single shared worker token; no per-worker credentials or mTLS.
* **Transport and isolation.** Plain HTTP inside the compose network. No sandbox beyond a
  non-root user and a killable process (see SECURITY_MODEL.md).
* **Drive publish race.** The V1.2 race window for Google Drive publication remains.

## 10. Freeze status (13 items)

| # | Item | Status |
|---|---|---|
| 1 | Control plane / worker plane / artifact storage, typed contracts | Implemented; integration verified (containers) |
| 2 | Durable queue: leasing, heartbeats, timeouts, cancellation, retry | Deterministically verified; integration verified |
| 3 | Restart recovery (queue and executions) | Integration verified (S7, real restart) |
| 4 | Idempotent, atomic publication with version-conflict protection | Deterministically verified; integration verified |
| 5 | Artifact manifests with hashes, verified transfers | Deterministically verified; integration verified (corrupt upload rejected) |
| 6 | Security: auth and project scope, SSRF, no arbitrary commands, audit | Deterministically verified; integration verified (S8, S4) |
| 7 | SSE progress replacing polling | Integration verified (browser walkthrough; idle check) |
| 8 | Execution settings UI, no silent local fallback | Integration verified |
| 9 | Container images and compose with separate networks | Integration verified (single host) |
| 10 | Failure injection (worker kill, partition, restart, corrupt upload, conflict, cancel) | Integration verified |
| 11 | Hosted multi-machine cloud deployment | **Blocked / planned** (N9). The guide is marked unverified. |
| 12 | GPU worker execution | **Blocked** (N10) |
| 13 | Live AI planning on remote targets | **Blocked** (no credentials). Remote execution was verified with the deterministic provider only. |

Cloud verified: **none**. All "cloud" targets ran as containers on one host, labelled as
such. Live AI verified: none.

## 11. CI result on the frozen SHA

(filled in after the CI run on `923ca4f`)
