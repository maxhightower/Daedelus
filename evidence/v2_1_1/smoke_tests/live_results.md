# Live verification results

* Infrastructure: **completed** (the runner completed; this says nothing about model quality)
* Live evaluation: **BLOCKED** (12 requested: 0 pass, 0 fail, 12 blocked)
* Campaign usage: {'model_calls': 0, 'tokens': 0, 'known_cost_usd': 0.0, 'unpriced_calls': 0, 'failed_calls': 0, 'seconds': 1.7}

| Gate | Outcome | Kind | Reason |
|---|---|---|---|
| claude.smoke | BLOCKED | credentials | credentials unavailable: no Anthropic credentials detected in the environment (ANTHROPIC_API_KEY / ANTHROPIC_AUTH_TOKEN / ANTHROPIC_PROFILE) |
| claude.bench.A | BLOCKED | credentials | credentials unavailable: no Anthropic credentials detected in the environment (ANTHROPIC_API_KEY / ANTHROPIC_AUTH_TOKEN / ANTHROPIC_PROFILE) |
| claude.bench.B | BLOCKED | credentials | credentials unavailable: no Anthropic credentials detected in the environment (ANTHROPIC_API_KEY / ANTHROPIC_AUTH_TOKEN / ANTHROPIC_PROFILE) |
| claude.bench.C | BLOCKED | credentials | credentials unavailable: no Anthropic credentials detected in the environment (ANTHROPIC_API_KEY / ANTHROPIC_AUTH_TOKEN / ANTHROPIC_PROFILE) |
| claude.bench.D | BLOCKED | credentials | credentials unavailable: no Anthropic credentials detected in the environment (ANTHROPIC_API_KEY / ANTHROPIC_AUTH_TOKEN / ANTHROPIC_PROFILE) |
| claude.bench.E | BLOCKED | credentials | credentials unavailable: no Anthropic credentials detected in the environment (ANTHROPIC_API_KEY / ANTHROPIC_AUTH_TOKEN / ANTHROPIC_PROFILE) |
| gemini.smoke | BLOCKED | credentials | credentials unavailable: no Gemini credentials in the environment (GEMINI_API_KEY / GOOGLE_API_KEY, or Vertex AI configuration) |
| gemini.bench.A | BLOCKED | credentials | credentials unavailable: no Gemini credentials in the environment (GEMINI_API_KEY / GOOGLE_API_KEY, or Vertex AI configuration) |
| gemini.bench.B | BLOCKED | credentials | credentials unavailable: no Gemini credentials in the environment (GEMINI_API_KEY / GOOGLE_API_KEY, or Vertex AI configuration) |
| gemini.bench.C | BLOCKED | credentials | credentials unavailable: no Gemini credentials in the environment (GEMINI_API_KEY / GOOGLE_API_KEY, or Vertex AI configuration) |
| gemini.bench.D | BLOCKED | credentials | credentials unavailable: no Gemini credentials in the environment (GEMINI_API_KEY / GOOGLE_API_KEY, or Vertex AI configuration) |
| gemini.bench.E | BLOCKED | credentials | credentials unavailable: no Gemini credentials in the environment (GEMINI_API_KEY / GOOGLE_API_KEY, or Vertex AI configuration) |

NOT RUN (not requested): connector.google, connector.msgraph
