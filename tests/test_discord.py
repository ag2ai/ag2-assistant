"""Tests for the Discord adapter — gating/normalization and chunking (no network)."""

from types import SimpleNamespace

import pytest

from assistant.channels.discord import DiscordChannel
from assistant.channels.formatting import split_for_limit


def _discord_channel():

    ch = DiscordChannel(token="fake-token")
    ch._bot_user_id = 999
    return ch


def _fake_message(
    content,
    guild=True,
    channel_id=42,
    author_id=7,
    author_bot=False,
    mentions=(),
    attachments=(),
):
    author = SimpleNamespace(id=author_id, bot=author_bot, display_name="Test User")
    return SimpleNamespace(
        content=content,
        guild=SimpleNamespace(id=1) if guild else None,
        channel=SimpleNamespace(id=channel_id),
        author=author,
        mentions=list(mentions),
        attachments=list(attachments),
    )


def test_normalize_dm():
    ch = _discord_channel()
    inbound = ch._normalize(_fake_message("hello", guild=False))
    assert inbound is not None
    assert inbound.is_direct is True
    assert inbound.stable_id() == "discord:42"


def test_normalize_guild_with_mention_strips_token():
    ch = _discord_channel()
    bot = SimpleNamespace(id=999)
    msg = _fake_message("<@999> what is 2+2?", guild=True, mentions=[bot])
    inbound = ch._normalize(msg)
    assert inbound.is_direct is False
    assert inbound.mentioned is True
    assert "<@999>" not in inbound.text
    assert inbound.text == "what is 2+2?"


def test_normalize_guild_without_mention():
    ch = _discord_channel()
    inbound = ch._normalize(_fake_message("just chatting", guild=True))
    assert inbound.mentioned is False


def test_normalize_ignores_bot_authors():
    ch = _discord_channel()
    assert ch._normalize(_fake_message("hi", author_bot=True)) is None


def test_normalize_ignores_empty_content():
    ch = _discord_channel()
    assert ch._normalize(_fake_message("", guild=False)) is None


def test_normalize_accepts_attachment_only_dm():
    """A DM with no text but an attached file is still a message to handle."""
    ch = _discord_channel()
    att = SimpleNamespace(filename="photo.png", content_type="image/png", size=10)
    inbound = ch._normalize(_fake_message("", guild=False, attachments=[att]))
    assert inbound is not None
    assert inbound.is_direct is True


def test_requires_token(monkeypatch):
    monkeypatch.delenv("DISCORD_BOT_TOKEN", raising=False)

    with pytest.raises(ValueError):
        DiscordChannel()


# --- message chunking (Discord 2000-char limit) ---


def test_split_short_text_single_chunk():
    assert split_for_limit("hello", 2000) == ["hello"]


def test_split_long_text_multiple_chunks():
    text = "\n".join(["line " + str(i) for i in range(1000)])
    chunks = split_for_limit(text, 2000)
    assert len(chunks) > 1
    assert all(len(c) <= 2000 for c in chunks)


def test_split_hard_splits_overlong_line():
    chunks = split_for_limit("x" * 5000, 2000)
    assert all(len(c) <= 2000 for c in chunks)
    assert "".join(chunks) == "x" * 5000
