"""Per-profile settings surfaces: the settings panel, the health roll-up, the
MCP server list and the voice picker. Mirrors web/src/schemas/settings.ts.

A model's module follows its zod twin, not the phase it landed in — which is why
the health rows arrived here in phase 1, ahead of everything else.
"""

from typing import Literal

from pydantic import BaseModel

from .system import CodexStatusResponse, HealthStateOut


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


class McpServerOut(BaseModel):
    """One persisted MCP stdio server, in the PUBLIC projection: settings.py
    ``list_mcp_servers`` drops ``env`` and offers its key names instead, so a
    server's secrets never ride a settings fetch.

    ``env_keys`` carries a default because its zod twin does, and the gate reads
    zod on the input side — the wire truth is that a route answering this shape
    is free not to send it. Every route that does send it declares
    ``response_model_exclude_unset=True`` so the field is echoed, not invented.
    """

    name: str
    enabled: bool
    command: str
    args: list[str]
    cwd: str | None
    allowed_tools: list[str]
    blocked_tools: list[str]
    env_keys: list[str] = []


class ProviderKeyOut(BaseModel):
    """One provider's key presence, from secrets.py ``status()``: a last-4
    ``hint`` for every LLM provider and GitHub, and a ``base_url`` instead for
    Ollama, which has no key to hint at. Neither field is on both branches, so
    both carry defaults and GET /settings excludes what went unset.
    """

    set: bool
    hint: str | None = None
    base_url: str | None = None


class AssistantModelOut(BaseModel):
    """Display-only view of the resolved assistant model. Managed through
    /api/llm-configs — the settings panel shows it and cannot change it."""

    provider: str
    model: str


class FsRootsOut(BaseModel):
    """The filesystem anchors the folder pickers offer as shortcuts."""

    home: str
    cwd: str
    workspace: str


class ProfileSettingsResponse(BaseModel):
    """GET /api/p/{pid}/settings — everything the settings panel renders.

    ``codex`` is the very body GET /api/codex/status answers; both are
    ``codex_auth.status()``, so the model is reused rather than restated.

    The two override pairs are the ADR 0015 contract: ``*_override`` is what THIS
    profile pinned (None when it inherits, and also None when the pin dangles),
    ``*_active`` the id that actually resolves. The header switchers need both to
    render a choice and mark it inherited without a second fetch.
    """

    keys: dict[str, ProviderKeyOut]
    # Voice runs on the provider's own realtime endpoint, so a base_url never
    # makes it available — this is key presence, narrowed to the two providers
    # that have a realtime API.
    voice_available: dict[str, bool]
    assistant: AssistantModelOut
    llm_override: str | None
    llm_active: str | None
    live_override: str | None
    live_active: str | None
    codex: CodexStatusResponse
    voice_provider: str
    mcp_servers: list[McpServerOut]
    focuses: list[str]
    reply_timeout_s: float
    fs: FsRootsOut


class McpServerSavedResponse(BaseModel):
    """POST /api/p/{pid}/settings/mcp — the one server that was written, beside
    the refreshed list. The panel re-renders from the list and highlights the
    row; a bad config is a 400 {ok:false, error} built by hand, not this body."""

    ok: Literal[True]
    server: McpServerOut
    mcp_servers: list[McpServerOut]


class McpServersSnapshotResponse(BaseModel):
    """DELETE /api/p/{pid}/settings/mcp/{name} — the list alone, since the row it
    names is gone."""

    ok: Literal[True]
    mcp_servers: list[McpServerOut]


class McpHealthOkOut(BaseModel):
    """POST /api/p/{pid}/settings/mcp/{name}/health, server reachable: the tool
    names its discovery returned."""

    ok: Literal[True]
    tools: list[str]


class McpHealthErrorOut(BaseModel):
    """An unreachable or disabled server answers 200 with ok:false — a probe that
    fails is a fact about the server, not a transport error, and the panel shows
    the reason inline. Member ORDER mirrors McpHealth in
    web/src/schemas/settings.ts: the gate matches anyOf branches by index."""

    ok: Literal[False]
    error: str


# POST /api/p/{pid}/settings/mcp/{name}/health.
McpHealthResponse = McpHealthOkOut | McpHealthErrorOut


class VoiceOut(BaseModel):
    """One voice in a provider's catalogue: its id and the one-line style blurb
    the picker shows under it."""

    name: str
    style: str


class VoiceCatalogResponse(BaseModel):
    """GET /api/p/{pid}/voice/voices — the catalogue of the provider in scope
    plus the current selection. ``input_rate`` is the mic capture rate the client
    must use for this provider, so the picker and the voice socket agree."""

    voices: list[VoiceOut]
    current: str | None
    provider: str
    input_rate: int


class VoiceSelectedResponse(BaseModel):
    """POST /api/p/{pid}/voice/select — echoes the persisted voice."""

    ok: Literal[True]
    voice: str


class FocusesSavedResponse(BaseModel):
    """POST /api/p/{pid}/settings/focuses — the NORMALISED list (lowercased,
    deduped, slugs the input may not have been), which is why the route echoes it
    rather than answering a bare {ok}."""

    ok: Literal[True]
    focuses: list[str]


class LlmOverrideSavedResponse(BaseModel):
    """POST /api/p/{pid}/settings/llm-override — null means the override was
    cleared, and this profile is back to the install-wide Active."""

    ok: Literal[True]
    llm_override: str | None


class LiveOverrideSavedResponse(BaseModel):
    """POST /api/p/{pid}/settings/live-override — the Live (voice) counterpart."""

    ok: Literal[True]
    live_override: str | None


class ReplyTimeoutSavedResponse(BaseModel):
    """POST /api/p/{pid}/settings/reply-timeout — the stored value as a float,
    which is what the settings store coerced it to."""

    ok: Literal[True]
    reply_timeout_s: float
