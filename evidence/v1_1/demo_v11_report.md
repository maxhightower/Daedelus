# V1.1 demonstrations

Planner provider: `heuristic`; live provider: `none`; duration 24.7 s.

- provider `heuristic`: available (ok)
- provider `anthropic`: unavailable (no Anthropic credentials detected in the environment (ANTHROPIC_API_KEY / ANTHROPIC_AUTH_TOKEN / ANTHROPIC_PROFILE))
- provider `gemini`: unavailable (no Gemini credentials in the environment (GEMINI_API_KEY / GOOGLE_API_KEY, or Vertex AI configuration))
- provider `replay`: available (1 fixture(s))

## A. Image-guided 3D creation (stylised tree, Blender)

Status: **PASSED** - verification type: integration
- [x] workflow succeeded
- [x] trunk, branches and canopy were created as components
- [x] the photograph was analysed as the bound trunk region
- [x] the guide's polygon budget was extracted with its source text
- [x] evaluate/revise loop ran and reached its objective
- [x] final model satisfies the polygon budget (measured)
- [x] native .blend reopens
- [x] render and GLB previews produced
- [x] trunk and canopy have distinct materials from the references
- note: deterministic recipe 'tree' (heuristic provider - parameters come from measurements, not from recognising the references)
- note: polygon budget: 400.0; low-poly: True; twisted: True
- metrics: `{"seconds": 5.82, "poly_count": 178, "iterations": 0, "operations": 10, "provider": "heuristic", "trunk_color": "#453f2c", "canopy_color": "#464621"}`

## B. Component-specific visual adaptation (one reference changes one component)

Status: **PASSED** - verification type: integration
- [x] first run succeeded
- [x] trunk and canopy units both executed
- [x] impact preview: only the trunk unit is stale
- [x] second run succeeded
- [x] only the trunk unit executed; canopy skipped
- [x] revision changed exactly the trunk
- [x] canopy and branches are byte-for-byte unchanged in the scene
- [x] component preservation validated
- metrics: `{"trunk_color_before": "#928f80", "trunk_color_after": "#5a4f43"}`

## C. 2D semantic editing of one layer and one region (OpenRaster)

Status: **PASSED** - verification type: integration
- [x] workflow succeeded
- [x] only the sky layer changed
- [x] sun and hills layers unchanged
- [x] region edit changed only the hills layer
- [x] sky and sun unchanged by the region edit
- [x] OpenRaster file reopens

## D. Document-guided code modification (git + pytest)

Status: **PASSED** - verification type: synthetic-fixture
- [x] workflow succeeded (plan applied, tests run)
- [x] requirements extracted from the document with line locations
- [x] git commit recorded
- [x] reviewable diff contains slugify
- [x] allowlisted test command executed and passed
- note: planner response is a SYNTHETIC fixture (no live model available): it proves the plan -> git -> tests path, not natural-language understanding

## E. Video as optional instructional input

Status: **BLOCKED** - verification type: blocked
Blocked by: live video understanding needs a multimodal provider with credentials (Gemini for video files/URLs, or Claude for sampled frames); none available: gemini: no Gemini credentials in the environment (GEMINI_API_KEY / GOOGLE_API_KEY, or Vertex AI configuration); anthropic: no Anthropic credentials detected in the environment (ANTHROPIC_API_KEY / ANTHROPIC_AUTH_TOKEN / ANTHROPIC_PROFILE)
- [x] local analyser measures frames but reports no steps (no false understanding)
- note: gemini: no Gemini credentials in the environment (GEMINI_API_KEY / GOOGLE_API_KEY, or Vertex AI configuration)
- note: anthropic: no Anthropic credentials detected in the environment (ANTHROPIC_API_KEY / ANTHROPIC_AUTH_TOKEN / ANTHROPIC_PROFILE)
