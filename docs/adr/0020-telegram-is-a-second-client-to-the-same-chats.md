# Telegram is a second client to the same Chats, not a separate surface

Telegram was request/response and self-contained: a Peer's messages went into one Chat
named after the platform conversation, that Chat existed forever, and nothing that
happened elsewhere ever reached the platform. We make Telegram a **peer client to the
browser** instead — the same Chats, reachable from either side, with continuity across
devices as the explicit goal.

Three things follow, and they are one decision, not three.

**A Peer moves between Chats.** Changing the **Peer profile** always opens a fresh
Chat rather than resuming a per-Profile one, because resumption on switch hides which
conversation you are about to continue. `/new` does the same within a Profile, and
`/clear` deletes the current Chat outright (the same permanent `delete_chat` the
browser offers — hence a confirmation, and hence no separate "wipe the transcript in
place" operation, which would be a domain verb the browser does not have). The Chat is
materialised lazily, on the first message, so switching back and forth mints nothing.

**`/resume` attaches to any Chat in the Profile**, including Chats begun in the
browser — that is what makes this a client rather than a sandbox. On attaching, the
Peer is shown a header and the tail of the transcript, because the agent can see a
history the platform screen has never displayed.

**An attached Peer mirrors its Chat.** Completed messages in that Chat reach the Peer
whoever wrote them and from wherever, along with the agent's questions, rendered with
the same option buttons. Answering out of band is already the contract — `DurableAsker`
persists every prompt as an **Inquiry** and races the live transport against an answer
arriving from anywhere else — so first answer wins and the other surface's card
resolves. The Telegram adapter's habit of swallowing the next typed message as the
answer to a pending question is removed: with a mirror, a message typed while a turn is
running is a message for the turn, fed into it, not an answer to someone else's prompt.

## Considered options

- **Mirror only when the user has the chat open in Telegram** — impossible, not
  rejected. The Bot API gives bots no read receipts and no presence; a bot learns
  nothing about a user until that user acts. "Open" is therefore defined on our side:
  a Peer mirrors the Chat it is **Attached** to, and nothing else.
- **Mirror the full event stream** (deltas, tool calls, produce events) — rejected on
  platform limits before taste: editing faster than roughly once a second draws 429s
  and a chat tolerates about twenty messages a minute. Completed replies fit; a token
  stream does not.
- **Keep Telegram Chats in their own namespace** — rejected: it is precisely the
  cross-device continuity being asked for.
- **Resume the previous Chat automatically on Profile switch** — rejected in favour of
  explicit `/resume`; implicit resumption makes it ambiguous which conversation you
  have just re-entered.

## Consequences

- **A Telegram command can destroy work visible in the browser.** `/clear` is the same
  irreversible delete, so it confirms before acting.
- **Group Peers are constrained separately** — see ADR 0021. A shared thread that can
  attach to any Chat in a Profile would publish private conversations to everyone who
  can *read* the group, whom the pairing allowlist does not cover.
- **Long replies must be chunked.** The adapter never split text; against a 4096-character
  limit a long reply simply failed to send. Splitting happens after plain-text rendering
  (formatting changes the length), prefers paragraph then sentence boundaries, keeps code
  blocks whole, and is shared by all three delivery paths: the reply, a task notification,
  and the mirror.
- **Mirrored attachments are named, not carried.** A file attached in the browser reaches
  the Peer as its filename; a **File reference** is folded the same way rather than
  spilling its `Referenced files:` block of absolute paths into the conversation.
