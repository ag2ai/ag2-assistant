"""Tests for the Slack adapter — event parsing and gating (no network)."""

import pytest


def _slack_channel():
    from assistant.channels.slack import SlackChannel

    ch = SlackChannel(bot_token="xoxb-fake", app_token="xapp-fake")
    ch._bot_user_id = "UBOT"
    return ch


def test_requires_both_tokens(monkeypatch):
    monkeypatch.delenv("SLACK_BOT_TOKEN", raising=False)
    monkeypatch.delenv("SLACK_APP_TOKEN", raising=False)
    from assistant.channels.slack import SlackChannel

    with pytest.raises(ValueError):
        SlackChannel()


def test_mention_inbound_strips_bot_token():
    ch = _slack_channel()
    event = {"text": "<@UBOT> what is 2+2?", "user": "U123", "channel": "C1"}
    inbound = ch._mention_inbound(event)
    assert inbound is not None
    assert inbound.mentioned is True
    assert inbound.is_direct is False
    assert inbound.text == "what is 2+2?"
    assert inbound.session_id() == "slack:C1"


def test_mention_inbound_empty_after_strip_is_none():
    ch = _slack_channel()
    assert ch._mention_inbound({"text": "<@UBOT>", "channel": "C1"}) is None


def test_dm_inbound_basic():
    ch = _slack_channel()
    event = {
        "channel_type": "im",
        "text": "hello",
        "user": "U123",
        "channel": "D1",
    }
    inbound = ch._dm_inbound(event)
    assert inbound is not None
    assert inbound.is_direct is True
    assert inbound.session_id() == "slack:D1"


def test_dm_inbound_ignores_non_im():
    ch = _slack_channel()
    event = {"channel_type": "channel", "text": "hi", "user": "U123", "channel": "C1"}
    assert ch._dm_inbound(event) is None


def test_dm_inbound_ignores_own_messages():
    ch = _slack_channel()
    event = {"channel_type": "im", "text": "hi", "user": "UBOT", "channel": "D1"}
    assert ch._dm_inbound(event) is None


def test_dm_inbound_ignores_bot_and_subtype():
    ch = _slack_channel()
    assert (
        ch._dm_inbound({"channel_type": "im", "text": "hi", "bot_id": "B1", "channel": "D1"})
        is None
    )
    assert (
        ch._dm_inbound(
            {"channel_type": "im", "text": "edited", "subtype": "message_changed", "channel": "D1"}
        )
        is None
    )
