"""Tests for AG2 Assistant persistent user-profile memory."""

import pytest
from ag2.knowledge import SqliteKnowledgeStore

import assistant.agent as agent_mod
from assistant.agent import ask
from assistant.config import Config, LLMConfig, load_config
from assistant.memory import (
    PROFILE_PATH,
    build_knowledge_config,
    build_profile_prompt,
    clear_profile,
    read_profile,
    read_profile_sync,
    read_universal,
    write_universal,
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
    single = build_knowledge_config(store_path=tmp_path / "p2.db", every_n_turns=4, on_end=True)
    assert single.aggregate_trigger.on_end is True


def _knowledge_of(agent):
    """The KnowledgeConfig a built agent actually carries (AG2 keeps it here)."""
    return agent._knowledge_context.config


def _aggregate_model(agent) -> str:
    """The model the passive aggregation pass would call."""
    return _knowledge_of(agent).aggregate._config.model


def test_create_agent_single_shot_aggregates_on_end(paths):
    """The CLI single-shot path enables on_end so one turn still gets learned."""
    cfg = Config.for_paths(paths)
    agent = agent_mod.create_agent(cfg, memory=True, skills=False, single_shot=True)
    trigger = _knowledge_of(agent).aggregate_trigger
    assert trigger.on_end is True
    assert trigger.every_n_turns == cfg.memory.aggregate_every_n_turns


def test_long_running_chats_do_not_aggregate_on_end(paths):
    """The counterpart: a normal chat agent batches by turns only, so a closing
    conversation doesn't pay an aggregation call."""
    agent = agent_mod.create_agent(Config.for_paths(paths), memory=True, skills=False)
    assert _knowledge_of(agent).aggregate_trigger.on_end is False


def test_aggregation_uses_cheaper_model_by_default(paths):
    """On Gemini with no explicit aggregate_model, the pass uses the cheaper one."""
    agent = agent_mod.create_agent(Config.for_paths(paths), memory=True, skills=False)
    assert _aggregate_model(agent) == agent_mod._DEFAULT_AGGREGATE_MODEL["gemini"]


def test_explicit_aggregate_model_wins(paths):
    cfg = Config.for_paths(paths, llm=LLMConfig(aggregate_model="gemini-2.5-flash"))
    agent = agent_mod.create_agent(cfg, memory=True, skills=False)
    assert _aggregate_model(agent) == "gemini-2.5-flash"


async def test_read_profile_empty(tmp_path):
    result = await read_profile(store_path=tmp_path / "missing.db")
    assert result == ""


async def test_read_and_clear_profile_roundtrip(tmp_path):

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


async def test_universal_roundtrip_and_sync_read(tmp_path):
    """write_universal persists to user.db; read_universal reads it back; the
    synchronous read_profile_sync (used by the per-turn prompt builders) sees the
    same content, and returns '' for a missing DB."""

    user_db = tmp_path / "user.db"
    assert read_profile_sync(user_db) == ""  # missing file → empty, no raise
    assert await read_universal(user_db) == ""

    doc = "# User profile\n- Name: TestUser"
    await write_universal(doc, user_db)
    assert await read_universal(user_db) == doc
    assert read_profile_sync(user_db) == doc  # sync path matches the async store read


@pytest.mark.integration
async def test_profile_learned_after_conversation(tmp_path):
    """End-to-end: a conversation should produce a persisted profile in this
    profile's store (config.data_dir / profile.db)."""

    config = load_config()
    config.data_dir = tmp_path  # this profile's learned memory lands here
    store_path = tmp_path / "profile.db"

    await ask(
        "Please always keep your answers very short and bulleted. "
        "I hate long paragraphs. I usually work early mornings.",
        config,
        memory=True,
        platform="cli",
    )

    profile = await read_profile(store_path=store_path)
    assert isinstance(profile, str)
    assert len(profile) > 0
