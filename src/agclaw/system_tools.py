"""System tools — let the universal agent know and do everything in AGClaw.

These are retrieval + action tools over the whole system (tasks, chats, durable
HITL questions), so the one agent you talk to anywhere can answer "what tasks do
I have?" or "what did we discuss in that chat?" without holding all that context,
and can act — create/schedule/edit/cancel/archive/run tasks, answer questions.

They wrap the existing `TaskService` (tasks + inquiries) and, optionally, a chats
provider (the gateway, for conversation history). Each tool returns a concise
string the agent reads — never a giant blob — which is the point of retrieval.
"""

from typing import Annotated

from autogen.beta import tool
from pydantic import Field

_PREVIEW = 240  # chars of a produced asset to surface in a summary


def _note_started(task_id: str, title: str) -> None:
    """Record a just-created task so the surface (web chat) can show a task card."""
    from agclaw.agent import started_tasks_var

    lst = started_tasks_var.get()
    if lst is not None:
        lst.append({"id": task_id, "title": title})


def _fmt_schedule(t: dict) -> str:
    if not t.get("scheduled_for"):
        return ""
    rep = f"repeats {t['recurrence']}" if t.get("recurrence") else "one-off"
    return f" · ⏰ {t['scheduled_for']} ({rep})"


def _fmt_node(n: dict, depth: int = 0) -> str:
    pad = "  " * depth
    lines = [f"{pad}{n['title']} — {n['status']}{_fmt_schedule(n)}"]
    if depth == 0 and n.get("objective"):
        lines.append(f"{pad}objective: {n['objective']}")
    for d in n.get("deliverables", []):
        lines.append(f"{pad}  • {d['description']} [{d['status']}]")
        asset = d.get("asset")
        if asset:
            preview = asset[:_PREVIEW].replace("\n", " ")
            lines.append(f"{pad}    → {preview}{'…' if len(asset) > _PREVIEW else ''}")
    prog = n.get("progress") or []
    if prog:
        lines.append(f"{pad}  progress: {prog[-1].get('message', '')}")
    if n.get("error"):
        lines.append(f"{pad}  error: {n['error']}")
    for c in n.get("children", []):
        lines.append(_fmt_node(c, depth + 1))
    return "\n".join(lines)


def format_task(node: dict) -> str:
    """A concise, readable summary of a task node (id, status, schedule, deliverables
    with output previews, subtasks, progress) — used for surface context."""
    return _fmt_node(node)


