# Daedelus V2 — Distributed creative execution: architecture

V2 adds a remote execution layer. Adapter work (Blender modelling and rendering, Office
editing and LibreOffice recalculation, code tests) can run on worker machines while the
studio, the engine and the project data stay on the control plane.

The V0–V1.2 abstractions are unchanged: Source, Binding, Artifact, Component, Workflow,
Execution and Board. *Where* adapter work runs is a separate choice, and media type, purpose
and target remain independent of it.

```
 studio (browser / desktop)          control plane (daedelus serve)                workers
 ┌──────────────────────┐  HTTPS+SSE ┌────────────────────────────────────┐  HTTP  ┌──────────────────────┐
 │ canvas, inspector,   │◀──────────▶│ API · auth · engine · projects     │◀──────▶│ daedelus worker       │
 │ execution settings,  │  events    │ ClusterService                     │ lease/ │  (Blender | Office |  │
 │ job list             │            │  ├ durable job queue (SQLite WAL)  │ blobs  │   code tools)         │
 └──────────────────────┘            │  ├ blob store (sha256 CAS)         │        │  runjob subprocess    │
                                     │  ├ audit log (JSONL, fsync)        │        │  per job (killable)   │
                                     │  └ reaper (leases, deadlines)      │        └──────────────────────┘
                                     └────────────────────────────────────┘
```

## 1. Planes

| Plane | Responsibility | Code |
|---|---|---|
| Control plane | API, auth, workflow engine, planning, bindings and context, revision records, publication, job queue, event stream | `api.py`, `engine.py`, `distributed/service.py`, `router.py`, `queue.py` |
| Worker plane | Runs one adapter method per job on a materialised snapshot. No project database, no planning, no credentials beyond its worker token. | `distributed/worker.py`, `runjob.py` |
| Artifact storage | Content-addressed blobs and manifests; project trees and revisions | `distributed/cas.py`, `store.py` |

## 2. Typed contracts (`distributed/models.py`)

* **`JobRequest`** describes one job:
  * which call to make: `adapter`, `method` (`create | inspect | apply | preview | validate |
    export | diff | after_restore | side_effect_scope`), `entry` and JSON `args`;
  * the input files: the `native` manifest (and `before` for diffs), plus context `files` as
    blob hashes;
  * how to run it: `requires` (`cpu`/`gpu`), `timeout_s`, `max_attempts`;
  * concurrency and dedupe controls: `idempotency_key` and `base_digest` (optimistic
    concurrency);
  * `origin`: execution, node or API, for audit.

  No paths, no shell and no URLs cross the boundary.
* **`JobStatus`** has a state (`queued → leased → running → succeeded | failed | timed_out |
  cancelled | conflict`), an attempt counter, the worker, a lease expiry, and progress and
  message.
* **`JobResult`** carries `output` (the new native manifest, for mutating methods),
  `artifacts` (preview or export files), `value` (the serialised return value), logs and
  timing.
* **`Manifest`** maps relative path → sha256 / size / mode. Its `digest()` is the version of
  an artifact tree.

## 3. Durable queue (`distributed/queue.py`)

The queue is SQLite in WAL mode with `synchronous=FULL`. Every transition is one
`BEGIN IMMEDIATE` transaction.

* **Leasing.**
  * A worker long-polls and receives the oldest queued job whose `requires` and adapter
    match its capabilities.
  * Each attempt gets a fresh random lease token. Only the token's hash is stored.
* **Heartbeats** extend the lease, carry progress, and deliver cancellation.
* **Lease expiry** (the worker died or was partitioned) re-queues the job while attempts
  remain, and fails it otherwise. A revoked lease cannot heartbeat, complete or read blobs:
  late results are rejected (HTTP 409) and audited.
* **Deadlines.** `timeout_s` per attempt; exceeding it is `timed_out` once attempts are used
  up.
* **Cancellation.**
  * A queued job is cancelled immediately.
  * A running job is flagged; the worker kills the job's process group and acknowledges. An
    unacknowledged cancel completes when the lease expires.
* **Retry policy.**
  * Infrastructure failures are retried: input transfer, a runner crash, timeouts, lost
    leases.
  * Deterministic adapter errors are not retried.
* **Idempotency.** `idempotency_key` deduplicates submissions. A key whose job failed, was
  cancelled, timed out or conflicted is detached, so an explicit retry runs again.
* **Restart recovery.** On start the reaper expires leases that lapsed while the control
  plane was down. Workers that kept running keep heartbeating with their still-valid tokens
  after the restart.
* **Events.** Every transition appends to an `events` table (sequence numbers). The same
  table carries execution and revision events and is the source of the SSE stream.

## 4. Execution targets (`distributed/remote.py`)

`ProjectSettings.execution_target` and the per-node `execution` setting choose one of four
targets:

