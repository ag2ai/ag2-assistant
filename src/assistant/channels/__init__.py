"""AG2 Assistant channel adapters — messaging-platform frontends to the gateway."""

from assistant.channels.base import Channel, InboundMessage, should_respond
from assistant.channels.discord import DiscordChannel
from assistant.channels.router import ChannelRouter, Outcome
from assistant.channels.slack import SlackChannel
from assistant.channels.telegram import TelegramChannel

__all__ = [
    "Channel",
    "ChannelRouter",
    "InboundMessage",
    "Outcome",
    "should_respond",
    "get_channel",
]


def get_channel(platform: str, **kwargs) -> Channel:
    """Construct a channel adapter by platform name."""
    if platform == "telegram":
        return TelegramChannel(**kwargs)
    if platform == "discord":
        return DiscordChannel(**kwargs)
    if platform == "slack":
        return SlackChannel(**kwargs)
    raise ValueError(f"Unknown channel platform: {platform}")
