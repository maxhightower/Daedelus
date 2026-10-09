# Stage 1 smoke tests: BLOCKED

`live_results.json` is the manifest of `daedelus live-run` for the full requested campaign:
providers claude and gemini, stages smoke and bench, tasks A–E. It was run in this container,
which has no provider credentials.

* 12 requested gates: all **BLOCKED** (`credentials`), 0 model calls, 0 cost, **exit code 1**.
* Connector gates: NOT RUN (not requested); see `docs/LIVE_AI_TESTING.md` for connector setup.

What each smoke test asserts when credentials exist (`live_runner.smoke_gate`):
* authentication: `provider.available()`;
* the serving model and the request id are reported;
* structured image analysis of the CC0 coffee photo: observations returned that identify the
  cup, the live pathway `image`, not recorded;
* a schema-valid live plan, not recorded, with operations from the adapter catalogue that
  target only the bound layer, executed against a real OpenRaster artifact (a revision is
  recorded);
* usage accounted in the budget (analysis + plan).

The smoke budget is 4 calls, $0.50 and 120k tokens. Refusal, rate-limit, timeout and
malformed-response behaviour is verified with fixtures (`tests/test_live_runner.py` tests 4–6,
`tests/test_providers_v21.py`), **not live**. Gemini's video capability is reported as an
available pathway; it is exercised only by task E with a supplied video.
