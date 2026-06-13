"""Tests for the live environment context injected into the system prompt."""

from datetime import datetime

from agclaw.agent import environment_context, turn_prompt
from agclaw.config import Config


def test_environment_context_has_current_year():
    ctx = environment_context(Config())
    assert str(datetime.now().year) in ctx
    assert "Current date and time" in ctx


def test_environment_context_includes_location_when_set():
    config = Config()
    config.agent.location = "Sydney, Australia"
    ctx = environment_context(config)
    assert "Sydney, Australia" in ctx


def test_environment_context_omits_location_when_unset():
    config = Config()
    config.agent.location = None  # independent of any AGCLAW_LOCATION env var
    ctx = environment_context(config)
    assert "User location" not in ctx


def test_turn_prompt_includes_persona_and_environment():
    config = Config()
    prompt = turn_prompt(config)
    assert prompt[0] == config.agent.system_prompt
    assert "Current date and time" in prompt[1]
