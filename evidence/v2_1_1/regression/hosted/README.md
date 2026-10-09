# Hosted two-VM regression on V2.1.1: 39/39

Run [37916449105](https://github.com/maxhightower/Daedelus/actions/runs/37916449105) on
`4f4c5f9`. Application code is identical to the freeze `43ce9d6`; only the hosted harness's
phase channel changed (see `FREEZE_RUN_COLLISION.md`). Both jobs succeeded. Transcribed from
the job log:

```
HOSTED_REPORT {"passed": true, "metrics": {"machine_identity": {"boot_id_sha256": ["2241e97092457335",
"a448bad6291257ba"], "cloud_vm_id_sha256": ["99f3dbb28cd867dd", "3e04699882f29d78"],
"worker_public_ip_differs": true}, "hosted_blender_create_edit_seconds": 12.6, "event_channel": "long-poll",
"poll_first_event_seconds": 1.34, "hosted_agent_workflow_seconds": 8.4,
"hosted_recovery_after_worker_death_seconds": 23.2, "hosted_execution_after_restart_seconds": 101.5},
"n": 39, "failed": 0}
PASS: 39/39
```

All scenarios passed:
* H1: distinct machines, verified bubblewrap sandbox, provisioned credential;
* H2–H6: remote Blender, hashed inputs, atomic publication, validation, render over HTTPS, live
  progress via long-poll;
* the agent workflow (deterministic provider), whose execution budget is recorded;
* H8: worker death, retried under another identity;
* H9: version conflict, newer change kept;
* H7: control-plane restart, in-flight job reused;
* H10: all seven unauthorised-access checks;
* live revocation.

The metrics match V2.1 run 37884783288 within noise (Blender 12.6 s vs 12.2 s, recovery 23.2 s
vs 23.1 s, restart 101.5 s vs 102.7 s).
