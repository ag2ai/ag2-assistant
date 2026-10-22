# ACP sessions carry a destination, and the listener delivers task-run outcomes to its client

A Task created from Telegram, Discord or Slack records the conversation it came from
(`origin_channel`, `origin_chat`) and every run reports back there through that platform's
`Channel.notify`. A Task created in an ACP session recorded nothing: `acp` was not a push
platform, so `_origin` answered `(None, None)`, and ACP has no agent-initiated message
outside a prompt anyway. The run's outcome stayed in the web UI. AG2 Space reaches the
assistant through exactly this path (ADR 0032, agent-connect dialling `acp-serve`), so a
scheduled task created from a room never reported to the room.

Two additions, both on the ACP side and neither a new Channel:

**A session declares its destination.** A client may set `_meta["ag2/destination"]` on
`session/new`, a string or a mapping with `chat_id`. The session's Peer keeps it as
`destination`, and `_origin` prefers it over the Peer's chat id. The session id itself is not
a durable address: reconnect = new session = new Peer (ADR 0034), while the room the session
was opened for outlives every reconnect. A session without one keeps today's behaviour, with
the session id as origin.

**The listener has an outbound side.** `acp` joins `PushChannel`. Each supervised listener
owns an `Outbound`: the Client handles its connections have bound (reported by the connection
scope on `on_connect`), and a durable outbox under `<root>/acp_outbox/`. A task-run outcome
for the listener goes out as the extension notification `_ag2/notify` with
`{destination, text, connection}` on the newest connected client; a client that refuses the
send is dropped and the next one tried; with none connected the message waits in the outbox
and is flushed, in order, to the next client that binds. `ProfileManager.notify_channel`
routes a listener's Connection id there before looking for a Channel.

## Considered options

- **A file-based AG2 Space Channel in the assistant** — rejected in review: the Space
  integration is agent-connect over the ACP listener, and a second path beside it would be a
  parallel data path.
- **Treat `session/update` as the delivery** — rejected: an update belongs to a prompt turn;
  a run that finishes hours later has no turn to update, and a client that reconnected has a
  different session.
- **Deliver by session id** — rejected: it names a connection that may be gone; the
  destination names the place the client can still reach.

## Consequences

- The client owns the far side: it must implement `Client.ext_notification` for `_ag2/notify`
  and post the text where `destination` says. Nothing is delivered to a client that does not,
  and the message stays queued.
- Delivery is at-least-once in one case: a notification sent on a socket that dies before the
  client acts on it is not retried, while one the socket refuses is. The outbox is per listener
  and survives restarts.
- A standalone `acp-serve` builds no task service, so it creates no outcomes to deliver; the
  wiring is the manager's (`ag2-assistant run`).
