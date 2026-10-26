# Explorer creation flow — G-001 continuation

Base: `validation/daedelus-native-1-windows` at `ef295fa`.

Users can now create artifacts from Explorer without the demo builder or an API client.
The form offers blank 3D scenes, layered images, code projects, documents, spreadsheets,
and presentations using the existing adapters and revision lifecycle. Adapter availability
and supported templates gate creation; unavailable tools display their reason.

Explorer also creates independent, named workflows. New resources are placed on the active
board and selected. Select a workflow frame and use **+ Operation** to add its steps.
Double-click its Explorer entry to locate it if it lies outside the current viewport.

Creation rejects whitespace names, blocks repeated submissions, retains input on API errors,
and avoids placing an old project's result after the form unmounts. Board-placement failure
preserves the already-created resource and points users to Explorer rather than retrying
creation and duplicating the artifact.

## Validation

- `cd studio && npm test`: 29 tests passed, including 12 creation-flow tests.
- `cd studio && npm run build`: TypeScript and production Vite build passed.
- `studio/e2e/creation.e2e.mjs`: real backend + production frontend + headless Chromium.
  Documents, spreadsheets, presentations, layered images, and code projects created with
  initial revisions and persisted board placements. Two independent workflows created,
  placed, and retained after reload. No browser page errors.
- Blender was unavailable; its disabled control was verified. Actual Blender creation and
  packaged Windows UI verification are not claimed.

Run the browser check against a disposable workspace served by `daedelus serve` with the
built Studio distribution, then run `STUDIO_URL=http://127.0.0.1:8765 node e2e/creation.e2e.mjs`
from `studio`. The check creates a fresh project and leaves it available for inspection.

## Remaining work

G-001's creation gap is implemented; native acceptance still needs a Windows rerun.
D-004 (multiple desktop instances against one workspace) remains open. This change does
not complete the broader NATIVE-1 validation campaign or the blocked live-provider tests.
