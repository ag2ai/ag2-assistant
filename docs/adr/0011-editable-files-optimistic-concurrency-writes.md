# Editable Files: optimistic-concurrency writes via ETag / If-Match

ADR 0007 made the **Files** space user-writable through *coarse-grained* gestures —
Upload, mkdir, move, delete — each of which either creates a new path or removes
one, and none of which ever silently overwrites an existing file (a move onto an
occupied path is a `409`, an upload clash auto-suffixes). There was deliberately **no
way to rewrite the contents of a file in place**: the preview rail (ADR 0009) served
markdown, code, and text read-only.

We now let the user **edit a file's contents in place** from the preview rail
(markdown first; the mechanism is type-agnostic). In-place editing is a genuinely new
shape: it targets an existing path and *does* overwrite it — that is the whole point,
and is orthogonal to ADR 0007's never-overwrite rule, which governs *move/upload
collisions between two different files*, not a file rewriting itself.

The hard constraint is that **the agent writes into the same Files space concurrently**
(deliverables, chat output, scheduled tasks). A user can open `notes.md`, start
editing, and have the agent rewrite `notes.md` underneath them. A blind last-write
would silently destroy whatever the agent just wrote — invisible data loss the first
time a task touches an open file.

We therefore make in-place writes **optimistically concurrent**:

- `GET /files/raw` returns an **`ETag`** — an opaque, server-computed content hash of
  the file it just served.
- A write is `PUT /files/raw` carrying the client's `If-Match: <etag>`. The server
  recomputes the current file's hash and writes **only if it still matches**. A
  mismatch is a **`409 Conflict`**; a missing file is a **`404`** (this route edits
  existing files only — creation stays Upload's job).
- The client resolves a `409` by offering **Reload** (discard local edits, load disk)
  or **Overwrite** (re-issue the write forcing past the check). The human always
  decides who wins; the only way local edits die is if the user picks Reload.
- On a successful write the response carries the **new `ETag`**, which the editor
  adopts, so a subsequent save compares against what was just written rather than the
  stale open-time hash.

The hash is computed **only on the server**; the client treats the ETag as an opaque
token it echoes back. This sidesteps any JS↔Python hash-agreement problem (byte
encoding, trailing-newline normalisation) that a client-computed token would invite.

## Considered options

- **Last-write-wins (blind overwrite).** Rejected: zero bookkeeping, but a real
  silent-data-loss footgun given a concurrently-writing agent — the failure is
  invisible exactly when it matters (a scheduled task rewriting an open file).
- **Pessimistic lock while editing.** Rejected: the agent is autonomous and must not
  be blocked by an open editor, and locks leak when a rail is abandoned (× never
  clicked, tab closed). Wrong ownership model for a single-user assistant whose other
  actor is a background agent.
- **mtime as the version token instead of a content hash.** Rejected: coarse
  filesystem mtime granularity means two writes in the same clock tick look identical
  — a false *non*-conflict that reinstates the very data loss we set out to prevent.
  These are small text files; hashing is free.
- **Client-computed hash.** Rejected: requires JS and Python to hash byte-identically
  forever; `response.text()` normalisation vs a raw-bytes read is exactly the kind of
  divergence that turns every save into a spurious `409`. One hasher, on the server.
- **A diff/merge view on conflict.** Deferred, not rejected: Reload-or-Overwrite
  resolves conflicts without merge tooling, and with explicit save on a single-user
  install real conflicts should be rare (mostly agent-vs-user). A diff can layer on
  later if conflicts prove common.

## Consequences

- The Files space gains a fourth mutation verb — **in-place write** — alongside
  Upload/mkdir/move/delete, and it is the only one that overwrites an existing path on
  purpose. The never-silently-overwrite spirit of ADR 0007 is preserved: the overwrite
  is gated by an `If-Match` check and, on conflict, an explicit human choice.
- `GET /files/raw` now emits an `ETag`. Callers that ignore it (native `<iframe>`/
  `<img>` previews, downloads) are unaffected.
- The write route is edit-existing-only; there is deliberately no create-via-write
  path, so it never becomes a second, racy file-creation mechanism competing with
  Upload. A future "new markdown file" gesture is designed separately.
- Editing is offered only for **path-backed** previews. The path-less transient
  preview body (ADR 0009's documented in-memory exception) has nowhere to save and is
  not editable.
- Because the agent is never blocked, an agent write during an open edit is a normal,
  expected event that surfaces as a `409` on save rather than as corruption — the
  conflict is made visible and resolvable instead of silent.
