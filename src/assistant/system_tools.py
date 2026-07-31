"""System tools — let the universal agent know and do everything in AG2 Assistant.

These are retrieval + action tools over the whole system (tasks, chats, durable
HITL questions), so the one agent you talk to anywhere can answer "what tasks do
I have?" or "what did we discuss in that chat?" without holding all that context,
and can act — create/update/run/delete tasks, answer questions, manage voice.

They wrap the existing `TaskService` (tasks + inquiries) and, optionally, a chats
provider (the gateway, for conversation history). Each tool returns a concise
string the agent reads — never a giant blob — which is the point of retrieval.
"""

from typing import Annotated

from ag2 import Context, tool
from pydantic import Field

from assistant.peers import peer_for_chat

_CHAT_TAIL_TURNS = 20  # turns of a past conversation read_chat returns


def _followup_note(platform: str) -> str:
    """On a non-GUI channel (Telegram/Discord/Slack/multi), tell the user where any
    follow-up clarifying questions will appear — task HITL is answered in the web app,
    not on the channel. Empty on the web ("gateway"), where questions surface inline."""
    if platform and platform != "gateway":
        return " If I need any clarification to complete it, I'll ask in the AG2 Assistant web app."
    return ""


async def _emit_task_card(context, task_id: str, title: str, kind: str) -> None:
    """Emit a TaskCreated event onto the active chat stream so the event-stream
    client shows a task card. Best-effort."""
    if context is None:
        return
    from assistant.events import TaskCreated

    try:
        await context.send(TaskCreated(task_id, title=title, kind=kind))
    except Exception as exc:
        from assistant.observability import log_suppressed

        log_suppressed("chat task-card event emit", exc, task_id=task_id, kind=kind)


def _origin(context) -> tuple[str | None, str | None]:
    """The Peer the current turn is running for, as (connection_id, platform_chat_id) —
    run outcomes get pushed back through that Connection, whichever Chat the Peer has
    moved to since. (None, None) for a Chat no Peer started (web/CLI/task-run streams)."""
    from assistant.channels.base import PUSH_CHANNELS

    sid = str(getattr(getattr(context, "stream", None), "id", "") or "")
    peer = peer_for_chat(sid) if sid else None
    if peer is not None and peer.connection and peer.platform in PUSH_CHANNELS:
        return peer.connection, peer.chat_id
    return None, None


def _schedule_arg(kind: str, at: str, cron: str) -> dict:
    kind = (kind or "manual").strip().lower()
    return {"kind": kind, "at": (at or "").strip() or None, "cron": (cron or "").strip() or None}


def _task_line(t: dict) -> str:
    state = "paused" if t["paused"] else (t["schedule_desc"] or "manual")
    last = f" · last run: {t['last_run']['status']}" if t.get("last_run") else ""
    return f"{t['id']} · {t['name']} · {state}{last}"


