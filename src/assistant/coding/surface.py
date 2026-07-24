"""Project a coding run's state into a durable CodingSession A2UI surface.

The coding CLI does not emit our A2UI catalog, so we synthesize the surface
ourselves from the run's plan and the computed working-tree diff, then emit it as
an :class:`~assistant.events.A2UISurface` (which persists + replays). The Svelte
``CodingSession`` renderer consumes the ``component`` shape built here.
"""

from assistant.a2ui import CATALOG_ID, _component_data
from assistant.coding.diff import FileDiff
from assistant.events import A2UISurface


def _file_dict(f: FileDiff) -> dict:
    return {
        "path": f.path,
        "status": f.status,
        "added": f.added,
        "removed": f.removed,
        "hunks": f.hunks,
    }


def build_surface(
    *,
    surface_id: str,
    agent_label: str,
    directory: str,
    task: str,
    status: str,  # "running" | "done" | "failed"
    files: list[FileDiff],
    plan: list[dict] | None = None,
    summary: str = "",
    error: str = "",
) -> A2UISurface:
    """Build the durable CodingSession surface event for the current run state."""
    root: dict = {
        "id": "root",
        "component": "CodingSession",
        "agent": agent_label,
        "directory": directory,
        "task": task,
        "status": status,
        "plan": list(plan or []),
        "files": [_file_dict(f) for f in files],
    }
    if summary:
        root["summary"] = summary
    if error:
        root["error"] = error

    return A2UISurface(
        surface_id,
        catalog_id=CATALOG_ID,
        version="v1.0",
        component={**root, "_components": [root]},
        data=_component_data(root),
        title="Coding session",
        intent="generated-ui",
    )
