"""AG2 Assistant application events that ride the AG2 stream.

These are ordinary AG2 events (``BaseEvent`` subclasses), so they serialize,
persist (via ``EventLogWriter``), replay, and stream over the wire with exactly
the same `{type, data}` shape as every native event — no parallel model. The GUI
renders them by type alongside AG2's own events.

We only define what AG2 main doesn't already cover. Conversation, tool, voice,
HITL (`HumanInputRequest`), and task-lifecycle (`TaskStarted`/`TaskCompleted`/…)
events are AG2 native and reused as-is. These add the AG2 Assistant-specific signals:
a task was spawned from a chat, a deliverable was produced, a task was scheduled,
and the durable-inquiry lifecycle.
"""

from ag2.events import BaseEvent, Field


class AssistantEvent(BaseEvent):
    """Marker base for AG2 Assistant app-level events (no fields of its own)."""


class TaskCreated(AssistantEvent):
    """A background task was spawned (e.g. from a chat) — renders as a task card."""

    task_id: str = Field(kw_only=False)  # type: ignore[assignment]
    title: str = ""
    kind: str = "task"  # "task" | "scheduled"


class TaskScheduled(AssistantEvent):
    """A task was scheduled (or rescheduled) to run later — renders as a schedule note."""

    task_id: str = Field(kw_only=False)  # type: ignore[assignment]
    scheduled_for: str = ""  # ISO 8601
    recurrence: str = ""  # 5-field cron; "" for one-off
    recurrence_desc: str = ""  # human-readable cron description ("" for one-off)


class DeliverableProduced(AssistantEvent):
    """A task produced a deliverable — renders as a deliverable item with a preview."""

    task_id: str = Field(kw_only=False)  # type: ignore[assignment]
    deliverable_id: str = ""
    description: str = ""
    preview: str = ""  # short preview; full asset fetched via REST
    path: str = ""  # workspace-relative file the deliverable was saved as ("" if none)


class Attachment(AssistantEvent):
    """A file the user attached to a chat turn, saved to the workspace — renders in
    the thread as a thumbnail (images) or a file chip. `path` is workspace-relative."""

    path: str = Field(kw_only=False)  # type: ignore[assignment]
    name: str = ""
    media_type: str = ""


class ImageGenerated(AssistantEvent):
    """An image was generated/edited and saved to the workspace — renders inline as
    a clickable thumbnail. `path` is workspace-relative (served via /api/files/raw)."""

    path: str = Field(kw_only=False)  # type: ignore[assignment]
    prompt: str = ""
    media_type: str = "image/png"


class A2UISurface(AssistantEvent):
    """A declarative A2UI surface for the web client to render inline.

    This is the assistant app's stable envelope around upstream A2UI messages:
    once ``autogen.beta.a2ui`` is available locally, validated A2UI message
    events can be projected into this shape without changing the Svelte renderer.
    """

    surface_id: str = Field(kw_only=False)  # type: ignore[assignment]
    catalog_id: str = "https://ag2.ai/assistant/a2ui/catalog.json"
    version: str = "v1.0"
    component: dict = Field(default_factory=dict)  # type: ignore[assignment]
    data: dict = Field(default_factory=dict)  # type: ignore[assignment]
    title: str = ""
    intent: str = ""


class A2UISurfaceDataUpdated(AssistantEvent):
    """Durable data-model snapshot emitted after an A2UI server action."""

    surface_id: str = Field(kw_only=False)  # type: ignore[assignment]
    data: dict = Field(default_factory=dict)  # type: ignore[assignment]


class A2UIActionSubmitted(AssistantEvent):
    """A user action was accepted by the backend and is being handled."""

    surface_id: str = Field(kw_only=False)  # type: ignore[assignment]
    action_name: str = ""


