# Deploying the Daedelus control plane and workers (V2.1)

> **Verification status (V2.1).**
> * The images and the three compose topologies below are built and exercised in this
>   repository's cloud container and in CI, as containers on **one Docker host**
>   (`deploy/cluster_e2e.py`, with `--hardened --tls` for the V2.1 topology).
> * The **two-machine hosted recipe** (control plane on host A behind a public TLS endpoint,
>   worker on host B) is automated in `.github/workflows/hosted.yml`. It runs on two
>   independent GitHub-hosted VMs. Its result is recorded in `docs/V2_1_REPORT.md` and
>   `evidence/v2_1/hosted/`.
> * GPU workers remain unverified (register N10).

## 1. Images (`deploy/`)

| Image | Contents | Build |
|---|---|---|
| `daedelus-control` | API, studio, engine, queue, blob store. **No Blender, no LibreOffice.** | `control.Dockerfile` |
| `daedelus-worker-office` | worker + LibreOffice (calc / writer / impress) + poppler + bubblewrap | `worker.Dockerfile --build-arg WITH_OFFICE=1` |
| `daedelus-worker-blender` | worker + Blender 4.5 (CPU) + bubblewrap | `worker.Dockerfile --build-arg WITH_BLENDER=1` |
| `daedelus-worker-code` | worker + git + pytest + bubblewrap | `worker.Dockerfile --build-arg WITH_GIT=1` |

`deploy/build-images.sh` builds all four with the tags compose expects:

```bash
deploy/build-images.sh                                   # python:3.12-slim-bookworm base
deploy/build-images.sh --base ubuntu:24.04 \
   --proxy http://127.0.0.1:3128 --ca /path/proxy-ca.pem  # behind a TLS-intercepting proxy
deploy/build-images.sh --blender-dir /opt/blender-4.5    # use a local Blender build
```

All images run as uid 10001. The CA bundle for a build proxy is passed as a BuildKit secret
and is never stored in an image. **Licensing:** the Blender worker image contains GPL-licensed
Blender. Read `docs/LICENSING_BRIEF.md` before publishing it.

## 2. Topologies (compose)

### 2.1 Development (one host, V2-compatible)

```bash
cp deploy/.env.example deploy/.env            # long random values
docker compose -f deploy/compose.yml --env-file deploy/.env up -d --build --wait
```

* `edge`: the control plane's port, published on 127.0.0.1 only.
* `jobs` (`internal: true`): workers and the control plane. Workers have no internet access.
* Workers talk plain HTTP on the internal network (`DAEDELUS_ALLOW_INSECURE_CONTROL=1`).
  They enrol with the join token and then use their own revocable credential.

### 2.2 Hardened containers (V2.1)

```bash
python deploy/provision.py --hardened          # one credential per worker -> deploy/secrets/
docker compose -f deploy/compose.yml -f deploy/compose.hardened.yml --env-file deploy/.env up -d
```

**Workers:**
* run as a non-root user with every Linux capability dropped and `no-new-privileges`;
* have a read-only root filesystem, with size-limited tmpfs as the only writable space;
* have PID, memory and CPU limits;
* use the seccomp profile `deploy/seccomp/worker-sandbox.json`: Docker's default plus
  unprivileged user namespaces, regenerated with `deploy/seccomp/make_profile.py`;
* run every job in a bubblewrap sandbox (`DAEDELUS_JOB_ISOLATION=bwrap`). The sandbox has no
  network, a read-only view of the system, a private job directory and its own PID
  namespace. The worker verifies it at start and reports it at registration.

`systempaths=unconfined` lets bubblewrap mount a fresh `/proc` inside the job's PID
namespace. The worker has no capabilities and runs as uid 10001, so the unmasked root-only
`/proc` files remain unreadable to it.

**Credentials:** each worker has its own credential, mounted as a compose secret. The control
plane accepts no join token in this topology.

