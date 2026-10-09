# Creative benchmark v2.1.1 — provider `heuristic`

Status: **deterministic**

Budget per run: `{"max_model_calls": 24, "max_cost_usd": 2.0, "max_tokens": 600000, "max_seconds": 900, "max_iterations": 3, "call_timeout_s": 180, "max_retries": 2}`

| Task | Mode | Verification | Checks (all) | Checks (v2.1 set) | Live calls | Cost | Seconds | Rubric (measured) | Failure (automatic) |
|---|---|---|---|---|---|---|---|---|---|
| A | single | deterministic | 3/7 | 3/6 | 0 | $0.0000 | 4.52 | constraint_adherence=0 | inappropriate_operation_choice |
| A | iterative | deterministic | 3/7 | 3/6 | 0 | $0.0000 | 6.84 | constraint_adherence=0 | inappropriate_operation_choice |
| B | single | deterministic | 5/5 | 3/3 | 0 | $0.0000 | 6.84 | preservation=2 | — |
| B | iterative | deterministic | 5/5 | 3/3 | 0 | $0.0000 | 6.82 | preservation=2 | — |
| C | single | deterministic | 5/5 | 4/4 | 0 | $0.0000 | 0.53 | preservation=2 | — |
| C | iterative | deterministic | 5/5 | 4/4 | 0 | $0.0000 | 0.65 | preservation=2 | — |
| D | single | deterministic | 1/5 | 1/3 | 0 | $0.0000 | 0.5 | constraint_adherence=0, preservation=2 | no_operations_planned |
| D | iterative | deterministic | 1/5 | 1/3 | 0 | $0.0000 | 0.52 | constraint_adherence=0, preservation=2 | no_operations_planned |
| E | single | blocked (no video input: pass --video-file or --video-url (a real, appropriately licensed video demonstrating a technique)) | 0/0 | 0/0 | 0 | — | — | — | — |
| E | iterative | blocked (no video input: pass --video-file or --video-url (a real, appropriately licensed video demonstrating a technique)) | 0/0 | 0/0 | 0 | — | — | — | — |

Rubric dimensions not measurable by the harness (relevance, structural fidelity, usability) are left for human review and are never filled in from a model's own assessment.

## A / single

- [x] execution finished
- [ ] a native Blender object with geometry was created — []
- [x] the .blend file reopens
- [x] render produced
- [ ] polygon budget met (measured) — 0
- [ ] height 0.1 m met (measured) — None
- [ ] GLB export produced with mesh geometry *(new in 2.1.1)* — meshes=0

## A / iterative

- [x] execution finished
- [ ] a native Blender object with geometry was created — []
- [x] the .blend file reopens
- [x] render produced
- [ ] polygon budget met (measured) — 0
- [ ] height 0.1 m met (measured) — None
- [ ] GLB export produced with mesh geometry *(new in 2.1.1)* — meshes=0

## B / single

- [x] execution finished
- [x] the bound component changed
- [x] unrelated components preserved exactly (component states)
- [x] a new revision was recorded *(new in 2.1.1)*
- [x] the native .blend reopens *(new in 2.1.1)*

## B / iterative

- [x] execution finished
- [x] the bound component changed
- [x] unrelated components preserved exactly (component states)
- [x] a new revision was recorded *(new in 2.1.1)*
- [x] the native .blend reopens *(new in 2.1.1)*

## C / single

- [x] execution finished
- [x] the bound layer changed
- [x] unrelated layers preserved exactly
- [x] the OpenRaster file is a valid zip with stack.xml
- [x] the output is still layered (same layers as before) *(new in 2.1.1)*

## C / iterative

- [x] execution finished
- [x] the bound layer changed
- [x] unrelated layers preserved exactly
- [x] the OpenRaster file is a valid zip with stack.xml
- [x] the output is still layered (same layers as before) *(new in 2.1.1)*

## D / single

- [x] a schema-valid plan was produced
- [ ] a real git diff of textutil.py against the starting commit exists — 
- [ ] allowlisted tests pass — {"report": {"passed": false, "artifacts": [{"artifact_id": "art_bc66bba1440c", "artifact": "textutil", "revision_id": "rev_99bb648f15e7", "report": {"passed": false, "checks": [{"name": "tests", "pass
- [ ] held-out specification cases pass (not visible to the model) *(new in 2.1.1)* — slugify could not be run on the held-out cases: NotImplementedError
- [ ] only the permitted file changed *(new in 2.1.1)* — []

## D / iterative

- [x] a schema-valid plan was produced
- [ ] a real git diff of textutil.py against the starting commit exists — 
- [ ] allowlisted tests pass — {"report": {"passed": false, "artifacts": [{"artifact_id": "art_463a0a9da42b", "artifact": "textutil", "revision_id": "rev_9c584caf9689", "report": {"passed": false, "checks": [{"name": "tests", "pass
- [ ] held-out specification cases pass (not visible to the model) *(new in 2.1.1)* — slugify could not be run on the held-out cases: NotImplementedError
- [ ] only the permitted file changed *(new in 2.1.1)* — []

## E / single


## E / iterative


