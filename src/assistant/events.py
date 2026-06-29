"""AG2 Assistant application events that ride the AG2 stream.

These are ordinary AG2 events (``BaseEvent`` subclasses), so they serialize,
persist (via ``EventLogWriter``), replay, and stream over the wire with exactly
the same `{type, data}` shape as every native event — no parallel model. The GUI
renders them by type alongside AG2's own events (see docs/gui-redesign-plan.md).

We only define what AG2 main doesn't already cover. Conversation, tool, voice,
HITL (`HumanInputRequest`), and task-lifecycle (`TaskStarted`/`TaskCompleted`/…)
events are AG2 native and reused as-is. These add the AG2 Assistant-specific signals:
a task was spawned from a chat, a deliverable was produced, a task was scheduled,
and the durable-inquiry lifecycle.
"""

from autogen.beta.events import BaseEvent, Field


class AssistantEvent(BaseEvent):
    """Marker base for AG2 Assistant app-level events (no fields of its own)."""


class TaskCreated(AssistantEvent):
    """A background task was spawned (e.g. from a chat) — renders as a task card."""

    task_id: str = Field(kw_only=False)
    title: str = ""
    kind: str = "task"  # "task" | "scheduled"


class TaskScheduled(AssistantEvent):
    """A task was scheduled (or rescheduled) to run later — renders as a schedule note."""

    task_id: str = Field(kw_only=False)
    scheduled_for: str = ""  # ISO 8601
    recurrence: str = ""  # "" for one-off


class DeliverableProduced(AssistantEvent):
    """A task produced a deliverable — renders as a deliverable item with a preview."""

    task_id: str = Field(kw_only=False)
    deliverable_id: str = ""
    description: str = ""
    preview: str = ""  # short preview; full asset fetched via REST


class Attachment(AssistantEvent):
    """A file the user attached to a chat turn, saved to the workspace — renders in
    the thread as a thumbnail (images) or a file chip. `path` is workspace-relative."""

    path: str = Field(kw_only=False)
    name: str = ""
    media_type: str = ""


class ImageGenerated(AssistantEvent):
    """An image was generated/edited and saved to the workspace — renders inline as
    a clickable thumbnail. `path` is workspace-relative (served via /api/files/raw)."""

    path: str = Field(kw_only=False)
    prompt: str = ""
    media_type: str = "image/png"


class A2UISurface(AssistantEvent):
    """A declarative A2UI surface for the web client to render inline.

    This is the assistant app's stable envelope around upstream A2UI messages:
    once ``autogen.beta.a2ui`` is available locally, validated A2UI message
    events can be projected into this shape without changing the Svelte renderer.
    """

    surface_id: str = Field(kw_only=False)
    catalog_id: str = "https://ag2.ai/assistant/a2ui/catalog.json"
    version: str = "v1.0"
    component: dict = Field(default_factory=dict)
    data: dict = Field(default_factory=dict)
    title: str = ""
    intent: str = ""


class SubagentTrace(AssistantEvent):
    """One inner event from a subagent's own run, forwarded onto the parent task
    stream so the GUI nests it under the subagent card — live, persistent (rides
    the task event log), and recursive: a nested subagent's events arrive as
    further SubagentTraces and nest one level deeper.

    `subagent_id` matches the subagent's TaskStarted/TaskCompleted `task_id` (the
    card key); `inner` is the wrapped event in wire shape ``{type, data}``.
    """

    subagent_id: str = Field(kw_only=False)
    inner: dict = Field(default_factory=dict)


class InquiryRaised(AssistantEvent):
    """A durable HITL question/permission was raised (backed by InquiryStore).

    Mirrors AG2's transient ``HumanInputRequest`` but is durable and task-scoped,
    so it survives restarts and is answerable from any surface.
    """

    inquiry_id: str = Field(kw_only=False)
    task_id: str = ""
    question: str = ""
    detail: str = ""  # secondary context, e.g. the exact code/command to be run
    options: list[str] = Field(default_factory=list)
    kind: str = "question"  # "question" | "permission"


class InquiryAnswered(AssistantEvent):
    """A durable inquiry was answered — resolves the matching HITL item."""

    inquiry_id: str = Field(kw_only=False)
    answer: str = ""


class FeedbackGiven(AssistantEvent):
    """The user reacted to a generated item (👍/👎) with a mandatory reason. Rides the
    stream so the thumb state persists + replays (and shows in the AG2 inspector); a
    learner agent distils it into the memory profile.

    `target_kind` is "message" | "image" | "deliverable"; `target_id` is that kind's
    stable key (message → the reply's `created_at`; image → workspace path; deliverable
    → deliverable_id). `content` is an excerpt of what was rated and `request` the user's
    intent that produced it — both passed to the learner so it generalises correctly.
    """

    target_id: str = Field(kw_only=False)
    target_kind: str = "message"  # "message" | "image" | "deliverable"
    sentiment: str = "up"  # "up" | "down"
    reason: str = ""
    content: str = ""  # excerpt of the rated output
    request: str = ""  # the user's ask / task objective that produced it
