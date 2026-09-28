"""Connections and the three tables hung off one. Mirrors web/src/schemas/connection.ts.

A bot token never leaves the process: every body here carries
``assistant/secrets.py`` ``connection_token_status``, which reports presence and a
last-4 hint instead of the value.
"""

from pydantic import BaseModel


class TokenStatusOut(BaseModel):
    """One token env's presence and last-4 hint — never the value itself."""

    set: bool
    hint: str


class ConnectionOut(BaseModel):
    """One Connection as the API shows it.

    ``tokens`` is keyed by env-var name because a platform may need more than one
    (Zulip needs three), so which of them is missing is what the form has to say.
    ``paired_accounts`` is a count and not a roster: a live Connection with nobody
    paired answers nobody (ADR 0021), and the count is what lets Settings say so
    rather than leave it looking healthy. ``error`` is why the adapter is not
    live, null while it is.
    """

    id: str
    platform: str
    name: str
    tokens: dict[str, TokenStatusOut]
    default_profile: str | None
    active: bool
    error: str | None
    paired_accounts: int


class ConnectionListResponse(BaseModel):
    """GET /api/connections."""

    connections: list[ConnectionOut]


class ConnectionSurfaceOut(BaseModel):
    """One addressable surface of a Connection (assistant/connections.py surfaces()):
    ``dm`` plus ``group`` where the two are switched independently, a single ``all``
    where they are not."""

    kind: str
    id: str


class ConnectionExposureResponse(BaseModel):
    """GET and POST /api/connections/{cid}/exposure.

    ``exposure`` is {pid: {surface_id: reachable}} and is default-allow, so a
    profile nobody withdrew reads true on every surface. ``default_profile`` rides
    along because withdrawing the default's last surface clears it — one request
    changes both, so one body carries both.
    """

    surfaces: list[ConnectionSurfaceOut]
    exposure: dict[str, dict[str, bool]]
    default_profile: str | None


class PairedAccountOut(BaseModel):
    """One account allowed to reach a Connection. ``pending`` marks an invitation
    to a handle — nobody stands behind it until someone presents it, which is why
    ``account_id`` is null there."""

    key: str
    account_id: str | None
    handle: str | None
    pending: bool


class PairingCodeOut(BaseModel):
    """A one-time pairing code and the moment it stops working (a POSIX timestamp,
    written as ``time.time()``)."""

    code: str
    expires_at: float


class ConnectionPairingResponse(BaseModel):
    """GET /api/connections/{cid}/pairing and the two writes that answer with the
    whole roster. ``code`` is null whenever there is no unexpired one."""

    accounts: list[PairedAccountOut]
    code: PairingCodeOut | None


# POST /api/connections/{cid}/pairing/code answers with the freshly minted code
# ALONE, not the roster — so the body is the code, and null stays reachable
# because the route reads the store back rather than trusting the mint.
PairingCodeIssuedResponse = PairingCodeOut | None


class ConnectionGroupOut(BaseModel):
    """One group Peer of this Connection and the profile it is pinned to (null
    while it has none)."""

    chat_id: str
    profile: str | None


class GroupProfileOut(BaseModel):
    """A profile a group may be re-pointed at — id and display name only; the
    picker needs nothing else."""

    id: str
    name: str


class ConnectionGroupsResponse(BaseModel):
    """GET and POST /api/connections/{cid}/groups*.

    ``profiles`` are those exposed to THIS Connection's group surface, which is
    exactly what a group may be re-pointed at (ADR 0022) — so the picker's options
    arrive with the groups rather than being intersected client-side.
    """

    groups: list[ConnectionGroupOut]
    profiles: list[GroupProfileOut]
