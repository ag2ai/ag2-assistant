---
status: proposed
date: 2026-10-12
---

# AG-UI is a projection of the Event log, not its format

The web UI's chat leaves the hand-written WebSocket (`/api/p/{pid}/stream`) for AG-UI,
so a standard client can speak to the assistant and A2UI Cards travel as AG-UI
activities. The **Event log** keeps its current shape: it is also the agent's own
memory, reloaded into the AG2 stream on every Turn, and AG-UI events (text deltas, no
model requests, usage, or compaction) cannot be loaded back into it. AG-UI is therefore
an **AG-UI projection** of that log — the same function for live and replay, with
a model message's id is derived from the logged event it follows (streamed chunks are
not logged, so the response itself cannot name it), so both give the same ids.

## Decisions

- **The server owns the Chat.** The AG-UI thread id is the Chat id; from a run's
  `messages` only the new user turn is taken. Attachments, File references and Grants
  resolve on the server, so a client's replayed history is never trusted.
- **Transport is AG-UI's own.** A run is an HTTP POST answered with SSE. Events outside a
  run (Tasks finishing, a Peer writing in the same Chat) arrive on a separate per-Chat
  SSE subscription, and server-started Turns are framed by synthesised `RUN_STARTED` /
  `RUN_FINISHED`. History and paging stay a REST read, projected the same way.
- **An Inquiry is the interrupt.** A question inside a run ends the run with an
  interrupt outcome whose id is the Inquiry's; `resume` answers the Inquiry. The Turn
  keeps running in the gateway while no HTTP exchange is open. Questions outside a run
  (Tasks) are answered over REST. Both doors answer the same Inquiry, first answer wins.
- **The front end uses `@ag-ui/client`** with its zod event schemas (ADR 0026 holds).
- **The old socket is deleted in the same change** that adds this, with no dual support.
  Voice, `card-sources`, and Channels keep the `{type, data}` wire, so `wire.py` stays.

## Considered options

- **Persist AG-UI events as the log.** Rejected: the agent could no longer reload its
  memory from it, and a second log would have to be kept for the agent.
- **Use `ag2.ag_ui.AGUIStream.dispatch` as is.** Rejected for now: it binds one fixed agent,
  builds a new stream from the client's history, and holds a question only in memory,
  while our Turn is built per message and our Inquiries are durable. The adapter lives in
  this repo on the public `ag_ui` protocol package; moving it upstream is a later step.

## Consequences

- About 150 lines of the AG2 event mapping are duplicated here until upstream exposes it.
- A client cannot steer a running Turn over AG-UI (no event for it); that stays a
  separate call.
- A run posted to a Chat whose Turn is still running (including one waiting on an
  Inquiry) is refused with 409. Steering a running Turn stays the separate feed call,
  and the Turn resumes only by answering its Inquiry.
- A model message cut by an interrupt is closed at the interrupt; the response that
  follows the answer is sent whole under the same id, so a client replaces, not appends.

## Front-end decisions

- The SPA reads the stream with `runHttpRequest` + `transformHttpEventStream` from `@ag-ui/client` (no `HttpAgent`); `web/src/transport/agui.ts` owns the run (POST) and the subscription (GET).
- The subscription is closed while a run is in flight and reopened when it ends: the run carries the same events, so nothing is shown twice. A reconnect reloads the latest history page.
- `busy` is the run's state (`RUN_STARTED` → `RUN_FINISHED`/`RUN_ERROR`); the subscription opens with a synthesized `RUN_STARTED` when a Turn is already running.
- An assistant `TEXT_MESSAGE_END` carries `raw_event.created_at`, the log time `FeedbackGiven.target_id` keys on; stored ratings keep working. A subagent error carries `raw_event.status` (`failed`/`cancelled`).
- A message sent while a Turn runs goes to `POST /feed` and shows queued; `fed: false` runs it as a new Turn. An Inquiry that ended a run is answered by resuming it, any other over REST.
- Lost by the projection, accepted: the "(no reply)" placeholder for an empty reply (the turn simply ends without a bubble) and `continues` on a model response (tool chips are grouped by adjacency instead).
- The AG2 Inspector shows the AG-UI projection (`ag-ui.*`, tagged by subsystem; app events under their own names), filled from the loaded history page and the live events. Raw AG2 events are not sent: that would be a second data path beside the stream. Pages loaded by scrolling up are not added to it.
- Accepted gap: between a run's end and the subscription reopening (one SSE round trip) an event from a Peer or Task can be missed until the next reconnect, which reloads the latest page.
