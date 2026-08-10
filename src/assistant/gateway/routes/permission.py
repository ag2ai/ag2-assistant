"""Granted command rules, install-wide or scoped to one task.

Pairs with gateway/schemas/permission.py and web/src/schemas/permission.ts.
The task-scoped pair lives here rather than in routes/task.py because a module
follows its zod twin, and ``TaskRules`` is declared in permission.ts.

The two scopes share the store's vocabulary but not its file: the install-wide
routes read one ``permissions.json`` beside the Root, while a task's grants are
resolved through that task's profile runtime.
"""

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel

from assistant.gateway.profile_manager import ProfileRuntime
from assistant.gateway.routes.deps import GatewayDeps
from assistant.gateway.schemas import (
    Ok,
    PermissionMutatedResponse,
    PermissionSnapshotResponse,
    TaskRulesResponse,
)
from assistant.permissions import PermissionStore, command_rule, shell_prefix


class PermissionCommandAddRequest(BaseModel):
    tool: str
    prefix: str | None = None  # shell command prefix (e.g. "git"), or null for whole-tool


class PermissionCommandDeleteRequest(BaseModel):
    rule: str  # canonical rule string, e.g. "run_shell_command(git *)" or "run_code"


def build_router(d: GatewayDeps) -> APIRouter:
    """The install-wide command-rule store: one file shared by every profile."""
    r = APIRouter()

    def _permissions_store():
        """A fresh PermissionStore over the install-wide file. mtime self-refresh
        means live turns pick up any change on their next query — no manager.reload()."""
        return PermissionStore(d.paths.root / "permissions.json")

    def _permissions_snapshot(store) -> dict:
        return {"commands": store.granted_commands()}

    @r.get("/api/permissions", response_model=PermissionSnapshotResponse)
    async def get_permissions():
        """The install-wide permission state (command rules)."""
        return _permissions_snapshot(_permissions_store())

    @r.post("/api/permissions/commands", response_model=PermissionMutatedResponse)
    async def grant_permission_command(req: PermissionCommandAddRequest):
        """Grant a command rule. The rule string is built SERVER-SIDE via command_rule()
        so the frontend can't produce malformed syntax; a prefix that the matcher would
        never honour (fails the shell_prefix charset) is rejected 400 rather than minting
        a dead rule."""
        if not req.tool.strip():
            return JSONResponse({"error": "tool is required"}, status_code=400)
        prefix = req.prefix.strip() if req.prefix else None
        if prefix and shell_prefix(prefix) != prefix:
            return JSONResponse(
                {"error": f"invalid command prefix: {req.prefix!r}"}, status_code=400
            )
        store = _permissions_store()
        try:
            # grant_command re-parses the built rule (a tool name with spaces/parens
            # fails) and refuses bare grants on shell tools — both are 400s, not 500s.
            store.grant_command(command_rule(req.tool.strip(), prefix))
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        return {"ok": True, **_permissions_snapshot(store)}

    @r.delete("/api/permissions/commands", response_model=PermissionMutatedResponse)
    async def revoke_permission_command(req: PermissionCommandDeleteRequest):
        """Revoke a command rule by its canonical string. 404 if absent."""
        if not req.rule.strip():
            return JSONResponse({"error": "rule is required"}, status_code=400)
        store = _permissions_store()
        if not store.revoke_command(req.rule):
            return JSONResponse({"error": f"not granted: {req.rule}"}, status_code=404)
        return {"ok": True, **_permissions_snapshot(store)}

    return r


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
