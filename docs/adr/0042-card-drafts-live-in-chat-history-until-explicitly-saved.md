---
status: accepted
date: 2026-10-05
---

# Card drafts live in Chat history until explicitly saved

Issue #88 supports generating and revising an Ephemeral Card in the same Turn through
a separate bundled `card-author` MemorySkill. Persist its validated definition and
expanded A2UI surface in the Chat's event log, so rendering, iteration and saving survive
restart without changing the Turn's held Card catalog (ADR 0041).

Each Chat can contain several Card drafts, each with a stable identity and one current
version; revisions append to history and supersede only the previous version of that
draft. Only current versions offer Save, which writes the definition as a Profile Card
after an explicit user request; current instance values remain in Chat history.
Restoring an older definition through conversation appends a new current version of
the same draft. Repeated saving under an occupied name uses the ordinary explicit
Replace / Save as copy choice, including when that draft was saved previously.

This amends ADR 0036's file requirement for experimental definitions and ADR 0038's
single A2UI Skill boundary: `card-author` and `rich-views` have independent switches,
while Cards share vocabulary, validation, rendering and action semantics. Saving a
definition makes it available to future Turns through the existing Profile layer.

We considered registering each experiment immediately and storing draft files in a
separate directory. Keeping drafts in the event log preserves the running catalog,
retains the exact view shown to the user and avoids orphaned draft files or a second
source of truth; repeated revision payloads grow with the Chat's history instead.
