# Creative benchmark comparison (v2.1.1)

One run per task and mode for each provider (sample size n=1 per cell): differences are observations, not statistically supported rankings. Deterministic checks are authoritative; model assessments are labelled and never counted as checks.

## Coverage

| Label | Status | Detail |
|---|---|---|
| deterministic | deterministic | 10 runs; campaign None |
| claude | BLOCKED | no report (not run) |
| gemini | BLOCKED | no report (not run) |

## Per task

### Task A

| Label | Mode | Verification | Checks | v2.1 checks | Ops | Invalid units | Live calls | Cost | Seconds | Loop | Failure (automatic) | Assessment |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| deterministic | single | deterministic | 3/7 | 3/6 | 2 | 0 | 0 | $0.0000 | 4.52 | — | inappropriate_operation_choice | — |
| deterministic | iterative | deterministic | 3/7 | 3/6 | 2 | 1 | 0 | $0.0000 | 6.84 | corrective revision failed validation and was rolled back | inappropriate_operation_choice | — |
| deterministic | iterative vs single | | **unchanged** | | | | | | | | | |

### Task B

| Label | Mode | Verification | Checks | v2.1 checks | Ops | Invalid units | Live calls | Cost | Seconds | Loop | Failure (automatic) | Assessment |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| deterministic | single | deterministic | 5/5 | 3/3 | 1 | 0 | 0 | $0.0000 | 6.84 | — | — | — |
| deterministic | iterative | deterministic | 5/5 | 3/3 | 1 | 0 | 0 | $0.0000 | 6.82 | achieved | — | — |
| deterministic | iterative vs single | | **unchanged** | | | | | | | | | |

### Task C

| Label | Mode | Verification | Checks | v2.1 checks | Ops | Invalid units | Live calls | Cost | Seconds | Loop | Failure (automatic) | Assessment |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| deterministic | single | deterministic | 5/5 | 4/4 | 1 | 0 | 0 | $0.0000 | 0.53 | — | — | — |
| deterministic | iterative | deterministic | 5/5 | 4/4 | 1 | 0 | 0 | $0.0000 | 0.65 | achieved | — | — |
| deterministic | iterative vs single | | **unchanged** | | | | | | | | | |

### Task D

| Label | Mode | Verification | Checks | v2.1 checks | Ops | Invalid units | Live calls | Cost | Seconds | Loop | Failure (automatic) | Assessment |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| deterministic | single | deterministic | 1/5 | 1/3 | 0 | 0 | 0 | $0.0000 | 0.5 | — | no_operations_planned | — |
| deterministic | iterative | deterministic | 1/5 | 1/3 | 0 | 0 | 0 | $0.0000 | 0.52 | achieved | no_operations_planned | — |
| deterministic | iterative vs single | | **unchanged** | | | | | | | | | |

### Task E

| Label | Mode | Verification | Checks | v2.1 checks | Ops | Invalid units | Live calls | Cost | Seconds | Loop | Failure (automatic) | Assessment |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| deterministic | single | blocked (no video input: pass --video-file or --video-url (a real, appropriately licensed video demonstrating a technique)) | 0/0 | 0/0 | 0 | 0 | 0 | — | — | — | — | — |
| deterministic | iterative | blocked (no video input: pass --video-file or --video-url (a real, appropriately licensed video demonstrating a technique)) | 0/0 | 0/0 | 0 | 0 | 0 | — | — | — | — | — |
| deterministic | iterative vs single | | **n/a (blocked)** | | | | | | | | | |

