"""The Task primitive — a tracked, nestable unit of work.

Mirrors AG2's task lifecycle (created/running/completed/failed/cancelled) and adds
the states AG2 Assistant needs for its user-facing flow (scheduled, awaiting_input,
planning). Serialises to/from a plain dict for JSON persistence.
"""

import uuid
from dataclasses import dataclass, field


class DeliverableStatus:
    """Lifecycle of a single deliverable (a concrete promised output)."""

    PENDING = "pending"  # not produced yet
    PRODUCED = "produced"  # the agent produced it (asset attached) — awaiting check
    ACCEPTED = "accepted"  # verified against criteria (auto) or signed off (user)
    REJECTED = "rejected"  # failed criteria / user asked for rework


@dataclass
class Deliverable:
    """A concrete expected output with its own acceptance criteria + state.

    Stored on a Task as a plain dict (see `Task.deliverables`); this class is a
    typed helper for creating/serialising them.
    """

    id: str
    description: str
    criteria: str = ""  # definition of done for THIS deliverable
    status: str = DeliverableStatus.PENDING
    asset: dict | None = None  # the produced artifact {name, path, kind}
    notes: str = ""

    @staticmethod
    def new(description: str, criteria: str = "") -> dict:
        return Deliverable(
            id="dlv-" + uuid.uuid4().hex[:8], description=description, criteria=criteria
        ).__dict__


class TaskStatus:
    """Task lifecycle states (string constants for easy JSON/round-trip)."""

    PENDING = "pending"  # created, not yet started
    SCHEDULED = "scheduled"  # waiting for a scheduled time
    AWAITING_INPUT = "awaiting_input"  # paused on a HITL intake/permission prompt
    PLANNING = "planning"  # forming the plan / subtasks
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"

    TERMINAL = frozenset({COMPLETED, FAILED, CANCELLED})
    ACTIVE = frozenset({PENDING, SCHEDULED, AWAITING_INPUT, PLANNING, RUNNING})
    ALL = frozenset(ACTIVE | TERMINAL)


@dataclass
class Task:
    """A unit of work. Roots have `parent_id is None`; subtasks reference a parent."""

    id: str
    title: str
    description: str = ""  # the raw request
    objective: str = ""  # definition of done — what success looks like
    status: str = TaskStatus.PENDING
    parent_id: str | None = None

    # deliverables = concrete promised outputs that gate completion (list of
    # Deliverable dicts). auto_accept=True → a PRODUCED deliverable counts as done
    # once it meets criteria; False → needs explicit user acceptance (HITL).
    deliverables: list[dict] = field(default_factory=list)
    auto_accept: bool = True

    created_at: str = ""
    started_at: str | None = None
    ended_at: str | None = None

    # scheduling
    scheduled_for: str | None = None  # one-shot ISO datetime
    recurrence: str | None = None  # e.g. "daily@09:00" (later phase)

    # execution detail
    progress: list[dict] = field(default_factory=list)  # {at, message, pct?}
    result: str | None = None
    error: str | None = None
    plan: list[str] = field(default_factory=list)
    intake: dict = field(default_factory=dict)  # clarifying Q&A
    capability: str | None = None  # tag for recall
    capabilities: list[str] = field(default_factory=list)  # tool groups this task may use
    assets: list[dict] = field(default_factory=list)  # {name, path, kind}

    # origin / routing
    origin_channel: str | None = None
    origin_chat: str | None = None
    hitl_channel: str | None = None  # where to ask (override; default=origin)

    stream_id: str | None = None  # per-task event-log id
    run_of: str | None = None  # template id, set on a recurring task's per-occurrence run
    seen_at: str | None = None  # when the user opened this task/run (drives unread highlight)

    # run-history (see docs/task-run-history-plan.md): `summary` is this run's own
    # distilled digest of what it delivered (an enrichment cache; the durable record
    # stays the deliverables). `history_runs` optionally overrides the config default
    # for how many prior runs of this template feed the next run's context.
    summary: str = ""
    history_runs: int | None = None

    @property
    def is_terminal(self) -> bool:
        return self.status in TaskStatus.TERMINAL

    def deliverable_done(self, d: dict) -> bool:
        """A deliverable counts as done when accepted (or produced + auto_accept)."""
        st = d.get("status")
        if st == DeliverableStatus.ACCEPTED:
            return True
        return st == DeliverableStatus.PRODUCED and self.auto_accept

    def deliverables_satisfied(self) -> bool:
        """True if every deliverable is done (vacuously true if none defined)."""
        return all(self.deliverable_done(d) for d in self.deliverables)

    def pending_deliverables(self) -> list[dict]:
        return [d for d in self.deliverables if not self.deliverable_done(d)]

    def to_dict(self) -> dict:
        return dict(self.__dict__)

    @classmethod
    def from_dict(cls, data: dict) -> "Task":
        # ignore unknown keys so the store survives schema growth
        known = {k: data[k] for k in cls.__dataclass_fields__ if k in data}
        return cls(**known)
