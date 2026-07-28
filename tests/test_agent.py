"""Tests for AG2 Assistant agent."""

import pytest
from ag2.acp import ClaudeCodeConfig

from assistant.agent import _build_middleware, ask, create_agent, model_config
from assistant.config import Config
from assistant.middleware import LLMRetryMiddleware, LLMTimeoutMiddleware


def test_create_agent_default():
    agent = create_agent()
    assert agent is not None


def test_create_agent_custom_config():
    config = Config()
    config.agent.name = "test-bot"
    agent = create_agent(config)
    assert agent is not None


@pytest.mark.integration
async def test_ask_returns_response():
    """Integration test: requires GEMINI_API_KEY in environment."""
    response = await ask("Say hello in exactly 3 words.")
    assert isinstance(response, str)
    assert len(response) > 0


def test_model_config_claude_code(tmp_path):
    cfg = Config()
    cfg.llm.provider = "claude_code"
    cfg.llm.model = "sonnet"
    cfg.workspace_dir = tmp_path
    cfg.llm.provider_options["claude_code"] = {"turn_timeout": 120.0}
    mc = model_config(cfg)
    assert isinstance(mc, ClaudeCodeConfig)
    assert mc.model == "sonnet"
    assert mc.cwd == str(tmp_path)
    assert mc.turn_timeout == 120.0  # Advanced options reach the constructor


def test_build_middleware_claude_code_skips_llm_timeout():
    cfg = Config()
    # One ACP "LLM call" is a whole inner tool loop; the 180s per-call ceiling
    # would kill normal turns. ACPConfig.turn_timeout is the ceiling instead.
    cfg.llm.provider = "claude_code"
    mw = _build_middleware(cfg)
    assert not any(isinstance(m, LLMTimeoutMiddleware) for m in mw)
    assert any(isinstance(m, LLMRetryMiddleware) for m in mw)
    cfg.llm.provider = "gemini"
    assert any(isinstance(m, LLMTimeoutMiddleware) for m in _build_middleware(cfg))


def test_model_config_codex(tmp_path):
    import json

    from ag2.acp import CodexConfig

    cfg = Config()
    cfg.llm.provider = "codex"
    cfg.llm.model = "gpt-5.6-sol[medium]"
    cfg.workspace_dir = tmp_path
    cfg.llm.provider_options["codex"] = {"turn_timeout": 120.0}
    mc = model_config(cfg)
    assert isinstance(mc, CodexConfig)
    assert mc.cwd == str(tmp_path)
    assert mc.turn_timeout == 120.0  # Advanced options reach the constructor
    # The model field's "name[effort]" form reaches the adapter split into the
    # CODEX_CONFIG env JSON (ACPConfig.model itself is response metadata only).
    assert json.loads(mc.env["CODEX_CONFIG"]) == {
        "model": "gpt-5.6-sol",
        "model_reasoning_effort": "medium",
    }


def test_model_config_codex_empty_model(tmp_path):
    from ag2.acp import CodexConfig

    cfg = Config()
    cfg.llm.provider = "codex"
    cfg.llm.model = ""  # empty entry model = the CLI's own default
    cfg.workspace_dir = tmp_path
    mc = model_config(cfg)
    assert isinstance(mc, CodexConfig)
    assert mc.env is None


def test_build_middleware_codex_skips_llm_timeout():
    cfg = Config()
    # Same reasoning as claude_code: one ACP "LLM call" is a whole inner tool
    # loop; ACPConfig.turn_timeout is the ceiling instead.
    cfg.llm.provider = "codex"
    mw = _build_middleware(cfg)
    assert not any(isinstance(m, LLMTimeoutMiddleware) for m in mw)
    assert any(isinstance(m, LLMRetryMiddleware) for m in mw)
