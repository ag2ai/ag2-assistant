# AG2 Space is a Channel over the relay's task files

An AG2 Space cloud instance runs the assistant in a pod beside a relay bridge
(`ag2-sparrow`) that turns room messages into `workspace/tasks/<id>.txt` and posts
`workspace/results/<id>.txt` back to the room. The first integration was a connector
outside the assistant: a process that claimed each task file, called
`/api/p/{profile}/message` with the room folded into a chat id, and wrote the reply as
the result. That made every exchange a request and a reply. A Task the agent created
from a room had no `origin_channel`, so its run outcomes stayed in the web UI and never
reached the room.

AG2 Space is now a `Channel` (`channels/ag2space.py`), one Connection per instance
(ADR 0022). Its two settings are the workspace directory and the owner's Matrix id,
carried as the Connection's "tokens" so the existing registry, boot loop and API serve
it unchanged. Inbound, a task file becomes an `InboundMessage` whose `chat_id` is the
room and whose sender is the writer's Matrix id; the outcome becomes the result file.
Outbound, `notify` writes a `results/proactive-*.txt` whose first line names the room,
which the bridge already delivers. `ag2space` joins `PushChannel`, so a Task created in a
room records that room as its origin and its runs report back there.

Two rules carry over from the connector. Only owner-tier tasks run a turn; the tier is
the broker's decision, written last in the file by the trusted bridge, and the channel
parses it with the relay client's own parser rather than a copy. Completion is recorded
apart from delivery: the claim stays in `tasks/` until the bridge archives it, and
`state/answered/<id>` is what keeps a later poll, or a restarted process, from running the
task again; only a claim without a marker is a turn cut off mid-way, and is retried.

Pairing (ADR 0021) keys accounts by numeric id or handle. A Matrix id is neither, so
`OPAQUE_ID_PLATFORMS` lets the platform's ids be identity as entered. The single-channel
command pairs the owner it is run for; an install that creates the Connection through the
API pairs the owner through the pairing API.

## Considered options

- **Keep the connector and teach it to forward task-run events** — rejected: the
  assistant's own model already has the channel abstraction and the origin-channel
  report-back; a second path beside it would be a parallel data path the event-stream
  rule forbids.
- **A generic "file" channel** — rejected for now: the file grammar is the relay's, not a
  standard, and a second consumer would need the same parser and the same markers.

## Consequences

- The pod runs `ag2-assistant run` (gateway + channels) rather than `ag2-assistant gateway`,
  with `AG2SPACE_WORKSPACE` and `AG2SPACE_OWNER` in its environment, or creates the
  Connection through the API.
- The bridge delivers proactive files only when it has a destination configured; the
  deployment supplies the owner's DM room as its default.
- Questions a turn raises are mirrored into the room as numbered text. A room has no
  buttons, so they are answered on the surface that raised them.
