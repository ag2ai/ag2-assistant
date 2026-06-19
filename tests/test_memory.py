"""Tests for AG2 Assistant persistent user-profile memory."""

from pathlib import Path

import pytest

from assistant.memory import (
    PROFILE_PATH,
    build_knowledge_config,
    build_profile_prompt,
    clear_profile,
    read_profile,
)


def test_profile_prompt_includes_platform():
    prompt = build_profile_prompt("telegram")
    assert "telegram" in prompt
    # The four tracked dimensions must appear as headings.
    assert "How they like things done" in prompt
    assert "When they like things done" in prompt
    assert "What they dislike" in prompt
    assert "How they write" in prompt


def test_profile_prompt_forbids_preamble():
    # The aggregator must write only the document, not commentary about it.
    prompt = build_profile_prompt("cli").lower()
    assert "only" in prompt and "commentary" in prompt
    assert "preamble" in prompt


def test_profile_prompt_keeps_placeholders():
    # {existing} and {events} must survive as literal placeholders for AG2.
    prompt = build_profile_prompt("cli")
    assert "{existing}" in prompt
    assert "{events}" in prompt
    # The platform braces must have been interpolated, not left literal.
    assert "{platform}" not in prompt


def test_build_knowledge_config_is_passive(tmp_path):
    cfg = build_knowledge_config(platform="cli", store_path=tmp_path / "p.db")
    # Passive: no LLM-facing knowledge tool, no event-log dumping.
    assert cfg.expose_tool is False
    assert cfg.write_event_log is False
    assert cfg.aggregate is not None
    assert cfg.aggregate_trigger is not None


def test_build_knowledge_config_cadence(tmp_path):
    # Default: batch every N turns, don't fire on every turn-end.
    cfg = build_knowledge_config(store_path=tmp_path / "p.db", every_n_turns=4)
    assert cfg.aggregate_trigger.every_n_turns == 4
    assert cfg.aggregate_trigger.on_end is False
    # Single-shot (CLI): capture the one turn on conversation end.
    single = build_knowledge_config(
        store_path=tmp_path / "p2.db", every_n_turns=4, on_end=True
    )
    assert single.aggregate_trigger.on_end is True


def test_create_agent_single_shot_aggregates_on_end(tmp_path, monkeypatch):
    """The CLI single-shot path enables on_end so one turn still gets learned."""
    captured = {}

    import assistant.agent as agent_mod

    real = agent_mod.build_knowledge_config

    def spy(*args, **kwargs):
        captured.update(kwargs)
        return real(*args, **kwargs)

    monkeypatch.setattr(agent_mod, "build_knowledge_config", spy)
    from assistant.config import Config

    cfg = Config()
    agent_mod.create_agent(cfg, memory=True, skills=False, single_shot=True)
    assert captured["on_end"] is True
    assert captured["every_n_turns"] == cfg.memory.aggregate_every_n_turns


def test_aggregation_uses_cheaper_model_by_default(monkeypatch):
    """On Gemini with no explicit aggregate_model, the pass uses the cheaper one."""
    captured = {}
    import assistant.agent as agent_mod

    real = agent_mod.build_knowledge_config

    def spy(*args, **kwargs):
        captured.update(kwargs)
        return real(*args, **kwargs)

    monkeypatch.setattr(agent_mod, "build_knowledge_config", spy)
    from assistant.config import Config

    agent_mod.create_agent(Config(), memory=True, skills=False)
    assert (
        captured["aggregate_config"].model
        == agent_mod._DEFAULT_AGGREGATE_MODEL["gemini"]
    )


def test_explicit_aggregate_model_wins(monkeypatch):
    captured = {}
    import assistant.agent as agent_mod
    from assistant.config import Config, LLMConfig

    real = agent_mod.build_knowledge_config
    monkeypatch.setattr(
        agent_mod, "build_knowledge_config",
        lambda *a, **k: (captured.update(k), real(*a, **k))[1],
    )
    cfg = Config(llm=LLMConfig(aggregate_model="gemini-2.5-flash"))
    agent_mod.create_agent(cfg, memory=True, skills=False)
    assert captured["aggregate_config"].model == "gemini-2.5-flash"


async def test_read_profile_empty(tmp_path):
    result = await read_profile(store_path=tmp_path / "missing.db")
    assert result == ""


async def test_read_and_clear_profile_roundtrip(tmp_path):
    from autogen.beta.knowledge import SqliteKnowledgeStore

    store_path = tmp_path / "profile.db"
    store = SqliteKnowledgeStore(str(store_path))
    await store.write(PROFILE_PATH, "## How they like things done\nConcise answers.")

    text = await read_profile(store_path=store_path)
    assert "Concise answers" in text

    cleared = await clear_profile(store_path=store_path)
    assert cleared is True
    assert await read_profile(store_path=store_path) == ""

    # Clearing again is a no-op.
    assert await clear_profile(store_path=store_path) is False


@pytest.mark.integration
async def test_profile_learned_after_conversation(tmp_path, monkeypatch):
    """End-to-end: a conversation should produce a persisted profile."""
    import assistant.memory as memory_mod

    store_path = tmp_path / "profile.db"
    monkeypatch.setattr(memory_mod, "default_store_path", lambda: store_path)

    from assistant.agent import ask

    await ask(
        "Please always keep your answers very short and bulleted. "
        "I hate long paragraphs. I usually work early mornings.",
        memory=True,
        platform="cli",
    )

    profile = await read_profile(store_path=store_path)
    assert isinstance(profile, str)
    assert len(profile) > 0
