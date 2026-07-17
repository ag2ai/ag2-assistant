# Files becomes a user-writable file space in an IDE-style sidebar tab

The Files browser was a read-mostly modal over the agent's output — a flat,
newest-first list you could view, download, or delete. We move it into the Drawer
as a third segment (`Chats | Tasks | Files`), render it as a collapsible IDE-style
**Directory** tree, and — the load-bearing change — make the Files space
**user-writable**: upload, rename, move, new directory, and recursive directory
delete, alongside the agent's existing writes. The modal is retired.

## Considered options

- **Relocate the modal's list verbatim into a tab** (browse-only, flat) — smallest
  change, but keeps Files a read-mostly surface and doesn't deliver the IDE
  experience that motivated the work.
- **Full editor metaphor** (file tree in the sidebar, files open as tabs in the
  main area beside the Thread) — most IDE-like, but the main area is
  chat-thread-centric and a tabbed editor there is a large build that fights the
  existing shell. Rejected as out of proportion to the goal.

## Consequences

- **Files is no longer "the agent's output space" — it is shared read+write.** The
  glossary (`CONTEXT.md`) is amended accordingly. This is the reason for the ADR:
  a future reader seeing user-upload routes into the profile Files space would
  otherwise assume it contradicted the agent-only model.
- **New per-profile backend routes** join the existing `GET files` / `GET,DELETE
  files/raw`: `POST files/upload` (multipart → target directory),
  `POST files/move` (`{from, to}` — files and directories, name and path changes,
  subtree rewrite on directory move), and `POST files/mkdir`. Delete extends to
  remove directories recursively.
- **No silent data loss.** Uploads that clash on name auto-suffix `(2)`;
  move/rename onto an existing path is rejected with an inline error. Consistent
  with the app's delete-is-permanent, no-overwrite posture.
- **The tree is built client-side** from the existing flat `list_files` output
  (`{path, name, dir, size, modified}`) by splitting paths — no change to the read
  path. Ordering flips to IDE convention: directories first, then files, all
  alphabetical (the modal's newest-first is dropped). Empty directories are now
  representable (via New directory / move) where before the files-only listing
  never showed them.
- **"Directory" ≠ "Folder".** The tree's nesting nodes are Directories (inside the
  Root, no Grant); "Folder" stays reserved for the install-wide Grant registry
  (ADR 0006). Kept deliberately distinct to avoid collapsing two access models
  into one word.
- **Freshness is pull, not push.** The tab fetches on open and via a manual refresh
  button — no background poll — so a deliverable written while the tree is open
  won't appear until refreshed. A deliberate trade of liveness for zero polling
  cost.
