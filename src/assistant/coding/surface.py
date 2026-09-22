"""Fill the CodingSession Card with one coding run's state.

The coding CLI does not emit our A2UI catalog, so the server fills the Card's
fields itself — the run's plan and the computed working-tree diff — and the
ordinary Card path draws them: ``codingsession.card.yaml`` owns the look, and the
stream bridge expands the instance into primitives like any other Card's.

The first emit is an :class:`~assistant.events.A2UISurface` carrying the instance;
every later one is an :class:`~assistant.events.A2UISurfaceDataUpdated` on the same
surface, so a run in flight redraws without the layout being sent again.
"""

import re
from pathlib import PurePosixPath

from assistant.a2ui import CATALOG_ID, _component_data
from assistant.coding.diff import FileDiff
from assistant.events import A2UISurface, A2UISurfaceDataUpdated

# The Card the run is drawn as; its name is the one inside the Card file.
CARD = "CodingSession"

# The h1 is a display face and a task prompt is often a paragraph, so the headline
# is the first sentence, capped. The rest is the brief, under it.
HEADLINE_LIMIT = 96

_SENTENCE_END = re.compile(r"(?<=[.!?…:])\s+")

# The lines that stand in for an empty plan, an unchanged tree, and a file whose
# change has no text to show.
_WARMING = "Warming up the workshop — the plan and the edits will stream in here."
_UNCHANGED = "No file changes were made."
_NO_PREVIEW = "No preview — a binary or oversized file."


def _headline_and_brief(task: str) -> tuple[str, str]:
    """The run in one line — its first sentence, cut at a word to the display face's
    room — and whatever of the task that line did not say."""
    text = task.strip()
    first = text.split("\n", 1)[0]
    said = _SENTENCE_END.split(first, maxsplit=1)[0] or first
    if len(said) <= HEADLINE_LIMIT:
        return said, text[len(said) :].strip()
    kept = said[: HEADLINE_LIMIT - 1]
    head, _, _ = kept.rpartition(" ")
    said = (head or kept).rstrip()
    return said + "…", text[len(said) :].strip()


def _file_dict(diff: FileDiff, directory: str) -> dict:
    """One changed file: what it is called, what opens it, and what changed in it."""
    entry = {
        "path": diff.path,
        # The folder was approved for the run, so its files are reachable by the
        # absolute path the Files rail resolves a granted Folder's file by.
        "full": str(PurePosixPath(directory) / diff.path),
        "status": diff.status,
        "added": f"+{diff.added}",
        "removed": f"−{diff.removed}",
    }
    if diff.hunks:
        entry["hunks"] = diff.hunks
    else:
        entry["note"] = _NO_PREVIEW
    return entry


def card_fields(
    *,
    agent_label: str,
    directory: str,
    task: str,
    status: str,  # "running" | "done" | "failed"
    files: list[FileDiff],
    plan: list[dict] | None = None,
    summary: str = "",
    error: str = "",
) -> dict:
    """The CodingSession Card's fields for the current run state."""
    headline, brief = _headline_and_brief(task)
    fields: dict = {
        "title": headline,
        "agent": agent_label,
        "directory": directory,
        "status": status,
        "plan": list(plan or []),
        "files": [_file_dict(diff, directory) for diff in files],
    }
    if brief:
        fields["brief"] = brief
    if files:
        fields["changed"] = f"{len(files)} file{'' if len(files) == 1 else 's'} changed"
        fields["added"] = f"+{sum(diff.added for diff in files)}"
        fields["removed"] = f"−{sum(diff.removed for diff in files)}"
    elif status == "done":
        fields["note"] = _UNCHANGED
    elif status == "running" and not fields["plan"]:
        fields["note"] = _WARMING
    if summary:
        fields["summary"] = summary
    if error:
        fields["error"] = error
    return fields


def build_surface(surface_id: str, fields: dict) -> A2UISurface:
    """The run's first emit: the Card instance on a new surface."""
    root = {"id": "root", "component": CARD, **fields}
    return A2UISurface(
        surface_id,
        catalog_id=CATALOG_ID,
        version="v1.0",
        component={**root, "_components": [root]},
        data=_component_data(root),
        title="Coding session",
        intent="generated-ui",
    )


def surface_data(surface_id: str, fields: dict) -> A2UISurfaceDataUpdated:
    """A later emit: the run's state as a data-model snapshot, layout untouched."""
    return A2UISurfaceDataUpdated(surface_id, data=fields)
