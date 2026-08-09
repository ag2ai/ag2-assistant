"""Per-profile settings surfaces. Mirrors web/src/schemas/settings.ts.

Phase 1 needs only the profile-health rows (GET /api/p/{pid}/health); the rest of
this module arrives with phase 7. A model's module follows its zod twin, not the
phase it landed in.
"""

from pydantic import BaseModel

from .system import HealthStateOut


class HealthMcpServerOut(BaseModel):
    name: str
    enabled: bool


class HealthChannelOut(BaseModel):
    """One connection that defaults to this profile.

    Field list read off the handler, not off the zod schema: settings.ts used to
    declare a `token_present` the gateway never sent, and to omit the `connection`
    and `name` it does send. The zod side was corrected to match this.
    """

    connection: str
    name: str
    platform: str
    active: bool
    error: str | None


class HealthCheckOut(BaseModel):
    """One subsystem row. The mcp and channels rows carry extra detail."""

    id: str
    label: str
    state: HealthStateOut
    detail: str
    servers: list[HealthMcpServerOut] | None = None
    items: list[HealthChannelOut] | None = None


class ProfileHealthResponse(BaseModel):
    """GET /api/p/{pid}/health — `overall` rolls up the core signals only."""

    overall: HealthStateOut
    checks: list[HealthCheckOut]
