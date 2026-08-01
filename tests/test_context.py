"""Tests for the live environment context injected into the system prompt."""

from datetime import datetime

from assistant.agent import (
    chat_turn_timeout_guidance,
    environment_context,
    focuses_guidance,
    turn_prompt,
    universal_memory_guidance,
    universal_turn_prompt,
)
from assistant.config import Config
from assistant.memory import write_universal
from assistant.settings import Settings


def test_environment_context_has_current_year(paths):
    ctx = environment_context(Config.for_paths(paths))
    assert str(datetime.now().year) in ctx
    assert "Current date and time" in ctx


def test_environment_context_includes_location_when_set(paths):
    config = Config.for_paths(paths)
    config.agent.location = "Sydney, Australia"
    ctx = environment_context(config)
    assert "Sydney, Australia" in ctx


def test_environment_context_omits_location_when_unset(paths):
    config = Config.for_paths(paths)
    config.agent.location = None  # independent of any AG2ASSISTANT_LOCATION env var
    ctx = environment_context(config)
    assert "User location" not in ctx


def test_chat_turn_timeout_guidance_uses_configured_budget(paths):
    config = Config.for_paths(paths)
    config.gateway.reply_timeout_s = 480
    guidance = chat_turn_timeout_guidance(config)
    assert "480 seconds" in guidance
    assert "background task" in guidance


def test_universal_turn_prompt_includes_chat_turn_timeout_guidance(paths):
    config = Config.for_paths(paths)
    config.gateway.reply_timeout_s = 480
    assert "480 seconds" in " ".join(universal_turn_prompt(config))


def test_focuses_guidance_omitted_when_unset(paths, tmp_path):
    config = Config.for_paths(paths)
    config.data_dir = tmp_path  # no settings.json → no focuses
    assert focuses_guidance(config) == ""


def test_focuses_guidance_includes_line_when_set(paths, tmp_path):
    config = Config.for_paths(paths)
    config.data_dir = tmp_path
    Settings(tmp_path / "config.yaml").set_focuses(["research", "coding"])
    line = focuses_guidance(config)
    assert "focus areas for this profile" in line
    assert "research, coding" in line


def test_universal_turn_prompt_injects_focuses_when_set(paths, tmp_path):
    config = Config.for_paths(paths)
    config.data_dir = tmp_path
    Settings(tmp_path / "config.yaml").set_focuses(["writing"])
    prompt = universal_turn_prompt(config)
    joined = " ".join(prompt)
    assert "focus areas for this profile: writing" in joined


def test_universal_turn_prompt_omits_focuses_when_unset(paths, tmp_path):
    config = Config.for_paths(paths)
    config.data_dir = tmp_path  # no settings.json
    prompt = universal_turn_prompt(config)
    joined = " ".join(prompt)
    assert "focus areas for this profile" not in joined


async def _seed_universal(root_dir, text):

    await write_universal(text, root_dir / "user.db")


def test_universal_memory_guidance_omitted_when_empty(paths, tmp_path):
    config = Config.for_paths(paths)
    config.root_dir = tmp_path  # no user.db
    assert universal_memory_guidance(config) == ""


async def test_universal_memory_guidance_includes_doc_when_set(paths, tmp_path):
    config = Config.for_paths(paths)
    config.root_dir = tmp_path
    await _seed_universal(tmp_path, "# User profile\n- Name: TestUser")
    section = universal_memory_guidance(config)
    assert "shared across all profiles" in section
    assert "Name: TestUser" in section


async def test_universal_turn_prompt_injects_universal_doc(paths, tmp_path):
    """The universal doc coexists with the per-profile focuses in one prompt."""
    config = Config.for_paths(paths)
    config.root_dir = tmp_path
    config.data_dir = tmp_path
    await _seed_universal(tmp_path, "# User profile\n- Name: TestUser")
    Settings(tmp_path / "config.yaml").set_focuses(["research"])
    prompt = universal_turn_prompt(config)
    joined = " ".join(prompt)
    # both layers present: universal identity facts AND the per-profile focus line
    assert "Name: TestUser" in joined
    assert "shared across all profiles" in joined
    assert "focus areas for this profile: research" in joined


async def test_turn_prompt_injects_universal_doc_when_memory_on(paths, tmp_path):
    config = Config.for_paths(paths)
    config.root_dir = tmp_path
    await _seed_universal(tmp_path, "# User profile\n- Name: TestUser")
    joined = " ".join(turn_prompt(config, memory=True))
    assert "Name: TestUser" in joined
    # with memory off, no universal section is injected
    assert "Name: TestUser" not in " ".join(turn_prompt(config, memory=False))


def test_turn_prompt_includes_persona_and_environment(paths):
    config = Config.for_paths(paths)
    prompt = turn_prompt(config)
    assert prompt[0] == config.agent.system_prompt
    # behaviour guidance is always injected, even with a custom persona
    assert any("ask how they" in p for p in prompt)
    assert any("long-term memory" in p for p in prompt)
    assert any("Current date and time" in p for p in prompt)


def test_turn_prompt_can_omit_memory_guidance(paths):
    prompt = turn_prompt(Config.for_paths(paths), memory=False)
    joined = " ".join(prompt)
    assert "long-term memory" not in joined
    assert "Current date and time" in joined


def test_behavior_guidance_survives_custom_persona(paths):
    config = Config.for_paths(paths)
    config.agent.system_prompt = "You are Jarvis."
    prompt = turn_prompt(config)
    assert prompt[0] == "You are Jarvis."
    # the "don't silently work around failures — ask the user" rule is still there
    joined = " ".join(prompt)
    assert "do not" in joined.lower() or "don't" in joined.lower()
    assert "ask how they" in joined
