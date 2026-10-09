# Hosted run 37883447177 (code `5860085`): 38/39

GitHub Actions run: https://github.com/maxhightower/Daedelus/actions/runs/37883447177
Artifact `hosted-control-evidence` id 11594674361 (30-day retention). Transcribed from the job
log, as for run 1.

**Machine identity, now proven by hard evidence.** Each host reported sha256 prefixes of its
own identifiers:

| | control (Host A) | worker (Host B) |
|---|---|---|
| kernel boot id | `0b37e3622ceb5bbe` | `95e042696a8a45a1` |
| Azure VM id | `a6deff0e48df53ce` | `91e22b0fa5f4675b` |
| public IP | differs (`worker_public_ip_differs: true`) | |

Metrics:
* Blender create and edit on the remote VM: 8.4 s.
* Agent workflow: 6.2 s.
* Recovery after worker death: 22.5 s.
* Execution after control-plane restart: 92.7 s.

The only failure was the event stream through the Cloudflare quick tunnel. The new diagnostics
show what happened:

```
"sse_diagnostics": {"lines": 0, "status": 200, "headers": {"content-type": "text/event-stream; charset=utf-8",
  "transfer-encoding": "chunked", "cf-cache-status": "DYNAMIC", "cache-control": "no-cache, no-transform",
  "server": "cloudflare"}}
```

The response was accepted (200, correct headers), but **not one byte** of the body arrived
within 120 s, not even the immediate `hello` frame. Cloudflare's documentation states that
quick tunnels do not support Server-Sent Events
(https://developers.cloudflare.com/cloudflare-one/networks/connectors/cloudflare-tunnel/do-more-with-tunnels/trycloudflare/).
This is a limit of the anonymous tunnel used to expose the control plane, not of Daedelus. The
same stream works through the Caddy TLS edge (cluster S11) and a local TLS proxy.

**Response:**
* Daedelus now has a long-poll form of the event channel (`GET /api/projects/{pid}/events/poll`).
* The studio falls back to it automatically when the stream sends nothing for 8 s. It shows
  "live updates (polling)", and the V2.1 walkthrough tests it with the stream blocked.
* The hosted H6 check accepts either channel and records which one delivered.

All other checks passed: H1–H5, H7–H10, the agent workflow and live revocation. The check lines
are identical to run 1, with the identity check now passing.
