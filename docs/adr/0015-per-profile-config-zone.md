# Per-profile configuration zone; retire the Folders matrix and Memory modal

Configuring a Profile was scattered across three surfaces — Settings → Profiles (list
+ focus pills), a standalone Settings → Folders Section (an install-wide grant
**matrix** with a row per Profile), and a separate Memory **Modal** (two layers:
shared "Who you are" + this profile's persona). We collapse per-profile configuration
into **one tabbed zone**: the Settings → **Profiles** Section keeps the profile list on
top and gains a three-tab strip — **Profile Memory · Folders · Focus areas** — that
always configures the **active** Profile. The multi-profile Folders matrix and the
Memory modal are retired; cross-profile Folder granting is now done by switching the
active Profile.

The load-bearing move is **per-profile scoping**: two things that are install-wide got
evicted from the zone rather than allowed to reach across Profiles from inside a
surface that reads as "this profile." The **Folders** tab shows only the active
Profile's **Grants** (one `read`/`read+write`/`none` switch per **Folder**); adding a
folder registers it install-wide *and* auto-grants this Profile `read`. The shared
**"Who you are"** memory moves to Settings → **Advanced** (install-wide), while the
**Profile Memory** tab edits the persona layer only.

The zone reorganization needs **no backend changes**: every endpoint already exists
(`setFocuses`, `getMemory`/`setMemory`, `globalMemory`/`setGlobalMemory`,
`createFolder`, `setGrant`, `revokeGrant`, `deleteFolder`). "Add = create then grant
`read`" is client-side orchestration.

## Per-profile model Active override (the one part that touches the backend)

The section header also gains two model switchers — **Text** (reusing the composer's
`ModelSwitcher`) and **Live** (a parallel one) — that set a per-profile **Active
override**: which shared model is Active *for this profile*. This deliberately relaxes
the "single Active, install-wide" invariant (glossary *Active*; ADR 0004). The model
**list stays shared** — only the *selection* becomes per-profile, carried in the
profile's config overlay (`llm` is already an overlay section). Resolution precedence:
**env pin > profile override > install-wide Active > env fallback**. Each switcher
offers "use install default" to clear the override. Unlike the rest of the zone this
needs new backend surface — a profile-scoped "use this model" that writes the override
(not the install-wide `useLlmConfig`), and the settings payload must report both the
override and the effective Active so the switcher can show inherited-vs-overridden.
See ADR 0004's amendment.

## Considered options

- **Keep the install-wide Folders matrix as a power-user Section** alongside the
  per-profile tab — rejected: two surfaces for one concept, the redundancy the
  redesign exists to remove.
- **Put both memory layers in the Profile Memory tab** — rejected: editing the shared
  layer there silently rewrites every Profile's identity base from a surface framed as
  "this profile."
- **A whole new Page or standalone modal** for the zone — rejected: larger structural
  change than warranted; the Settings Modal already titles itself with the active
  Profile and reloads on Profile switch, so the Profiles Section is the natural home.

## Consequences

- **Cross-profile Folder granting requires switching Profile.** There is no single view
  of "which Profiles can reach Folder X" anymore. Accepted deliberately as the cost of a
  coherent per-profile surface; the Grant data model is unchanged (still install-wide
  registry + per-profile grants), so a matrix could return later as a pure read view
  without a model change.
- **Add-a-folder auto-grants `read`, not `read+write`.** Write is an explicit upgrade via
  the switch — the safe default for a freshly exposed path.
- **The Memory modal is deleted, not slimmed** (`Memory.svelte`, the `memoryOpen` store,
  its `App.svelte` mount + `anyModalOpen` gate, and `openMemory`). Advanced edits the
  shared doc inline.
- **Onboarding is untouched** — its own first-run focus/folder pickers are a separate flow.
- **No migration.** Pure frontend reorganization over existing, unchanged endpoints and
  stored state.
