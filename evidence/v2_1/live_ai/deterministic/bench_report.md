# V2.1 creative benchmark — provider `heuristic`

Status: **deterministic**

Budget per run: `{"max_model_calls": 24, "max_cost_usd": 2.0, "max_tokens": 600000, "max_seconds": 900, "max_iterations": 3, "call_timeout_s": 180, "max_retries": 2}`

| Task | Mode | Verification | Checks | Live calls | Cost | Seconds | Rubric (measured) |
|---|---|---|---|---|---|---|---|
| A | single | deterministic | 3/6 | 0 | $0.0000 | 4.67 | constraint_adherence=0 |
| A | iterative | deterministic | 3/6 | 0 | $0.0000 | 6.01 | constraint_adherence=0 |
| B | single | deterministic | 3/3 | 0 | $0.0000 | 5.6 | preservation=2 |
| B | iterative | deterministic | 3/3 | 0 | $0.0000 | 6.04 | preservation=2 |
| C | single | deterministic | 4/4 | 0 | $0.0000 | 0.43 | preservation=2 |
| C | iterative | deterministic | 4/4 | 0 | $0.0000 | 0.48 | preservation=2 |
| D | single | deterministic | 1/3 | 0 | $0.0000 | 0.44 | constraint_adherence=0, preservation=2 |
| D | iterative | deterministic | 1/3 | 0 | $0.0000 | 0.48 | constraint_adherence=0, preservation=2 |
| E | single | blocked (no video input: pass --video-file or --video-url (a real, appropriately licensed video demonstrating a technique)) | 0/0 | 0 | — | — | — |
| E | iterative | blocked (no video input: pass --video-file or --video-url (a real, appropriately licensed video demonstrating a technique)) | 0/0 | 0 | — | — | — |

Rubric dimensions not measurable by the harness (relevance, structural fidelity, usability) are left for human review and are never filled in from a model's own assessment.

## A / single

- [x] execution finished
- [ ] a native Blender object with geometry was created — []
- [x] the .blend file reopens
- [x] render produced
- [ ] polygon budget met (measured) — 0
- [ ] height 0.1 m met (measured) — None

## A / iterative

- [x] execution finished
- [ ] a native Blender object with geometry was created — []
- [x] the .blend file reopens
- [x] render produced
- [ ] polygon budget met (measured) — 0
- [ ] height 0.1 m met (measured) — None

## B / single

- [x] execution finished
- [x] the bound component changed
- [x] unrelated components preserved exactly (component states)

## B / iterative

- [x] execution finished
- [x] the bound component changed
- [x] unrelated components preserved exactly (component states)

## C / single

- [x] execution finished
- [x] the bound layer changed
- [x] unrelated layers preserved exactly
- [x] the OpenRaster file is a valid zip with stack.xml

## C / iterative

- [x] execution finished
- [x] the bound layer changed
- [x] unrelated layers preserved exactly
- [x] the OpenRaster file is a valid zip with stack.xml

## D / single

- [x] a schema-valid plan was produced
- [ ] a real git diff of textutil.py against the starting commit exists — 
- [ ] allowlisted tests pass — {"report": {"passed": false, "artifacts": [{"artifact_id": "art_628090852d88", "artifact": "textutil", "revision_id": "rev_2939aafd180f", "report": {"passed": false, "checks": [{"name": "tests", "pass

## D / iterative

- [x] a schema-valid plan was produced
- [ ] a real git diff of textutil.py against the starting commit exists — 
- [ ] allowlisted tests pass — {"report": {"passed": false, "artifacts": [{"artifact_id": "art_9dead48b6ae4", "artifact": "textutil", "revision_id": "rev_cddc482d051e", "report": {"passed": false, "checks": [{"name": "tests", "pass

## E / single


## E / iterative


