# ACP sessions persist as ordinary Chats; drop_history detaches, never deletes

An ACP conversation becomes a real Chat in the bound Profile — same transcript convention, same
`chats.db`, same list the web UI reads — not a parallel ACP-only store. The mapping rides the
records ADR-0022 built: each session is a Peer (`platform="acp"`, `chat_id` = the ACP session
id, `connection` = the listener), and `peer.chat` names the Chat.

Three rules with reasons:

- **`drop_history` detaches.** Upstream calls `Storage.drop_history` at session close, idle-TTL
  expiry, and eviction — but it calls *our* implementation, so what upstream treats as deletion
  we treat as releasing the live stream. The Chat and transcript survive every one of those,
  which is what makes container restarts (routine, scheduled, someone else's) non-destructive.
- **A Chat is born on the first prompt, never on `session/new`** — probes, health checks and
  aborted connections leave no empty Chats (the channels' own "fresh chat, nothing said in it
  yet" behavior).
- **Reconnect = new session = new Chat**, accumulating in `peer.chats`. Upstream advertises
  `load_session=False`, so there is no protocol route back into a prior session; if
  `session/load` lands upstream (proposal under evaluation), reconnection upgrades to
  reattachment via `peer.chat` with no storage redesign — the events already survive.

## Consequences

- The session↔Chat correlation has no public seam: `Storage` sees only a stream id, while the
  ACP session id lives on `AgentSession`. We observe `SessionStore.create` — the one point both
  ids are minted together — by wrapping the private `_ConnectionScope._sessions` slot. Sound
  under the pinned ag2 floor, fragile across upgrades; the clean fix is an upstream
  session-created callback, tracked on the spec's watch list.
- ACP-originated Chats get ordinary retention — no special ageing, no surprise disappearances;
  the owner manages them like any other chat.