def build_system_tools(tasks, chats=None) -> list:
    """Build the system toolkit. `tasks` is a TaskService; `chats` (optional) is a
    provider with `list_sessions()` and `transcript(session_id)` (the gateway)."""

    # ---- tasks: retrieval ----
    @tool
    async def list_tasks(
        status: Annotated[str, Field(description="Filter: active, completed, stopped, archived, or empty for all current.")] = "",
    ) -> str:
        """List tasks (id · status · title · schedule). Use to find a task to act on."""
        items = await tasks.list_all(status or None)
        if not items:
            return "No tasks."
        return "\n".join(f"{t['id']} · {t['status']} · {t['title']}{_fmt_schedule(t)}" for t in items)

    @tool
    async def get_task(
        task_id: Annotated[str, Field(description="The task id.")],
    ) -> str:
        """Full detail of one task: objective, schedule, deliverables (with output
        previews), subtasks, progress. Use before acting on or reporting a task."""
        node = await tasks.get_task(task_id)
        return _fmt_node(node) if node else "Task not found."

    # ---- tasks: actions ----
    @tool
    async def create_task(
        request: Annotated[str, Field(description="The full job to carry out as a background task.")],
    ) -> str:
        """Start a background task (it clarifies if needed, then runs). For
        substantial/multi-step work — not quick answers you can give now."""
        tid = await tasks.submit_request(request)
        _note_started(tid, request)  # surfaces a task card in the chat
        return f"Created task {tid}. It will ask any clarifying questions, then run."

    @tool
    async def schedule_task(
        request: Annotated[str, Field(description="The job to run when due.")],
        when: Annotated[str, Field(description="First run as ISO 8601 datetime (from your env clock).")],
        recurrence: Annotated[str, Field(description="Repeat: daily/hourly/weekly, 'every N units', 'weekdays', 'weekends', 'mon,wed,fri', or empty.")] = "",
    ) -> str:
        """Schedule a task to run later, optionally recurring."""
        tid = await tasks.schedule_task(request, when, recurrence or None)
        _note_started(tid, request)  # surfaces a task card in the chat
        return f"Scheduled task {tid} for {when}{' (' + recurrence + ')' if recurrence else ''}."

    @tool
    async def reschedule_task(
        task_id: Annotated[str, Field(description="The task id.")],
        when: Annotated[str, Field(description="New ISO datetime, or empty to keep.")] = "",
        recurrence: Annotated[str, Field(description="New repeat (see schedule_task), 'off' to stop, or empty to keep.")] = "",
    ) -> str:
        """Change when a task runs and/or how it repeats."""
        return await tasks.reschedule(task_id, when, recurrence)

    @tool
    async def add_subtask(
        task_id: Annotated[str, Field(description="Parent task id.")],
        title: Annotated[str, Field(description="Subtask title.")],
        description: Annotated[str, Field(description="What it should do.")] = "",
        capabilities: Annotated[str, Field(description="Comma-separated: web, code, files, calendar, drive.")] = "web",
    ) -> str:
        """Add a subtask. (For a scheduled task this updates the plan; it runs on schedule.)"""
        return await tasks.add_subtask(task_id, title, description, capabilities)

    @tool
    async def add_deliverable(
        task_id: Annotated[str, Field(description="The task id.")],
        description: Annotated[str, Field(description="The output to add.")],
        criteria: Annotated[str, Field(description="How to tell it's acceptable.")] = "",
    ) -> str:
        """Add a required output (deliverable) to a task."""
        return await tasks.add_deliverable(task_id, description, criteria)

    @tool
    async def set_task_objective(
        task_id: Annotated[str, Field(description="The task id.")],
        objective: Annotated[str, Field(description="The revised objective.")],
    ) -> str:
        """Update what a task is trying to achieve."""
        return await tasks.set_objective(task_id, objective)

    @tool
    async def cancel_task(
        task_id: Annotated[str, Field(description="The task id.")],
        subtask: Annotated[str, Field(description="Partial title of a subtask to cancel; empty = whole task.")] = "",
    ) -> str:
        """Cancel a task (or one subtask). Stops it; for recurring tasks it stops firing."""
        return await tasks.cancel_target(task_id, subtask)

    @tool
    async def archive_task(
        task_id: Annotated[str, Field(description="The task id.")],
    ) -> str:
        """Archive a FINISHED task (hide it). Active tasks can't be archived — cancel them instead."""
        ok, reason = await tasks.set_archived(task_id, True)
        if ok:
            return "Archived."
        return "Can't archive an active task — cancel it first." if reason == "active" else "Task not found."

    @tool
    async def run_task_now(
        task_id: Annotated[str, Field(description="The task id.")],
    ) -> str:
        """Run a task now — a scheduled task runs an occurrence immediately, keeping its schedule."""
        return await tasks.run_now(task_id)

    # ---- durable HITL questions ----
    @tool
    async def list_open_questions() -> str:
        """List questions/approvals awaiting a human answer (id · task · text)."""
        pend = await tasks.pending_inquiries()
        if not pend:
            return "No open questions."
        return "\n".join(
            f"{p['id']} · task {p['task_id']} · {p['text']}"
            + (f"  options: {p['options']}" if p.get("options") else "")
            for p in pend
        )

    @tool
    async def answer_question(
        question_id: Annotated[str, Field(description="The question (inquiry) id.")],
        answer: Annotated[str, Field(description="The answer to give.")],
    ) -> str:
        """Answer an open question/approval on the user's behalf."""
        ok = await tasks.answer_inquiry(question_id, answer)
        return "Answered." if ok else "No such open question."

    out = [
        list_tasks, get_task, create_task, schedule_task, reschedule_task,
        add_subtask, add_deliverable, set_task_objective, cancel_task,
        archive_task, run_task_now, list_open_questions, answer_question,
    ]

    # ---- chats (optional) ----
    if chats is not None:
        @tool
        async def list_chats() -> str:
            """List past conversations (session id · last update · preview)."""
            sess = await chats.list_sessions()
            if not sess:
                return "No conversations."
            return "\n".join(
                f"{s['session_id']} · {s.get('turns', 0)} turns · {s.get('preview', '')}" for s in sess
            )

        @tool
        async def read_chat(
            session_id: Annotated[str, Field(description="The conversation/session id.")],
        ) -> str:
            """Read a past conversation's transcript (most recent turns)."""
            msgs = await chats.transcript(session_id)
            if not msgs:
                return "No such conversation (or it's empty)."
            tail = msgs[-20:]
            return "\n".join(f"{m['role']}: {m['text']}" for m in tail)

        out += [list_chats, read_chat]

    return out
