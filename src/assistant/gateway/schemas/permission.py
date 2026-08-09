"""Permissions: canonical command rule strings, install-wide or task-scoped.

Mirrors web/src/schemas/permission.ts. Phase 2 carries only the task-scoped
read (GET /api/p/{pid}/tasks/{task_id}/permissions), because its zod twin
lives here; the install-wide surfaces arrive with phase 6.
"""

from pydantic import BaseModel


class TaskRulesResponse(BaseModel):
    """GET /api/p/{pid}/tasks/{task_id}/permissions — this task's own rules
    only, never a union with the install-wide set."""

    rules: list[str]
