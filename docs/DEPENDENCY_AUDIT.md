# Dependency and asset licence audit

The project's own licence has **not** been chosen by the owner; no licence file is added and
package manifests declare none (earlier `Apache-2.0` declarations in `pyproject.toml` and
`Cargo.toml` were removed in V1.1 for that reason). This audit records what each third-party
component requires so the owner can choose compatibly. Versions/licences below were read from
installed package metadata, not recalled.

## Python (backend)

| Package | Version checked | Licence | Use | Notes |
|---|---|---|---|---|
| fastapi | 0.143.0 | MIT | API | |
| pydantic | 2.14.0 | MIT | models | |
| uvicorn | 0.54.0 | BSD-3-Clause | server | |
| python-multipart | 0.0.32 | Apache-2.0 | uploads | |
| pillow | 12.3.0 | MIT-CMU | images | |
| numpy | 2.5.3 | BSD-3-Clause (+ bundled permissive) | measurements | |
| pypdf | 6.19.0 | BSD-3-Clause | PDF text | |
| httpx | 0.28.1 | BSD-3-Clause | URL ingestion | |
| anthropic (optional `anthropic` extra) | 1.12.1 | MIT | Claude provider | behind provider boundary |
| google-genai (optional `gemini` extra) | 2.29.0 | Apache-2.0 | Gemini provider | behind provider boundary |
| openpyxl | 3.1.5 | MIT | XLSX (V1.2) | |
| python-docx | 1.2.0 | MIT | DOCX (V1.2) | depends on lxml |
| python-pptx | 1.0.2 | MIT | PPTX (V1.2) | depends on lxml, XlsxWriter |
| lxml | 6.1.3 | BSD-3-Clause | XML | bundles libxml2/libxslt (MIT) |
| XlsxWriter | 3.2.9 | BSD-2-Clause | pptx charts | |
| pyinstaller (build only) | 6.22.3 | GPL-2.0+ with bootloader exception | desktop sidecar freezing | the exception permits distributing frozen apps under any licence |

## Studio (JavaScript)

| Package | Version | Licence |
|---|---|---|
| react | 18.3.1 | MIT |
| @xyflow/react | 12.12.0 | MIT |
| three | 0.169.0 | MIT |
| monaco-editor / @monaco-editor/react | 0.52.2 / 4.7.0 | MIT |
| @tauri-apps/api (and Tauri) | 2.12.1 | Apache-2.0 OR MIT |
| playwright (dev/test only) | 1.56.1 | Apache-2.0 |

## External applications (invoked as separate processes, not linked or bundled)

| Tool | Licence | How it is used | Consideration |
|---|---|---|---|
| Blender | GPL-2.0-or-later | `blender --background` subprocess running `blender_worker.py` | Scripts that import `bpy` run inside Blender; if the worker script is distributed it is reasonable to treat *that file* as GPL-compatible. The rest of Daedelus talks to Blender only through files and process I/O. Keep the adapter replaceable. |
| LibreOffice (V1.2 previews/recalc) | MPL-2.0 | headless `soffice` subprocess | not bundled |
| ffmpeg | LGPL/GPL depending on build | subprocess for video frames | not bundled |
| git | GPL-2.0 | subprocess for the code adapter | not bundled |

## Model providers

Anthropic and Google APIs are optional remote services used through their official SDKs;
their terms of service apply to API use. No provider is required for the core product
(the deterministic local provider covers offline use).

## Bundled media (demonstration inputs)

See `backend/daedelus/demo_assets/v11/ATTRIBUTION.json`.

| File | Licence | Author / source |
|---|---|---|
| tree_trunk_photo.jpg | CC BY-SA 2.0 | Evelyn Simak, geograph.org.uk/photo/777464 |
| *_render.png (6 files) | CC0 1.0 | Poly Haven (polyhaven.com) |

The CC BY-SA photograph is data used by demonstrations; it must keep its attribution and
licence if redistributed and is easy to replace with any other photograph.
