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