| Target | Resolution |
|---|---|
| `local` | This machine. Errors if the adapter's tools are missing. |
| `automatic` | Local when the tools are present here, otherwise a connected **CPU** worker, otherwise an error. Never a GPU worker. |
| `cloud_cpu` | A connected CPU worker offering the adapter, **else an error**. |
| `cloud_gpu` | A connected GPU worker offering the adapter, **else an error**. |

A remote target **never falls back silently to local execution**.

How a target takes effect:

* `use_target()` sets a context variable for the node run or API call.
* `adapters.get_adapter()` then returns a `RemoteAdapter` proxy whenever the resolved target
  is remote. The engine, manual edits, revision recording and dependency sync run unchanged.
* The decision and its reason, plus every job (id, worker, attempts), are stored in the node
  run's `outputs.execution` and shown in the studio.

Inside the `RemoteAdapter` proxy:

* Catalogue and static validation (`info`, `op_spec`, `validate_operations`) stay local; they
  are pure Python.
* Every method that touches files becomes a job:
  1. The native tree is snapshotted into the blob store.
  2. The job is submitted and the proxy waits on it, relaying progress events and checking
     for engine cancellation.
  3. Mutating methods are then **published** (§5).

## 5. Publication: atomic, idempotent, version-checked

The result of `create`, `apply` or `after_restore` replaces the artifact's native directory:

1. Let `current` be the digest of the native tree now.
2. If `current == output.digest()`, the result is already published: do nothing (idempotent).
3. If `current == base_digest` (the tree the job ran on), check that every output blob is
   present, build the new tree in a staging directory (verifying each blob's hash), and
   **rename-swap** it in. Readers see the old tree or the new one, never a mix.
4. Otherwise the tree changed while the job ran. This is a **version conflict**: the job is
   marked `conflict`, nothing is written, and the event is audited.

The engine then inspects, validates and records a revision exactly as for local execution
(checkpoint, scope preservation and constraints all apply).

## 6. Workers (`distributed/worker.py`, `runjob.py`)

The worker loop:

1. Register (name, capabilities, adapters).
2. Long-poll for a lease.
3. Fetch the job's blobs. Each is verified by hash and cached; the lease limits access to
   exactly that job's inputs.
4. Run the method in a **child process** (`python -m daedelus.distributed.runjob`), in its
   own session.
5. Heartbeat meanwhile. Kill the process group on cancel, deadline or lost lease.
6. Upload only the missing blobs; the control plane re-hashes every upload and rejects
   mismatches.
7. Complete.

Constraints on what a worker may do:

* Workers execute only the fixed adapter methods.
* Code-adapter test commands must be in the project's `allowed_commands` **and** in the
  worker's `DAEDELUS_WORKER_ALLOWED_COMMANDS`.
* A worker refuses to advertise `gpu` unless `DAEDELUS_GPU_VERIFIED=1` is set by an operator
  who checked the device.

For tests, fault injection is available through `DAEDELUS_WORKER_FAULT`: `crash_after_lease`,
`stall_heartbeat`, `corrupt_upload`, `duplicate_complete`, `slow:<s>`.

## 7. Restart recovery of executions

When an API server starts, it calls `Engine.interrupted()` for every project:

1. Executions left `running` have their running nodes reset to pending.
2. A local checkpoint taken by the interrupted node is restored.
3. The execution resumes in the background, and this is audited.

Remote jobs submitted by the resumed node carry the same deterministic idempotency key
(execution, node, method, input digest, args). A job that finished, or is still running on a
worker, is therefore **reused, not recomputed**. Publication then follows §5.

## 8. Live progress (SSE)

`GET /api/projects/{pid}/events` streams `execution`, `revision` and job events (`queued`,
`leased`, `progress`, `retry`, `succeeded`, `failed`, `timed_out`, `cancelled`, `conflict`,
`published`). Each event carries its sequence id, so a client resumes with `Last-Event-ID`
or `?since=`.

The studio holds one `EventSource` per open project. It replaces the V1 polling loops for
executions and artifact heads. The V2 walkthrough checks that no API request is made during
10 s of idle time.

## 9. Deployment topology

See [CLOUD_DEPLOYMENT.md](CLOUD_DEPLOYMENT.md). The images are:

* the control plane, *without* Blender or LibreOffice;
* `worker-blender`, `worker-office` and `worker-code`.

The compose file uses an `edge` network (published port bound to 127.0.0.1) and an internal
`jobs` network. Workers sit only on `jobs`, with no route out of the host.

## 10. What is not claimed

* The container tests run on **one Docker host**: a CI runner or this cloud container. That
  verifies the protocol across process and network boundaries. It is not a hosted
  multi-machine deployment (register N9).
* No GPU worker has run (N10). The `cloud_gpu` path is verified only up to "no GPU worker →
  explicit refusal".
* Throughput and latency figures in the V2 report are from local containers and are not
  cloud performance claims.
