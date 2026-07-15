# The frontend owns all accent-color knowledge; the backend stores an opaque hex

A Profile's **Accent** (its visual-identity colour) is stored and transmitted as a
single opaque `#rrggbb` hex. The backend never knows about named palettes: it
validates only the hex *format*, keeps no catalogue of allowed colours, and does not
check accents for uniqueness across profiles. The six preset palettes, their
hand-tuned 10-step ramps, and the derivation of a ramp for any custom colour live
entirely in the frontend. Selecting a preset simply sends its hex, exactly like a
custom colour.

## Considered Options

- **Backend-validated palette enum (the previous design)** — the profile stored a
  name (`"teal"`) from a closed set of six, validated server-side and forced unique
  while ≤6 profiles. Rejected: it caps identity at six curated colours and makes
  "let the user pick any colour" a cross-stack change (new field/validation/migration)
  every time the palette set moves. The catalogue is a *presentation* concern, not a
  domain rule the backend should adjudicate.
- **Open hex, but keep a backend copy of the preset catalogue** (for a CLI default and
  uniqueness) — rejected: it re-introduces the colour catalogue on the backend for no
  domain benefit, so the same list must be kept in sync in two languages. The CLI keeps
  only a single fallback literal (`#109e91`) so `create` works argument-free; that is a
  default, not a catalogue.
- **Auto-adjust custom colours for contrast** (darken/ink-flip so white text stays
  legible) — rejected for now: the applied accent would differ from the picked hex,
  which is its own surprise, and it adds luminance math + a token flip. Accepted as a
  possible future addition; today it is garbage-in-garbage-out.

## Consequences

- The backend can never reject "an ugly colour" or enforce that two profiles look
  distinct — those are frontend-only affordances (the form still hides a preset another
  profile already holds, but a custom pick can reproduce any colour). Accepted: it is a
  nudge, not a constraint.
- The applied theme is a pure function of the stored hex: a hex equal to a preset's
  `--p-500` renders that preset's hand-tuned ramp; any other hex gets a ramp derived in
  JS. Nothing about *how* the colour was chosen is stored.
- "Palette" now means two different things by layer — the frontend's preset catalogue +
  derived ramp (kept), versus the domain concept, which is **Accent** (one colour). The
  glossary records this; the wire/storage field is `accent`.
- No migration path: this lands with a fresh install. Pre-existing registries storing a
  palette *name* are not read back (there is no name→hex compatibility shim).
