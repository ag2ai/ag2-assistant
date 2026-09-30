# The rich-views description names the cases its Cards cover

The `rich-views` Skill's description — the only A2UI text a turn carries — stops being a
fixed sentence. It is rendered from the Cards this profile is offered: each Card may declare
a short `topic`, the case it is ready for ("the weather", "stock, fund or crypto prices"), and
the description lists them. The body shrinks to what a Card needs — the index and how to draw
one — and the A2UI protocol at large becomes a resource, `reference/protocol.md`.

Amends ADR 0038: the catalog entry no longer "never changes". Amended by ADR 0040: a Card is
drawn by a Skill script.

## Context

Live, with the Skill enabled and in the prompt, three models (GPT-5.4 Mini, GPT-5.6 Terra,
GPT-5.6 Luna) answered "what is the weather in Berlin?" in prose and never loaded the Skill.
The push to draw unasked lived in the body, which a model only reads once it has already
decided to draw; the description spoke of "a rich view", a thing no user question is about.
Every other Skill's description names the question it answers, and those do get loaded.

The body was ~10.4k characters, two thirds of it AG2's generic protocol text — message types,
`callFunction`, `actionResponse`, the basic controls — none of which drawing a Card needs.

## Decision

- **A Card may declare a `topic`** (≤60 characters). The description lists the topics of the
  Cards available now, within the Agent Skills 1024-character cap, and says to load the Skill
  before answering such a question. A Card with no `topic` is not named there — the
  server-filled CodingSession has none, so the model is not invited to draw it — but it stays
  in the index. The `topic` is explicit rather than cut from the description's first clause,
  which would depend on how each author phrased it.
- **The body is about Cards**: the index, then four steps — gather the data, read the Card's
  detail, emit it, do not restate it.
- **The protocol is a resource**, read to compose several Cards on one surface or to draw from
  the basic components when no Card fits.

## Consequences

- The description is part of the Skill catalog the agent is built with, so it is a snapshot:
  a Card added, removed or switched reaches it on the next agent build (a profile reload), as
  a Skill installed mid-session does. The body and resources stay rendered per read. The Card
  switches in #87 must trigger that reload, as the Skill switches do.
- Settings shows the fixed opening sentence, not the per-profile list — the row is read
  without resolving a catalog.
- A turn's prompt replaces the agent's own (AG2 seeds the agent's only when the caller
  passes none), so the gateway opens every turn with the agent's system prompt — the
  persona and the skills catalog — before its per-turn guidance. Until then no Skill's
  description reached a web chat, and moving the catalog into a Skill had left the turn
  with no A2UI text at all.
- Measured on seven prompts (six with a Card, one control) through a real gateway turn:
  GPT-5.6 Luna went from 1/7 to 14/14 over two runs; GPT-5.4 Mini reaches 2/7. A weaker
  model may still need a resident index of Card names.
