"""Tests for AGClaw agent."""

import pytest

from agclaw.agent import ask, create_agent
from agclaw.config import Config


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
