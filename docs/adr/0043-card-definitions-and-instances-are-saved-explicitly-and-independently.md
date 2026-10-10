---
status: accepted
date: 2026-10-06
---

# Card definitions and instances are saved explicitly and independently

Saving an Ephemeral Card's definition creates a reusable Profile Card offered through
the `rich-views` Skill's Card catalog. Saving a concrete Card instance creates an
independent copy of its rendered message in Files, where opening it shows a preview
even after its originating Chat is deleted. Both actions require an explicit user request and
are independent: saving a definition does not save its concrete instance to Files,
and saving an instance does not publish its definition to the catalog.

Any rendered Card instance can be saved, including Bundled, Global, Profile and
Ephemeral Cards in historical messages. An older draft version can be saved as an
instance copy; ADR 0042's current-version restriction applies to saving its definition
for reuse in the catalog.

Rendering resolves a Card definition into a self-contained message: expanded A2UI
layout and data, without a rendering dependency on the reusable definition. Saving
an instance writes a copy of that message to disk; no original field schema or Card
definition is required. Later edits to or deletion of the reusable Card do not change
the saved instance's appearance. The instance's values remain editable; editing its
file changes the values shown in its preview.

The saved copy has its own identity. Editing or deleting it does not change the
Card in the originating Chat's history, and subsequent changes in that Chat do not
automatically update the file. The file and the historical message are separate objects.
Repeated saving creates a new instance file; an existing copy is never silently overwritten.
Saving uses a Save as dialog with a proposed available filename; the user can change
the name and destination directory in the Profile's Files space and explicitly confirm.

For the initial delivery, FilesTree opens the copy through the shared Card renderer
in view-only mode, with inactive Card actions and inputs. Source-file editing remains
available through the ordinary Files editor; opening the preview starts no agent Turn.

Rendering alone keeps the instance in Chat history without creating an instance file.
This extends ADR 0042 with explicit instance copies while retaining the original
Chat history. It replaces issue #86's proposal for automatically created files shared
between Chat and Files, and for saved instances redrawn with later catalog layouts.
Explicit saving lets the user retain a reusable definition, a particular object,
or both, without filling Files or the catalog with every experiment.
