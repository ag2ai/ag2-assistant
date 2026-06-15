"""Task-control tools — let an agent modify ONE task from a conversation.

These back the per-task chat ("also research SpaceX", "drop the dining subtask",
"make it shorter", "cancel it"). Each tool is bound to a single task_id and edits
the durable store directly; the background runner picks up added work and cascades
cancels, so the chat composes with everything already built. Edits apply
immediately (the user owns the task) and the agent reports what it did.

The behaviour lives in plain async functions (testable without the agent); the
`@tool` wrappers in `build_task_tools` are thin closures over them.
"""

import asyncio
from typing import Annotated

from autogen.beta import tool
from pydantic import Field


class _BlockingAsker:
    """Parking asker for resumed work — HITL resolves out of band via inquiries."""

    async def ask(self, question, timeout=None):
        await asyncio.Event().wait()


async def render_task(store, task_id: str) -> str:
    """A compact text snapshot of a task and its subtree (for the agent's context)."""
    t = await store.get(task_id)
    if t is None:
        return "(task not found)"
    lines = [f"Task: {t.title}", f"Status: {t.status}"]
    if t.objective:
        lines.append(f"Objective: {t.objective}")
    delivs = t.deliverables or []
    if delivs:
        lines.append("Deliverables:")
        lines += [f"  - {d.get('description')} [{d.get('status')}]" for d in delivs]
    kids = await store.children(task_id)
    if kids:
        lines.append("Subtasks:")
        lines += [f"  - {c.title} [{c.status}]" for c in kids]
    return "\n".join(lines)


async def resume_task(store, manager, task_id: str) -> None:
    """Re-open a settled task and hand it back to the runner after an edit."""
    t = await store.get(task_id)
    if t is None:
        return
    if t.is_terminal:
        await store.reopen(task_id)
    await manager.submit(task_id, asker=_BlockingAsker())


async def do_add_subtask(store, manager, task_id, title, description="", capabilities="web") -> str:
    if await store.get(task_id) is None:
        return "Task not found."
    caps = [c.strip() for c in (capabilities or "").split(",") if c.strip()] or ["web"]
    child = await store.add_subtask(
        task_id, title, description, reopen_parent=True, capabilities=caps,
    )
    await store.add_deliverable(child.id, description or f"Output of: {title}")
    await resume_task(store, manager, task_id)
    return f"Added subtask '{title}' (capabilities: {', '.join(caps)}). It will run now."


async def do_set_objective(store, task_id, objective) -> str:
    if await store.get(task_id) is None:
        return "Task not found."
    await store.update(task_id, objective=objective)
    return "Updated the objective."


async def do_add_deliverable(store, manager, task_id, description, criteria="") -> str:
    if await store.get(task_id) is None:
        return "Task not found."
    await store.add_deliverable(task_id, description, criteria)
    await resume_task(store, manager, task_id)
    return f"Added deliverable: {description}."


async def do_cancel(store, manager, task_id, subtask="") -> str:
    if subtask:
        kids = await store.children(task_id)
        match = next((c for c in kids if subtask.lower() in c.title.lower()), None)
        if match is None:
            return f"No subtask matching '{subtask}'."
        await manager.cancel(match.id, reason="cancelled via task chat")
        return f"Cancelled subtask '{match.title}'."
    await manager.cancel(task_id, reason="cancelled via task chat")
    return "Cancelled the task."


def build_task_tools(store, manager, task_id: str) -> list:
    """Build the task-control toolkit bound to `task_id` (thin wrappers over the
    do_* functions above)."""

    @tool
    async def task_status() -> str:
        """Show this task's current objective, deliverables, and subtasks with their status."""
        return await render_task(store, task_id)

    @tool
    async def add_subtask(
        title: Annotated[str, Field(description="Short title of the new subtask.")],
        description: Annotated[str, Field(description="What this subtask should do.")] = "",
        capabilities: Annotated[
            str, Field(description="Comma-separated tool groups it needs: web, code, files, calendar, drive.")
        ] = "web",
    ) -> str:
        """Add a subtask (e.g. 'also research X'). It's scheduled and run automatically."""
        return await do_add_subtask(store, manager, task_id, title, description, capabilities)

    @tool
    async def set_objective(
        objective: Annotated[str, Field(description="The revised objective / definition of done.")],
    ) -> str:
        """Update what this task is trying to achieve."""
        return await do_set_objective(store, task_id, objective)

    @tool
    async def add_deliverable(
        description: Annotated[str, Field(description="The output to add.")],
        criteria: Annotated[str, Field(description="How to tell it's acceptable.")] = "",
    ) -> str:
        """Add a required output (deliverable) to this task."""
        return await do_add_deliverable(store, manager, task_id, description, criteria)

    @tool
    async def cancel(
        subtask: Annotated[
            str, Field(description="Partial title of a subtask to cancel; empty = the whole task.")
        ] = "",
    ) -> str:
        """Cancel this task, or one subtask by (partial) title. Cancels immediately."""
        return await do_cancel(store, manager, task_id, subtask)

    return [task_status, add_subtask, set_objective, add_deliverable, cancel]
