# Local dry run of the hosted harness (NOT hosted-cloud evidence)

Both "hosts" ran in the same cloud container; a local TLS forwarder (private CA) stood in for the
Cloudflare quick tunnel; phases went through `DAEDELUS_HOSTED_PHASE_FILE` instead of a commit
status. Purpose: debug the harness before spending a GitHub run.

Result: first attempt 36/39; after the fixes 37/39 (only the two same-host checks fail).
* 2 expected failures: "worker's network address is not the control host's" and "different
  machine (host name)" - impossible on one container; they are what the real run must show.
* 1 real defect found (H9): after a refused late result, the manual-edit path rolled the
  artifact back to its checkpoint and so **erased the newer change** the version check had just
  protected (the engine's retry loop had the same flaw). Fixed with `VersionConflict` (no
  rollback, no retry; the edit/node fails and the newer files stay) and a regression test
  (`test_version_conflict_on_edit_keeps_the_newer_change`) that fails on the old code.
* An earlier attempt also found that the supervisor let workers advertise adapters their
  credentials do not allow (registration refused, 403) - fixed.

The hosted-cloud result is in `../` (GitHub-hosted runners), not here.
