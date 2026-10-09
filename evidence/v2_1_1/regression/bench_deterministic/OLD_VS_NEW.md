# Deterministic benchmark: V2.1 (preserved) vs v2.1.1

Provider `heuristic` (no AI model). V2.1 evidence: `evidence/v2_1/live_ai/deterministic/`
(frozen V2.1 code `733c1b6`), kept unchanged. v2.1.1: this directory.

| Task | Mode | V2.1 checks (V2.1) | V2.1 checks (v2.1.1 run) | New 2.1.1 checks | Verdict change |
|---|---|---|---|---|---|
| A | single | 3/6 | 3/6 | GLB mesh: fail | none; fails for the same reason (no geometry) |
| A | iterative | 3/6 | 3/6 | GLB mesh: fail | none |
| B | single | 3/3 | 3/3 | revision recorded, `.blend` reopens: pass | none |
| B | iterative | 3/3 | 3/3 | pass | none |
| C | single | 4/4 | 4/4 | still layered: pass | none |
| C | iterative | 4/4 | 4/4 | pass | none |
| D | single | 1/3 | 1/3 | held-out spec: fail (stub raises `NotImplementedError`); file scope: fail (no diff) | none |
| D | iterative | 1/3 | 1/3 | fail | none |
| E | both | blocked (no video) | blocked (no video) | — | none |

No V2.1 check was removed, renamed or reinterpreted. Every V2.1 verdict is identical. The new
checks only add requirements:
* A must also export a non-empty mesh;
* B must record a revision and reopen;
* C must stay layered;
* D must also satisfy 8 held-out cases and touch only `textutil.py`;
* E must identify procedural steps.

The automatic failure analysis now names why the heuristic fails:
* **A:** `inappropriate_operation_choice`. It planned `set_taper` and `set_material` on an
  empty artifact, and no operation that creates geometry.
* **D:** `no_operations_planned`. The heuristic cannot write code.

These are planner limitations, the baseline a live model must beat. They are not Blender or
adapter failures.