def build_system_tools(tasks, settings, chats=None, platform: str = "gateway") -> list:
    """Build the system toolkit. `tasks` is a TaskService; `settings` is the profile's
    `Settings` (the voice get/set tools read/write it, so they touch only this
    profile); `chats` (optional) is a provider with `list_chats()` and
    `transcript(chat_id)` (the gateway). `platform` is the surface ("gateway" for
    web, else a channel name) — on a channel, task confirmations note that follow-up
    questions are asked in the web app."""
    note = _followup_note(platform)

    # ---- tasks: a task is name + prompt + optional model + schedule ----
    @tool
    async def list_tasks() -> str:
        """List the user's tasks (id · name · schedule · last run)."""
        items = await tasks.list_tasks()
        return "\n".join(_task_line(t) for t in items) if items else "No tasks."

    @tool
    async def get_task(
        task_id: Annotated[str, Field(description="The task id (task_…).")],
    ) -> str:
        """Full detail of one task: prompt, model, schedule, and its runs."""
        t = await tasks.get_task(task_id)
        if t is None:
            return "Task not found."
        lines = [
            f"{t['id']} · {t['name']}" + (" · PAUSED" if t["paused"] else ""),
            f"prompt: {t['prompt']}",
            f"model: {t['model'] or 'profile default'}",
            f"schedule: {t['schedule_desc']}"
            + (f" (next run {t['next_run_at']})" if t["next_run_at"] else ""),
        ]
        if t.get("description"):
            lines.append(f"desc: {t['description']}")
        for r in t["runs"][:10]:
            done = r["ended_at"] or ""
            lines.append(f"  run {r['id']} · {r['status']} · {done} · {r['summary'] or r['error']}")
        return "\n".join(lines)

    @tool
    async def create_task(
        name: Annotated[str, Field(description="Short human name for the task.")],
        prompt: Annotated[str, Field(description="Standing instructions executed on every run.")],
        context: Context,
        model_config_id: Annotated[
            str, Field(description="Optional LLM configuration id; empty = profile default.")
        ] = "",
        schedule_kind: Annotated[
            str,
            Field(description="manual (on demand) | once (single future run) | cron (recurring)."),
        ] = "manual",
        at: Annotated[
            str,
            Field(description="ISO 8601 datetime for schedule_kind='once' (from your env clock)."),
        ] = "",
        cron: Annotated[
            str,
            Field(
                description="5-field cron for schedule_kind='cron', e.g. '0 9 * * *' = daily "
                "09:00, '0 9 * * 1-5' = weekdays 09:00; or @hourly/@daily/@weekly/@monthly."
            ),
        ] = "",
        description: Annotated[
            str, Field(description="Optional task description; empty = none.")
        ] = "",
    ) -> str:
        """Create a task. Ask the user anything unclear BEFORE calling this —
        the prompt is what runs unattended, so it must be self-contained. A task's
        working folders are managed in the task's Folders UI, not through this tool."""
        connection, chat = _origin(context)
        try:
            task = await tasks.create_task(
                name=name,
                prompt=prompt,
                model=model_config_id or None,
                schedule=_schedule_arg(schedule_kind, at, cron),
                origin_channel=connection,
                origin_chat=chat,
                description=description or None,
            )
        except ValueError as exc:
            return str(exc)  # correctable: retry with a valid schedule/model
        await _emit_task_card(context, task["id"], name, "task")
        return f"Created task {task['id']} — {task['schedule_desc']}.{note}"

    @tool
    async def update_task(
        task_id: Annotated[str, Field(description="The task id.")],
        name: Annotated[str, Field(description="New name; empty = keep.")] = "",
        prompt: Annotated[str, Field(description="New prompt; empty = keep.")] = "",
        model_config_id: Annotated[
            str, Field(description="New LLM config id; 'default' = profile default; empty = keep.")
        ] = "",
        schedule_kind: Annotated[
            str, Field(description="manual | once | cron to change the schedule; empty = keep.")
        ] = "",
        at: Annotated[str, Field(description="ISO datetime for once.")] = "",
        cron: Annotated[str, Field(description="5-field cron for cron.")] = "",
        paused: Annotated[
            str, Field(description="'true' to pause, 'false' to resume; empty = keep.")
        ] = "",
        description: Annotated[str, Field(description="New description; empty = keep.")] = "",
    ) -> str:
        """Edit any field of a task. Empty args keep the current value. A task's
        working folders are managed in the task's Folders UI, not through this tool."""
        patch: dict = {}
        if name:
            patch["name"] = name
        if prompt:
            patch["prompt"] = prompt
        if model_config_id:
            patch["model"] = None if model_config_id == "default" else model_config_id
        if schedule_kind:
            patch["schedule"] = _schedule_arg(schedule_kind, at, cron)
        if paused:
            patch["paused"] = paused.strip().lower() == "true"
        if description:
            patch["description"] = description
        if not patch:
            return "Nothing to change — pass at least one field."
        try:
            t = await tasks.update_task(task_id, **patch)
        except ValueError as exc:
            return str(exc)
        if t is None:
            return "Task not found."
        state = "paused" if t["paused"] else t["schedule_desc"]
        return f"Updated '{t['name']}' — {state}."

    @tool
    async def run_task_now(
        task_id: Annotated[str, Field(description="The task id.")],
        context: Context,
    ) -> str:
        """Start a run of the task immediately (its schedule is unchanged)."""
        run = await tasks.start_run(task_id, trigger="manual")
        if run is None:
            return "Task not found."
        await _emit_task_card(context, task_id, "", "task")
        return f"Started run {run.id}."

    @tool
    async def delete_task(
        task_id: Annotated[str, Field(description="The task id.")],
    ) -> str:
        """Permanently delete a task, its runs, and their chats. Irreversible."""
        return "Deleted." if await tasks.delete_task(task_id) else "Task not found."

    # ---- durable HITL questions ----
    @tool
    async def list_open_questions() -> str:
        """List questions/approvals awaiting a human answer (id · task · text)."""
        pend = await tasks.pending_inquiries()
        if not pend:
            return "No open questions."
        return "\n".join(
            f"{p['id']} · task {p['root_id']}"
            + (f" ({p['task_title']})" if p.get("task_title") else "")
            + f" · {p['text']}"
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

    # ---- voice ----
    @tool
    async def list_voices() -> str:
        """List the available realtime voices (name · style) and the current one."""
        cur = settings.get_voice()
        return f"Voice provider: {settings.voice_provider()}\nCurrent voice: {cur}\n" + "\n".join(
            f"{n} — {s}" + (" (current)" if n == cur else "")
            for n, s in settings.voices_for().items()
        )

    @tool
    async def set_voice(
        voice: Annotated[
            str, Field(description="Voice name, e.g. Puck, Kore, Sulafat (see list_voices).")
        ],
    ) -> str:
        """Change the assistant's realtime voice (persists; applies next voice session)."""
        if not settings.set_voice(voice):
            return f"Unknown voice '{voice}'. Use list_voices to see options."
        return f"Voice set to {voice}. It'll apply the next time you start a voice chat."

    out = [
        list_tasks,
        get_task,
        create_task,
        update_task,
        run_task_now,
        delete_task,
        list_open_questions,
        answer_question,
        list_voices,
        set_voice,
    ]

    # ---- chats (optional) ----
    if chats is not None:

        @tool
        async def list_chats() -> str:
            """List past conversations (chat id · last update · preview)."""
            sess = await chats.list_chats()
            if not sess:
                return "No conversations."
            return "\n".join(
                f"{s['chat_id']} · {s.get('turns', 0)} turns · {s.get('preview', '')}" for s in sess
            )

        @tool
        async def read_chat(
            chat_id: Annotated[str, Field(description="The chat id.")],
        ) -> str:
            """Read a past conversation's transcript — the most recent turns of it.

            Long conversations are truncated to the latest turns; the result says so
            when earlier turns were dropped, and those earlier turns cannot be read.
            """
            msgs = await chats.transcript(chat_id)
            if not msgs:
                return "No such conversation (or it's empty)."
            tail = msgs[-_CHAT_TAIL_TURNS:]
            body = "\n".join(f"{m['role']}: {m['text']}" for m in tail)
            if len(msgs) > len(tail):
                dropped = len(msgs) - len(tail)
                return (
                    f"[showing the last {len(tail)} of {len(msgs)} turns — the {dropped} "
                    f"earlier turns are NOT included and cannot be retrieved]\n{body}"
                )
            return body

        out += [list_chats, read_chat]

    return out
