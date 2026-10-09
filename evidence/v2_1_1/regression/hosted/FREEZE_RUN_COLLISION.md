# Hosted runs on the freeze commit `43ce9d6`: harness collision (found and fixed)

Pushing the same commit to two branches started two hosted runs at once:
* [37915239671](https://github.com/maxhightower/Daedelus/actions/runs/37915239671), branch
  `claude/blissful-clarke-sow429`: **success**;
* [37915242161](https://github.com/maxhightower/Daedelus/actions/runs/37915242161), branch
  `opus/daedelus-v2-1-1-live-ai-validation`: **37/39**.

Both published their phases as the commit status `daedelus-hosted-phase` on the same SHA, so
each worker supervisor also followed the *other* run's phases. In run 37915242161, worker
`w-crash` was already registered at 10:07:17, before that run published phase 2 at 10:07:45.
Its two failed H8 checks ("the doomed worker is the only worker"; "retried on another
identity") are consequences of the foreign workers. All other 37 checks passed, including
distinct machines (boot ids `10c35329…`/`5ebd942f…`, VM ids `e064801a…`/`70f3f862…`), remote
Blender, the long-poll event channel (first event 1.31 s), H9, H7, H10 and live revocation.

Neither run is clean evidence, because both shared one phase channel. Fix: the phase status
context is now per workflow run (`daedelus-hosted-phase/<GITHUB_RUN_ID>`) in
`deploy/hosted/control_e2e.py` and `worker_supervisor.py`. A single clean re-run is recorded in
`README.md`.
