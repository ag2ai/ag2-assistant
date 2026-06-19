# AG2 Assistant Examples

## `network_gateway_spike.py` — distributed gateway prototype

A proof-of-concept showing an AG2 Assistant agent served over WebSocket via AG2's `Hub`,
reachable from a separate process (the "gateway" client) that could run on
another machine. Validates the distributed-network direction for Phase 2.

### Run it

```bash
# Terminal 1 — start the server (hosts the agent on a Hub, serves over ws)
python examples/network_gateway_spike.py server

# Terminal 2 — connect over WebSocket as a gateway client and ask a question
python examples/network_gateway_spike.py client "What is the capital of Japan?"
python examples/network_gateway_spike.py client "Search the web: what is AG2?"
```

### What it demonstrates

- **Distributed transport** — agent on a `Hub` served via `serve_ws`; client
  connects with `WsLink` from a separate process (potentially another host).
- **Hub as the message bus** — every message is routed through the hub, written
  to a per-channel WAL, and auditable. This is the foundation we'd otherwise
  hand-build for the gateway.
- **Tools work over the wire** — a web-search request from the client runs
  server-side in the agent and the result returns over the WebSocket.
- **`consulting` channel** — strict one-question-one-response, auto-closes after
  the reply. The gateway client is a `HumanClient` (no LLM).

### Why it matters for AG2 Assistant

The gateway (Phase 2) can be built *on* the Hub instead of from scratch —
inheriting routing, durable channels, audit, auth, and rate limits — and the
WebSocket transport gives AG2 Assistant a federation story (agents across devices /
machines / users) that single-process OpenClaw can't match. See
`docs/research-ag2-beta.md` → "Network & Distributed".
