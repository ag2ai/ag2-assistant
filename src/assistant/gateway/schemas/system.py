"""Install-wide status surfaces: health, usage, activity, coding agents, fs picker.

Mirrors web/src/schemas/system.ts. Field lists come from that file, which was
validated against real responses.
"""

from typing import Literal

from pydantic import BaseModel


class HealthResponse(BaseModel):
    """GET /api/health and the gateway's own status().

    The zero-profile stub answers {status, profiles}; a running install answers
    gateway/core.py status(). Only `status` is common to both, so everything
    else carries a default.
    """

    status: str
    model: str | None = None
    memory: bool | None = None
    platform: str | None = None
    chats: int | None = None
    profiles: int | None = None


class UsageTotalsOut(BaseModel):
    """One day's spend: usage.py _blank minus by_model."""

    prompt: float
    completion: float
    total: float
    cost: float
    priced: bool


class UsageResponse(UsageTotalsOut):
    """GET /api/p/{pid}/usage — today() plus the date it covers."""

    date: str
    by_model: dict[str, UsageTotalsOut]


class UsageRowOut(UsageResponse):
    """One profile's row inside the install-wide roll-up."""

    pid: str
    name: str


class UsageRollupResponse(BaseModel):
    """GET /api/usage — the install-wide sum carries neither date nor by_model."""

    profiles: list[UsageRowOut]
    total: UsageTotalsOut


class StatusRowOut(BaseModel):
    """One row of GET /api/status, which answers a bare array, not an envelope."""

    pid: str
    busy: bool
    running_tasks: int
    unseen_done: int


class CodingAgentOut(BaseModel):
    name: str
    label: str
    available: bool


class CodingAgentsResponse(BaseModel):
    """GET /api/coding/agents — `error` appears only when a bridge lookup failed."""

    mode: Literal["local", "bridge"]
    bridge: str | None
    connected: bool
    error: str | None = None
    agents: list[CodingAgentOut]


class CatalogModelOut(BaseModel):
    """coding/model_catalog.py as_view."""

    id: str
    name: str
    description: str


class CodingCatalogResponse(BaseModel):
    """GET /api/coding/{agent}/models — `reason` says WHY a catalog came back empty."""

    models: list[CatalogModelOut]
    current: str
    reason: Literal["", "adapter_missing", "bridge", "probe_failed"]


class FsDirOut(BaseModel):
    name: str
    path: str


class FsListingOkOut(BaseModel):
    """GET /api/fs/list, readable directory."""

    ok: Literal[True]
    path: str
    parent: str | None
    dirs: list[FsDirOut]


class FsListingErrorOut(BaseModel):
    """GET /api/fs/list answers 200 with ok:false when the path is unreadable.

    Declared as a separate member rather than optional fields on the success
    model, so the two shapes stay distinguishable — the Literal on `ok`
    discriminates them, which is what keeps the union from coercing one into the
    other. Member ORDER mirrors FsListing in web/src/schemas/system.ts: the gate
    matches anyOf branches by index.
    """

    ok: Literal[False]
    error: str


class FsMkdirResponse(BaseModel):
    """POST /api/fs/mkdir — failures are non-2xx JSONResponse, not this body."""

    ok: Literal[True]
    path: str


class MemoryDocResponse(BaseModel):
    """GET /api/memory and GET /api/p/{pid}/memory — the raw document text."""

    text: str


class IdentitySeededResponse(BaseModel):
    """POST /api/identity — seed-only.

    `reason` says why a seed was skipped ("empty" or "exists") and is absent on
    the branch that actually wrote, hence the default.
    """

    ok: bool
    seeded: bool
    reason: str | None = None


HealthStateOut = Literal["ok", "warn", "down", "off"]
