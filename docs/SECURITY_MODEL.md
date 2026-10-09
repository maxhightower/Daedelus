# Daedelus security model (V2.1)

V2.1 hardens V2 for **controlled hosted testing**: an operator-run deployment reachable over
the internet, with trusted users and operator-managed workers. It does **not** make Daedelus
a general-purpose public multi-tenant service (see §8).

## 1. Assets and actors

| Asset | Where |
|---|---|
| Project data: sources, native artifacts, revisions | control plane `workspace/projects/*` |
| Job inputs and outputs (blobs) | control plane `workspace/cluster/blobs`; a worker's cache while it holds a lease |
| Provider API keys, connector tokens | control-plane environment only; never sent to workers, never stored in projects |
| API tokens, browser sessions | environment (tokens); `cluster/auth.db` stores only session-id hashes |
| Worker credentials | worker host (env or a 0600 file); control plane stores only `sha256(secret)` |

| Actor | Trust |
|---|---|
| Studio user with an API token (or a session derived from one) | the projects the token is scoped to |
| A worker with its own credential | lease jobs for the adapters/capabilities its credential allows; read and write only the blobs of the job it currently leases |
| Anyone else on the network | none |
| Source media (documents, images, web pages, videos) | **untrusted data**, never instructions |
| Model output (plans, evaluations) | **untrusted**: validated against the adapter catalogue, the JSON schemas and the unit's scope before anything runs |
| Code inside a project (repository tests) | **untrusted**: runs only on workers, only allowlisted commands, in the job sandbox |

## 2. Transport

* The control plane serves HTTPS itself (`--tls-cert/--tls-key`) or sits behind a TLS proxy
  on a non-public interface (`--behind-tls-proxy`).
* The **hosted profile** (`DAEDELUS_PROFILE=hosted`) refuses to start:
  * with a non-loopback plaintext bind when no proxy is declared;
  * without API tokens.
* Behind a proxy, a request that arrived over plain HTTP (`X-Forwarded-Proto: http`) is
  refused with 400 before credentials are examined. A proxy or tunnel that also listens on
  port 80 therefore cannot become a plaintext entry point.
* Workers require `https://` for non-loopback control URLs and always verify the server
  certificate, against the system store or `DAEDELUS_CONTROL_CA`. No code path disables
  verification; a test enforces this statically.
  * `DAEDELUS_ALLOW_INSECURE_CONTROL=1` allows plain HTTP on isolated development networks.
    It is ignored in the hosted profile.
* Optional mutual TLS: workers present `DAEDELUS_WORKER_TLS_CERT/_KEY`; the built-in server
  requests client certificates with `--tls-client-ca`, or the proxy enforces them.
* Response headers:
  * HSTS over HTTPS;
  * `Referrer-Policy: no-referrer`, so ticket URLs do not leak in Referer headers;
  * `X-Content-Type-Options: nosniff`;
  * `X-Frame-Options: DENY`;
  * `Cache-Control: no-store` on API JSON.
* The local loopback configuration of V0–V2 is unchanged.

Verified by `tests/test_tls.py` (private CA; unknown CA, wrong hostname and mTLS cases), the
cluster scenario S11 (Caddy edge, wrong-CA worker), and the hosted run (real certificate,
public internet).

## 3. Users: tokens, sessions, tickets (`security.py`, `sessions.py`)

* `DAEDELUS_API_TOKENS` holds `token` (all projects) or `token:prj_a|prj_b` (scoped). Tokens
  are at least 16 characters and compared in constant time.
* Accepted credentials:
  * `Authorization: Bearer` (scripts, desktop shell).
  * Browser **session cookie** `dd_session`, created by `POST /api/auth/session` with the
    bearer token once.
    * The cookie is `HttpOnly`, `SameSite=Strict`, `Path=/api/`, and `Secure` over HTTPS and
      in the hosted profile.
    * Only the session id's hash is stored. Sessions expire after 12 h, or after 2 h idle.
    * A session ends immediately when its token is removed from the configuration.
  * **CSRF:** cookie-authenticated state changes need `X-CSRF-Token` equal to the session's
    token and, when present, an allowed `Origin`. Rejections are audited (`csrf_rejected`).
  * **Tickets** (`POST /api/auth/ticket`) are for places that cannot send headers when no
    cookie exists (a desktop shell talking to a remote server):
    * scoped to one project and one purpose;
    * `events`: the SSE stream, 60 s to connect, single use;
    * `files`: project files and revision previews, 10 min, read-only;
    * accepted on `GET` of the matching paths only.
