# Installing skills from Settings: registry + upload + git, as snapshots

Until now only the agent installed skills, always into the active profile, always from the
skills.sh registry. We let the user install from Settings, from three sources — **registry
search**, **local upload**, and **git URL** — into a target chosen by *which surface* the
install starts from. Git and upload sources are copied in as **one-time snapshots**; the
origin is not tracked and there is no update/sync.

## Context

The agent already installs via `SkillSearchToolkit` (search/install/remove) over skills.sh,
defaulting to the profile skills dir. Users wanted to install their own skills — including
ones that only exist in a git repo — from the Settings UI. Execution trust is unchanged
across all sources: skill scripts still hit the blocklist / Docker sandbox regardless of
where the skill came from, so new sources add *ingestion* paths, not new *execution* risk.

## Decision

- **Three sources.** (1) **Registry search** — the existing skills.sh pipeline, exposed as
  HTTP endpoints for the frontend; addresses a single skill per entry. (2) **Local upload**
  — a `SKILL.md` or a zipped skill folder. (3) **Git URL** — clone, take the skill's files.
- **Target by surface, not a picker.** Installing from the **Application** Skills page →
  **Global** (Root); installing from the **Profiles zone** Skills tab → the **active
  Profile**. Location carries the scope, consistent with ADR 0015's split of install-wide
  vs per-profile surfaces.
- **Git/upload are snapshots.** Copy the skill's files into the target skills dir and
  discard the origin. A snapshot-installed skill is indistinguishable from a
  registry-installed one. "Update" = delete + re-install. No git URL/ref is persisted, no
  update action, no conflict handling.
- **Discover-and-pick for multi-skill sources.** A git repo or uploaded folder may contain
  several `SKILL.md`. Scan the source, present the discovered skills as a checklist, and
  install the selected subset into the surface's target. (Registry installs stay single —
  the registry addresses one skill per entry.)

## Considered options

- **Registry-only** — rejected: users explicitly need to install skills that live only in a
  git repo or as a hand-written file.
- **Git as a tracked, updatable source** (persist URL + ref, add an Update action) —
  rejected for v1: introduces origin tracking, ref pinning, and upstream-vs-local conflict
  handling the codebase has no analogue for (the registry path is install/remove only, no
  update). Recorded as a clean fast-follow if users maintain evolving skill repos.
- **Explicit Global/This-profile toggle in one install dialog** — rejected: redundant with
  the surface's own meaning and invites the mismatch of installing from the Profile list
  but picking Global.
- **One-skill-per-install (point at the subfolder)** — rejected: a skills-monorepo
  maintainer would feel the friction of installing paths one at a time; discover-and-pick
  is worth the extra selection step.

## Consequences

- **Upload/git add an ingestion + validation surface** (a dropped folder must be validated
  as a well-formed skill before it lands). Execution is still gated by the existing
  sandbox/blocklist, so the trust boundary is unchanged.
- **No provenance is retained.** After install there is no record that a skill came from git
  vs registry vs upload, so an "update from upstream" feature would need the tracked-source
  design (above), not just a new button.
- **Name collision on install replaces the existing skill in the target** (consistent with
  re-install; see ADR 0016's name-identity note).
