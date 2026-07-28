"""Channel.notify pushes an unsolicited message to a platform chat."""

from types import SimpleNamespace

import pytest

from assistant.channels.base import Channel


async def test_base_notify_raises_by_default():
    class Dumb(Channel):
        platform = "dumb"

        async def start(self, router):
            pass

        async def stop(self):
            pass

    with pytest.raises(NotImplementedError):
        await Dumb().notify("1", "hi")


# --- Telegram: mirrors the reply path's `self._app.bot.send_message`, formatted
# via `format_outbound` (markdown_to_plain) and chunked by
# `split_for_limit(..., TELEGRAM_LIMIT)`. ---


async def test_telegram_notify_sends_formatted_text_via_bot():
    from assistant.channels.telegram import TelegramChannel

    ch = TelegramChannel(token="fake-token")
    sent = []

    class FakeBot:
        async def send_message(self, chat_id, text):
            sent.append((chat_id, text))

    ch._app = SimpleNamespace(bot=FakeBot())
    await ch.notify("42", "**bold**")

    assert sent == [(42, "bold")]  # chat_id cast to int; markdown stripped


async def test_telegram_notify_chunks_long_text(monkeypatch):
    import assistant.channels.telegram as telegram_mod

    monkeypatch.setattr(telegram_mod, "TELEGRAM_LIMIT", 15)
    ch = telegram_mod.TelegramChannel(token="fake-token")
    sent = []

    class FakeBot:
        async def send_message(self, chat_id, text):
            sent.append((chat_id, text))

    ch._app = SimpleNamespace(bot=FakeBot())
    await ch.notify("42", "First part.\n\nSecond part.")

    assert sent == [(42, "First part."), (42, "Second part.")]


# --- Discord: mirrors `self._client.get_channel(...) or fetch_channel(...)` plus
# the reply path's `split_for_limit(..., DISCORD_LIMIT)` chunking loop. ---


async def test_discord_notify_sends_via_cached_channel():
    from assistant.channels.discord import DiscordChannel

    ch = DiscordChannel(token="fake-token")
    sent = []

    class FakeChannel:
        async def send(self, text):
            sent.append(text)

    class FakeClient:
        def get_channel(self, cid):
            assert cid == 99
            return FakeChannel()

    ch._client = FakeClient()
    await ch.notify("99", "hello world")

    assert sent == ["hello world"]


async def test_discord_notify_fetches_channel_when_not_cached():
    from assistant.channels.discord import DiscordChannel

    ch = DiscordChannel(token="fake-token")
    sent = []

    class FakeChannel:
        async def send(self, text):
            sent.append(text)

    class FakeClient:
        def get_channel(self, cid):
            return None

        async def fetch_channel(self, cid):
            return FakeChannel()

    ch._client = FakeClient()
    await ch.notify("99", "hello")

    assert sent == ["hello"]


async def test_discord_notify_chunks_long_text(monkeypatch):
    import assistant.channels.discord as discord_mod

    monkeypatch.setattr(discord_mod, "DISCORD_LIMIT", 5)
    ch = discord_mod.DiscordChannel(token="fake-token")
    sent = []

    class FakeChannel:
        async def send(self, text):
            sent.append(text)

    class FakeClient:
        def get_channel(self, cid):
            return FakeChannel()

    ch._client = FakeClient()
    await ch.notify("1", "one two")

    assert sent == ["one t", "wo"]  # split_for_limit hard-splits at the 5-char limit


# --- Slack: mirrors `self._app.client.chat_postMessage`, formatted via
# `format_outbound` (markdown_to_slack) and chunked by `split_for_limit(..., SLACK_LIMIT)`. ---


async def test_slack_notify_sends_formatted_text_via_app_client():
    from assistant.channels.slack import SlackChannel

    ch = SlackChannel(bot_token="xoxb-fake", app_token="xapp-fake")
    posted = []

    class FakeClient:
        async def chat_postMessage(self, channel, text):
            posted.append((channel, text))

    ch._app = SimpleNamespace(client=FakeClient())
    await ch.notify("C123", "**bold**")

    assert posted == [("C123", "*bold*")]  # markdown_to_slack: ** -> *


async def test_slack_notify_chunks_long_text(monkeypatch):
    import assistant.channels.slack as slack_mod

    monkeypatch.setattr(slack_mod, "SLACK_LIMIT", 5)
    ch = slack_mod.SlackChannel(bot_token="xoxb-fake", app_token="xapp-fake")
    posted = []

    class FakeClient:
        async def chat_postMessage(self, channel, text):
            posted.append((channel, text))

    ch._app = SimpleNamespace(client=FakeClient())
    await ch.notify("C123", "one two")

    assert posted == [("C123", "one t"), ("C123", "wo")]