class SubagentTrace(AssistantEvent):
    """One inner event from a subagent's own run, forwarded onto the parent task
    stream so the GUI nests it under the subagent card — live, persistent (rides
    the task event log), and recursive: a nested subagent's events arrive as
    further SubagentTraces and nest one level deeper.

    `subagent_id` matches the subagent's TaskStarted/TaskCompleted `task_id` (the
    card key); `inner` is the wrapped event in wire shape ``{type, data}``.
    """

    subagent_id: str = Field(kw_only=False)  # type: ignore[assignment]
    inner: dict = Field(default_factory=dict)  # type: ignore[assignment]


class InquiryRaised(AssistantEvent):
    """A durable HITL question/permission was raised (backed by InquiryStore).

    Mirrors AG2's transient ``HumanInputRequest`` but is durable and task-scoped,
    so it survives restarts and is answerable from any surface.
    """

    inquiry_id: str = Field(kw_only=False)  # type: ignore[assignment]
    task_id: str = ""
    question: str = ""
    detail: str = ""  # secondary context, e.g. the exact code/command to be run
    options: list[str] = Field(default_factory=list)  # type: ignore[assignment]
    kind: str = "question"  # "question" | "permission"


class InquiryAnswered(AssistantEvent):
    """A durable inquiry reached a terminal state — resolves the matching HITL item.

    Emitted for every resolution, not just a real answer: ``status`` is one of
    "answered" | "expired" (timed out unanswered) | "cancelled" (owning task ended)
    so the GUI can retire a stranded permission/question card and say *how* it
    resolved instead of leaving live-looking buttons that do nothing. ``answer`` is
    the chosen text for "answered", empty otherwise.
    """

    inquiry_id: str = Field(kw_only=False)  # type: ignore[assignment]
    answer: str = ""
    status: str = "answered"


class FeedbackGiven(AssistantEvent):
    """The user reacted to a generated item (👍/👎) with a mandatory reason. Rides the
    stream so the thumb state persists + replays (and shows in the AG2 inspector); a
    learner agent distils it into the memory profile.

    `target_kind` is "message" | "image" | "deliverable"; `target_id` is that kind's
    stable key (message → the reply's `created_at`; image → workspace path; deliverable
    → deliverable_id). `content` is an excerpt of what was rated and `request` the user's
    intent that produced it — both passed to the learner so it generalises correctly.
    """

    target_id: str = Field(kw_only=False)  # type: ignore[assignment]
    target_kind: str = "message"  # "message" | "image" | "deliverable"
    sentiment: str = "up"  # "up" | "down"
    reason: str = ""
    content: str = ""  # excerpt of the rated output
    request: str = ""  # the user's ask / task objective that produced it


class FeedbackCleared(AssistantEvent):
    """The user retracted a rating (toggled the 👍/👎 off). Rides the stream so the
    cleared state persists + replays — the GUI projects the thumb back to neutral.

    Carries no sentiment or reason: unmarking takes back only the *visible* rating.
    Any preference the learner already distilled into the memory profile is left
    untouched (editable directly in Settings → Memory), so this event never runs the
    learner. `target_kind`/`target_id` match the original FeedbackGiven's key.
    """

    target_id: str = Field(kw_only=False)  # type: ignore[assignment]
    target_kind: str = "message"  # "message" | "image" | "deliverable"


class TurnCancelled(AssistantEvent):
    """The user stopped a turn while it was running. Emitted after AG2 cancels the
    run, so it lands after whatever the turn had already produced (tool calls and
    their results stay in the history) — the thread shows where the work stopped,
    and replays that way."""

    chat_id: str = Field(kw_only=False)  # type: ignore[assignment]
    reason: str = "Stopped"


class TurnFailed(AssistantEvent):
    """A turn ended in an error (timeout, provider fault, connection drop) rather
    than a reply. Emitted before the turn's history is persisted, so it lands after
    whatever the turn had already produced — the thread keeps that work and says why
    it stopped instead of simply ending mid-air.

    `error` is a short, user-facing sentence; the traceback stays in the debug record
    written by ``capture_failure``, never in the chat."""

    chat_id: str = Field(kw_only=False)  # type: ignore[assignment]
    error: str = ""
