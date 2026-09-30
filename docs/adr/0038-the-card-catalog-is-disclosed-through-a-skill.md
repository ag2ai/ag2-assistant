# The Card catalog is disclosed through a Skill, not carried in the prompt

> Amended by ADR 0039: the description is rendered from the Cards' topics, and the
> protocol at large moved from the body to a resource.

The A2UI catalog stops being resident system-prompt text. It becomes a **Skill**: one line
in the agent's skill catalog, a body holding the rules plus an index of every available
**Card** by name and description, and one resource per Card carrying its schema and worked
example, fetched only when the agent has decided to draw. The Skill is Bundled, so a user
who wants a profile with no A2UI at all turns it off with the same switch that turns off any
other Skill.

## Context

The A2UI section was ~26k characters — about 6.5k tokens — attached to **every turn of every
chat**, whether or not a Card was ever drawn. Measured, it splits into roughly 11k of rules
and worked examples, 10k of component schemas, and 4k of envelope and actions; a Card costs
about 1.9k characters. That was tolerable while the app owned all eleven Cards. Once the
catalog is a directory a user and a plugin can write into, the resident cost becomes theirs
to inflate without limit, and a hundred Cards is not expressible at all.

Skills in this app already solve exactly this: the agent sees name and description, and pulls
the body on demand.

## Decision

- **A Bundled Skill owns A2UI.** Its description — the only resident text — says when to
  reach for a Card. Its body carries the behavioural rules and the Card index. Per-Card
  detail is a resource, read on demand.
- **Behaviour stays resident, catalogue does not.** The push to prefer a Card over prose is
  what makes the feature happen at all; an agent will not fetch what it does not know is
  appropriate. The part that grows with the number of Cards is the part that is deferred.
- **The body is rendered per read.** The index reflects what is on disk now, not what was
  there when the agent was built — the Skill catalog entry stays a construction-time
  snapshot, which is fine because it never changes.
- **It passes through the same filter as every other Skill**, classified as Bundled: one
  switch, install-wide Disabled and per-profile Suppression, no second mechanism. When it is
  off, the A2UI runtime and its middleware are not attached either — the profile stops
  drawing Cards and stops paying for them.
- **The rule forbidding tool calls to discover the catalog is inverted.** It existed because
  there was nothing to call.

## Considered options

- **Everything resident, with a per-Card size cap** — rejected on scale. The resident text is
  a stable, cacheable prefix and therefore cheaper per turn than it looks; but it grows
  linearly with a number the app does not control, and a plugin-rich install would price
  every chat, including the chats that never draw anything.
- **Index as a separate resource, static body** — rejected: it buys a static body at the cost
  of a third round-trip before every single render, forever.
- **One Skill per Card** — rejected: the index would be resident for free and cost one fewer
  round-trip, but Cards and Skills would share one list in the prompt and two different
  Settings screens. A Skill is instruction; a Card is a shape to render into.

## Consequences

- **Two round-trips before the first Card of a conversation** — load the Skill, read the
  Card. Thereafter both stay in context.
- **A Card may be drawn from memory without its schema being fetched**, and miss a field
  name. The existing single validation retry is the backstop.
- **The rendered body is uncapped and re-rendered on every read**, so the loader's own
  fingerprint cache is what bounds the work.
- **This depends on a dynamic Skill body upstream** (ag2#3254), which also makes skill reads
  async and context-aware — a breaking change this app's own filtered skill runtime must
  absorb when the pin moves.
