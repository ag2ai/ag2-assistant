"""Auth methods an ACP listener advertises.

Advertises ``terminal`` only, never ``agent`` (ADR 0035). Setup completes out of
band — the owner configures a profile through the gateway's browser onboarding —
so ``authenticate`` always rejects — see ``AssistantAuthMethods``.

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

from acp import schema
from ag2.acp.auth import AuthenticationFailedError, AuthMethod, AuthProvider

from assistant.agent import ACP_PROVIDERS
from assistant.config import Config
from assistant.secrets import KEY_ENV

__all__ = ("SETUP_HINT", "AssistantAuthMethods", "choose_auth", "profile_has_credentials")

_TERMINAL_METHOD_ID = "terminal"
SETUP_HINT = (
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
                description=SETUP_HINT,
            ),
        ]

    async def authenticate(self, method_id: str, **kwargs: Any) -> None:
        if method_id != _TERMINAL_METHOD_ID:
            raise AuthenticationFailedError(f"unknown auth method {method_id!r}")
        raise AuthenticationFailedError(f"{method_id!r} completes out of band — {SETUP_HINT}")


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
