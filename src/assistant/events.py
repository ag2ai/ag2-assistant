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
    kind: str = "task"          # "task" | "scheduled"


class TaskScheduled(AssistantEvent):
    """A task was scheduled (or rescheduled) to run later — renders as a schedule note."""

    task_id: str = Field(kw_only=False)
    scheduled_for: str = ""     # ISO 8601
    recurrence: str = ""        # "" for one-off


class DeliverableProduced(AssistantEvent):
    """A task produced a deliverable — renders as a deliverable item with a preview."""

    task_id: str = Field(kw_only=False)
    deliverable_id: str = ""
    description: str = ""
    preview: str = ""           # short preview; full asset fetched via REST


class InquiryRaised(AssistantEvent):
    """A durable HITL question/permission was raised (backed by InquiryStore).

    Mirrors AG2's transient ``HumanInputRequest`` but is durable and task-scoped,
    so it survives restarts and is answerable from any surface.
    """

    inquiry_id: str = Field(kw_only=False)
    task_id: str = ""
    question: str = ""
    options: list[str] = Field(default_factory=list)
    kind: str = "question"      # "question" | "permission"


class InquiryAnswered(AssistantEvent):
    """A durable inquiry was answered — resolves the matching HITL item."""

    inquiry_id: str = Field(kw_only=False)
    answer: str = ""
