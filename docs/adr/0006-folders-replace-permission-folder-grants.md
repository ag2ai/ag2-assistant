# Folders replace the Permissions folder layer and the Project folder

The Project folder mechanism (one read-only path per profile, implemented by
seeding a `repo-files` filesystem MCP) and the Permissions folder half (grant/block
sets with deny-overrides) were two overlapping authorities over the same question —
"may the agent touch this path?" — and the MCP mount silently bypassed Permissions
entirely. We replace both with a single system: an install-wide, path-unique
**Folder** registry (the Secret pattern from ADR 0005) plus per-profile and
per-chat **Grants** carrying a mode (`read` / `read+write`). Permissions shrinks to
commands only. Designed for fresh installs; no migration of existing
`project_folder` settings or folder grant/block stores.

## Considered options

- **Folder as sugar over Permissions** (mint grants underneath, keep deny-overrides) —
  least surgery, but keeps two vocabularies for one concept and preserves the
  block machinery the new model doesn't need.
- **Parallel systems** (Folder governs MCP mounts, Permissions governs native tools) —
  the status quo's incoherence, rejected outright.

## Consequences

- **Pure allowlist, monotone.** No block/deny concept; exclusion is achieved by
  granting narrower Folders. Overlapping grants resolve by union (most permissive
  covering Grant wins); chat access = profile Grants ∪ chat Grants. Adding a Grant
  can never reduce access anywhere — this is what keeps the registry auditable at
  a glance. The old "profile can narrow the install" property is gone by design;
  a future sandboxed-chat feature would be a flag, not a grant semantics change.
  *(Amended 2026-07-17 — see "Amendment: per-chat override" below.)*
- **Native enforcement.** The agent's own file tools consult the grant store
  per call (mode-aware evolution of the `_covers()` path check). The auto-seeded
  `repo-files` MCP dies with the Project folder.
- **Runtime minting stays.** The HITL first-touch prompt survives with scope
  choice — Allow once (nothing persisted) / grant to chat / grant to profile /
  deny (nothing persisted) — and approving auto-creates the Folder, auto-named
  and renameable, mirroring inline Secret minting.
- **Profile Files space is outside the system** — always read+write to its own
  profile, never a Folder; the registry answers only "what of the user's disk is
  exposed", and cross-granting one profile's Files to another stays impossible.
- **Secret-style lifecycle.** Deleting a Folder is always allowed and revokes all
  Grants instantly; a disk-moved path is a badged, repointable state.

## Amendment: per-chat override (2026-07-17)

The strict "chat Grants only ever widen" rule made a common ask impossible:
turning a profile-granted Folder *off for one conversation* forced an
install-wide change affecting every chat. We relax the monotone property **for
chat scope only**: a chat-scoped Grant now **overrides** the profile-scope Grant
on the same Folder for that one chat — widening it (`read` → `read_write`),
narrowing it (`read_write` → `read`), or blocking it via a new chat-only
`none` mode. With no chat override the profile Grant stands unchanged, and
resolution across nested Folders still takes the most permissive *surviving*
Grant.

Scope of the relaxation is deliberately narrow: profile-scope Grants remain a
pure monotone allowlist (no profile-scope `none`), so the install-wide picture
is still auditable at a glance; only a specific chat can hold a narrowing or
blocking override, and it never touches the profile Grant. The override lives in
the same `folders.json` grant record (`chat_id` set, `mode: none|read|read_write`)
and is surfaced in the composer's per-chat **Folder access** modal.
