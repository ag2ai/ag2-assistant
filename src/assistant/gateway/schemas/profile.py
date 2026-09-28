"""Profiles — the registry every client boots from. Mirrors web/src/schemas/profile.ts."""

from pydantic import BaseModel


class ProfileOut(BaseModel):
    """One registry entry as the API shows it.

    ``workspace`` is derived, not stored: the registry keeps ids and display
    fields, and the path is computed from the install layout on each read.
    ``created`` is an ISO-8601 UTC string, not a timestamp.
    """

    id: str
    name: str
    accent: str
    workspace: str
    created: str


class ProfileListResponse(BaseModel):
    """GET /api/profiles — the §3.5 boot contract, answered in every state.

    ``archived`` is a second list rather than a flag on ``profiles`` because the
    Settings "Archived" section is a different surface (ADR 0003): the two are
    never rendered together. Both are empty on a fresh install, where
    ``active_default`` is null and ``onboarded`` false.

    The two versions ride this payload so a booting client needs no second
    request; nothing else answers them.
    """

    profiles: list[ProfileOut]
    archived: list[ProfileOut]
    active_default: str | None
    onboarded: bool
    version: str
    ag2_version: str


class ProfileEnvelopeResponse(BaseModel):
    """POST /api/profiles, POST /api/profiles/{pid} and .../restore.

    An envelope rather than a bare ProfileOut, matching what the front end parses
    (`ProfileEnvelope`): a write answers with the profile it wrote, and the key
    leaves room to say more about the write later without breaking the shape.
    """

    profile: ProfileOut
