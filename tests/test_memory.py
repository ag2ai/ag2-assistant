"""Tests for AGClaw persistent user-profile memory."""

from pathlib import Path

import pytest

from agclaw.memory import (
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
    import agclaw.memory as memory_mod

    store_path = tmp_path / "profile.db"
    monkeypatch.setattr(memory_mod, "default_store_path", lambda: store_path)

    from agclaw.agent import ask

    await ask(
        "Please always keep your answers very short and bulleted. "
        "I hate long paragraphs. I usually work early mornings.",
        memory=True,
        platform="cli",
    )

    profile = await read_profile(store_path=store_path)
    assert isinstance(profile, str)
    assert len(profile) > 0
