# Deploying the Daedelus control plane and workers

> **Verification status.** The images and the compose topology below are built and exercised
> in CI and in a cloud development container, as containers on **one Docker host**
> (`deploy/cluster_e2e.py`). A hosted multi-machine deployment and GPU workers have **not**
> been verified (native-validation register N9, N10). The guidance for those is labelled
> *unverified*.

## Images (`deploy/`)

| Image | Contents | Dockerfile |
|---|---|---|
| `daedelus-control` | API, studio, engine, queue, blob store. **No Blender, no LibreOffice.** | `control.Dockerfile` |
| `daedelus-worker-office` | worker + LibreOffice (calc / writer / impress) + poppler | `worker.Dockerfile --build-arg WITH_OFFICE=1` |
| `daedelus-worker-blender` | worker + Blender 4.5 (CPU) | `worker.Dockerfile --build-arg WITH_BLENDER=1` |
| `daedelus-worker-code` | worker + git + pytest | `worker.Dockerfile --build-arg WITH_GIT=1` |

All images run as uid 10001.

Optional build arguments:

* `PYTHON_IMAGE` / `NODE_IMAGE` select base images from a mirror, e.g.
  `mirror.gcr.io/library/python:3.12-slim-bookworm` when Docker Hub rate-limits.
* An optional BuildKit secret `ca` supplies an extra CA bundle for TLS-intercepting build
  proxies. It is never stored in the image.

## Single host (compose)

```bash
cp deploy/.env.example deploy/.env      # then put long random values in it
docker compose -f deploy/compose.yml --env-file deploy/.env up -d --build --wait
open "http://127.0.0.1:8765/?token=<one of DAEDELUS_API_TOKENS>"
```

Topology:

* **Networks.**
  * `edge`: the control plane's port, published on **127.0.0.1 only**.
  * `jobs` (`internal: true`): workers and the control plane. Workers have no internet
    access and no published ports.
* **Volume.** `control-data` holds `/data/workspace`: projects, `cluster/queue.db`, blobs and
  `audit.jsonl`. Back it up as one unit, because the queue and the blob store refer to each
  other.
* **Scaling.** `docker compose up -d --scale worker-office=3` adds workers. Jobs are leased
  one at a time per worker.
* **Projects.** In the studio, open ⚙ *Execution* (top bar) and choose *Cloud CPU* (or
  *Automatic*). Per node, set the agent node's `execution` field.

## Environment reference

| Variable | Where | Meaning |
|---|---|---|
| `DAEDELUS_API_TOKENS` | control | `tok` or `tok:prj_a\|prj_b`, comma-separated. Required for a non-loopback bind. |
| `DAEDELUS_WORKER_TOKENS` | control | Accepted worker secrets. Unset means the worker endpoints are disabled. |
| `DAEDELUS_ALLOWED_HOSTS` | control | Extra Host headers, e.g. the service name `control` or a public DNS name. |
| `DAEDELUS_CORS_ORIGINS` | control | Extra browser origins. |
| `DAEDELUS_LEASE_S` | control | Lease length (default 30 s; compose uses 20). A worker heartbeats every lease/3. |
| `DAEDELUS_RESUME_INTERRUPTED` | control | `0` disables automatic resumption after restart. |
| `DAEDELUS_FETCH_ALLOWLIST`, `DAEDELUS_FETCH_ALLOW_PORTS`, `DAEDELUS_FETCH_MAX_MB` | control | Outbound URL-ingestion policy. |
| `DAEDELUS_WORKER_TOKEN` | worker | Its secret. |
| `DAEDELUS_CONTROL_URL` | worker | e.g. `http://control:8765` |
| `DAEDELUS_WORKER_CAPABILITIES` | worker | `cpu`, or `cpu,gpu`. GPU also requires `DAEDELUS_GPU_VERIFIED=1`. |
| `DAEDELUS_WORKER_ADAPTERS` | worker | Defaults to every adapter whose tools are present. |
| `DAEDELUS_WORKER_ALLOWED_COMMANDS` | worker | Test commands the worker may run (default `python -m pytest`). |
| `DAEDELUS_MAX_BLOB_MB` | control | Upload cap per blob (default 2048). |

## Multi-host (unverified guidance)

1. **Control plane** on one VM, with a persistent disk for `/data` and TLS terminated in
   front of it (Caddy, nginx or a cloud load balancer). Set `DAEDELUS_ALLOWED_HOSTS` to the
   public name, and expose only 443.
2. **Workers** on separate VMs, or a managed container service, in a private subnet. Allow
   egress to the control plane's private address only and point `DAEDELUS_CONTROL_URL` at
   it. Workers need no inbound ports.
3. **GPU workers.** Use the Blender image on a GPU host with the vendor container toolkit.
   Verify that Cycles sees the device, then set `DAEDELUS_GPU_VERIFIED=1` and
   `DAEDELUS_WORKER_CAPABILITIES=cpu,gpu`. *Not performed in this repository (N10).*
4. Rotate tokens by adding the new value next to the old one (both comma-separated), then
   restarting the workers with the new token and removing the old one.

## Operations

* **Health.** `GET /api/health`; the compose healthcheck uses it.
* **Status.** `GET /api/cluster/status` (admin token) lists workers, liveness and job counts
  by state.
* **Audit.** `GET /api/cluster/audit?n=500`, or read `cluster/audit.jsonl` on the volume.
* **Draining a worker.** Stop it with SIGTERM. It finishes nothing new, and its current lease
  is re-queued after expiry.
* **Restarting the control plane is safe.**
  * Queued and leased jobs survive.
  * Workers keep heartbeating.
  * Interrupted executions resume, and the jobs they had submitted are reused through their
    idempotency keys.
