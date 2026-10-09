# Daedelus V2.1: capability activation and cloud hardening (report)

**Summary.** V2.1 is an **engineering implementation with outstanding external validation**.
It is not a fully activated production service.
* **Verified:**
  * Security hardening: deterministic tests, integration runs and a hosted cluster run.
  * Hosted multi-machine execution, on two independent GitHub-hosted cloud VMs.
  * The distributed performance target, met with safety controls left on.
* **Blocked:** live Claude, live Gemini and live Microsoft/Google connectors. No credentials or
  test accounts were provided. Every harness for them is built and fixture-tested, and each one
  reports BLOCKED. None of them claims a live result.

Status vocabulary (handoff §15):
* **Implemented:** production code exists.
* **Deterministically verified:** tests passed against controlled inputs.
* **Integration verified:** a real application or local service integration passed.
* **Live AI verified:** real model calls performed the operation.
* **Hosted cloud verified:** control and worker ran on independent hosted machines.
* **Live connector verified:** a real external account completed the tests.
* **Blocked:** a required external capability or credential was unavailable.
* **Failed:** a test ran but did not meet its acceptance conditions.
* **Planned:** not implemented.

## 1. Branch and SHAs

| | |
|---|---|
| Working branch | `claude/blissful-clarke-sow429` (session-mandated name), mirrored to `opus/daedelus-v2-1-activation-hardening` at freeze |
| Base | `opus/daedelus-v2-distributed-execution` @ `cdc2597` (frozen V2 application code `923ca4f`) |
| Final V2.1 application code | **`733c1b6`** (Linux CI green; hosted run 37884783288 39/39 on this exact commit) |
| Hosted runs | GitHub Actions [37882286579](https://github.com/maxhightower/Daedelus/actions/runs/37882286579) (code `37322df`, 37/39), [37883447177](https://github.com/maxhightower/Daedelus/actions/runs/37883447177) (`5860085`, 38/39), **[37884783288](https://github.com/maxhightower/Daedelus/actions/runs/37884783288) (`733c1b6`, 39/39)** |

No merge into `main` and no pull request were made.

## 2. Frozen application code

`733c1b6`. All later commits touch only evidence and documentation.

## 3. Implementation by phase

| Phase | What was built | Status |
|---|---|---|
| 0. Baseline | Full V0–V2 regression on `cdc2597` in a clean worktree: backend 144/4 skipped; studio 14/14; V0 40/40; V1.1 A–D (E blocked); V1.2 28/28; walkthroughs 60/19/23/15; cluster 42/42 | Integration verified |
| 1. Security | **Worker identity:** per-worker hashed credentials `ddw1.<id>.<secret>`, adapter and capability restrictions, join tokens that only enrol, leases bound to the authenticated worker, revocation that also revokes held leases, rotation with a grace period. **Transport:** built-in TLS or TLS-proxy mode, HTTPS-only behind proxies, HSTS and security headers, certificate verification on workers, optional mTLS. **Browser:** HttpOnly SameSite=Strict session cookie with CSRF token and Origin check, short-lived project-scoped tickets, query tokens refused, sign-in screen. **Job isolation:** environment allowlist, private job directories, rlimits; a bubblewrap profile verified by a self-test probe; hardened containers with seccomp. **Inputs:** connect-time DNS pinning against SSRF, numeric and IPv6-embedded address normalisation, manifest validation, symlink-safe repository snapshots. **Hosted profile:** `DAEDELUS_PROFILE=hosted` | Deterministically, integration and hosted cloud verified |
| 2. Live AI | **Provider audit** (Claude `claude-opus-5-5` with adaptive thinking, explicit effort, structured output and server-side refusal fallback; Gemini `gemini-3.8-flash` with the Files API for large videos). Typed errors (timeout, rate limit, over budget). Usage, cost and provenance, with unknown prices reported as unknown. **Execution budgets**, enforced before each call and covering remote jobs. **Plan reuse** after a restart. **Preflight** confirmation. **Benchmark** tasks A–E on licensed references | Implemented and deterministically verified; **live: Blocked** |
| 3. Hosted | Two-VM GitHub Actions recipe: control behind a Cloudflare quick tunnel; worker on a separate VM in the hosted profile with bubblewrap; credentials sealed to a key made on the worker VM; phases driven by commit status; scenarios H1–H10 plus agent workflow and revocation | **Hosted cloud verified** (§8) |
| 4. Connectors | Drive conservative publication (pinned backup revision, post-write race detection, `ConnectorConflict(written=True)`), Graph `If-Match`, discovery and creation, deletion, revision APIs; opt-in live harness that touches only files it creates; least-privilege scopes documented | Implemented and deterministically verified; **live: Blocked** |
| 5. Performance | Inspect cache keyed by content digest, post-inspect from mutating jobs, batched side-effect scopes, event-driven queue, lazy adapter import; per-phase worker timings, bytes and isolation reported | **Deterministically and integration verified**: Office pipeline −36 % with the sandbox on |
| 6. Regression and docs | V2.1 regression, studio walkthrough, evidence, documentation, freeze | see §4 and §14 |

Defects found and fixed during V2.1 (each with a regression test):
* **Data loss, present since V2, found by the hosted harness (H9).** After a refused late
  result, the edit path rolled back to its checkpoint and erased the newer change. The engine's
  retry loop could re-apply on top. Fixed with `VersionConflict`: no rollback and no retry.
* A worker died on 502 from a TLS proxy while the control plane restarted (found by cluster
  S7), and on a 500 from the lease poll.
* The hosted supervisor let workers advertise adapters their credentials forbid.
* bubblewrap hid the Python install and Blender when they live under `/home` or `/root`.
* Caddy could not bind port 443 with all capabilities dropped.

## 4. Baseline versus final

| Suite | V2 baseline (`cdc2597`) | V2.1 |
|---|---|---|
| Backend pytest (Blender 4.5.3, LibreOffice 24.2) | 144 passed, 4 skipped | **236 passed, 10 skipped** on `733c1b6` (the skips are the opt-in live tests and host-specific cases) |
| Studio typecheck / unit tests | pass / 14 of 14 | **pass / 14 of 14** |
| V0 demonstration | 40/40 | **40/40** |
| V1.1 demonstrations | A–D passed, E blocked | **A–D passed, E blocked** (no video-capable credentials) |
| V1.2 demonstration | 28/28 | **28/28** |
| Walkthroughs spatial / semantic / office / execution / perf | 60 / 19 / 23 / 15 / ok | **60 / 19 / 23 / 15 / ok** (one threshold in the execution walkthrough changed from 3 to 2 jobs per creation, because creation no longer needs a separate inspect job; every check is unchanged) |
| V2.1 studio walkthrough (sign-in, sessions, CSRF, preflight, budget, polling fallback, sign-out) | n/a | **31/31** |
| Container cluster | 42/42 (S1–S8) | **70/70** hardened+TLS (S1–S11) |
| Hosted two-VM run | n/a | run 1 37/39, run 2 38/39 (check defects, then the quick tunnel's missing SSE support), **run 3 39/39** |
| Failure injection (handoff §11, 15 cases) | n/a | **15/15 covered, all passing** (map below) |

Failure injection, handoff §11 (every test asserts the safe outcome):

| # | Case | Test |
|---|---|---|
| 1 | credential revoked during execution | `test_security_v21::test_worker_credential_revoked_during_execution`, hosted revocation scenario |
| 2 | registration with incorrect identity | `test_security_v21::test_registration_with_wrong_identity_and_restricted_credential` |
| 3 | result under another worker's lease | `test_security_v21::test_lease_is_bound_to_the_worker_that_took_it`, `::test_stolen_lease_token_is_rejected_over_http` |
| 4 | control-plane restart during a model-planned job | `test_failure_injection_v21::test_restart_reuses_recorded_plan_without_new_model_call`, cluster S7, hosted H7 |
| 5 | worker crash during artifact generation | `test_distributed` crash recovery, cluster S5, hosted H8 |
| 6, 7 | model timeout, rate limit | `test_failure_injection_v21::test_model_timeout_and_rate_limit_change_nothing` |
| 8 | invalid model operation | `test_failure_injection_v21::test_invalid_model_operation_is_rejected` |
| 9 | out-of-scope component edit | `test_failure_injection_v21::test_out_of_scope_edit_is_rejected` |
| 10 | artifact changed during a remote job | `test_distributed::test_version_conflict_is_not_published`, `::test_version_conflict_on_edit_keeps_the_newer_change`, hosted H9 |
| 11 | stale external document version | `test_connectors_v21::test_drive_stale_version_is_refused_before_any_write`, `::test_drive_race_window_is_detected_and_reported_not_hidden` |
| 12 | failed TLS verification | `test_tls::test_failed_tls_verification_is_refused` (no CA, unrelated CA, wrong host name), cluster S11 |
| 13 | prompt injection in media | `test_failure_injection_v21::test_prompt_injection_in_source_is_data_not_instructions` |
| 14 | insufficient memory, timeout | `test_security_v21::test_process_sandbox_enforces_memory_limit`, `test_distributed::test_cancel_and_timeout`, `test_budget_jobs_v21` |
| 15 | cancelled job returning a late result | `test_distributed::test_lost_lease_requeues_and_late_result_is_rejected`, `test_security_v21::test_failed_and_cancelled_jobs_publish_nothing` |

## 5. Security changes and residual risks

See [SECURITY_MODEL.md](SECURITY_MODEL.md) for the full model. The hardened+TLS cluster run (S9–S11) and the hosted run
(H1, H10, revocation) cover these:
* Revocation takes a live worker out of the pool. The revoked worker exits with code 3, and its leases return to the queue.
* A forged or revoked credential is refused, and worker endpoints refuse API tokens.
* Leases are fenced to the worker identity that took them.
* Workers verify certificates; a wrong certificate authority means no registration.
* No plaintext endpoint is public.
* The session cookie is Secure, HttpOnly and SameSite=Strict. CSRF and foreign-Origin requests
  are refused. No token appears in any URL or in browser storage.
* SSRF to the cloud metadata service is refused on a real Azure VM.
* Code jobs on the remote VM find no network, no secrets and no metadata service.

**Residual risks:**
* The deployment is single-tenant.
* Bearer secrets need host protection.
* The bubblewrap sandbox shares the host kernel. Use gVisor or Firecracker for hostile code at
  scale.
* No encryption at rest.
* Drive publication cannot be atomic.
* seccomp is not applied inside bubblewrap on the Ubuntu runner.
* Windows workers have no per-job memory limit.

## 6. Live AI

**Blocked.** The environment holds no `ANTHROPIC_API_KEY` or Gemini key. Neither the handoff
nor the environment supplied credentials, and none were requested to be pasted.

Evidence:
* `evidence/v2_1/live_ai/anthropic/` and `evidence/v2_1/live_ai/gemini/`: BLOCKED reports
  naming the missing variables.
* `evidence/v2_1/live_ai/deterministic/`: the same benchmark run with the deterministic
  provider. It shows the harness works and how a non-AI planner does:
  * B and C pass (bound component changed, others preserved).
  * A fails: no geometry and no height from the heuristic.
  * D's tests fail: the heuristic cannot write code.
  * E is blocked: no video was supplied.

These failures are expected of a heuristic and are the baseline that a live model must beat.

To activate:
1. Store the keys as secrets of the protected `daedelus-live` environment.
2. Run the `live.yml` workflow (`workflow_dispatch`) or run
   `daedelus bench-v21 --provider anthropic|gemini`.

The workflow sets the budget (24 calls, $2, 600k tokens, 900 s per run). Expected cost for the
full A–D set: about 10 runs × at most $2, so at most $20 at list prices.

## 7. Creative output examples

Deterministic only: `evidence/v2_1/live_ai/deterministic/*.png` contains renders of tasks A
and B, single-pass and iterative. These are not AI outputs. Hosted renders:
`hosted_render.png` in the hosted control artifact, made by Blender on the remote VM.

## 8. Hosted deployment

**Hosted cloud verified.** The control plane and the worker ran on two separate GitHub-hosted
Azure VMs, connected over the public internet through a Cloudflare quick tunnel with a real
certificate.

Run [37882286579](https://github.com/maxhightower/Daedelus/actions/runs/37882286579): 37/39.
* **Passed:**
  * H2–H6: remote Blender. Hashed inputs, native outputs, atomic publication, validation, and the
    render served over HTTPS.
  * The agent workflow end to end, with the deterministic provider. All adapter work was remote
    and the budget was recorded.
  * H8: worker death. The lease expired and attempt 2 ran under the other identity, in 22.9 s.
  * H9: version conflict. The newer change was kept, which also verifies the fix.
  * H7: control-plane restart. The execution resumed and the in-flight job was reused.
  * H10: unauthorised access, all seven checks.
  * Live revocation.
* **Failed (two check defects, not topology defects):**
  * The host-name check: runner images share a hostname. The network-address check passed, and
    the worker log shows a separate VM.
  * The event stream through the tunnel delivered nothing. Likely cause: proxy compression. Fixed
    with `no-transform`.

Details: `evidence/v2_1/hosted/run_37882286579/`.

Run [37883447177](https://github.com/maxhightower/Daedelus/actions/runs/37883447177): 38/39.
* Machine identity was proven by distinct kernel boot IDs, Azure VM IDs and public IPs.
* The event stream received **zero bytes** through the tunnel: status 200, correct headers, not
  even the `hello` frame. Cloudflare documents that quick tunnels do not support Server-Sent
  Events.
* Response: a long-poll form of the event channel, with an automatic studio fallback.

**Run [37884783288](https://github.com/maxhightower/Daedelus/actions/runs/37884783288) on the
frozen code `733c1b6`: 39/39, passed.**
* Live progress reached the client through the long-poll fallback (first event at 1.35 s).
* Blender create and edit on the remote VM: 12.2 s.
* Recovery after worker death: 23.1 s.
* Resume after a control-plane restart: 102.7 s.

Evidence: `evidence/v2_1/hosted/run_37884783288/`.

What was live and what was deterministic: execution, transport, identities, isolation and
recovery were real and hosted. The AI planning step used the deterministic provider (Blocked
above).

## 9. Live connectors

**Blocked.** No Microsoft 365 or Google Workspace test account or token was provided.
`evidence/v2_1/connectors/{msgraph,google}/` holds BLOCKED reports.

Ready to run:
* `daedelus connectors-live`, which needs `DAEDELUS_LIVE_CONNECTORS=1`, a token and a test
  folder ID in a secret store.
* It creates `daedelus-live-test-<id>.xlsx`, edits one sheet, publishes, triggers a stale-version
  conflict and deletes the file.
* It never touches files it did not create.

The Drive race behaviour and Graph `If-Match` are fixture-tested (`test_connectors_v21.py`).

## 10. Distributed performance

Source: `evidence/v2_1/performance/README.md`. Three repeats; the first run cold, then warm.

| Workload | V2 | V2.1 (bubblewrap) | Gain |
|---|---|---|---|
| W1 Blender | 19.21 s, 13 jobs | 10.55 s, 7 jobs | 45 % |
| W2 Office pipeline | 60.00 s, 45 jobs | 38.15 s, 28 jobs | **36 %** (target ≥20 %) |
| W3 code | 9.27 s, 12 jobs | 3.97 s, 8 jobs | 57 % |
| W4 mixed | 23.53 s, 22 jobs | 14.03 s, 14 jobs | 40 % |
| W5 cancel / crash recovery | 21.5 s / 22.5 s | 20.5 s / 19.6 s | unchanged (lease-bound) |

* The process profile gives the same numbers, so the sandbox costs nothing measurable here.
* What remains is native work (Blender, LibreOffice) plus about 0.2 s of start-up per sandboxed
  job runner.
* In the hardened+TLS containers the Office pipeline went from 44.5 s (45 jobs) before the
  optimisations to 27.3 s (28 jobs).

## 11. Screenshots

`docs/screenshots/v2_1/`:
* `v21_01_login.png`: sign-in screen.
* `v21_02_worker_isolation.png`: worker with its verified bubblewrap isolation and live jobs.
* `v21_03_preflight.png`: run preflight with targets, model, limits and confirmation.
* `v21_04_budget.png`: execution usage against the budget, with node execution provenance.

* `v21_05_polling_fallback.png`: the studio with its event stream blocked, showing "live updates (polling)" and jobs arriving.

The V0–V2 walkthrough screenshots were regenerated on V2.1 in the run workspaces. The committed V1 to V2 screenshots in `docs/screenshots/` are kept unchanged, as the frozen milestone record.

## 12. Native artifact output paths

* Hosted: the `hosted-control-evidence` artifact holds `hosted_render.png` and
  `hosted_blender_jobs.json`, which records publication digests.
* Cluster: `evidence/v2_1/security/cluster_hardened_tls/blender_render.png` and the job JSON files.
* Benchmark: `evidence/v2_1/live_ai/deterministic/`.
* Demos: the V0, V1.1 and V1.2 demo reports and native outputs were written to the regression run directories; the logs are in `evidence/v2_1/regression/final_v21/`.

The native `.blend`, `.xlsx`, `.docx`, `.pptx`, `.ora` and repository files stay in the run
workspaces. They are validated by reopening (`file_reopens` checks); they are not committed.

## 13. External resources and cleanup

| Resource | Created | Cleanup |
|---|---|---|
| GitHub-hosted runners (hosted runs) | 2 VMs per run, ~9 min | destroyed by GitHub at job end |
| Cloudflare quick tunnels | 1 per run, anonymous, no account | terminated with the job (log: "Terminate orphan process: cloudflared") |
| Worker credentials (hosted) | 2 per run, in the run's own SQLite | gone with the VM; one revoked during the run |
| Actions artifacts | handoff (1-day retention), evidence (30 days) | expire automatically |
| Commit statuses `daedelus-hosted-phase` | on hosted-run commits | final state `success` ("done") |
| Paid cloud infrastructure | none | n/a |
| External documents (connectors) | none (Blocked) | n/a |

No container image was pushed to any registry.

## 14. CI and Windows

⟨fill⟩ Linux CI and the Windows desktop build (core tests, sidecar, Tauri installer) run on
every push. The Windows installer is built but was **not interactively tested** (register N1).

## 15. Remaining validation gaps

See [NATIVE_VALIDATION_REGISTER.md](NATIVE_VALIDATION_REGISTER.md):
* N6/N7: live Claude and Gemini.
* N8/N12: live connectors.
* N10: GPU workers.
* N13: hardened isolation on a production kernel with AppArmor.
* N14: real-certificate TLS on a long-lived deployment.
* Windows installer runtime (N1, N3).
* The full handoff §17 demonstration with *live* AI. Every step except live interpretation and
  planning ran for real (hosted); those steps are Blocked.

## 16. Licensing decisions for the owner

[LICENSING_BRIEF.md](LICENSING_BRIEF.md): no licence has been chosen. Decisions needed:
* the code licence (Apache-2.0 recommended);
* THIRD_PARTY_NOTICES;
* the GPL source offer before publishing any Blender worker image;
* whether to keep the CC BY-SA demonstration photograph.

## 17. Recommendations for V2.2 / V3

1. Run the live gates as soon as credentials exist, within the stated budgets: `live.yml`, then
   `connectors-live` against a dedicated test tenant or account.
2. Make hosted runs pass the event-stream check consistently through tunnels. Alternatively,
   offer a long-poll fallback for proxies that buffer.
3. Per-project worker restrictions, for multi-tenant pools.
4. A VM-level job sandbox (gVisor or Firecracker) for untrusted code at scale. Keep bubblewrap
   for trusted pools.
5. Warm per-adapter runners (for example a resident LibreOffice) as an opt-in for trusted
   pools: the next performance step, which trades isolation between jobs for speed.
6. A real-certificate, long-lived deployment (N14) with backup and restore drills.
