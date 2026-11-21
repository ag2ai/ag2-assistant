"""Auth methods an ACP listener advertises.

Advertises ``terminal`` only, never ``agent`` (ADR 0035). Setup completes out of
band — the owner configures a profile through the gateway's browser onboarding —
so ``authenticate`` always rejects — see ``AssistantAuthMethods``.

ACP v1 lets an agent advertise a ``terminal`` method only to a client that declares
``clientCapabilities.auth.terminal``. ``AssistantACPAgent`` drops the method for
any other client (see ``client_supports_terminal_auth``).

Upstream deadlock workaround (ADR 0035): ``ag2.acp.agent`` seeds a connection's
``_authenticated`` flag from ``owner._auth is None`` and only a wire ``authenticate``
call ever flips it — which terminal auth never makes. So passing a
provider gates every session forever. ``choose_auth`` sidesteps this by choosing
the auth object at construction time from on-disk credential state: credentialed
⇒ ``None`` (ungated, methods unadvertised); otherwise ⇒ the provider (advertised,
gated until setup completes and the listener restarts).
"""

import json
from collections.abc import Mapping
from typing import Any

import acp
from acp import schema
from ag2.acp.agent import ACPAgent, _ConnectionScope
from ag2.acp.auth import AuthenticationFailedError, AuthMethod, AuthProvider

from assistant.agent import ACP_PROVIDERS
from assistant.config import Config
from assistant.secrets import KEY_ENV

__all__ = (
    "AssistantACPAgent",
    "AssistantAuthMethods",
    "NegotiatedAuthScope",
    "choose_auth",
    "client_supports_terminal_auth",
    "profile_has_credentials",
)

_TERMINAL_METHOD_ID = "terminal"
# The ACP Registry's validator predates ``auth.terminal`` and signals terminal-auth
# support with this ``clientCapabilities._meta`` key instead.
_REGISTRY_TERMINAL_AUTH_META = "terminal-auth"
_SETUP_HINT = (
    "Run `ag2-assistant gateway` in a terminal, finish setup at http://127.0.0.1:8800 "
    "(profile, model provider and key), then restart this listener."
)


class AssistantAuthMethods:
    """Declares ``terminal``; ``authenticate`` always rejects, pointing at setup."""

    def methods(self) -> "list[AuthMethod]":
        return [
            schema.TerminalAuthMethod(
                type="terminal",
                id=_TERMINAL_METHOD_ID,
                name="Terminal setup",
                description=_SETUP_HINT,
            ),
        ]

    async def authenticate(self, method_id: str, **kwargs: Any) -> None:
        if method_id != _TERMINAL_METHOD_ID:
            raise AuthenticationFailedError(f"unknown auth method {method_id!r}")
        raise AuthenticationFailedError(f"{method_id!r} completes out of band — {_SETUP_HINT}")


def client_supports_terminal_auth(capabilities: schema.ClientCapabilities | None) -> bool:
    """Whether a client may be offered ``terminal`` auth methods.

    ACP v1 permits them only when ``clientCapabilities.auth.terminal`` is true, and
    an omitted capability means unsupported. The Registry validator's
    ``_meta["terminal-auth"]`` counts too, so a Registry listing check still sees
    the method.
    """
    if capabilities is None:
        return False
    if capabilities.auth is not None and capabilities.auth.terminal:
        return True
    return bool((capabilities.field_meta or {}).get(_REGISTRY_TERMINAL_AUTH_META))


class NegotiatedAuthScope(_ConnectionScope):
    """A connection scope whose ``initialize`` advertises only the auth methods the
    client declared it supports."""

    __slots__ = ()

    async def initialize(
        self,
        protocol_version: int,
        client_capabilities: schema.ClientCapabilities | None = None,
        client_info: schema.Implementation | None = None,
        **kwargs: Any,
    ) -> schema.InitializeResponse:
        response = await super().initialize(
            protocol_version, client_capabilities, client_info, **kwargs
        )
        if client_supports_terminal_auth(client_capabilities) or not response.auth_methods:
            return response
        methods = [m for m in response.auth_methods if m.type != _TERMINAL_METHOD_ID]
        return response.model_copy(update={"auth_methods": methods})


class AssistantACPAgent(ACPAgent):
    """``ACPAgent`` whose connections negotiate auth methods (``NegotiatedAuthScope``)."""

    def bind(self, client: "acp.Client | None") -> _ConnectionScope:
        scope = NegotiatedAuthScope(self, client)
        self._scope = scope
        return scope


def _codex_subscription_signed_in(config: Config) -> bool:
    """True when our own ChatGPT-subscription token store (``paths.codex_tokens``)
    holds a token. Reads the file directly — no CodexAuth flow (network refresh,
    CLI-login fallback) is exercised here, only the on-disk fact."""
    try:
        data = json.loads(config.paths.codex_tokens.read_text())
    except Exception:
        return False
    return bool(data.get("access_token") or data.get("refresh_token"))


def profile_has_credentials(config: Config, env: Mapping[str, str]) -> bool:
    """True when the resolved profile config can run a turn without more setup.

    Mirrors ``assistant.agent.model_config``'s own key resolution: Ollama and the
    ACP coding-CLI providers need no key (disk login owns auth); an OpenAI
    subscription profile needs our own ChatGPT tokens on disk; every other
    provider needs its key in the resolved secret env or the process env.
    """
    provider = config.llm.provider.lower()
    if provider == "ollama" or provider in ACP_PROVIDERS:
        return True
    if provider == "openai" and config.llm.auth_mode == "subscription":
        return _codex_subscription_signed_in(config)
    key_env = KEY_ENV.get(provider, config.llm.api_key_env)
    return bool(config.secret_env.get(key_env) or env.get(key_env))


def choose_auth(config: Config | None, env: Mapping[str, str]) -> AuthProvider | None:
    """The auth object a listener construction site should pass to ``ACPAgent``.

    ``None`` (ungated, methods unadvertised) for a resolved, credentialed profile.
    ``AssistantAuthMethods()`` (advertised, gated — see module docstring) for a cold
    start (``config`` is ``None``: no profile resolved) or a resolved profile
    without usable credentials yet.
    """
    if config is not None and profile_has_credentials(config, env):
        return None
    return AssistantAuthMethods()
