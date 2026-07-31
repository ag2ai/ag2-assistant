"""AG2 Assistant channel adapters — messaging-platform frontends to the gateway."""

from assistant.channels.base import Channel, InboundMessage, should_respond
from assistant.channels.discord import DiscordChannel
from assistant.channels.router import AvailableProfile, ChannelRouter, Choose, Outcome
from assistant.channels.slack import SlackChannel
from assistant.channels.telegram import TelegramChannel

# Token env-var name → the constructor argument the platform's adapter takes it as.
# A Connection stores its tokens under these names and is handed them explicitly.
TOKEN_ARGS = {
    "TELEGRAM_BOT_TOKEN": "token",
    "DISCORD_BOT_TOKEN": "token",
    "SLACK_BOT_TOKEN": "bot_token",
    "SLACK_APP_TOKEN": "app_token",
}

__all__ = [
    "AvailableProfile",
    "Channel",
    "ChannelRouter",
    "Choose",
    "InboundMessage",
    "Outcome",
    "should_respond",
    "get_channel",
    "TOKEN_ARGS",
]


def get_channel(platform: str, **kwargs) -> Channel:
    """Construct a channel adapter by platform name, from the token(s) passed in."""
    if platform == "telegram":
        return TelegramChannel(**kwargs)
    if platform == "discord":
        return DiscordChannel(**kwargs)
    if platform == "slack":
        return SlackChannel(**kwargs)
    raise ValueError(f"Unknown channel platform: {platform}")
