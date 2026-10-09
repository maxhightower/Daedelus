# V2 baseline: container end-to-end (S1–S8) on code `cdc2597`

Result **42/42 passed** (the V2 freeze recorded 42/42).

Images: built from a clean worktree of `cdc2597` (V2 application code unchanged) with
the V2.1 Dockerfiles. Those differ only in the base image (`ubuntu:24.04`, since Debian
mirrors are blocked here), apt over HTTPS through the egress proxy, and Blender 4.5.3 LTS
from a local build. Image IDs are in `image_ids.txt`. Harness: the V2 `deploy/cluster_e2e.py`
and `deploy/compose.yml` from the same worktree, on one Docker host (this cloud container).

These metrics are the performance baseline for Phase 5:
blender create+edit 17.2 s; office pipeline 44.4 s / 45 jobs; worker-kill recovery 25.5 s;
execution after a control-plane restart 84.1 s.