### 2.3 TLS edge (V2.1, the single-host form of the hosted profile)

```bash
python deploy/provision.py --hardened --tls    # + private CA and server certificate
docker compose -f deploy/compose.yml -f deploy/compose.hardened.yml -f deploy/compose.tls.yml \
    --env-file deploy/.env up -d
curl --cacert deploy/tls/ca.crt https://localhost:8443/api/health
```

* Caddy terminates TLS 1.2/1.3 on `127.0.0.1:8443` and sends HSTS.
* The control plane publishes **no port** and sits on an internal `backend` network that
  only the edge shares.
* Workers reach the control plane through the edge over HTTPS and verify its certificate
  against the deployment CA (`DAEDELUS_CONTROL_CA`).
* The control plane runs `DAEDELUS_PROFILE=hosted`.
* Unknown Host headers are answered by the edge with 421 and never reach the application.

For a public deployment, replace the private CA with a real certificate. Caddy can obtain one
automatically when you give it a real domain name in `deploy/tls/Caddyfile`.

## 3. Hosted multi-machine recipe

Minimal topology (handoff §6.1):

| Host | Runs | Inbound |
|---|---|---|
| **A: control plane** | `daedelus serve --host 127.0.0.1 --behind-tls-proxy` (hosted profile), a TLS proxy (Caddy, a cloud load balancer, or a tunnel), persistent disk for the workspace | 443 only |
| **B: creative worker** | `daedelus worker` (hosted profile) with Blender and/or LibreOffice, bubblewrap | none |

Steps:

1. **Host A.**
   * Set `DAEDELUS_PROFILE=hosted`, `DAEDELUS_API_TOKENS` (long random values),
     `DAEDELUS_ALLOWED_HOSTS=<public name>` and `DAEDELUS_FORWARDED_ALLOW_IPS=<proxy address>`.
   * Start `daedelus serve --host 127.0.0.1 --port 8765 --behind-tls-proxy`.
   * Point the TLS proxy at `127.0.0.1:8765`. For SSE, turn off response buffering (Caddy:
     `flush_interval -1`).

   The hosted profile refuses to start without tokens, and refuses a non-loopback plaintext
   bind. Requests the proxy received over plain HTTP are rejected with 400.
2. **Credential for host B.** On host A, run
   `daedelus cluster credential create --name worker-b --adapters blender --out worker-b.cred`.
   Move the file to host B over a channel you trust. The hosted e2e seals it to a key
   generated on host B; see `deploy/hosted/handoff.py`.
3. **Host B.**
   * Install bubblewrap.
   * On Ubuntu 24.04, allow unprivileged user namespaces:
     `sysctl kernel.apparmor_restrict_unprivileged_userns=0`. Without that, the sandbox
     self-test fails, and the hosted profile refuses code jobs on that worker.
   * Run:
     ```
     DAEDELUS_PROFILE=hosted DAEDELUS_WORKER_CREDENTIAL_FILE=worker-b.cred \
     DAEDELUS_BLENDER=/opt/blender/blender daedelus worker \
     --control https://<public name> --name worker-b
     ```
   * The certificate is verified against the system store, or against
     `DAEDELUS_CONTROL_CA` for a private CA. A plain-HTTP control URL is refused.
   * Optional mutual TLS: set `DAEDELUS_WORKER_TLS_CERT`/`_KEY` and require client
     certificates at the proxy.
4. In the studio, choose *Cloud CPU* for the project, or per node.

`.github/workflows/hosted.yml` performs exactly this on two GitHub-hosted runners, with a
Cloudflare quick tunnel as host A's TLS endpoint. It then runs the mandatory hosted tests
(H1–H10; see `deploy/hosted/control_e2e.py`). Trigger it with *Run workflow* or with a commit
message containing `[hosted-e2e]`.

## 4. Credentials and rotation

