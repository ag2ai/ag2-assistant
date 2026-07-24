# A failed turn keeps its work

A chat turn writes to two stores when it completes, in `Gateway._persist_turn`: the
**event log** (`/log/<chat>.jsonl`), which is the durable record the thread replays
from, and the **display transcript** (`/transcript/<chat>.json`), a lightweight
role/text document the sidebar lists from. A stub transcript holding only the user's
message is written up front, the instant a message is accepted, so a chat appears in
the list *during* a long agentic turn instead of living solely in the page's local
state.

That call sat in `send_message`'s `else:` clause, so it ran **only if the turn raised
nothing at all**. Any exception — a `reply_timeout_s` expiry, a provider `400`, a
dropped connection — re-raised straight past it, as did a cancellation that wasn't
the user's stop button.

The result was worse than a lost reply. The chat kept its stub, so it still *listed*
in the sidebar with a preview, but no event log was ever written — and since the
thread renders from the log replay, opening it showed an empty chat. Not even the
user's own message. Meanwhile anything the turn actually did (a task created, a file
written, mail sent) had already committed to its own store and survived, so the
side effects outlived every trace of the conversation that caused them.

This was not a rare path. A single install had nine such failures recorded in its
debug snapshots — four timeouts, plus `ClientError`, `BadRequestError`,
`OpenAIError` and `APIConnectionError` — and two chats sitting in the damaged state.
The worst shape is the most valuable turn: a long agentic run that does real work and
times out at the end, losing the entire record of what it did.

We therefore **persist on every exit path, not just the clean one**:

- The `except Exception` handler emits a **`TurnFailed`** event carrying a short,
  user-facing reason, then calls `_persist_turn`, then re-raises. Callers see the same
  exception they always did; the difference is purely that the work is now on disk.
- The `except asyncio.CancelledError` handler persists **before** deciding whether to
  re-raise, so a cancellation that isn't a user stop — a process shutdown mid-turn —
  keeps the turn too. Awaiting inside a cancelled task's handler completes normally
  (verified under a double cancel), so this needs no `shield`.
- Ordering is load-bearing: `_persist_turn` snapshots `stream.history`, so the
  `TurnFailed` event must reach the stream **first** or it will not make the log.
- `TurnFailed` projects to an `alert` note that ends the turn, mirroring
  `TurnCancelled`. The thread keeps its tool calls and partial text and then says why
  it stopped, rather than ending mid-air and replaying as perpetually "thinking".

The reason string is deliberately short and human (`"The turn timed out before it
finished."`). The traceback and the full history shape stay in the debug record that
`capture_failure` already writes — diagnostics belong there, not in the chat.

## Considered options

- **Leave it; a failed turn has nothing worth keeping.** Rejected on the evidence: the
  turn has usually done substantial work before failing, and its side effects persist
  regardless. Discarding only the record is the worst of both — the user sees a task
  appear with no explanation of where it came from.
- **Persist in a `finally:`.** Rejected: the success path already persists in `else:`,
  so a `finally` would either double-write or need a guard flag to suppress it. Two
  explicit handlers say what they mean at the point it matters.
- **Persist silently, with no `TurnFailed` event.** Rejected: it fixes the data loss
  but leaves a thread that stops without explanation, which reads as a different bug.
  The event is a handful of lines and reuses the whole `TurnCancelled` projection.
- **Put the exception text or traceback in the note.** Rejected: provider errors are
  multi-line JSON blobs, and a traceback in a chat bubble is noise the user cannot act
  on. Collapsed to one sentence; the detail is already captured for debugging.
- **Recover the already-damaged chats.** Not possible: their events were never
  written, so there is nothing to recover. Handled as a separate cleanup question.

## Consequences

- A failed turn now yields a transcript entry with an **empty agent message**. The
  sidebar is unaffected — its preview comes from the first *user* message — and the
  auto-titler is deliberately left alone, so a failed chat finally gets a real title
  instead of showing raw user text.
- `emit_event` persists the log itself, so a failed turn writes the log twice (once
  via the event, once via `_persist_turn`). Harmless, cheap, and the same shape the
  existing stop path already had.
- The turn's exception still propagates unchanged, so every caller — REST, WebSocket
  bridge, channels, task runs — keeps its current error behaviour. This ADR changes
  what is *retained*, never what is *raised*.
- A `SIGKILL`, or cancellation after the event loop has torn down, remains
  unrecoverable. Graceful shutdown is covered; process death is not, and the stub
  will still be all that survives in that case.
