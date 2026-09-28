"""Permissions: canonical command rule strings, install-wide or task-scoped.

Mirrors web/src/schemas/permission.ts. The install-wide store is one file shared
by every profile, so its snapshot is a flat list of rule strings; the task-scoped
read answers the same shape under a different key (``rules``, not ``commands``)
because it is a different set — this task's own grants, never the global ones.
"""

from typing import Literal

from pydantic import BaseModel


class PermissionSnapshotResponse(BaseModel):
    """GET /api/permissions — every install-wide command rule, canonical form."""

    commands: list[str]


class PermissionMutatedResponse(BaseModel):
    """POST/DELETE /api/permissions/commands — the refreshed snapshot beside
    ``ok``, so the settings list re-renders from the answer. The task-scoped
    revoke answers a bare ``{ok}`` instead and uses ``Ok``: there the caller
    already holds the one list it touched."""

    ok: Literal[True]
    commands: list[str]


class TaskRulesResponse(BaseModel):
    """GET /api/p/{pid}/tasks/{task_id}/permissions — this task's own rules
    only, never a union with the install-wide set."""

    rules: list[str]
