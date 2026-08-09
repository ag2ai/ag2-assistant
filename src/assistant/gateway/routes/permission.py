"""Granted command rules, install-wide or scoped to one task.

Pairs with gateway/schemas/permission.py and web/src/schemas/permission.ts.
Phase 2 carries only the task-scoped pair — they belong here rather than in
routes/task.py because a module follows its zod twin, and ``TaskRules`` is
declared in permission.ts. The install-wide routes arrive with phase 6.
"""

from fastapi import APIRouter, Depends
from fastapi.responses import Response
from pydantic import BaseModel

from assistant.gateway.profile_manager import ProfileRuntime
from assistant.gateway.routes.deps import GatewayDeps
from assistant.gateway.schemas import Ok, TaskRulesResponse


class PermissionCommandDeleteRequest(BaseModel):
    rule: str  # canonical rule string, e.g. "run_shell_command(git *)" or "run_code"


def build_profile_router(d: GatewayDeps, get_runtime) -> APIRouter:
    """The /api/p/{pid} task-scoped permission slice."""
    r = APIRouter()

    @r.get("/tasks/{task_id}/permissions", response_model=TaskRulesResponse)
    async def task_permissions(task_id: str, runtime: ProfileRuntime = Depends(get_runtime)):
        """This task's own granted command rules — never the global set (mirrors
        ``GET /api/permissions``, scoped via ``task_id``). 404 on an unknown task."""
        if await runtime.tasks.get_task(task_id) is None:
            return Response(status_code=404)
        return {"rules": runtime.gateway.permissions.granted_commands(task_id=task_id)}

    @r.delete("/tasks/{task_id}/permissions", response_model=Ok)
    async def revoke_task_permission(
        task_id: str,
        req: PermissionCommandDeleteRequest,
        runtime: ProfileRuntime = Depends(get_runtime),
    ):
        """Revoke one of this task's own command rules by its canonical string
        (mirrors ``DELETE /api/permissions/commands``, scoped via ``task_id``).
        404 on an unknown task; an absent/already-revoked rule is a plain
        ``{"ok": false}`` — the task-scoped set is small enough that a client
        double-revoking isn't an error worth a 404."""
        if await runtime.tasks.get_task(task_id) is None:
            return Response(status_code=404)
        ok = runtime.gateway.permissions.revoke_command(req.rule, task_id=task_id)
        return {"ok": ok}

    return r
