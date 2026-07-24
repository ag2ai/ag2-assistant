# @-file references resolve by path, not inline bytes

The composer already has one way to put a file in front of the agent: an
**Attachment** (`+`, paste, drop) encodes the file's bytes inline and sends them
with the message, transient and message-scoped. That is the right tool for a file
the agent *cannot otherwise reach* — an OS file off the user's disk.

It is the wrong tool for a file the agent **already accesses**: everything in the
profile's **Files** space, and everything inside a Folder this chat holds a Grant
to. Those files are already on disk and already permitted; re-encoding their bytes
into every message duplicates data the agent can read for itself and grows with
the file on every send.

So `@` introduces a second, distinct concept — a **File reference**: an inline
pointer the user drops into a sentence (`compare @0011-… with the code`) that
names an already-accessible file or Directory. It carries a **path**, not bytes.
At send time each reference resolves to an absolute path and is appended to the
message as a `Referenced files:` block; the agent opens it itself — `read_file`
for a file, `list_folder` for a Directory. This reuses the turn-level
`PermissionManager`, which already auto-allows the Files space and any granted
Folder, so a reference never triggers a redundant permission prompt.

The picker is fed by a new **chat-aware server-side search endpoint**: it takes
the typed query and returns ranked file+directory matches across the Files space
plus every Folder granted to this profile-and-chat (minus any chat block),
bounded to a top-N. Search lives on the server because a granted Folder can be a
whole code repo — enumerating tens of thousands of paths into the browser on
every composer open does not scale.

## Considered options

- **Inline the bytes (reuse the Attachment pipeline), sourced from accessible
  files.** The agent would see the content guaranteed this turn with no tool call.
  Rejected: it duplicates bytes already on disk, re-encodes the file on every
  send, grows unboundedly with file size, and collapses a distinct concept
  ("point at a file I can already reach") into Attachment ("carry a file I
  can't"). The whole value of `@` is that the file is *already there*.
- **Eager client-side enumeration** — walk the Files space and every granted
  Folder once, ship the flat list to the browser, filter locally. Instant
  filtering and no search endpoint, but a large granted repo blows any cap and
  silently drops files. Rejected in favour of server-side search that is correct
  at any repo size.
- **Inline path expansion instead of an appended block** — splice each file's
  absolute path into the sentence in place of its `@label`. Reads as one natural
  sentence, but forces the composer to track mention character-ranges so edits and
  duplicate filenames (`files.py` × 2) don't desync the label→path map — the
  fragile part of mention inputs. The appended-block form keeps picks as a tracked
  list (like Attachments), so the inline `@label` stays cosmetic and no text
  surgery is needed.
- **A structured `references[]` channel on `send()`** — cleanest data model, but
  breaks the inline "compare @a with @b" phrasing that motivates `@`, and adds
  wire plumbing the appended-block form avoids entirely.

## Consequences

- **`@` and `+` mean different things, on purpose.** `+`/paste/drop = Attachment
  (inline bytes, file the agent can't otherwise reach). `@` = File reference (a
  path, file the agent already can). The glossary carries both so the split stays
  legible.
- **A reference is only as good as the access behind it.** `@` surfaces solely
  what the endpoint says is reachable for this chat; a reference to a file whose
  Grant is later revoked, or that is moved/deleted, degrades to `read_file`'s
  ordinary "File not found" / permission message — no special-casing.
- **No `send()` change.** The `Referenced files:` block is appended to the message
  text client-side; the existing `(text, attachments)` contract is untouched.
- **New endpoint to own.** The chat-aware search endpoint is new backend surface;
  it must honour the same `mode_for` resolution the agent's own reads do, so the
  picker can never offer a file the agent would be denied.
- **Works in both Threads.** The Composer is shared, so `@` is available in Chat
  and Task Threads with no extra work.
- **Directories are in scope.** A reference can name a Directory; the block
  annotates it so the agent runs `list_folder` rather than `read_file`. This keeps
  one gesture (`@`) mapping to two agent actions — accepted as the cost of letting
  the user point at a subtree without leaning on the coarser Folder/Grant UI.
