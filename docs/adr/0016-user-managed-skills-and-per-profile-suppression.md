# User-managed skills: install-wide state + per-profile Suppression

Skills were previously all-or-nothing on disk — present meant available, absent meant
gone — and only the agent installed them. We give the user total control from Settings
by adding a **Disabled** state (installed but dropped from the `<available_skills>`
catalog) and a per-profile **Suppression** override, structured as a mirror of the
Folders model (ADR 0006 / 0015) but with the default **inverted**: a Global or Bundled
skill is available to a profile *unless* turned off, where Folders are unreachable
*unless* granted.

## Context

Three skill layers exist (glossary): **Bundled** (first-party, read-only, ships with the
app), **Global** (user-installed at the Root, shared by every profile), **Profile**
(installed inside one profile). Users asked to enable/disable/delete skills at both the
application (install-wide) and profile level, and to have that control feel immediate.

## Decision

- **New Skill state — Enabled/Disabled.** Disabled keeps the skill on disk but out of the
  catalog. This is distinct from Delete (removal from disk).
- **Two scopes, mirroring Folders' structure.** An install-wide state lives at the skill's
  home layer; a per-profile **Suppression** override lets one profile turn a shared
  (Global or Bundled) skill off for itself only. Resolution: a Global/Bundled skill is
  available to a profile **iff** it is install-wide Enabled **AND** not Suppressed here.
- **Default inverted from Folders.** Absence of a per-profile record means *inherit "on"*
  — exactly the model **Active override** semantics (ADR 0004/0015), the opposite of a
  Folders Grant (default-deny, opt-in). No migration: every existing skill stays available
  everywhere on upgrade.
- **Bundled skills are disable-able but never deletable.** Read-only on disk, so their
  install-wide Disabled state and per-profile Suppression are recorded in a separate small
  store keyed by skill name, not as a file mutation.
- **Delete cascade-purges Suppression.** Deleting a Global skill install-wide also drops
  every profile's Suppression record for it — the exact Folders precedent
  (`delete_folder` drops its Grants). A later same-named re-install returns default-ON
  everywhere, with no ghost suppression.
- **Every change rebuilds the agent.** The `<available_skills>` catalog is a
  construction-time snapshot, so a change is applied by a reference-swap `reload(pid)` (the
  same seam the per-profile model override uses). A per-profile change reloads only that
  profile; an install-wide change (Global/Bundled enable/disable/delete) **eagerly fans
  out** a reload to every live runtime, so "disabled everywhere" is true everywhere at
  once — including profiles serving channels/tasks in the background. An in-flight turn
  finishes on the old catalog; the next turn sees the change.

## Considered options

- **Two independent scopes (no per-profile override), disable at the home layer only** —
  rejected: a global disable is an everyone-affecting hammer; users wanted "off for this
  profile" without deleting a shared skill for the others.
- **Literal Folders mirror (default-OFF, opt-in per profile)** — rejected: it flips
  current behavior, dark-starting every profile's skills after upgrade and forcing a
  re-enable sweep. A skill is a capability you add, not a path you expose.
- **Hide Bundled skills from the UI** — rejected: if the point is total control, three
  always-on skills the user can't see or silence is a leaky abstraction.
- **Accept "takes effect next session"** — rejected: a human flipping a switch expects the
  agent to stop using the skill now; the reload seam already exists and is cheap.

## Consequences

- **A skill's off-state can live in three places:** a Global/Profile skill's own layer
  (install-wide flag), the separate Bundled-state store, and per-profile Suppression
  records. Resolution must consult all applicable ones. Keep the query in one place.
- **Skills are identified by name.** The catalog already dedupes by name with precedence
  **Profile > Global > Bundled**; Suppression and Disabled records key by name within
  scope. Re-installing a name replaces the prior skill.
- **Install-wide changes touch every profile.** The fan-out reload is bounded (few live
  runtimes) and idiomatic here (`ProfileManager.runtimes()`), but it is real work on each
  toggle — acceptable because install-wide changes are rare and deliberate.
