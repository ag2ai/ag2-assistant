"""Terminal + env_var auth methods (ADR 0035), and the credential-state seam that
picks between advertising them and staying ungated.

Drives real ``ag2.acp`` connections in-process (``ag2.acp.testing.connect``) — no
real LLM (``ag2.testing.TestConfig``), no subprocess.
"""

import asyncio

import acp
import pytest
from ag2 import Agent
from ag2.acp import ACPAgent
from ag2.acp.testing import connect
from ag2.testing import TestConfig

from assistant.acp.auth import AssistantAuthMethods, choose_auth, profile_has_credentials


def test_choose_auth_is_none_for_a_credentialed_profile(config):
    """Ollama needs no key: mirrors ``assistant.agent.model_config``'s own check."""
    cfg = config.model_copy(deep=True)
    cfg.llm.provider = "ollama"
    assert profile_has_credentials(cfg, {}) is True
    assert choose_auth(cfg, {}) is None


def test_choose_auth_advertises_for_a_keyless_real_provider(config):
    """The default provider (gemini) with no key anywhere is honestly keyless."""
    assert config.llm.provider == "gemini"
    assert profile_has_credentials(config, {}) is False
    assert isinstance(choose_auth(config, {}), AssistantAuthMethods)


def test_choose_auth_advertises_for_cold_start():
    """No profile resolved (``config=None``) — the registry-CI scenario."""
    assert isinstance(choose_auth(None, {}), AssistantAuthMethods)


def test_profile_has_credentials_reads_the_secret_env_or_process_env(config):
    cfg = config.model_copy(deep=True)
    cfg.llm.provider = "anthropic"
    assert profile_has_credentials(cfg, {}) is False
    assert profile_has_credentials(cfg, {"ANTHROPIC_API_KEY": "sk-test"}) is True
    cfg.secret_env = {"ANTHROPIC_API_KEY": "sk-test"}
    assert profile_has_credentials(cfg, {}) is True


def test_profile_has_credentials_openai_subscription_reads_codex_tokens(config, paths):
    cfg = config.model_copy(deep=True)
    cfg.llm.provider = "openai"
    cfg.llm.auth_mode = "subscription"
    assert profile_has_credentials(cfg, {}) is False
    paths.codex_tokens.parent.mkdir(parents=True, exist_ok=True)
    paths.codex_tokens.write_text('{"access_token": "tok"}')
    assert profile_has_credentials(cfg, {}) is True


async def test_unconfigured_advertises_methods_and_session_new_fails_auth_required():
    """Cold/keyless: ``initialize`` carries both methods; ``session/new`` fails
    ``auth_required`` promptly rather than hanging."""
    agent = Agent(name="acp-test", config=TestConfig("42"))
    acp_agent = ACPAgent(
        agent, name="AG2 Assistant", version="0.0.0-test", auth=choose_auth(None, {})
    )

    async with connect(acp_agent, initialize=False) as (client, _recorder):
        init = await asyncio.wait_for(client.initialize(protocol_version=acp.PROTOCOL_VERSION), 5)
        ids = {m.id for m in init.auth_methods}
        assert ids == {"terminal", "env_var"}

        with pytest.raises(acp.RequestError) as exc_info:
            await asyncio.wait_for(client.new_session(cwd="."), 5)
    assert exc_info.value.code == -32000


async def test_credentialed_profile_serves_session_new_and_prompt_with_no_authenticate(config):
    """Ollama (or any credentialed profile): ``auth=None`` — no ``authenticate`` call
    is ever made, and the turn still completes."""
    cfg = config.model_copy(deep=True)
    cfg.llm.provider = "ollama"
    auth = choose_auth(cfg, {})
    assert auth is None

    agent = Agent(name="acp-test", config=TestConfig("pong"))
    acp_agent = ACPAgent(agent, name="AG2 Assistant", version="0.0.0-test", auth=auth)

    async with connect(acp_agent) as (client, recorder):
        session = await client.new_session(cwd=".")
        response = await client.prompt(session_id=session.session_id, prompt=[acp.text_block("hi")])
    assert response.stop_reason == "end_turn"


async def test_authenticate_always_rejects_terminal_and_env_var():
    """Both methods complete out of band — ``authenticate`` never succeeds for either."""
    provider = AssistantAuthMethods()
    with pytest.raises(Exception, match="terminal"):
        await provider.authenticate("terminal")
    with pytest.raises(Exception, match="env_var"):
        await provider.authenticate("env_var")


async def test_authenticate_over_the_wire_surfaces_as_a_request_error():
    agent = Agent(name="acp-test", config=TestConfig("42"))
    acp_agent = ACPAgent(
        agent, name="AG2 Assistant", version="0.0.0-test", auth=AssistantAuthMethods()
    )

    async with connect(acp_agent) as (client, _recorder):
        with pytest.raises(acp.RequestError):
            await client.authenticate(method_id="terminal")


def test_methods_schema_matches_the_registry_terminal_gate():
    """research/08: the registry's CI gate is a non-empty ``authMethods`` whose types
    resolve to ``agent`` or ``terminal`` — assert a ``terminal`` entry is present."""
    methods = AssistantAuthMethods().methods()
    assert methods, "authMethods must be non-empty for the registry gate"
    types = {m.type for m in methods}
    assert "terminal" in types
    assert "agent" not in types
