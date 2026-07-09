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

from ag2 import tool
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
    if t.scheduled_for:
        rep = f", repeats {t.recurrence}" if t.recurrence else " (one-off)"
        lines.append(f"Scheduled for: {t.scheduled_for}{rep}")
    if t.objective:
        lines.append(f"Objective: {t.objective}")
    delivs = t.deliverables or []
    if delivs:
        lines.append("Deliverables:")
        lines += [f"  - [{d.get('id')}] {d.get('description')} [{d.get('status')}]" for d in delivs]
    kids = await store.children(task_id)
    if kids:
        lines.append("Subtasks:")
        lines += [f"  - {c.title} [{c.status}]" for c in kids]
    return "\n".join(lines)


def _is_scheduled(t) -> bool:
    """A scheduled task (template) — its plan changes apply to future runs; it must
    NOT be executed on edit, only when the scheduler fires it."""
    from assistant.tasks.model import TaskStatus

    return t.status == TaskStatus.SCHEDULED or bool(t.scheduled_for and not t.is_terminal)


async def resume_task(store, manager, task_id: str) -> None:
    """Re-open a settled task and hand it back to the runner after an edit —
    EXCEPT a scheduled task, which keeps running on its schedule, not on edit."""
    t = await store.get(task_id)
    if t is None or _is_scheduled(t):
        return
    if t.is_terminal:
        await store.reopen(task_id)
    await manager.submit(task_id, asker=_BlockingAsker())


async def do_add_subtask(store, manager, task_id, title, description="", capabilities="web") -> str:
    t = await store.get(task_id)
    if t is None:
        return "Task not found."
    scheduled = _is_scheduled(t)
    caps = [c.strip() for c in (capabilities or "").split(",") if c.strip()] or ["web"]
    child = await store.add_subtask(
        task_id,
        title,
        description,
        reopen_parent=not scheduled,
        capabilities=caps,
    )
    await store.add_deliverable(child.id, description or f"Output of: {title}")
    await resume_task(store, manager, task_id)  # no-op for scheduled tasks
    when = " on its next scheduled run" if scheduled else " now"
    return f"Added subtask '{title}' (capabilities: {', '.join(caps)}). It will run{when}."


async def do_set_objective(store, task_id, objective) -> str:
    if await store.get(task_id) is None:
        return "Task not found."
    await store.update(task_id, objective=objective)
    return "Updated the objective."


async def do_add_deliverable(store, manager, task_id, description, criteria="") -> str:
    if await store.get(task_id) is None:
        return "Task not found."
    await store.add_deliverable(task_id, description, criteria)
    await resume_task(store, manager, task_id)  # no-op for scheduled tasks
    return f"Added deliverable: {description}."


async def do_remove_deliverable(store, manager, task_id, deliverable_id) -> str:
    t = await store.get(task_id)
    if t is None:
        return "Task not found."
    before = len(t.deliverables or [])
    await store.remove_deliverable(task_id, deliverable_id)
    after = await store.get(task_id)
    if after is not None and len(after.deliverables or []) == before:
        return f"No deliverable with id '{deliverable_id}'."
    await resume_task(store, manager, task_id)  # no-op for scheduled tasks
    return f"Removed deliverable {deliverable_id}."


async def do_set_deliverables(store, manager, task_id, descriptions) -> str:
    if await store.get(task_id) is None:
        return "Task not found."
    descs = [d.strip() for d in (descriptions or []) if d and d.strip()]
    if not descs:
        return "Give at least one deliverable description."
    await store.set_deliverables(task_id, descs)
    await resume_task(store, manager, task_id)  # no-op for scheduled tasks
    return f"Set {len(descs)} deliverable(s): " + "; ".join(descs) + "."


