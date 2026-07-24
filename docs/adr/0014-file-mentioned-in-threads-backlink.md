# The file preview's "Mentioned in" backlink is a loose, path-historical transcript scan

The preview rail shows one **Active file**; its only backlink today is **Reveal**
(jump to the file in the Files tree). We add a second: a header affordance that
lists the **Threads** whose transcript mentions this file — a reverse link from a
file back to the conversations touching it. A **Thread** here is a plain **Chat**
*or* a **Task Run**: both are event streams in the same `chats.db` (a run's
transcript lives at `task-run:{run_id}`), so one scan covers both and the stream
key classifies each hit for free. A **Task** itself never appears — it holds
configuration, not a transcript; only its Runs can match.

The relationship is derived **on demand** when the file is previewed — a
`LIKE`-based scan of the current profile's chat store — with **no maintained
index** and no new persisted state. It is always fresh and adds no write-path or
migration surface.

Three deliberate, surprising calls a future reader would question:

- **"Mentioned", not "referenced".** The scan matches the file's path as a **loose
  substring** anywhere in a transcript — a `Referenced files:` block, a produce
  event, tool output, or bare prose. This intentionally widens past the original
  "referenced ∪ produced" intent to "the path appears somewhere", so the label
  reads **"Mentioned in N threads"** and promises no more than that.
- **Path-historical, no move/rename tracking.** Files have no stable identity in
  this system — only a path. Transcripts freeze the path as it was written, so a
  moved/renamed file yields an empty list and a reused path inherits stale hits.
  Accepted rather than building a file-identity layer this app doesn't have.
- **Full-path match, both forms.** We search the file's full path, OR-ing its
  representations — a Files-space file matches on its **absolute** form (Referenced
  blocks) *and* its **workspace-relative** form (produce/attachment events, prose);
  a Folder file matches its absolute path. Not the bare basename.

## Considered options

- **Structured match (exactly Referenced ∪ Produced).** Parse only the
  `Referenced files:` block and produce events, so the count means precisely
  "referenced or produced here". Rejected in favour of the looser substring scan:
  "any thread that talks about this file" was judged the more useful net, at the
  cost of precision on generic filenames.
- **Basename match.** Loosest, catches every path form plus casual filename
  mentions. Rejected: a generic name (`config.json`, `notes.md`) would collapse
  unrelated files together with no way to tell them apart. Full-path keeps hits
  tied to the actual file.
- **Maintained reverse index (`path → threads`).** Fast reads, but new persisted
  state, write-path coupling, and invalidation/migration for deletes and the
  path-collision case. Rejected for now: a single indexed `LIKE` over one
  profile's chat store is fine at personal-assistant scale and always fresh. If it
  ever gets slow we add an index behind the same endpoint — the UI contract holds.
- **Roll matching runs up to their parent Task (one row per task).** Compact for a
  heavily-scheduled task, but hides which run mentioned the file. Rejected: each
  matching Run is its own row (opening its exact stream), consistent with a Run
  being an openable Thread; plain Chats stay one row each.

## Consequences

- **Current profile only.** Profiles are fully isolated; the scan spans only this
  profile's streams and can never surface another profile's Chats or Runs.
- **Path-backed previews only.** The transient/path-less rail body has no file to
  trace, so the affordance is absent there.
- **Self-hiding.** Zero matches hides the header icon entirely — no dead empty
  state.
- **New read-only endpoint.** The scan is new backend surface, but read-only and
  index-free; it reuses the existing `chats.db` event store.
- **Generic filenames over-match, by design.** A file named `notes.md` will list
  threads discussing *other* `notes.md`-pathed files that happen to share a path
  string. Accepted as the cost of the loose "mentioned" promise.
