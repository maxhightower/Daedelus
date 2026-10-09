# Live Gemini benchmark: BLOCKED

No live Gemini call was made in V2.1.1.

* **Credentials:** no `GEMINI_API_KEY` / `GOOGLE_API_KEY` / Vertex configuration in this
  container.
* **Pricing:** no authoritative current price for `gemini-3.8-flash` is recorded in
  `semantic/service.PRICES`. Even with a key, the runner therefore BLOCKS Gemini (`pricing`)
  unless the dispatch sets `allow_unpriced=true`. The run is then bounded only by the call and
  token caps, with **no monetary guarantee**, and the manifest shows `cost_complete: false`. To
  get a dollar bound instead, add the model's current list price to `PRICES` from Google's
  pricing page, with its date.
* **Video (task E):** needs a `video_url` of a licensed technique video on an approved host
  (YouTube or `upload.wikimedia.org` by default). None was supplied, so task E is BLOCKED for
  both providers.
* **Recorded outcome:** `../smoke_tests/live_results.json`: `gemini.smoke` and
  `gemini.bench.A`–`E` are **BLOCKED** (`credentials`).

To unblock, follow `docs/LIVE_AI_TESTING.md` §6 and §7: environment secret `GEMINI_API_KEY`, a
Google Cloud budget alert, then a dispatch with `allow_unpriced=true` and a deliberately small
`max_model_calls`.
