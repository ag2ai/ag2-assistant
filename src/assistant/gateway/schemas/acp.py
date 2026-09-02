"""ACP listeners: install-level Connections (``platform="acp"``, ADR 0031) with a
live server task per configured port. Mirrors web/src/schemas/acp.ts.

A listener's shared token never rides a list or detail body (ADR 0024 spirit) —
only ``has_token`` says whether one is set. The raw value is returned exactly
once, from the create and rotate-token responses, and nowhere else.
"""

from pydantic import BaseModel


class AcpListenerOut(BaseModel):
    """One ACP listener as Settings shows it.

    ``running`` and ``error`` follow the same honesty pattern as a channel
    Connection's ``active``/``error`` (see ``ConnectionOut``): a listener that is
    configured but not running, or unreachable because its port collided, must not
    read the same as a healthy one.
    """

    id: str
    name: str
    profile: str
    port: int | None
    running: bool
    error: str | None
    has_token: bool


class AcpListenerListResponse(BaseModel):
    """GET /api/acp/listeners."""

    listeners: list[AcpListenerOut]


class AcpListenerCreatedResponse(BaseModel):
    """POST /api/acp/listeners. ``token`` is the raw shared secret — generated
    here when the request omits one — shown exactly this once so the owner can
    hand it to the client (AG2 Space); it is never echoed again. Empty for a
    stdio listener (no ``port``), which has no upgrade request to carry one."""

    listener: AcpListenerOut
    token: str


class AcpListenerTokenRotatedResponse(BaseModel):
    """POST /api/acp/listeners/{id}/rotate-token: the same one-time reveal as
    creation, since rotating is minting a new secret under the same id."""

    listener: AcpListenerOut
    token: str
