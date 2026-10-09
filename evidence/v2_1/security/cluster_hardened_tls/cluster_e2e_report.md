# Container end-to-end

single Docker host: control plane and workers as containers on separate networks; not a hosted multi-machine deployment; no GPU

Compose files: compose.yml, compose.hardened.yml, compose.tls.yml

Result: **PASSED** (70/70), 463.4 s


## setup
- [x] compose stack healthy

## S1 topology
- [x] control plane container has no Blender and no LibreOffice
- [x] control plane reports Blender unavailable locally
- [x] worker network is internal (no route out of the host)
- [x] workers cannot reach the internet
- [x] published port (edge) binds to 127.0.0.1 only
- [x] the control plane itself publishes no port (TLS edge only)
- [x] Blender, Office and code workers registered

## S2 Blender worker
- [x] Blender artifact created on a worker
- [x] remote edit produced a checked revision
- [x] render preview produced by the worker and served by the control plane
- [x] every Blender job ran on the Blender worker
- [x] components exist after publication

## S3 Office workers
- [x] research pipeline succeeded with all adapter work on workers
- [x] node wb resolved to cloud_cpu
- [x] node doc resolved to cloud_cpu
- [x] node deck resolved to cloud_cpu
- [x] formulas recalculated by LibreOffice inside the office worker
- [x] cross-artifact dependencies synced remotely

## S4 code worker
- [x] code artifact created on the code worker
- [x] allowlisted tests ran on the code worker and passed
- [x] a command the project allows but the WORKER's allowlist does not is not executed

## S5 worker killed mid-job
- [x] the slow worker leased the job
- [x] request completed after the worker was killed
- [x] job retried (attempt 2) and succeeded on another worker
- [x] lease expiry recorded in the job's event history

## S6 network partition
- [x] the partitioned-to-be worker leased the job
- [x] request completed despite the partition
- [x] the partitioned worker's late result was rejected and audited
- [x] exactly one result published for the job

## S7 control-plane restart
- [x] an apply job is running on a worker when the control plane restarts
- [x] control plane back after restart
- [x] interrupted execution resumed and succeeded
- [x] the in-flight apply job finished on its worker and was reused (not recomputed)
- [x] node log records the resumption
- [x] resumption audited

## S8 security
- [x] API rejects requests without a token
- [x] worker endpoint rejects a bad worker token
- [x] Host header outside the allowlist is rejected (DNS rebinding)
- [x] SSRF guard refuses http://169.254.169.254/latest/meta-data/
- [x] SSRF guard refuses http://control:8765/api/health
- [x] SSRF guard refuses http://127.0.0.1:8765/api/projects
- [x] authentication failures are audited

## S9 worker identity
- [x] every worker registered with its own provisioned credential
- [x] a forged worker credential cannot register
- [x] the old shared join token no longer enrols workers
- [x] the soon-to-be-revoked worker leased the job
- [x] revocation revoked the worker's lease
- [x] the revoked worker stopped (exit code 3)
- [x] the job completed on a legitimate worker
- [x] nothing was accepted from the revoked worker
- [x] revocation audited

## S10 isolation
- [x] worker-office: non-root, no capabilities, no-new-privileges, seccomp filter, read-only root, PID and memory limits (inspected and seen from /proc)
- [x] worker-blender: non-root, no capabilities, no-new-privileges, seccomp filter, read-only root, PID and memory limits (inspected and seen from /proc)
- [x] worker-code: non-root, no capabilities, no-new-privileges, seccomp filter, read-only root, PID and memory limits (inspected and seen from /proc)
- [x] worker 6766be22eea5-1: per-job bubblewrap sandbox verified by its self-test
- [x] worker d4d3258cf7d2-1: per-job bubblewrap sandbox verified by its self-test
- [x] worker 0e3e7d69e0a9-1: per-job bubblewrap sandbox verified by its self-test
- [x] repository tests that try to escape the sandbox all find it closed (network, credentials, environment, system files, processes, other jobs)
- [x] a memory bomb fails the job's tests, not the worker
- [x] the code worker is still alive after the memory bomb

## S11 TLS
- [x] HTTPS with the deployment CA verifies and serves the API
- [x] HSTS header present
- [x] a client without the deployment CA refuses the connection
- [x] no plaintext control-plane endpoint on the host
- [x] workers cannot reach the control plane's plaintext port
- [x] workers registered over HTTPS through the edge
- [x] a worker with the wrong CA fails certificate verification and never registers
- [x] browser session cookie is Secure, HttpOnly and SameSite=Strict (hosted profile)
- [x] job events stream to the client through the TLS proxy as they happen

## Metrics

- provisioned_credentials: ['worker-blender', 'worker-code', 'worker-office']
- blender_create_edit_seconds: 13.3
- office_pipeline_seconds: 27.3
- office_pipeline_jobs: 28
- recovery_after_worker_kill_seconds: 25.1
- execution_after_restart_seconds: 42.0
- sse_first_event_seconds_via_tls: 1.02
