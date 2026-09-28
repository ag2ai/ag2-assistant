# A Card's look is data, not code

A **Card** declares its layout in its own file, composed from a shared vocabulary of
primitives and design tokens the front end ships. The front end bundles **no Card** — it
bundles the vocabulary that draws every Card, ours and the user's alike. The bespoke Svelte
renderer per first-party Card is removed, and with it the privileged class of Cards whose
look could only arrive with a release.

## Context

A Card's look lived in three different places at once: eight hand-written Svelte components
keyed by component type, four more layouts inlined as branches of the surface renderer, and
two further branches inside the generic renderer for Cards nested in a layout — so one Card
could be drawn two different ways depending on where it appeared. None of it was reachable
from a file, so a user-authored Card would have fallen through to a generic fallback and
rendered as a title and a row of pills.

The tempting reading of "Cards become data" is that only the *schema* moves to a file and
the look stays in code. That keeps the app's Cards beautiful and makes everyone else's
unusable, which defeats the point of the directory.

## Decision

- **Richness is a shared vocabulary, not per-Card code.** The primitive set grows the visual
  atoms the existing looks actually need — a sparkline, a status badge, a metric with a
  delta, a comparison table, a timeline, a lead-media block — and the primitives gain a
  styling vocabulary (tone, emphasis, size, alignment, spacing) whose values resolve to the
  existing design tokens. **A Card never carries a raw colour or a length.**
- **The ceiling is common.** Whatever a Bundled Card can express, a Profile Card can express,
  because there is exactly one renderer and one vocabulary.
- **Repetition belongs to the renderer, not to the emitter.** A list binds to an array and
  repeats one template; bindings inside a template resolve relative to the item. A Card's
  layout is therefore static and independent of how many rows the data happens to have.
- **A relative binding is marked, and the mark is ours.** Inside a repeated template a path
  opening with `.` reads the item (`./day`, or `.` for the item itself); any other path reads
  the whole data model. Canonical A2UI evaluates a template *in* the item's scope and needs no
  mark, but a Card's row must reach both its item and the Card's own fields, so one of the two
  has to be marked. A layout authored against the published catalog alone renders blank here.
- **The server expands, the browser stays ignorant.** The model emits a Card's fields; the
  server pairs them with the layout from the file and publishes ordinary primitives. No
  client needs to know that Cards exist as files.
- **A Card may link to the app's own nouns** — a Task, a Chat, a File, a Folder — through a
  reference primitive that the front end resolves into a URL (ADR 0008). Available to every
  Card, so app-awareness is not a privilege of first-party ones.
- **A Card knows nothing about tools.** No shared constants, no tool identity. Where a
  vocabulary must be constrained — the weather glyph names — the constraint belongs to the
  primitive, and the tool targets the primitive's vocabulary.

## Considered options

- **Keep the bespoke renderers, move only the schema to files** — rejected: it permanently
  splits Cards into ours (beautiful, code) and theirs (from blocks, data), which is the
  problem the directory exists to solve.
- **Let a Card carry scoped CSS** — kept as a deliberate escape hatch, not taken now. It is
  third-party styling in our DOM: it needs a property allowlist, a scope guarantee, and a
  ban on remote references, which leak the viewer's address on every render. Revisit only
  when a real Card proves the vocabulary cannot express it.
- **Let a Card carry full markup in a sandboxed frame** — rejected: theme, sizing, data
  binding and actions all have to be tunnelled across the boundary, and a thread routinely
  shows several Cards at once.
- **Unroll repetition on the server** — rejected: the components would freeze at the row
  count of the moment they were emitted, so a later data-only update could change values but
  never add a row. That is precisely what a Card outliving its Chat needs to do.

## Consequences

- **The vocabulary is the ceiling, and it is ours to raise.** A look nobody can express is a
  missing primitive, and adding one serves every Card at once — but it is a release, not a
  file drop.
- **The riskiest Card decides the design.** The heaviest existing Card is migrated first, end
  to end; if it cannot be expressed, the escape hatch above is reconsidered before eleven more
  files are written against the format. **It was, and it could**: MarketBoard is a file, drawn
  from `Sparkline`, `Metric` and the tone vocabulary it grew. The scoped-CSS hatch stays shut.
- **The vocabulary is written down** in `src/assistant/cards/VOCABULARY.md` — the primitives, the
  styling words, and the tokens each resolves to. It is one list for every Card, ours and the
  user's, and the place a new primitive is recorded when one is added.
- **Relative bindings are a new concept in the protocol**, and must be carried through value
  resolution, data writes and action context alike — otherwise a button inside a repeated row
  cannot say which row it belongs to.
