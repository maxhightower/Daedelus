# Hosted run 37884783288 (code `733c1b6`): **39/39, passed**

GitHub Actions run: https://github.com/maxhightower/Daedelus/actions/runs/37884783288 (both jobs
green). Artifact `hosted-control-evidence` id 11596297096 (30-day retention). Transcribed from
the job log.

**Topology.** Two GitHub-hosted Azure VMs. The control plane is bound to 127.0.0.1 and exposed
only through a Cloudflare quick tunnel (`https://relaxation-tsunami-protest-exists.trycloudflare.com`,
public HTTPS with a real certificate). The worker runs in the hosted profile on the other VM,
with the bubblewrap job sandbox verified. It connects over the public internet with a
provisioned per-worker credential, sealed to a key generated on its own VM.

Distinct machines (sha256 prefixes reported by each host):

| | control | worker |
|---|---|---|
| kernel boot id | `84c47f62d3322540` | `f297248931fb6ad5` |
| Azure VM id | `ca1b03273bce485c` | `ee3c8c285a8a77f2` |
| public IP | differs | |

## Checks (all PASS)

```
H1 independent hosts: registered over HTTPS; network address differs; different machine (boot id, VM id);
   verified bubblewrap sandbox; provisioned per-worker credential
H2-H6 remote Blender: artifact created remotely; operations executed remotely; every job on the remote
   worker; hashed inputs downloaded; native outputs published atomically (digests); revision validated
   (file reopens); render served over HTTPS; job progress reached the client live through the public
   TLS endpoint
E2E agent workflow (deterministic provider): model created remotely; workflow succeeded with all adapter
   work remote; node resolved to the remote CPU worker; budget and usage recorded; targeted component and
   siblings intact
H8 worker shutdown: doomed worker is the only worker; it leased the job; request completed after it died;
   lease expired, attempt 2 on another worker identity
H9 late result vs newer revision: refused (version conflict); newer change not overwritten; job marked
   conflict, nothing published
H7 control-plane restart: remote apply in flight at restart; control plane back (same volume, same
   tunnel); execution resumed and succeeded; in-flight job reused, not recomputed
H10 unauthorised access: anonymous refused; query-string token refused; forged worker credential refused;
   worker endpoints refuse an API token; no public plaintext endpoint; cloud-metadata SSRF refused on a
   real cloud host; remote code job finds no network, no secrets, no metadata service (4/4)
Revocation of a live remote worker: revoked; disappeared from the live pool
PASS: 39/39
```

Metrics (`HOSTED_REPORT`):

```
hosted_blender_create_edit_seconds 12.2   hosted_agent_workflow_seconds 8.5
hosted_recovery_after_worker_death_seconds 23.1   hosted_execution_after_restart_seconds 102.7
event_channel "long-poll"   poll_first_event_seconds 1.35   sse_first_event_seconds null
sse_diagnostics {lines: 0, status: 200, server: cloudflare}   job_seconds [0.21, 3.55, 0.61, 1.28, 0.21, 3.11, 1.73]
```

The event stream again received zero bytes through the quick tunnel: Cloudflare documents
that quick tunnels do not support SSE. Live progress reached the client through the long-poll
fallback, with the first event 1.35 s after the request.

## What was real and what was not

* **Hosted and real:** the two VMs, public-internet transport over TLS, per-worker identities and
  revocation, the bubblewrap sandbox, headless Blender on the worker VM, hashed input transfer,
  atomic publication, recovery from worker death and control-plane restart, conflict safety, and
  the network refusals.
* **Deterministic:** the planning step of the agent workflow used the deterministic provider.
  Live AI is Blocked (no credentials); see `docs/V2_1_REPORT.md` §6.
