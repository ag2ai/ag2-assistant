"""Tests for the channel layer — gating and Telegram normalization (no network)."""

from types import SimpleNamespace

import pytest

from agclaw.channels.base import InboundMessage, should_respond


def _msg(text="hi", is_direct=True, mentioned=False) -> InboundMessage:
    return InboundMessage(
        text=text,
        sender_id="u1",
        chat_id="c1",
        platform="telegram",
        is_direct=is_direct,
        mentioned=mentioned,
    )


def test_session_id_is_per_chat():
    assert _msg().session_id() == "telegram:c1"


def test_should_respond_dm():
    assert should_respond(_msg(is_direct=True)) is True


def test_should_respond_group_without_mention():
    assert should_respond(_msg(is_direct=False, mentioned=False)) is False


def test_should_respond_group_with_mention():
    assert should_respond(_msg(is_direct=False, mentioned=True)) is True


def test_should_respond_empty_text():
    assert should_respond(_msg(text="   ", is_direct=True)) is False


# --- Telegram normalization (fake Update objects) ---


def _telegram_channel():
    from agclaw.channels.telegram import TelegramChannel

    ch = TelegramChannel(token="fake-token")
    ch._bot_username = "agclawbot"
    ch._bot_id = 999
    return ch


def _fake_update(text, chat_type="private", chat_id=42, user_id=7, reply_to_bot=False):
    chat = SimpleNamespace(type=chat_type, PRIVATE="private", id=chat_id)
    from_user = SimpleNamespace(id=user_id, full_name="Test User")
    reply_to = None
    if reply_to_bot:
        reply_to = SimpleNamespace(from_user=SimpleNamespace(id=999))
    message = SimpleNamespace(
        text=text, chat=chat, from_user=from_user, reply_to_message=reply_to
    )
    return SimpleNamespace(message=message)


def test_normalize_dm():
    ch = _telegram_channel()
    inbound = ch._normalize(_fake_update("hello", chat_type="private"))
    assert inbound is not None
    assert inbound.is_direct is True
    assert inbound.mentioned is False
    assert inbound.text == "hello"
    assert inbound.session_id() == "telegram:42"


def test_normalize_group_with_mention_strips_handle():
    ch = _telegram_channel()
    inbound = ch._normalize(
        _fake_update("@agclawbot what is 2+2?", chat_type="supergroup")
    )
    assert inbound.is_direct is False
    assert inbound.mentioned is True
    assert "@agclawbot" not in inbound.text
    assert inbound.text == "what is 2+2?"


def test_normalize_group_without_mention():
    ch = _telegram_channel()
    inbound = ch._normalize(_fake_update("just chatting", chat_type="group"))
    assert inbound.is_direct is False
    assert inbound.mentioned is False


def test_normalize_group_reply_to_bot_counts_as_mention():
    ch = _telegram_channel()
    inbound = ch._normalize(
        _fake_update("thanks!", chat_type="group", reply_to_bot=True)
    )
    assert inbound.mentioned is True


def test_normalize_ignores_non_text():
    ch = _telegram_channel()
    update = SimpleNamespace(message=SimpleNamespace(text=None))
    assert ch._normalize(update) is None


def test_telegram_requires_token(monkeypatch):
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    from agclaw.channels.telegram import TelegramChannel

    with pytest.raises(ValueError):
        TelegramChannel()
