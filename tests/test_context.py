"""Tests for the live environment context injected into the system prompt."""

from datetime import datetime

from assistant.agent import environment_context, turn_prompt
from assistant.config import Config


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
    config.agent.location = None  # independent of any AG2ASSISTANT_LOCATION env var
    ctx = environment_context(config)
    assert "User location" not in ctx


def test_turn_prompt_includes_persona_and_environment():
    config = Config()
    prompt = turn_prompt(config)
    assert prompt[0] == config.agent.system_prompt
    # behaviour guidance is always injected, even with a custom persona
    assert any("ask how they" in p for p in prompt)
    assert any("Current date and time" in p for p in prompt)


def test_behavior_guidance_survives_custom_persona():
    config = Config()
    config.agent.system_prompt = "You are Jarvis."
    prompt = turn_prompt(config)
    assert prompt[0] == "You are Jarvis."
    # the "don't silently work around failures — ask the user" rule is still there
    joined = " ".join(prompt)
    assert "do not" in joined.lower() or "don't" in joined.lower()
    assert "ask how they" in joined
