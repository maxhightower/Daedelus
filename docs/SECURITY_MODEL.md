# Daedelus security model (V2)

## Assets and actors

| Asset | Where |
|---|---|
| Project data: sources, native artifacts, revisions | control plane `/data/workspace/projects/*` |
| Job inputs and outputs (blobs) | control plane `/data/workspace/cluster/blobs`; a worker's cache while it holds a lease |
| Provider API keys (Anthropic / Gemini) and connector tokens (Graph / Google) | control-plane environment only; never sent to workers, never stored in projects |

| Actor | Trust |
|---|---|
| Studio user holding an API token | full access to the projects the token is scoped to |
| Worker holding the worker token | can lease jobs and read/write **only the blobs of the job it currently leases** |
| Anyone else on the network | none |
| Source media (documents, images, web pages, videos) | **untrusted data**; never treated as instructions (V1.1 prompt-injection handling) |

## Controls

### Exposure
* `daedelus serve` binds `127.0.0.1` by default and **refuses a non-loopback bind unless
  `DAEDELUS_API_TOKENS` is set**.
* The compose file publishes the control plane on `127.0.0.1` only.
* `TrustedHostMiddleware` accepts only loopback Host headers, plus `DAEDELUS_ALLOWED_HOSTS`.
  This blocks DNS-rebinding attacks from web pages against a local server.
* CORS is limited to the desktop shell and loopback origins, plus `DAEDELUS_CORS_ORIGINS`.
  V1 allowed `*`; this was tightened in V2.

### Authentication and project scope (`security.py`)
* `DAEDELUS_API_TOKENS` holds comma-separated entries. `token` means all projects (admin);
  `token:prj_a|prj_b` scopes a token to those projects. Tokens must be at least 16 characters
  and are compared in constant time.
* Every `/api` request needs a token, except `/api/health` and the worker protocol. The token
  can be sent as an `Authorization: Bearer` header, a `token` query parameter (for `<img>`
  and `EventSource`), or the `dd_token` cookie.
* Scoped tokens:
  * see only their projects in `/api/projects`;
  * get 403 on any other project path;
  * cannot create projects or use `/api/cluster/status|audit`.
* The studio takes a token from `?token=`, keeps it in `sessionStorage` for that tab only,
  and sends it as a bearer header.

### Worker plane
* Worker endpoints are **disabled** unless `DAEDELUS_WORKER_TOKENS` is set.
* Every lease attempt has its own random token. Only its sha256 is stored.
* Blob downloads are limited to the blobs listed in the leased job: the native manifest, the
  `before` manifest, and context files. A token for job A cannot read anything else, and such
  attempts are audited as `blob_scope_denied`.
* Uploads are hashed by the control plane and must match the claimed sha256; mismatches are
  rejected with 422 and audited. A size cap is set by `DAEDELUS_MAX_BLOB_MB`.
* Manifests are checked for path traversal (`..`, absolute paths) and symlinks before
  anything is materialised.
* Workers run only the fixed adapter methods, each in a killable child process. **No
  arbitrary commands.** Code tests must appear in both the project allowlist and the worker
  allowlist (`DAEDELUS_WORKER_ALLOWED_COMMANDS`).
* In compose, workers sit on an `internal` network: no internet egress and no exposure.
  They reach only the control plane.
* Workers never receive provider keys or connector tokens. Planning happens on the control
  plane.

### Outbound requests (SSRF, `netsafe.py`)
URL ingestion (web pages, video metadata, thumbnails) goes through `safe_get`, which checks
**every redirect hop** against these rules:
* only http/https on ports 80/443, plus any in `DAEDELUS_FETCH_ALLOW_PORTS`;
* no credentials in the URL;
* every resolved address must be public. Loopback, RFC 1918 / ULA, link-local (including
  169.254.169.254 cloud metadata), CGNAT, multicast, reserved, unspecified, and IPv4-mapped
  forms of these are refused;
* the optional domain allowlist `DAEDELUS_FETCH_ALLOWLIST` applies;
* at most 3 redirects;
* the body is capped at 10 MB by default.

Connector traffic (Graph / Drive) goes only to fixed API hosts and validated ids (V1.2).

### Audit log
`cluster/audit.jsonl` is append-only and fsynced per record. It records:
* authentication failures (API and worker), scope denials and lease rejections;
* worker registrations, leases and completions;
* blob hash mismatches and blob-scope denials;
* publications, republications and conflicts;
* late results discarded;
* cancellations, execution-target changes, resumed executions, and reaper actions.

`GET /api/cluster/audit` requires an admin (unscoped) token.

## Residual risks and non-goals
* **Transport.** The control plane serves plain HTTP. Put TLS in front of it (reverse
  proxy or load balancer) whenever traffic leaves a single host. Worker and API tokens are
  bearer secrets.
* **DNS rebinding at fetch time.** Addresses are checked before connecting. When an HTTP(S)
  proxy performs DNS resolution, the resolved address is not pinned (TOCTOU).
* **Shared worker token.** Any holder can register as a worker and lease jobs, and so read
  those jobs' inputs. Per-worker credentials and mTLS are not implemented.
* **Isolation inside a worker.** Jobs run as a non-root user in a child process. There is
  no seccomp or gVisor sandbox. Adapters process untrusted files (e.g. `.blend` files can
  carry Python; Blender runs with `--factory-startup --disable-autoexec`, so embedded scripts
  do not run; Office files are opened with libraries and headless LibreOffice).
* **Encryption at rest** is not provided; use encrypted volumes.
* **Multi-tenancy.** Tokens are scoped by project, but there is a single control plane and
  workers are shared across projects. Do not mix mutually untrusted tenants on one worker
  pool.
* **Not verified live.** Hosted deployment, real TLS termination and GPU workers (register
  N9, N10).
