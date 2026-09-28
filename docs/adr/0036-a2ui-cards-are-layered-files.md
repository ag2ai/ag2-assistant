# A2UI Cards are layered files, and the Profile layer lives in the Files space

A **Card** stops being a Python literal in the app's source and becomes a file, discovered
from three layers the way Skills are (ADR 0016): **Bundled** (ships with the app),
**Global** (installed at the Root, shared by every profile), **Profile**. The Profile layer
is the asymmetry worth recording: it does **not** sit beside the profile's Skills, it lives
inside the profile's own **Files** space, so the agent writes a Card with the ordinary file
tools it already has and the user sees it in the Files tree without a new surface.

## Context

Every Card was declared in one 843-line module: ~11 component schemas plus a hand-written
prompt carrying a routing line and a worked example for each. Adding a Card meant editing
that module and shipping a release. A user could not add one at all, and a plugin had
nothing to install into — the catalog was not a place you can put a file.

Skills solved the same problem with a three-layer directory plus an install-wide state
store and per-profile Suppression. Cards mirror that, with one deliberate deviation.

## Decision

- **A Card is one file**, holding its name, its description, the field schema the model
  fills in, the layout, and one worked example. A Card's **identity is the `name` inside
  the file**, never the filename — a user renaming a file in the Files tree must not orphan
  every instance of that Card.
- **Three layers, Skill precedence.** Profile > Global > Bundled, deduped by name.
- **Bundled and Global live in their own directories**; the **Profile layer lives inside
  the profile's Files space.**
- **Place and marker together.** A Card is a file with the Card suffix inside the Card
  directory. Anything else there is not a Card and is passed over in silence; a file that
  claims to be a Card and fails to parse or validate is **skipped with a warning**, and the
  rest of the catalog still loads. One broken file never costs the user their other Cards.
- **Card state is its own document**, `cards.json`, beside `skills.json` — same shape
  (install-wide Disabled + typed per-profile records, default-on), same concurrency
  machinery, extracted into one reusable store rather than copied.
- **The loader is the single resolution seam.** A Disabled or Suppressed Card is absent
  from the catalog the agent is offered, from the detail it can fetch, and from validation.

## Considered options

- **Profile Cards beside Profile Skills, outside the Files space** — the literal mirror of
  ADR 0016. Rejected: the agent authoring a Card would need a bespoke write path, and the
  user would need a new UI surface to see a file they cannot otherwise reach. Putting them
  in the Files space buys both for free.
- **Cards anywhere in the Files space, found by marker alone** — rejected: it forces a walk
  of the whole file tree on every catalog build and leaves the user with no place that
  answers "where are my Cards?".
- **One shared state document for Skills and Cards** — rejected: a cascade purge keyed by
  name would reach across types, and a Skill and a Card may legitimately share a name.
- **Identity from the filename** — rejected: renaming a file in the Files tree is an
  ordinary, encouraged act; it must not silently redefine what a Card is.

## Consequences

- **The same artifact appears in two places in the UI.** Profile Cards are ordinary rows in
  the Files tree; Bundled and Global Cards need a mounted section, as user-managed Skills do.
  This is the price of the deviation and is paid in the Settings work, not in the loader.
- **The Profile layer is user-writable at any moment**, by an editor, by the agent's file
  tools, or by a Settings write — with no API call to hook. Freshness therefore cannot rely
  on explicit invalidation: the catalog is re-resolved from disk, short-circuited by a
  fingerprint over the directory listings and the files' mtime and size.
- **A Card may be deleted by ordinary means.** In the Files space `rm` is `rm`; that is the
  cost of the file space being the user's, and it is symmetric with every other artifact
  living there.
