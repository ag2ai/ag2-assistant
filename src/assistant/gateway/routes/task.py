"""Tasks (standing configuration), their runs, and the durable HITL inquiries a
run blocks on. Each run is a chat on the stream ``task-run:<id>``.

Pairs with gateway/schemas/task.py (the response models) and
web/src/schemas/task.ts (their zod twins) — same file name in all three trees.

The two ``/tasks/{task_id}/permissions`` routes are NOT here: their zod twin
(``TaskRules``) lives in permission.ts, so they sit in routes/permission.py.
``/hitl/pending`` is here for the same reason in reverse — it reads a registry
this module otherwise knows nothing about, but ``HitlPending`` is declared in
task.ts, and the client renders its rows in the same strip as the inquiries.
"""

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel

from assistant.gateway.profile_manager import ProfileRuntime
from assistant.gateway.routes.deps import GatewayDeps
from assistant.gateway.schemas import (
    HitlPendingResponse,
    InquiryListResponse,
    NewTaskEnvelopeResponse,
    Ok,
    RunDetailEnvelopeResponse,
    RunListResponse,
    TaskEnvelopeResponse,
    TaskListResponse,
)
from assistant.tasks import TaskStoreCorruptionError


class TaskCreate(BaseModel):
    # Empty name triggers the service's cheap-model auto-naming from the prompt.
    name: str = ""
    prompt: str
    model: str | None = None
    schedule: dict | None = None
    description: str = ""
    recall_depth: int = 0


class TaskPatch(BaseModel):
    name: str | None = None
    prompt: str | None = None
    model: str | None = None  # "" clears back to the profile default
    schedule: dict | None = None
    paused: bool | None = None
    starred: bool | None = None
    description: str | None = None
    recall_depth: int | None = None  # 0 = none, -1 = all


class AnswerRequest(BaseModel):
    answer: str