async def do_reschedule(store, task_id, when="", recurrence="") -> str:
    """Change a task's run time and/or repeat; (re-)arms it as SCHEDULED."""
    from datetime import datetime

    from assistant.tasks.model import TaskStatus
    from assistant.tasks.scheduling import describe_cron, next_occurrence, normalize_cron

    t = await store.get(task_id)
    if t is None:
        return "Task not found."
    fields: dict = {}
    if when:
        fields["scheduled_for"] = when  # explicit time wins (one-off override of the cron)
    rec = (recurrence or "").strip().lower()
    if rec in ("off", "none", "stop", "no", "once", "one-off"):
        fields["recurrence"] = None
    elif rec:
        if normalize_cron(rec) is None:
            return (
                f"I don't understand the repeat '{recurrence}'. Use standard 5-field "
                "cron, e.g. '0 9 * * *' (daily 09:00) or '0 4-14 * * 1-5' (hourly "
                "04:00–14:00 weekdays), or @hourly/@daily/@weekly."
            )
        fields["recurrence"] = rec
        if not when:  # repeat changed without a time → re-arm onto the new cron
            fields["scheduled_for"] = next_occurrence(rec, datetime.now().astimezone()).isoformat()
    if not fields:
        return "Tell me the new time and/or how it should repeat."
    if not (fields.get("scheduled_for") or t.scheduled_for):
        return "Give me a time to run it (there's no current scheduled time)."
    fields["status"] = TaskStatus.SCHEDULED
    await store.update(task_id, **fields)
    cur = await store.get(task_id)
    desc = describe_cron(cur.recurrence)
    rep = f"repeats {cur.recurrence} ({desc})" if cur.recurrence else "one-off"
    return f"Rescheduled: next run {cur.scheduled_for} · {rep}."


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
            str,
            Field(
                description="Comma-separated tool groups it needs: web, code, files, calendar, drive."
            ),
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
    async def remove_deliverable(
        deliverable_id: Annotated[
            str, Field(description="The deliverable id to remove (see task_status for ids).")
        ],
    ) -> str:
        """Remove ONE deliverable from this task by id. Call task_status first to see the ids."""
        return await do_remove_deliverable(store, manager, task_id, deliverable_id)

    @tool
    async def set_deliverables(
        descriptions: Annotated[
            list[str],
            Field(
                description="The full new set of deliverables — this REPLACES all current "
                "ones. One short description per item."
            ),
        ],
    ) -> str:
        """Replace this task's deliverables with a fresh set. Use this when RELAXING or
        re-scoping (e.g. 'just one serene landscape, any style') so stale requirements
        don't pile up and cause duplicate outputs — prefer it over add_deliverable for changes."""
        return await do_set_deliverables(store, manager, task_id, descriptions)

    @tool
    async def reschedule(
        when: Annotated[
            str,
            Field(
                description="New next-run time as an ISO 8601 datetime (compute "
                "from your environment's current date/time). Empty = keep current time."
            ),
        ] = "",
        recurrence: Annotated[
            str,
            Field(
                description="New repeat as standard 5-field cron (minute hour "
                "day-of-month month day-of-week), e.g. '0 9 * * *' = daily 09:00, "
                "'0 4-14 * * 1-5' = hourly 04:00–14:00 weekdays; or @hourly/@daily/"
                "@weekly; or 'off' to stop repeating. Empty = keep current."
            ),
        ] = "",
    ) -> str:
        """Change WHEN this task runs and/or how it repeats — e.g. 'make it weekly',
        'move it to 8am tomorrow', 'stop repeating'."""
        return await do_reschedule(store, task_id, when, recurrence)

    @tool
    async def cancel(
        subtask: Annotated[
            str, Field(description="Partial title of a subtask to cancel; empty = the whole task.")
        ] = "",
    ) -> str:
        """Cancel this task, or one subtask by (partial) title. Cancels immediately."""
        return await do_cancel(store, manager, task_id, subtask)

    return [
        task_status,
        add_subtask,
        set_objective,
        add_deliverable,
        remove_deliverable,
        set_deliverables,
        reschedule,
        cancel,
    ]
