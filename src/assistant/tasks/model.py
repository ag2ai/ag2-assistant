"""Task + Run — the two task-subsystem primitives (Cowork-style).

A Task is standing configuration: name + prompt + optional model + schedule +
paused. A Run is one execution of it; its transcript is an ordinary chat
stream (``task-run:{run_id}`` in chats.db), so a run is a chat you can open,
steer while it works, and keep talking to afterwards. Both serialise to plain
dicts for JSON persistence, mirroring how chats are stored.
"""

from dataclasses import asdict, dataclass, field
from typing import Literal

# What started a run: the scheduler's regular firing, an exhausted one-shot
# slot, or an explicit "Run now" (UI button / agent tool).
RunTrigger = Literal["schedule", "once", "manual"]


class RunStatus:
    """Run lifecycle states (string constants for easy JSON round-trip)."""

    RUNNING = "running"
    NEEDS_INPUT = "needs_input"  # blocked on a durable inquiry answer
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"

    TERMINAL = frozenset({COMPLETED, FAILED, CANCELLED})
    ALL = frozenset({RUNNING, NEEDS_INPUT} | TERMINAL)


class ScheduleKind:
    """Schedule union tags (string constants for easy JSON round-trip)."""

    MANUAL = "manual"
    ONCE = "once"
    CRON = "cron"

    ALL = (MANUAL, ONCE, CRON)  # order = order in the validation error message


def manual_schedule() -> dict:
    return {"kind": ScheduleKind.MANUAL, "at": None, "cron": None}


def normalize_schedule(raw: dict | None) -> dict:
    """Canonical ``{kind, at, cron}`` for a schedule union.

    Raises ``ValueError`` with a user/agent-correctable message on bad input —
    callers map it to HTTP 422 or a tool reply.
    """
    from assistant.tasks.scheduling import normalize_cron, parse_dt

    if raw is None:
        return manual_schedule()
    kind = str(raw.get("kind") or "").strip().lower()
    if kind not in ScheduleKind.ALL:
        raise ValueError(
            f"schedule.kind must be one of {', '.join(ScheduleKind.ALL)}, not {kind!r}"
        )
    if kind == ScheduleKind.MANUAL:
        return manual_schedule()
    if kind == ScheduleKind.ONCE:
        at = str(raw.get("at") or "").strip()
        if not at or parse_dt(at) is None:
            raise ValueError(
                "schedule.at must be an ISO 8601 datetime for kind='once', "
                "e.g. 2026-08-01T09:00:00+03:00"
            )
        return {"kind": ScheduleKind.ONCE, "at": at, "cron": None}
    cron = normalize_cron(str(raw.get("cron") or ""))
    if cron is None:
        raise ValueError(
            "schedule.cron must be standard 5-field cron (minute hour dom month dow), "
            "e.g. '0 9 * * *' = daily 09:00, or @hourly/@daily/@weekly/@monthly"
        )
    return {"kind": ScheduleKind.CRON, "at": None, "cron": cron}


@dataclass
class Task:
    """Standing task configuration — what to run, on what model, when."""

    id: str
    name: str
    prompt: str = ""
    model: str | None = None  # llm_configs entry id; None = profile default
    schedule: dict = field(default_factory=manual_schedule)
    paused: bool = False

    # delivery routing: the messaging channel (and its native chat id) the task
    # was created from, so run outcomes can be pushed back there. None for web.
    origin_channel: str | None = None
    origin_chat: str | None = None

    next_run_at: str | None = None  # ISO; derived from schedule (None: manual/paused)
    created_at: str = ""
    updated_at: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "Task":
        # ignore unknown keys so the store survives schema growth (and old records)
        known = {k: data[k] for k in cls.__dataclass_fields__ if k in data}
        return cls(**known)


@dataclass
class Run:
    """One execution of a Task. The transcript lives on the run's chat stream;
    this record is just durable metadata for listing and cross-run context."""

    id: str
    task_id: str
    status: str = RunStatus.RUNNING
    trigger: RunTrigger = "manual"
    started_at: str = ""
    ended_at: str | None = None
    summary: str = ""  # one-line outcome (cheap-model distilled)
    error: str | None = None
    seen_at: str | None = None  # drives the unread highlight

    @property
    def stream_id(self) -> str:
        return f"task-run:{self.id}"

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "Run":
        known = {k: data[k] for k in cls.__dataclass_fields__ if k in data}
        return cls(**known)