def build_profile_router(d: GatewayDeps, get_runtime) -> APIRouter:
    """The /api/p/{pid} task slice. Registration order below is the order these
    handlers had in app.py — see AGENTS.md on route order."""
    r = APIRouter()

    @r.get("/tasks", response_model=TaskListResponse)
    async def list_tasks(runtime: ProfileRuntime = Depends(get_runtime)):
        """Task rows for the drawer (needs-input first, then newest)."""
        return {"tasks": await runtime.require_tasks().list_tasks()}

    @r.post("/tasks", response_model=NewTaskEnvelopeResponse)
    async def create_task(req: TaskCreate, runtime: ProfileRuntime = Depends(get_runtime)):
        """Create a task; empty ``name`` auto-generates one from the prompt
        (service-side). 422 with {error} on a bad schedule/model."""
        try:
            task = await runtime.require_tasks().create_task(
                name=req.name,
                prompt=req.prompt,
                model=req.model,
                schedule=req.schedule,
                description=req.description,
                recall_depth=req.recall_depth,
            )
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=422)
        return {"task": task}

    @r.get("/tasks/{task_id}", response_model=TaskEnvelopeResponse)
    async def get_task(task_id: str, runtime: ProfileRuntime = Depends(get_runtime)):
        try:
            task = await runtime.require_tasks().get_task(task_id)
        except TaskStoreCorruptionError as exc:
            return JSONResponse({"error": str(exc)}, status_code=500)
        if task is None:
            return Response(status_code=404)
        return {"task": task}

    @r.patch("/tasks/{task_id}", response_model=TaskEnvelopeResponse)
    async def update_task(
        task_id: str, req: TaskPatch, runtime: ProfileRuntime = Depends(get_runtime)
    ):
        """Edit any subset of task fields; model='' clears to the profile default."""
        patch = {k: v for k, v in req.model_dump().items() if v is not None}
        if req.model == "":  # explicit clear back to the profile default
            patch["model"] = None
        if not patch and req.model != "":
            return JSONResponse({"error": "empty patch"}, status_code=400)
        try:
            task = await runtime.require_tasks().update_task(task_id, **patch)
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=422)
        if task is None:
            return Response(status_code=404)
        return {"task": task}

    @r.delete("/tasks/{task_id}", response_model=Ok)
    async def delete_task(task_id: str, runtime: ProfileRuntime = Depends(get_runtime)):
        """Delete the task, its runs, and their chat streams. Irreversible."""
        if not await runtime.require_tasks().delete_task(task_id):
            return Response(status_code=404)
        return {"ok": True}

    @r.post("/tasks/{task_id}/run", response_model=RunDetailEnvelopeResponse)
    async def run_task(task_id: str, runtime: ProfileRuntime = Depends(get_runtime)):
        """Run now — start a run immediately; the schedule is unchanged."""
        run = await runtime.require_tasks().start_run(task_id, trigger="manual")
        if run is None:
            return Response(status_code=404)
        return {"run": await runtime.require_tasks().get_run(run.id)}

    @r.get("/tasks/{task_id}/runs", response_model=RunListResponse)
    async def list_runs(task_id: str, runtime: ProfileRuntime = Depends(get_runtime)):
        """The task's run history (newest first), as on the task page."""
        task = await runtime.require_tasks().get_task(task_id)
        if task is None:
            return Response(status_code=404)
        return {"runs": task["runs"]}

    @r.post("/tasks/{task_id}/seen", response_model=Ok)
    async def task_runs_seen(task_id: str, runtime: ProfileRuntime = Depends(get_runtime)):
        """Mark every finished run of one task opened (clears the whole list at once)."""
        await runtime.require_tasks().mark_task_runs_seen(task_id)
        return {"ok": True}

    @r.get("/runs/{run_id}", response_model=RunDetailEnvelopeResponse)
    async def get_run(run_id: str, runtime: ProfileRuntime = Depends(get_runtime)):
        """One run's durable header (status/summary/task name) for the run page."""
        run = await runtime.require_tasks().get_run(run_id)
        if run is None:
            return Response(status_code=404)
        return {"run": run}

    @r.post("/runs/{run_id}/stop", response_model=Ok)
    async def stop_run(run_id: str, runtime: ProfileRuntime = Depends(get_runtime)):
        """Stop a live run; whatever it already produced stays in its thread."""
        return {"ok": await runtime.require_tasks().stop_run(run_id)}

    @r.post("/runs/{run_id}/seen", response_model=Ok)
    async def run_seen(run_id: str, runtime: ProfileRuntime = Depends(get_runtime)):
        """Mark a finished run opened (clears its unread highlight)."""
        return {"ok": await runtime.require_tasks().mark_run_seen(run_id)}

    @r.get("/inquiries/pending", response_model=InquiryListResponse)
    async def inquiries_pending(
        task_id: str | None = None, runtime: ProfileRuntime = Depends(get_runtime)
    ):
        """Open HITL inquiries (clarifications / approvals) awaiting an answer."""
        return {"pending": await runtime.require_tasks().pending_inquiries(task_id)}

    @r.post("/inquiries/{inquiry_id}/answer", response_model=Ok)
    async def answer_inquiry(
        inquiry_id: str,
        req: AnswerRequest,
        runtime: ProfileRuntime = Depends(get_runtime),
    ):
        ok = await runtime.require_tasks().answer_inquiry(inquiry_id, req.answer)
        if not ok:
            return Response(status_code=404)
        return {"ok": True}

    @r.get("/hitl/pending", response_model=HitlPendingResponse)
    async def hitl_pending(runtime: ProfileRuntime = Depends(get_runtime)):
        """Open questions in THIS profile's TRANSIENT HITL registry, for a UI client
        to render. It rides with the tasks module because its zod twin
        (``HitlPending``) is declared in task.ts — the strip merges these rows with
        the durable inquiries above and renders one list."""
        return {"pending": runtime.hitl.pending_list()}

    return r
