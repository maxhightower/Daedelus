# V2 container end-to-end

single Docker host: control plane and workers as containers on separate networks; not a hosted multi-machine deployment; no GPU

Result: **PASSED** (42/42), 630.8 s


## setup
- [x] compose stack healthy

## S1 topology
- [x] control plane container has no Blender and no LibreOffice
- [x] control plane reports Blender unavailable locally
- [x] worker network is internal (no route out of the host)
- [x] workers cannot reach the internet
- [x] published port binds to 127.0.0.1 only
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

## Metrics

- blender_create_edit_seconds: 17.2
- office_pipeline_seconds: 44.4
- office_pipeline_jobs: 45
- recovery_after_worker_kill_seconds: 25.5
- execution_after_restart_seconds: 84.1
