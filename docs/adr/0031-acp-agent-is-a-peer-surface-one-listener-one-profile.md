# The ACP Agent role is a peer surface, and one listener serves one Profile

AG2 Assistant now plays both ends of the Agent Client Protocol. `src/assistant/coding/` was
already the ACP **Client** (the assistant driving CLI coding agents as subprocesses);
`src/assistant/acp/` is the ACP **Agent** — the assistant itself being driven by an external
client (an editor, AG2 Space). The two roles share no code and meet only through repo-wide
services (`PermissionManager`, `ProfileRegistry`, storage): upstream ag2 made `mappers`/
`testing`/`types` serve both roles from one module and the result is a standing
singular-vs-plural trap (`session.py` is a Client subprocess, `sessions.py` a served
conversation), which we chose not to reproduce. The adjacent names are the cost; both packages'
docstrings carry the disambiguation.

**One listener serves exactly one Profile, fixed at launch.** Isolation is physical — the other
Profiles are not loaded in that serving path — rather than a reachability policy. This matches
upstream `ACPAgent`, which is one-tenant-per-process with no principal reaching the turn, instead
of fighting it. Serving N Profiles remotely means N listeners on N ports; that cost is accepted.

**ADR-0022's exposure machinery deliberately does not apply to ACP.** Exposure answers "which of
the owner's Profiles may answer on this surface" — a policy needed because one bot process can
reach every Profile. A one-Profile listener answers it structurally, so there is no default-allow
question to take a position on; the binding is a plain field on the listener record, validated
once at creation.

An ACP listener is a real Connection (`platform="acp"`) and each ACP session is a Peer — but the
listener records live in their own files (`acp_connections.json` + `acp_secrets.json`), not
`connections.json`. That file is exactly what the channel boot loop iterates
(`start_channel` → `CHANNEL_TOKEN_ENVS[platform]`, an unguarded lookup), so an ACP entry there
would be booted as a bot-token messaging channel and crash `Gateway.start()`. Invisible by
construction beats guarded by memory. The listener token likewise stays out of `SecretStore`'s
connection tokens, whose `CHANNEL_TOKEN_ENV_NAMES` allowlist is a closed set of bot-token env
vars that must keep meaning what it says.

## Considered options

- **ACP as one more channel platform** (join `CHANNEL_PLATFORMS`) — rejected: every consumer of
  that tuple assumes a token-bearing messaging adapter with a `channel_factory`; ACP is neither,
  and the boot loop would crash on it.
- **Client-chosen profile per `session/new`** — rejected: a client's declaration is a request,
  never a grant, and upstream offers no per-session principal to verify it against.
