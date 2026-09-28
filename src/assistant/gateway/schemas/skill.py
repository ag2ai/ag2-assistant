"""Skills: the install-wide projection, one profile's resolved view, and the
registry/git/upload install flows (ADR 0016, ADR 0017). Mirrors
web/src/schemas/skill.ts.

Every shape here comes in two: the install-wide surface answers ``SkillOut``
rows, the profile surface answers ``ProfileSkillOut`` — the same row plus the
two fields only a profile can resolve. Keeping them apart rather than making
``suppressed``/``available`` optional is what stops the install-wide page from
claiming a per-profile answer it never computed.
"""

from typing import Literal

from pydantic import BaseModel

# Read off each skill's on-disk location (assistant/skills.py:40-45): under the
# bundled first-party dir → bundled, under a profile's skills_dir → profile,
# otherwise the Global layer.
SkillOrigin = Literal["bundled", "global", "profile"]


class SkillOut(BaseModel):
    """One install-wide skill row: Bundled or Global, with its install-wide
    Enabled state. A Profile skill never appears here — it lives in one profile
    and only the profile projection sees it."""

    name: str
    description: str
    origin: SkillOrigin
    enabled: bool


class ProfileSkillOut(SkillOut):
    """The same row as seen from one profile, plus its per-profile resolution.

    ``suppressed`` is this profile's own off-record — a Suppression of an
    inherited shared skill, or a Profile skill's own Disable. ``available`` is
    the resolved answer both fields feed (``SkillStateStore.is_available``), so a
    skill Disabled install-wide reads unavailable here even when this profile
    never suppressed it.
    """

    suppressed: bool
    available: bool


class SkillListResponse(BaseModel):
    """GET /api/skills — the install-wide projection behind Application → Skills."""

    skills: list[SkillOut]


class ProfileSkillListResponse(BaseModel):
    """GET /api/p/{pid}/skills — inherited Bundled/Global skills (Suppressible
    here) plus the profile's own skills (Enable/Disable here)."""

    skills: list[ProfileSkillOut]


class SkillMutatedResponse(BaseModel):
    """POST /api/skills/{name}/state and DELETE /api/skills/{name} — the refreshed
    install-wide projection, so the page re-renders from the answer instead of
    re-fetching."""

    ok: Literal[True]
    skills: list[SkillOut]


class ProfileSkillMutatedResponse(BaseModel):
    """The profile's four state routes (suppress, un-suppress, state, delete):
    the same echo over the profile's own projection."""

    ok: Literal[True]
    skills: list[ProfileSkillOut]


class SkillSearchHitOut(BaseModel):
    """One skills.sh registry hit. ``install_id`` is what the install route wants
    back — the name alone doesn't address a registry entry."""

    name: str
    install_id: str
    description: str
    installs: int


class SkillSearchResultsResponse(BaseModel):
    """POST /api/skills/search — target-agnostic, both surfaces search through it."""

    results: list[SkillSearchHitOut]


class DiscoveredSkillOut(BaseModel):
    """A SKILL.md found in a source but not installed: the checklist row a user
    ticks before the install call."""

    name: str
    description: str


class SkillDiscoveredResponse(BaseModel):
    """The four discover routes (git or upload, install-wide or profile). The
    scan never touches disk state, so both surfaces answer the same shape."""

    skills: list[DiscoveredSkillOut]


class SkillInstalledResponse(BaseModel):
    """POST /api/skills/install and /install-upload — what landed, plus the
    refreshed install-wide projection. ``installed`` carries rows rather than
    names because both ``registry_install`` and ``install_from_source`` yield
    ``{name, description}``."""

    ok: Literal[True]
    installed: list[DiscoveredSkillOut]
    skills: list[SkillOut]


class ProfileSkillInstalledResponse(BaseModel):
    """The profile's two install routes: same envelope over the profile's own
    projection, because only this profile reloaded."""

    ok: Literal[True]
    installed: list[DiscoveredSkillOut]
    skills: list[ProfileSkillOut]
