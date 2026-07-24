# Profile delete is archive-first, irreversible, and separate from archive

Archiving a profile is soft and reversible (flag flipped, runtime torn down, folder
kept). We are adding two operations to complete the lifecycle: **Restore**
(un-archive + boot live) and **Delete** (permanent `rmtree` of the profile's folder
plus removal of its registry entry). Delete is the first and only operation in the
app that destroys profile state, so it is deliberately gated: it acts *only* on an
already-Archived profile — there is no one-step delete of a live profile — and the
UI requires typing the profile's name to confirm. Both live in a collapsed
"Archived" section in Settings → Profiles.

## Considered Options

- **One-step delete on a live profile (archive-then-purge in a single action)** —
  rejected: it would force delete to replicate every archive guardrail (last-profile
  refusal, active_default reassignment, runtime teardown) and give a mis-tap a direct
  path to erasing a running profile's entire data tree. Archive-first makes delete a
  clean `rmtree + drop entry` on a profile that is already stopped, already not the
  active default, and already not the last live one.
- **Registry-only delete (drop the entry, leave the folder)** — rejected: it reclaims
  no disk and orphans state that can never be recovered through the UI — strictly
  worse than archive. Delete only earns its place by actually destroying the folder.
- **Overload `DELETE /api/profiles/{pid}` to archive-or-purge by state** — rejected:
  the same call doing wildly different, irreversible things based on hidden state is a
  footgun. Instead: `DELETE /api/profiles/{pid}` stays archive; hard delete is
  `DELETE /api/profiles/{pid}?purge=true`, and `POST /api/profiles/{pid}/restore`
  un-archives. The explicit `purge=true` flag makes the soft→hard escalation
  deliberate on the server.

## Consequences

- Purge must reject (`409`) a profile that is not yet archived — this is what
  enforces archive-first at the API layer, independent of the UI.
- Restore is all-or-nothing: it flips `archived:false` then boots the runtime, and
  rolls the flag back to `true` if the boot fails, preserving the manager's
  "unarchived ⟺ running" invariant (a registered-but-not-running profile is otherwise
  treated as a server bug).
- The registry-entry vs folder/runtime split mirrors create: `profiles.py` owns the
  registry mutation (`restore_profile`, `delete_profile`), the ProfileManager owns the
  `_boot` on restore and the `rmtree` on purge.
- Web-only for now — no CLI `restore`/`delete` commands — even though
  `profiles list --all` already shows archived profiles. The `profiles.*` functions
  are reusable if CLI parity is added later.
