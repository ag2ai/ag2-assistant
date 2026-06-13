"""Tests for AGClaw configuration."""

from pathlib import Path

from agclaw.config import AgentConfig, Config, LLMConfig


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
