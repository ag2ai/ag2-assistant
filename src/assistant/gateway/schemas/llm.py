"""Named LLM and live (voice) configurations. Mirrors web/src/schemas/llm.ts.

Both config views are shaped by the same rule: name the key a real call would
send, never the key itself. Hence the ``SecretRefOut``/``SharedKeyOut`` pair plus
a ``key_source`` token, which together let the UI say honestly why a
keyless-looking config still works.
"""

from typing import Any, Literal

from pydantic import BaseModel

from .primitives import SecretRefOut, SharedKeyOut
from .system import CatalogModelOut


class DepsStatusOut(BaseModel):
    """Optional provider-library state for a config type (llm_configs.deps_status)."""

    ok: bool
    extra: str
    install: str


# Which key an actual call would send (llm_configs.key_source).
KeySourceOut = Literal["secret", "shared", "not_needed", "none", "subscription", "cli_login"]


class LlmConfigOut(BaseModel):
    """One named LLM configuration as the API exposes it.

    ``signed_in`` is added only for ``type == "openai_subscription"`` — the live
    ChatGPT sign-in state the chip needs without a second fetch — so it carries a
    default and every route returning this model excludes unset fields.
    """

    id: str
    name: str
    type: str
    model: str
    base_url: str
    host: str
    options: dict[str, Any]
    # {tool id: options} — the provider-native tools switched on for this config.
    # Availability is per type; see assistant.builtin_tools.
    builtin_tools: dict[str, dict[str, Any]]
    secret_id: str
    secret: SecretRefOut | None
    secret_missing: bool
    key_source: KeySourceOut
    images: bool
    deps: DepsStatusOut
    shared_key: SharedKeyOut
    active: bool
    signed_in: bool | None = None


class LlmEnvOverrideOut(BaseModel):
    """The env pin banner payload: whichever of AG2ASSISTANT_LLM_PROVIDER /
    AG2ASSISTANT_MODEL is set. Each key is present only when its variable is,
    which is why both carry a default rather than being nullable-required."""

    provider: str | None = None
    model: str | None = None


class LlmConfigListResponse(BaseModel):
    """GET /api/llm-configs. ``provider_deps`` covers every known type, not just
    the configured ones — the "Add model" template grid reads it for types no
    config uses yet."""

    configs: list[LlmConfigOut]
    active: str | None
    env_override: LlmEnvOverrideOut | None
    provider_deps: dict[str, DepsStatusOut]
    # type -> the provider-native tool ids it offers, for every type (same reason
    # as provider_deps). Ids only — the labels are the web's.
    builtin_tools_by_type: dict[str, list[str]]


class LlmConfigSavedResponse(BaseModel):
    """POST /api/llm-configs and POST /api/llm-configs/{cid}."""

    ok: Literal[True]
    config: LlmConfigOut
    active: str | None


class ProviderCatalogResponse(BaseModel):
    """GET /api/llm-configs/models — a provider's model catalog in the same
    ``{models, current, reason}`` envelope the ACP route uses, with
    provider_catalog.py's own reasons. ``current`` is always empty here: a
    provider names no model of its own."""

    models: list[CatalogModelOut]
    current: str
    reason: Literal["", "unauthorized", "unreachable", "no_list_endpoint", "not_probeable"]


LiveKeySourceOut = Literal["secret", "shared", "none"]


class LiveConfigOut(BaseModel):
    """One named live (voice) configuration as the API exposes it."""

    id: str
    name: str
    provider: str
    model: str
    voice: str
    secret_id: str
    secret: SecretRefOut | None
    secret_missing: bool
    key_source: LiveKeySourceOut
    shared_key: SharedKeyOut
    active: bool


class LiveProviderOut(BaseModel):
    """A voice provider's defaults, which seed the add-form and the templates."""

    name: str
    default_model: str
    default_voice: str


class LiveConfigListResponse(BaseModel):
    """GET /api/live-configs."""

    configs: list[LiveConfigOut]
    active: str | None
    providers: list[LiveProviderOut]


class LiveConfigSavedResponse(BaseModel):
    """POST /api/live-configs and POST /api/live-configs/{cid}."""

    ok: Literal[True]
    config: LiveConfigOut
    active: str | None


class PingResultResponse(BaseModel):
    """Both Test buttons on success. A failure is a 502 built by hand, so the
    ``ok: false`` branch never rides this model."""

    ok: Literal[True]
    reply: str
    latency_ms: int
