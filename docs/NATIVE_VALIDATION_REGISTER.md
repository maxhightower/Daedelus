# Deferred native-validation register

All development and verification for V1.1, V1.2, V2, V2.1 and V2.1.1 runs in cloud Linux containers
and GitHub Actions runners. Each item below needs a real desktop, a physical GPU, a live
account, live model credentials or hosted infrastructure. An item stays open until someone
performs it and records the result here.

Status vocabulary (V2.1):
* **open:** not yet attempted;
* **blocked:** the required access is unavailable;
* **verified:** performed, with evidence linked.

| # | Item | Why it cannot be verified in the cloud environment | Milestone | Status (V2.1 → V2.1.1) |
|---|---|---|---|---|
| N1 | Install and run the Windows NSIS/MSI installer on a Windows desktop: first launch, sidecar start, Blender discovery, uninstall | CI builds the installers but has no interactive desktop session | V0–V2.1 | open. The installer is built in CI on every push; not installed. |
| N2 | Board and 3D performance on a physical GPU (e.g. RTX-class) with several live 3D views | only headless Chromium with SwiftShader software rendering is available | V1–V2.1 | open |
| N3 | Tauri WebView2 behaviour on Windows: drag/drop, file dialogs, Monaco, WebGL. V2.1 adds the login screen and cross-origin ticket mode. | no Windows desktop | V1–V2.1 | open |
| N4 | Blender GUI round-trip: open a Daedelus-produced `.blend` in the Blender UI and edit it by hand | headless Blender only (files are verified to reopen headlessly) | V1.1 | open |
| N5 | Open generated `.xlsx` / `.docx` / `.pptx` in Microsoft Excel / Word / PowerPoint | no Microsoft Office; files are validated by reopening with the authoring libraries and headless LibreOffice | V1.2 | open |
| N6 | Live Claude semantic analysis, planning and evaluation: `DAEDELUS_LIVE_SEMANTIC=1`, `DAEDELUS_LIVE_CLAUDE=1`, `daedelus bench-v21 --provider anthropic` | no Anthropic credentials were provided to this project. V2.1 audited the request shape against the current API, added typed errors, budgets and the benchmark, and wired the opt-in workflow `live.yml`. | V1.1–V2.1.1 | **blocked**. V2.1.1 made the live path trustworthy (`daedelus live-run`, hardened `live.yml`, campaign budget, honest outcome classification) but still had no key, and the workflow is not dispatchable until it is on `main`. Evidence: `evidence/v2_1_1/claude/`, `evidence/v2_1_1/smoke_tests/`. |
| N7 | Live Gemini image and video understanding (including a public tutorial video URL) and image generation | no Gemini credentials. V2.1 moved the default model to `gemini-3.8-flash` (2.5-flash shuts down 2026-10-16) and added the Files API path for large videos. | V1.1–V2.1.1 | **blocked** (credentials; additionally no authoritative Gemini price, so a run needs `allow_unpriced`; no licensed video supplied for task E). Evidence: `evidence/v2_1_1/gemini/`. |
| N8 | Live cloud document connections (Microsoft Graph / Google Workspace) | no accounts or credentials. V2.1 added the opt-in harness `daedelus connectors-live`, the least-privilege scopes and the safe test-account procedure. | V1.2–V2.1 | **blocked**. Evidence: `evidence/v2_1/connectors/` (BLOCKED reports). |
| N9 | Hosted remote worker deployment on independent hosts, not containers on one runner | V2.1 ran control plane and worker on two independent GitHub-hosted VMs, connected over the public internet through a TLS quick tunnel (`hosted.yml`). Quick tunnels do not carry server-sent events, so the studio's long-poll fallback carried live updates | V2–V2.1 | **verified** (hosted cloud): run 37884783288 on `733c1b6`, 39/39, distinct Azure VMs (boot ids, VM ids, public IPs). Evidence: `evidence/v2_1/hosted/run_37884783288/`. Remaining: a long-lived deployment (N14). |
| N10 | GPU worker execution (Cycles GPU rendering on a remote GPU worker) | no GPU workers | V2–V2.1 | blocked |
| N11 | Formula results as calculated by Microsoft Excel (V1.2 verifies LibreOffice recalculation against independently computed values) | no Microsoft Excel | V1.2 | open |
| N12 | Microsoft Graph / Google Drive publish conflicts against a live account: 412 on eTag mismatch; the Drive race window | no accounts. V2.1 replaced the Drive check-then-write with conservative publication (backup pin, post-write race detection, `ConnectorConflict(written=True)`), tested against fixtures. Live behaviour is unverified. | V1.2–V2.1 | **blocked** |
| N13 | Hardened worker isolation on a production host kernel: AppArmor enforcing; user namespaces enabled deliberately | verified in this cloud container (Docker 29, seccomp, cgroup v1, no AppArmor) and on GitHub runners; not on a long-lived production host | V2.1 | open |
| N14 | Real-certificate TLS in front of a long-lived deployment (Caddy with ACME, or a cloud load balancer) | the hosted e2e uses a Cloudflare quick-tunnel certificate; the compose TLS edge uses a private CA | V2.1 | open |
| N15 | GitHub environment `daedelus-live` protection: required reviewers, deployment-branch policy (`main`, `opus/*`), environment-scoped secrets; `live.yml` present on `main` so it can be dispatched | account-level settings, not visible or settable from the repository or this session | V2.1.1 | **open (unverified)**. Exact setup in `docs/LIVE_AI_TESTING.md` §6. |
| N16 | Live creative benchmark A–E with real Claude and Gemini, including single vs iterative comparison and cross-provider assessment | needs N6, N7 and N15 | V2.1.1 | **blocked**. Harness and deterministic baseline ready: `evidence/v2_1_1/regression/bench_deterministic/`, `evidence/v2_1_1/comparison/`. |