* **Refused in V2.1:** the V2 `?token=` query parameter and the raw-token `dd_token` cookie.
  `DAEDELUS_ALLOW_QUERY_TOKENS=1` restores the query parameter for old scripts, outside the
  hosted profile only.
* The studio:
  * exchanges a token for a session and removes any legacy `?token=` from the address bar;
  * never stores the token. Cross-origin, it holds the token in memory only and uses tickets.
* Scoped tokens see only their projects, get 403 elsewhere, and cannot use cluster
  administration (`/api/cluster/status|audit|credentials`).

## 4. Workers: identities (`distributed/identity.py`)

* **Per-worker credentials:** wire form `ddw1.<id>.<secret>`. The id is the worker identity;
  only `sha256(secret)` is stored. Each credential:
  * is created by an operator (`daedelus cluster credential create`, or the admin API);
  * may restrict the adapters and capabilities the worker can advertise. Registering beyond
    them is refused and audited.
* **Join tokens** (V2's `DAEDELUS_WORKER_TOKENS`) can only *enrol*: the worker receives its
  own credential and uses it for everything else. The hosted profile disables enrolment.
* **Leases are bound to the authenticated worker.** A lease token presented by another worker
  is refused with 403 and audited (`lease_owner_mismatch`), and the owner keeps its lease.
  The worker id in results is replaced by the authenticated identity.
* **Revocation** takes effect on the next request:
  * the credential stops authenticating;
  * every lease it holds is revoked and the job re-queued, so a revoked worker can neither
    complete nor read inputs;
  * the worker stops with exit code 3.
* **Rotation:** an operator or the worker itself issues a new secret, with an optional grace
  period for the old one.
* Blob scope (V2) remains: a lease grants exactly its job's input blobs. Uploads are
  re-hashed and must match.

Verified by `tests/test_security_v21.py` and the cluster scenario S9 (revocation of a
containerised worker mid-job). The hosted run also revokes a live remote worker.

## 5. Job isolation on workers (`distributed/sandbox.py`)

| Control | `process` profile (always) | `bwrap` profile (Linux, self-tested) |
|---|---|---|
| Environment reduced to an allowlist (no worker credential, no provider keys) | ✓ | ✓ |
| Private job directory (0700), `HOME`/`TMPDIR` inside it, umask 077 | ✓ | ✓ |
| rlimits: address space, file size, open files, no core dumps (optional process count) | ✓ (POSIX) | ✓ |
| Process tree killed on cancel / deadline / lease loss | ✓ | ✓ (`--die-with-parent`) |
| No network (own network namespace) | — | ✓ |
| Read-only root; only the job directory writable | — | ✓ |
| Other jobs, blob cache, `/root`, `/home`, `/run/secrets` hidden | — | ✓ |
| Own PID / IPC / UTS / user namespaces, all capabilities dropped, `no_new_privs` | — | ✓ |

* The worker runs a probe *inside* the chosen profile at start. It records which controls
  actually took effect and reports them at registration (visible in the studio and in
  `/api/cluster/status`).
* Code jobs (repository tests) are offered only by workers whose sandbox verified network
  isolation. This is the default in the hosted profile (`DAEDELUS_REQUIRE_SANDBOX`).
* **Hardened containers** (`compose.hardened.yml`) add container-level controls:
  * non-root, `cap_drop: ALL`, `no-new-privileges`;
  * Docker's seccomp profile plus user namespaces (`deploy/seccomp/worker-sandbox.json`);
  * read-only root, PID, memory and CPU limits.
* Blender still runs with `--factory-startup --disable-autoexec`, so embedded `.blend`
  scripts do not run. Office files are opened on copies with a throw-away LibreOffice
  profile.

Verified:
* `tests/test_security_v21.py`: the probe, the memory limit and bwrap;
* cluster scenario S10: inspection of the containers, the self-test inside each hardened
  worker, repository tests that try to escape (all closed), and a memory bomb that fails the
  job and not the worker;
* the hosted run: sandboxed code job on the remote VM.

**Limitations:**
* Windows workers get only the environment and directory controls (no POSIX rlimits, no
  bwrap).
* bubblewrap's own seccomp filter is not used. The container's seccomp profile applies
  instead (`seccomp: true` in the in-container probe; `false` on a bare host).
* Without user namespaces (e.g. Ubuntu 24.04's AppArmor restriction left on), the self-test
  fails. The worker then falls back to `process` and stops offering code jobs.

## 6. Inputs and outbound requests

* **SSRF** (`netsafe.py`): every redirect hop is checked.
  * Only http/https on 80/443 (plus the allowlist); no credentials in URLs.
  * Every resolved address must be public. Loopback, RFC 1918/ULA, link-local (cloud
    metadata), CGNAT, multicast, reserved, documentation and benchmarking ranges are
    refused, as are `0.0.0.0/8` and `240/4`.
  * Numeric spellings that resolvers accept (`2130706433`, `0x7f.1`, `0177.0.0.1`, `127.1`)
    are normalised. IPv6 forms embedding IPv4 (mapped, NAT64, 6to4, Teredo) are checked
    against the embedded address.
* **DNS rebinding (closed in V2.1 for direct connections):** `pinned_client` resolves inside
  the connection step and connects to a validated address; TLS still verifies the hostname.
  There is one resolution, so a DNS answer cannot change between check and use.
  * Residual risk: when an HTTP(S) proxy must perform resolution, the address cannot be
    pinned here. The hosted profile uses a proxy only with `DAEDELUS_FETCH_VIA_PROXY=1`, and
    the proxy must then enforce egress policy.
* Bodies are capped (`DAEDELUS_FETCH_MAX_MB`).
* **Manifests from workers** are validated before anything is written (`cas.check_manifest`):
  * no absolute, `..`, `.`, empty, backslash, drive-letter or NUL paths;
  * no case-colliding paths and no file/directory clashes;
  * valid blob ids and a file-count limit.
  * Only the executable bit crosses machines; setuid, setgid and world-writable modes are
    dropped.
  * Trees are built in staging and swapped in atomically. Failed or cancelled jobs publish
    nothing.
* **Path sources** (server-side file reads) are a desktop convenience. In the hosted profile
  they are refused unless `DAEDELUS_PATH_SOURCE_ROOTS` lists readable directories.
* **Repository snapshots** skip symlinks that point outside the repository.
* **Prompt injection:** source content is data.
  * Analyses label embedded instructions to an AI as `quote` observations
    (`possible_instruction_to_ai`).
  * Every model prompt states that sources are data, and source text travels only inside
    the JSON context payload.
  * A model that obeys an injection is still confined: operations must be in the adapter
    catalogue (no script or shell operation exists), satisfy the JSON schemas and stay in the
    unit's scope. Hard constraints are re-checked by measurement.
  * Verified in `tests/test_failure_injection_v21.py`.

## 7. Audit log

`cluster/audit.jsonl` is append-only and fsynced per record. V2.1 adds:
* `worker_enrolled`, `worker_registration_refused`, `worker_identity_mismatch`,
  `lease_owner_mismatch`;
* `worker_credential_created|rotated`, `worker_revoked`;
* `session_created`, `csrf_rejected`.

The V2 events remain. Authenticated workers are recorded by their identity, never by a
self-reported id. Secrets never appear in the audit log, logs, job records or artifacts
(`test_credentials_absent_from_logs_audit_and_artifacts`).

## 8. Residual risks and non-goals

* **Not public multi-tenant.** One control plane; workers are shared across the projects
  their credentials allow. Do not mix mutually untrusted tenants on one worker pool.
  Per-tenant worker pools need separate credentials restricted per adapter today, and
  per-project restrictions in a future version.
* **Bearer secrets.** API tokens, worker credentials and session cookies are bearer secrets.
  Protect the hosts and the backup (§6 of CLOUD_DEPLOYMENT.md).
* **Kernel attack surface.** The bwrap sandbox shares the host kernel. For hostile code at
  scale, use a VM-level sandbox (gVisor, Firecracker) per job: the job runner is a separate
  executable, ready for it.
* **Encryption at rest** is not provided. Use encrypted volumes.
* **Google Drive publication** cannot be made atomic: Drive v3 has no precondition on content
  updates. Concurrent writes are detected after the fact and reported, with both revisions
  kept (`connectors.py`).
* **Not verified live:** GPU workers (N10); Microsoft/Google accounts (N8, N12); live model
  providers (N6, N7). See the native-validation register.