| Operation | Command |
|---|---|
| Create (secret shown once) | `daedelus cluster credential create --name W --adapters blender --capabilities cpu [--out FILE]` or `POST /api/cluster/credentials` (admin token) |
| List (no secrets) | `daedelus cluster credential list` / `GET /api/cluster/credentials` |
| Rotate (operator) | `daedelus cluster credential rotate ID [--grace 60]`. The old secret stays valid for the grace period. |
| Rotate (worker itself) | `daedelus worker --rotate-hours 24`, with `DAEDELUS_WORKER_CREDENTIAL_FILE` set so the new secret persists |
| Revoke | `daedelus cluster credential revoke ID` / `POST /api/cluster/credentials/ID/revoke`. The worker's leases are revoked at once and the worker stops (exit 3). |
| API tokens | Add the new token next to the old one in `DAEDELUS_API_TOKENS`, restart, then remove the old one. Browser sessions created with a removed token stop working immediately. |

## 5. Environment reference

| Variable | Where | Meaning |
|---|---|---|
| `DAEDELUS_PROFILE` | both | `hosted` turns on the hosted rules (`daedelus/profile.py`). |
| `DAEDELUS_API_TOKENS` | control | `tok` or `tok:prj_a\|prj_b`, comma-separated. Required for a non-loopback bind and in the hosted profile. |
| `DAEDELUS_WORKER_TOKENS` | control | Join tokens that may **enrol** workers (development). Disabled in the hosted profile unless `DAEDELUS_ALLOW_WORKER_ENROLLMENT=1`. |
| `DAEDELUS_TLS_CERT` / `_KEY` / `_CLIENT_CA` | control | Built-in HTTPS (and optional client certificates). |
| `DAEDELUS_BEHIND_TLS_PROXY`, `DAEDELUS_FORWARDED_ALLOW_IPS` | control | Trust `X-Forwarded-*` headers from these proxy addresses. |
| `DAEDELUS_ALLOWED_HOSTS`, `DAEDELUS_CORS_ORIGINS` | control | Extra Host headers / browser origins. |
| `DAEDELUS_SESSION_HOURS` | control | Browser session lifetime (default 12; 2 h idle). |
| `DAEDELUS_PATH_SOURCE_ROOTS` | control | Directories server-side path sources may read. In the hosted profile path sources are refused without it. |
| `DAEDELUS_FETCH_ALLOWLIST`, `_ALLOW_PORTS`, `_MAX_MB`, `DAEDELUS_FETCH_VIA_PROXY` | control | Outbound URL-ingestion policy. |
| `DAEDELUS_LEASE_S`, `DAEDELUS_MAX_BLOB_MB` | control | Lease length (default 30 s) and upload cap. |
| `DAEDELUS_WORKER_CREDENTIAL` / `_CREDENTIAL_FILE` | worker | Its own credential (preferred). |
| `DAEDELUS_WORKER_TOKEN` | worker | Join token (development enrolment). |
| `DAEDELUS_CONTROL_URL`, `DAEDELUS_CONTROL_CA` | worker | HTTPS URL; private CA bundle. |
| `DAEDELUS_ALLOW_INSECURE_CONTROL` | worker | Allows a plain-HTTP control URL on isolated development networks. Never honoured in the hosted profile. |
| `DAEDELUS_JOB_ISOLATION` | worker | `auto` (default), `bwrap` (refuse to start without it), or `process`. |
| `DAEDELUS_REQUIRE_SANDBOX` | worker | Adapters only offered with a verified network-isolating sandbox (default `code` in the hosted profile). |
| `DAEDELUS_JOB_MEMORY_MB`, `_MAX_FILE_MB`, `_NOFILE`, `_NPROC` | worker | Per-job resource limits. |
| `DAEDELUS_WORKER_CAPABILITIES`, `DAEDELUS_GPU_VERIFIED` | worker | `cpu` or `cpu,gpu` (GPU only after operator verification). |
| `DAEDELUS_WORKER_ADAPTERS`, `DAEDELUS_WORKER_ALLOWED_COMMANDS` | worker | Offered adapters; test commands the worker may run. |

