"""AG2 Assistant channel adapters — messaging-platform frontends to the gateway."""

from assistant.channels.base import Channel, InboundMessage, should_respond
from assistant.channels.discord import DiscordChannel
from assistant.channels.slack import SlackChannel
from assistant.channels.telegram import TelegramChannel

__all__ = [
    "Channel",
    "InboundMessage",
    "should_respond",
    "get_channel",
    "CHANNEL_TOKEN_KWARGS",
]

# Token env var name → the constructor kwarg that carries it, so callers can pass
# tokens explicitly instead of relying on the adapters' os.environ fallback.
CHANNEL_TOKEN_KWARGS = {
    "TELEGRAM_BOT_TOKEN": "token",
    "DISCORD_BOT_TOKEN": "token",
    "SLACK_BOT_TOKEN": "bot_token",
    "SLACK_APP_TOKEN": "app_token",
}


def get_channel(platform: str, **kwargs) -> Channel:
    """Construct a channel adapter by platform name."""
    if platform == "telegram":
        return TelegramChannel(**kwargs)
    if platform == "discord":
        return DiscordChannel(**kwargs)
    if platform == "slack":
        return SlackChannel(**kwargs)
    raise ValueError(f"Unknown channel platform: {platform}")
