# V2.1 creative benchmark — provider `heuristic`

Status: **deterministic**

Budget per run: `{"max_model_calls": 24, "max_cost_usd": 2.0, "max_tokens": 600000, "max_seconds": 900, "max_iterations": 3, "call_timeout_s": 180, "max_retries": 2}`

| Task | Mode | Verification | Checks | Live calls | Cost | Seconds | Rubric (measured) |
|---|---|---|---|---|---|---|---|
| A | single | deterministic | 4/6 | 0 | $0.0000 | 4.34 | constraint_adherence=1 |
| A | iterative | deterministic | 4/6 | 0 | $0.0000 | 6.18 | constraint_adherence=1 |
| B | single | deterministic | 3/3 | 0 | $0.0000 | 5.06 | preservation=2 |
| B | iterative | deterministic | 3/3 | 0 | $0.0000 | 5.87 | preservation=2 |
| C | single | deterministic | 4/4 | 0 | $0.0000 | 0.41 | preservation=2 |
| C | iterative | deterministic | 4/4 | 0 | $0.0000 | 0.45 | preservation=2 |
| D | single | deterministic | 2/3 | 0 | $0.0000 | 0.41 | constraint_adherence=0, preservation=2 |
| D | iterative | deterministic | 2/3 | 0 | $0.0000 | 0.43 | constraint_adherence=0, preservation=2 |
| E | single | blocked (no video input: pass --video-file or --video-url (a real, appropriately licensed video demonstrating a technique)) | 0/0 | 0 | — | — | — |
| E | iterative | blocked (no video input: pass --video-file or --video-url (a real, appropriately licensed video demonstrating a technique)) | 0/0 | 0 | — | — | — |

Rubric dimensions not measurable by the harness (relevance, structural fidelity, usability) are left for human review and are never filled in from a model's own assessment.

## A / single

- [x] execution finished
- [ ] a native Blender object with geometry was created — []
- [x] the .blend file reopens
- [x] render produced
- [x] polygon budget met (measured)
- [ ] height 0.1 m met (measured) — None

## A / iterative

- [x] execution finished
- [ ] a native Blender object with geometry was created — []
- [x] the .blend file reopens
- [x] render produced
- [x] polygon budget met (measured)
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
- [x] a real git diff of textutil.py exists
- [ ] allowlisted tests pass — {"report": {"passed": false, "artifacts": [{"artifact_id": "art_afae0af5f211", "artifact": "textutil", "revision_id": "rev_cb826516fa1e", "report": {"passed": false, "checks": [{"name": "tests", "pass

## D / iterative

- [x] a schema-valid plan was produced
- [x] a real git diff of textutil.py exists
- [ ] allowlisted tests pass — {"report": {"passed": false, "artifacts": [{"artifact_id": "art_fdcd6c915b3f", "artifact": "textutil", "revision_id": "rev_3034f349296a", "report": {"passed": false, "checks": [{"name": "tests", "pass

## E / single


## E / iterative


