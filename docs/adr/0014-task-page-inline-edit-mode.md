# The Task page edits itself inline; there is no Task-edit Modal

A **Task** had two surfaces: `TaskPage`, a read-only view of its config plus run
history, and `TaskEditModal`, a **Modal** floated over that page for both create
(`/t/new`, `task = null`) and edit. The modal duplicated the page's field set in a
second layout, carried its own chrome, and split create/edit across a modal that
was reached two ways (the page's pencil, and the Drawer kebab's "Edit" via the
`pendingTaskEdit` one-shot signal).

We are folding editing back into the page: the page has a **read-only state** and an
**edit state**, and Save/Cancel move between them. No Modal. The question this ADR
settles is not *whether* to inline it — it is *how editing commits*, because that
choice ripples into the create flow, folders, and every field's interaction.

## Considered options

- **Whole-page edit mode with an atomic Save (chosen).** The page reads as today's
  calm read-only view until the user clicks **Edit**, which flips the *entire* page
  into a form — every field a control at once. Changes (including folder Grants)
  buffer locally; **Save** commits them together and **Cancel** discards all of them.
  Create is the same form opened directly in edit state, where Save POSTs instead of
  PATCHes. One mental model ("edit, then save"), one code path shared with create,
  and the existing modal logic (buffer, `pendingFolders`, single Save, error banner)
  relocates almost verbatim.
- **Per-field inline autosave ("editable on click").** Each field is individually
  click-to-reveal and PATCHes on blur; no Save button; the page is always live. This
  reads well for free-text but forces a draft/commit dance for create (no id to PATCH
  until a task exists), splits fields into blur-commit vs explicit-close buckets for
  the multi-control widgets (schedule, folders), and makes "a Task always has a
  prompt" an edge to defend on every blur. Rejected: it optimises the rare edit-one-
  field case at the cost of a materially more complex create flow and failure model.
- **Keep the Modal, restyle it.** Cheapest, but preserves the duplicated field set
  and the float-over-page indirection this change exists to remove.

## Consequences

- **Folder Grants buffer with everything else and commit atomically on Save.** This
  is the load-bearing sub-decision. A **Grant** is an install-wide Folder-subsystem
  entity, not a task field, and the shared `TaskFolders` component grants *live* —
  so making Cancel truly revert folders means edit mode holds an intended grant set
  and, on Save, **diffs it against the task's current effective grants** and replays
  the create/set-mode/revoke ops. Create keeps its simpler "mint the buffered set
  after POST" path (`pendingFolders`). We chose atomicity over reusing live grants
  because a half-applied Cancel (task reverted, folders already changed) is a lie the
  user would not expect. Cost: the diff is the one genuinely new piece of logic.
- **The prompt is the only hard-required field.** Save is disabled while it is empty
  (as the modal already gated). On edit this means a Task can never be saved into a
  promptless state; on create it means an empty form cannot POST.
- **Create and edit are one surface.** `/t/new` opens the page directly in edit
  state, single-column (no History / Always-allowed to show yet); Save POSTs (auto-
  naming from the prompt when name is blank, unchanged from `create_task` today) and
  lands on `/t/{id}` read-only; Cancel returns to the Tasks tab. On commit the page
  reflows into the two-column live layout — that reflow is the "it exists now" signal.
- **`pendingTaskEdit` is repurposed, not deleted.** The Drawer kebab keeps its
  **Edit** item; the one-shot signal now means "enter edit state on arrival" instead
  of "open the modal." Plain row-click still lands read-only. The page's pencil button
  stays as the on-page Edit trigger.
- **`TaskEditModal.svelte` is deleted.** Its field set, buffer, `pendingFolders`
  path, single Save, and error banner move into `TaskPage`'s edit state.
- **No backend changes.** `create_task`, `update_task`, and the Folder-grant APIs
  already cover every commit this makes; only the frontend composition changes.
- **CONTEXT.md is unchanged.** The **Modal** glossary entry is generic and **Task** is
  untouched; removing one dialog coins no new term.
