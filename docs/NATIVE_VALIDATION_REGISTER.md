# Deferred native-validation register

All development and verification for V1.1, V1.2 and V2 runs in cloud Linux containers and
GitHub Actions runners. The items below need a real desktop, a physical GPU or a live
account and are **not** verified. Each stays open until someone performs it and records the
result here.

| # | Item | Why it cannot be verified in the cloud environment | Milestone | Status |
|---|---|---|---|---|
| N1 | Install and run the Windows NSIS/MSI installer on a Windows desktop (first launch, sidecar start, Blender discovery, uninstall) | CI builds the installers but has no interactive desktop session | V0–V2 | open |
| N2 | Board/3D performance on a physical GPU (e.g. RTX-class) with several live 3D views | only headless Chromium with SwiftShader software rendering is available | V1–V2 | open |
| N3 | Tauri WebView2 behaviour on Windows (drag/drop, file dialogs, Monaco, WebGL) | no Windows desktop | V1–V2 | open |
| N4 | Blender GUI round-trip: open a Daedelus-produced `.blend` in the Blender UI and edit it by hand | headless Blender only (files are verified to reopen headlessly) | V1.1 | open |
| N5 | Open generated `.xlsx` / `.docx` / `.pptx` in Microsoft Excel / Word / PowerPoint | no Microsoft Office; files are validated by re-opening with the authoring libraries and LibreOffice (headless) | V1.2 | open |
| N6 | Live Claude semantic analysis/planning/evaluation (`DAEDELUS_LIVE_SEMANTIC=1`, `DAEDELUS_LIVE_CLAUDE=1`) | no Anthropic credentials in the environment | V1.1 | blocked |
| N7 | Live Gemini image/video understanding (incl. a public tutorial video URL) and image generation | no Gemini credentials in the environment | V1.1 | blocked |
| N8 | Live cloud document connections (Microsoft Graph / Google Workspace) | no accounts or credentials | V1.2 | blocked |
| N9 | Hosted remote worker deployment (real cloud host, not containers on one runner) | no hosted infrastructure or credentials | V2 | blocked |
| N10 | GPU worker execution (Cycles GPU rendering on a remote GPU worker) | no GPU workers | V2 | blocked |
| N11 | Formula results as calculated by Microsoft Excel (V1.2 verifies LibreOffice recalculation against independently computed values) | no Microsoft Excel | V1.2 | open |
| N12 | Microsoft Graph / Google Drive publish conflicts against a live account (412 on eTag mismatch; Drive check-then-write race window) | no accounts or credentials; contracts tested with synthetic fixtures only | V1.2 | blocked |
