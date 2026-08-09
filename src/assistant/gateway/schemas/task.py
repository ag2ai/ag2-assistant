"""Tasks, their runs, and the durable HITL inquiries a run can block on.

Mirrors web/src/schemas/task.ts. Field lists come from that file, which was
validated against real responses; the bodies themselves are built by
gateway/tasks_service.py (_run_view, _task_row).
"""

from typing import Literal

from pydantic import BaseModel

RunStatusOut = Literal["running", "needs_input", "completed", "failed", "cancelled"]


class RunOut(BaseModel):
    """One run, as tasks_service._run_view builds it.

    ``trigger`` is a bare str, not the Literal tasks/model.py pins it to: run
    records outlive the version that wrote them, and no view reads the field, so
    an older value must be rendered rather than rejected.
    """

    id: str
    task_id: str
    status: RunStatusOut
    trigger: str
    started_at: str | None
    ended_at: str | None
    summary: str | None
    error: str
    seen: bool


class ScheduleOut(BaseModel):
    """A task's schedule, canonicalised to {kind, at, cron} by
    tasks/model.py normalize_schedule.

    ``at`` and ``cron`` carry defaults rather than being required-nullable
    because tasks/store.py create_task accepts a schedule dict unnormalised
    (``schedule or manual_schedule()``), so a persisted row can be missing a key
    that normalize_schedule would have filled. Both are declared: the schedule
    editor reads them (components/task/ScheduleField.svelte), and a model that
    did not declare them would silently drop them from the response.
    """

    kind: Literal["manual", "once", "cron"]
    at: str | None = None
    cron: str | None = None


class TaskOut(BaseModel):
    """One task row for the drawer — tasks_service._task_row."""

    id: str
    name: str
    prompt: str
    model: str | None
    description: str
    schedule: ScheduleOut
    schedule_desc: str
    paused: bool
    starred: bool
    next_run_at: str | None
    created_at: str
    updated_at: str
    last_run: RunOut | None
    unread: int
    needs_input: bool


class TaskWithRunsOut(TaskOut):
    """The same row plus its run history, as get_task returns it."""

    runs: list[RunOut]


class TaskListResponse(BaseModel):
    """GET /api/p/{pid}/tasks — needs-input first, then newest."""

    tasks: list[TaskOut]


class TaskEnvelopeResponse(BaseModel):
    """GET and PATCH /api/p/{pid}/tasks/{task_id} — both answer through
    get_task, so both carry the run history."""

    task: TaskWithRunsOut


class NewTaskEnvelopeResponse(BaseModel):
    """POST /api/p/{pid}/tasks — create_task answers with a freshly built row,
    which has no ``runs`` key yet."""

    task: TaskOut


class RunDetailOut(RunOut):
    """tasks_service.get_run stamps the owning task's name onto the run view —
    the run header reads it."""

    task_name: str


class RunDetailEnvelopeResponse(BaseModel):
    """GET /api/p/{pid}/runs/{run_id} and POST /api/p/{pid}/tasks/{id}/run."""

    run: RunDetailOut


class RunListResponse(BaseModel):
    """GET /api/p/{pid}/tasks/{task_id}/runs — the history without task_name,
    since the caller already knows the task."""

    runs: list[RunOut]


class InquiryOut(BaseModel):
    """One open HITL inquiry. The last three fields resolve the asking run back
    to its task so the strip can label and link the source."""

    id: str
    task_id: str
    chat: str
    kind: str
    text: str
    detail: str
    options: list[str]
    created_at: str
    root_id: str | None
    task_title: str
    run_id: str | None


class InquiryListResponse(BaseModel):
    """GET /api/p/{pid}/inquiries/pending."""

    pending: list[InquiryOut]
