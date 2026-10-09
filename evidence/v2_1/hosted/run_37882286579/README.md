# Hosted run 37882286579 (code `37322df`): 37/39

GitHub Actions run: https://github.com/maxhightower/Daedelus/actions/runs/37882286579
Artifacts (kept by GitHub for 30 days): `hosted-control-evidence` (id 11594318728),
`hosted-worker-evidence` (id 11594744315). They could not be downloaded into the development
container (egress policy blocks the artifact storage host), so this record is transcribed from
the job logs, which carry every check line and the metrics verbatim.

## Topology (from the logs)

| | Host A: control plane | Host B: worker |
|---|---|---|
| GitHub runner | GitHub Actions 1000032097 | GitHub Actions 1000032096 |
| Reached via | Cloudflare quick tunnel `https://derby-develops-logo-liz.trycloudflare.com` (public HTTPS, real certificate); API bound to 127.0.0.1 only | outbound HTTPS to that URL over the public internet |
| VM | (control host evidence in the artifact) | Azure `Standard_D4ads_v5`, centralus, kernel 6.17.0-1022-azure, vmId sha256 prefix `29fdc431362df6fd`, boot id prefix `31827d714102bdff`, public IP 52.173.163.137 (ephemeral runner address) |
| Job sandbox | n/a | bubblewrap, self-test verified: no network, read-only root, private job dir, own PID namespace, no capabilities, no-new-privs, memory rlimit, no secrets in env (seccomp filter not applied: Ubuntu runner, user namespaces) |

## Checks

```
PASS [H1 independent hosts] the worker on the other host registered over HTTPS
PASS [H1 independent hosts] the worker's network address is not the control host's
FAIL [H1 independent hosts] the worker runs on a different machine (host name) - ('runnervmmprz5', 'runnervmmprz5')
PASS [H1 independent hosts] the worker reports a verified bubblewrap job sandbox
PASS [H1 independent hosts] the worker authenticated with a provisioned per-worker credential
PASS [H2-H6 remote Blender] Blender artifact created by the remote worker
PASS [H2-H6 remote Blender] Blender operations executed on the remote worker
PASS [H2-H6 remote Blender] every job ran on the remote worker
PASS [H2-H6 remote Blender] the worker downloaded the hashed inputs (apply job read the native files)
PASS [H2-H6 remote Blender] native outputs returned and were published atomically (digests recorded)
PASS [H2-H6 remote Blender] the published revision was validated (file reopens)
PASS [H2-H6 remote Blender] the render made on the worker is served by the control plane over HTTPS
FAIL [H2-H6 remote Blender] the event stream delivered job progress through the public TLS endpoint - []
PASS [E2E agent workflow (deterministic provider)] model created remotely
PASS [E2E agent workflow (deterministic provider)] agent workflow succeeded with all adapter work on the remote worker
PASS [E2E agent workflow (deterministic provider)] the node resolved to the remote CPU worker
PASS [E2E agent workflow (deterministic provider)] the execution recorded its budget and usage
PASS [E2E agent workflow (deterministic provider)] the targeted component and its siblings exist after publication
PASS [H8 worker shutdown] a worker that will die mid-job is the only worker
PASS [H8 worker shutdown] the doomed worker leased the job
PASS [H8 worker shutdown] the request completed after the worker died
PASS [H8 worker shutdown] lease expired, job retried (attempt 2) on another worker identity
PASS [H9 late result vs newer revision] the late result was refused (version conflict)
PASS [H9 late result vs newer revision] the newer change was not overwritten
PASS [H9 late result vs newer revision] the job is marked conflict, nothing published
PASS [H7 control-plane restart] a remote apply job is in flight when the control plane restarts
PASS [H7 control-plane restart] control plane back after restart (same volume, same tunnel)
PASS [H7 control-plane restart] the interrupted execution resumed and succeeded
PASS [H7 control-plane restart] the in-flight remote job finished and was reused, not recomputed
PASS [H10 unauthorised access] anonymous API request refused
PASS [H10 unauthorised access] query-string token refused
PASS [H10 unauthorised access] forged worker credential refused
PASS [H10 unauthorised access] worker endpoints refuse an API token
PASS [H10 unauthorised access] no plaintext control-plane endpoint is public (HTTP is refused or redirected)
PASS [H10 unauthorised access] URL ingestion of the cloud metadata service is refused on a real cloud host
PASS [H10 unauthorised access] code artifact created on the remote worker
PASS [H10 unauthorised access] repository tests on the remote worker find no network, no secrets and no cloud metadata service (all 4 escape attempts fail)
PASS [Revocation of a live remote worker] credential revoked
PASS [Revocation of a live remote worker] the revoked remote worker disappeared from the live pool
HOSTED_REPORT {"passed": false, "metrics": {"hosted_blender_create_edit_seconds": 12.1, "sse_first_event_seconds": null, "job_seconds": [0.26, 3.44, 0.66, 1.29, 0.26, 3.22, 1.48], "hosted_agent_workflow_seconds": 8.5, "hosted_recovery_after_worker_death_seconds": 22.9, "hosted_execution_after_restart_seconds": 97.0}, "n": 39, "failed": 2}
```

Worker host: `WORKER_EXITS {"w-main": 3, "w-crash": 17, "w-slow": 0}`. Exit 3 is the worker's
"credential revoked" exit (the revocation scenario); 17 is the injected crash (H8).

## The two failures

1. **Host-name check: a defect in the check, not in the topology.** GitHub runner VMs share
   the image host name `runnervmmprz5`. The network-address check passed, and the worker's own log
   shows a distinct Azure VM. The check now compares the kernel boot id and the cloud VM id that
   each host publishes (sha256 prefixes).
2. **Event stream through the tunnel: no events within 120 s.** The same check passes through a
   local TLS proxy and through the Caddy edge (cluster S11). Likely cause: Cloudflare compressing,
   and so buffering, the `text/event-stream` response. The server now sends
   `Cache-Control: no-cache, no-transform`, the harness asks for `Accept-Encoding: identity`, and
   it records the stream's status, headers and line count, so a repeat failure is diagnosable.

Both fixes are verified in the next run (see `../README.md`).