## 6. Operations

* **Health:** `GET /api/health`.
* **Status:** `GET /api/cluster/status`. Each worker entry shows its deployment, isolation
  self-test and credential kind. The studio's ⚙ *Execution* panel shows the same.
* **Audit:** `GET /api/cluster/audit?n=500`, or `cluster/audit.jsonl` on the volume. It
  records enrolments, revocations, lease owner mismatches, CSRF rejections, sessions,
  publications and conflicts.
* **Logs:** the control plane writes warnings to stdout (collect them with the container
  runtime). Each job's logs are stored with its result (`GET /api/projects/{pid}/jobs/{id}`).
* **Restart handling:**
  * Queued and leased jobs survive a control-plane restart, and workers keep heartbeating.
  * Interrupted executions resume.
  * Remote jobs are reused through their idempotency keys.
  * Model plans recorded before the restart are reused if the unit is unchanged (V2.1), so
    no model call is repeated.
  * Workers ride out 502/503/504 answers from a proxy while the control plane restarts
    (V2.1).
* **Backup.** Stop the control plane (or snapshot the volume atomically) and copy
  `/data/workspace` as **one unit**: `projects/`, `cluster/queue.db*`, `cluster/blobs/`,
  `cluster/auth.db*` and `cluster/audit.jsonl` refer to each other. Example:
  ```
  docker compose stop control
  docker run --rm -v daedelus_control-data:/data -v $PWD:/b alpine tar czf /b/daedelus-backup.tgz -C /data .
  docker compose start control
  ```
* **Restore.** Into an empty volume:
  ```
  docker run --rm -v daedelus_control-data:/data -v $PWD:/b alpine tar xzf /b/daedelus-backup.tgz -C /data
  ```
  Then start the control plane. Leases that expired while it was down are re-queued.
  Workers re-register automatically. Revoke worker credentials that may have leaked with the
  backup: `cluster/queue.db` holds only hashes, but the backup is still sensitive.
* **Cleanup / deletion:**
  * `docker compose ... down -v` removes containers, networks and the data volume.
  * `rm -rf deploy/secrets/*.cred deploy/tls/*.key` removes the local secrets.
  * Revoke credentials that were used outside this host.
  * The hosted workflow creates no persistent resources: its VMs, tunnel and credentials end
    with the run, and its artifacts expire after 1 day (handoff) or 30 days (evidence).

## 7. Microsoft / Google connectors: least-privilege setup

| Service | Token variable | Scope | Notes |
|---|---|---|---|
| Google Drive | `DAEDELUS_GOOGLE_TOKEN` | `https://www.googleapis.com/auth/drive.file` | Covers only files the app created or the user opened with it. Add `drive.readonly` only to import pre-existing Google-native files. |
| Microsoft Graph | `DAEDELUS_MSGRAPH_TOKEN` | `Files.ReadWrite` (delegated) | For a tighter grant use `Files.SelectedOperations.Selected` and assign only the test folder. |

**Safe test account procedure** (for `daedelus connectors-live`, see
`backend/daedelus/connectors_live.py`):

1. Use a dedicated test account, or at minimum a dedicated empty folder that you own.
2. Obtain a short-lived OAuth access token for that account with the scope above. The token
   is passed through the environment or CI secrets only, never written into a project or
   file.
3. Set `DAEDELUS_LIVE_CONNECTORS=1`, the token and `DAEDELUS_LIVE_<GOOGLE|MSGRAPH>_FOLDER`.
4. Run `daedelus connectors-live --connector google|msgraph`. The harness creates its own
   `daedelus-live-test-<id>.xlsx`, edits, publishes and conflicts only on that file, and
   deletes it at the end. It never modifies anything it did not create.

In CI, use `.github/workflows/live.yml` (manual dispatch, protected environment
`daedelus-live`).
