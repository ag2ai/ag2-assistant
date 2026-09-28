# Two ACP doors: stdio for the Registry, WebSocket-only for remote

The same served agent has two front doors: `ag2-assistant acp` (stdio — what the ACP Registry
lists, launched by the client, `uvx` from a cold start) and `ag2-assistant acp-serve`
(long-running WebSocket listener, default port 8802 — what AG2 Space is configured to reach by
URL). The split is forced, not stylistic: the Registry's `distribution` schema admits only
`binary`/`npx`/`uvx` with `additionalProperties: false`, so no entry can name a remote endpoint —
every listed agent is local-launch, including Goose, whose production WebSocket server is simply
absent from its registry entry. We copy that split rather than invent around it.

**The remote door is WebSocket-only.** The remote-transport RFD requires HTTP/2 for its
Streamable HTTP profile — which uvicorn, our server, does not serve — while stating in RFC-2119
language that clients MUST support WebSocket and servers MAY be WebSocket-only. WS-only therefore
excludes no conformant client and halves the surface. Consequence with teeth: the SDK's
`create_asgi_app` speaks Streamable HTTP too, so the listener's guard **refuses every plain-HTTP
scope outright (404)** — an `http` request must never reach the SDK app, or an unauthenticated
transport we chose not to serve exists anyway.

**Auth is a per-listener shared token, checked at the WebSocket upgrade** (`Authorization:
Bearer`, constant-time compare, close-before-accept so a bad token fails the handshake itself),
with loopback binding by default and a hard startup refusal to bind a non-loopback interface
with no token. This deliberately does not repeat the gateway's documented "no auth of its own,
trusted network only" trade: this port drives an agent with tools, and if `session/load` ever
lands upstream a session id becomes a credential presented by whoever reaches the port. TLS
terminates at a reverse proxy. The surface is declared experimental and the
`agent-client-protocol` SDK is pinned exactly — a patch release (0.12.1) has already broken a
compatible-looking range once.
