# The Files tree's Folder section is Thread-scoped

The Files tree has always been **profile-scoped**: it renders the profile's
**Files** space, and it shows the same thing no matter which **Chat** or **Task** is
open. That squares with the invariant that a **Tab** (Chats / Tasks / **Files**) is
*orthogonal* to the open **Thread** — switching Tabs changes only the rail, never
the main pane, and the Files tree never depended on the conversation.

Making a granted **Folder**'s files previewable/downloadable in the tree breaks that
cleanly, because **Folder** reachability is not profile-scoped. A **Grant** resolves
per **profile ∪ chat**: a Folder can be granted to *one chat only*, widened or
narrowed by a chat override, or chat-blocked. `FolderStore.mode_for(folder, profile,
chat_id)` is the single source of truth, and it takes a `chat_id`. The `@`-picker
(ADR 0012) already resolves its corpus against the open Thread, and the agent reads
files against that same resolution. So the question is only: does the *tree's* Folder
section obey the same per-Thread truth, or a coarser profile-only one?

## Considered options

- **Thread-scoped Folder section (chosen).** The Folder roots shown in the tree are
  exactly those readable for the currently open Thread (`profile ∪ chat`, chat
  overrides and blocks applied); with no Thread open, only profile-level grants show.
  The tree, the `@`-picker, and the agent all read one `mode_for` truth, so they can
  never disagree about what is reachable. Cost: switching Chats can change which
  Folder roots the Files tree shows — the tree's Folder section is no longer
  Thread-independent.
- **Profile-only Folder section.** Show only Folders granted at the *profile* level,
  so the tree stays perfectly Thread-orthogonal. Rejected: a Folder granted to *this
  chat only* would be invisible in the tree even though the `@`-picker offers its
  files and the agent can read them — two sources of truth that visibly disagree,
  and the exact "permission-denied surprise" ADR 0012 set out to avoid, inverted
  (here: reachable-but-unbrowsable).
- **Reconcile the two into a merged, stable view** (union of profile grants plus a
  pinned snapshot of chat grants). Rejected: it invents a third reachability notion
  that neither `mode_for` nor the agent honors, so it would drift from what the agent
  can actually read — the one thing this whole area is built to keep aligned.

## Consequences

- **The Files-space section stays Thread-independent; only the Folder section is
  Thread-scoped.** The two halves of the tree now answer to different scopes, and the
  glossary says so (see **Folder**, **Directory** in CONTEXT.md).
- **One access truth, everywhere.** The tree, the `@`-picker, and the agent's own
  reads all resolve through `mode_for`. A file is browsable in the tree iff the
  picker would offer it iff the agent could read it — by construction.
- **`chat_id` reaches the file endpoints.** The `/files/*` routes that serve or
  mutate an absolute (Folder) path must carry the open Thread's `chat_id` to resolve
  the right Grant; a Files-space (relative) path ignores it and keeps today's
  profile-scoped, no-Grant behavior. Absent a `chat_id`, an absolute path authorizes
  against profile-level grants only.
- **Reveal degrades softly across Threads.** Because the Folder section is
  Thread-scoped, revealing a **Folder** file previewed in one Chat is a no-op if you
  switch to a Thread that does not grant that Folder — consistent with ADR 0012's "a
  reference is only as good as the access behind it."
- **Switching Threads can restructure the tree's Folder section.** Roots appear,
  disappear, or change mode as chat overrides come and go. The Active-file preview
  rail persists (it lives in the URL fragment), so a Folder file can remain previewed
  while its root leaves the tree — it then degrades to the ordinary "not reachable"
  rail state rather than pinning a stale row.
