"""Tests for AGClaw configuration."""

import json
from pathlib import Path

from assistant.config import AgentConfig, Config, LLMConfig, load_config


def test_default_config():
    config = Config()
    assert config.llm.provider == "gemini"
    assert config.llm.model.startswith("gemini")
    assert config.agent.name == "agclaw"
    assert config.data_dir == Path.home() / ".agclaw"


def test_custom_llm_config():
    llm = LLMConfig(provider="openai", model="gpt-4o", api_key_env="OPENAI_API_KEY")
    config = Config(llm=llm)
    assert config.llm.provider == "openai"
    assert config.llm.model == "gpt-4o"


def test_custom_agent_config():
    agent = AgentConfig(name="test-agent", system_prompt="You are a test agent.")
    config = Config(agent=agent)
    assert config.agent.name == "test-agent"
    assert "test agent" in config.agent.system_prompt


def test_load_config_defaults_when_no_file(tmp_path):
    cfg = load_config(tmp_path / "missing.json")
    assert cfg.llm.provider == "gemini"


def test_load_config_reads_json(tmp_path):
    p = tmp_path / "config.json"
    p.write_text(json.dumps({
        "llm": {"provider": "anthropic", "model": "claude-sonnet-4-6",
                "api_key_env": "ANTHROPIC_API_KEY", "aggregate_model": "claude-haiku"},
        "memory": {"aggregate_every_n_turns": 9},
    }))
    cfg = load_config(p)
    assert cfg.llm.provider == "anthropic"
    assert cfg.llm.model == "claude-sonnet-4-6"
    assert cfg.llm.aggregate_model == "claude-haiku"
    assert cfg.memory.aggregate_every_n_turns == 9


def test_env_overrides_json(tmp_path, monkeypatch):
    p = tmp_path / "config.json"
    p.write_text(json.dumps({"llm": {"model": "from-json"}}))
    monkeypatch.setenv("AGCLAW_MODEL", "from-env")
    monkeypatch.setenv("AGCLAW_SANDBOX", "docker")
    monkeypatch.setenv("AGCLAW_AGGREGATE_EVERY_N", "7")
    cfg = load_config(p)
    assert cfg.llm.model == "from-env"  # env beats json
    assert cfg.tools.sandbox == "docker"
    assert cfg.memory.aggregate_every_n_turns == 7


def test_malformed_json_falls_back_to_defaults(tmp_path):
    p = tmp_path / "config.json"
    p.write_text("{ not valid json ")
    cfg = load_config(p)
    assert cfg.llm.provider == "gemini"


def test_model_config_gemini_and_aggregate_override():
    from assistant.agent import model_config

    cfg = Config(llm=LLMConfig(provider="gemini", model="gemini-3.5-flash"))
    mc = model_config(cfg)
    assert type(mc).__name__ == "GeminiConfig"
    assert mc.model == "gemini-3.5-flash"
    # aggregate override picks a different (cheaper) model, same provider
    mc2 = model_config(cfg, "gemini-2.5-flash")
    assert type(mc2).__name__ == "GeminiConfig"
    assert mc2.model == "gemini-2.5-flash"


def test_model_config_dispatches_anthropic(monkeypatch):
    import pytest

    pytest.importorskip("anthropic")  # needs `pip install ag2[anthropic]`
    from assistant.agent import model_config

    monkeypatch.setenv("ANTHROPIC_API_KEY", "x")
    cfg = Config(llm=LLMConfig(provider="anthropic", model="claude-sonnet-4-6",
                               api_key_env="ANTHROPIC_API_KEY"))
    assert type(model_config(cfg)).__name__ == "AnthropicConfig"
