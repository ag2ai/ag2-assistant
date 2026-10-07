---
status: accepted
date: 2026-10-07
---

# Screens compose file references into one surface

Issue #123 adds a Screen: a user-composed page of Card instances outside a Chat.
A `.screen.yaml` file in Profile Files stores a primitive layout and `CardInstance`
references to instance files, rather than copies of their data or catalog names.
Paths are relative to that Profile's Files root. Expansion produces one A2UI surface,
with component ids and absolute bindings namespaced per reference. Relative bindings
remain legal only in repeated templates, using the existing loader's scope validation.

The instance file owns its retained source configuration and fetched values. Several
Screens can reference the same file, and an explicitly requested Chat file reference
can display it too. Source operations target the original Profile/file/source context;
portable instance UUIDs alone cannot identify an object. Automatic refresh shares its
declared interval across consumers; manual refresh remains immediate. Typed source
events project the result to all open consumers without an assistant Turn.

This does not replace ADR 0043's independent Save copies or ADR 0044's independent
ordinary Chat instances. A new `CardInstanceReference` event explicitly names a file
and reads its current state for display. Existing Chat renderings stay snapshots;
copying one to Files does not turn it into a live reference. Missing files produce
an unavailable-reference state, and moving a file requires updating its references.

The Screen Skill creates instance files and Screen layouts through validated,
create-only scripts. Rearrangement uses ordinary Files edits. Viewing, layout edits
and source refresh do not change reusable definitions. Profile isolation, code-version
approval and Secret authorization remain governed by ADRs 0044/0045. Authored state
is durable, while fetched file values retain #122's bounded display-cache behavior.

Screens are a standalone main-pane destination in the Screens tab. They are not a
Thread or a Modal. Inputs and historical actions remain passive; only source controls
are active. Dragging and action write-back are explicitly deferred by issue #123.
