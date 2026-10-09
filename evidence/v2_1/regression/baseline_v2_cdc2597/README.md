# V2 baseline (Phase 0) — code `cdc2597` (= application code `923ca4f`)

Re-run in this V2.1 cloud container before any V2.1 change was measured. The backend
tests, demos and walkthroughs ran against a clean `git worktree` of `cdc2597`
(`/tmp/v2base`). The interpreter ran isolated (`python -I`) with that worktree first on
`sys.path`.

| Suite | Result | Recorded V2 result |
|---|---|---|
| Backend pytest (Blender 4.5.3 LTS, LibreOffice 24.2, `DAEDELUS_REQUIRE_LIBREOFFICE=1`) | **144 passed, 4 skipped** (the 4 opt-in live-AI tests), 404 s | 144 passed, 4 skipped |
| Studio typecheck / unit tests / build | **pass / 14 of 14 / pass** | 14/14 |
| V0 demonstration | **40/40** | 40/40 |
| V1.1 demonstrations | **A–D passed** (A–C integration, D synthetic fixture); **E blocked** (no credentials) | same |
| V1.2 demonstration | **28/28** | 28/28 |
| V1 spatial walkthrough | **60/60** | 60/60 |
| V1.1 semantic walkthrough | **19/19** | 19/19 |
| V1.2 office walkthrough | **23/23** | 23/23 |
| V2 execution walkthrough | **15/15** | 15/15 |
| Board performance | completed (`perf_results.json` if present) | — |
| Distributed container integration (S1–S8) | see `../baseline_v2_cluster/` | 42/42 |

Environment differences from the V2 record:
* Blender is 4.5.3 LTS, not 4.5.14: `download.blender.org` is blocked by this sandbox's
  egress policy. The official 4.5.3 Linux build came out of the `linuxserver/blender:4.5.3`
  image from Docker Hub.
* A first attempt at the walkthroughs started the server from the modified `backend/`
  directory, and `python -m` loaded the in-progress V2.1 package. That run is **not**
  counted. The results above come from the corrected re-run on the V2 code.
