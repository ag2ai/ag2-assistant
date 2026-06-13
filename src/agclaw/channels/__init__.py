"""AGClaw channel adapters — messaging-platform frontends to the gateway."""

from agclaw.channels.base import Channel, InboundMessage, should_respond

__all__ = ["Channel", "InboundMessage", "should_respond", "get_channel"]


def get_channel(platform: str, **kwargs) -> Channel:
    """Construct a channel adapter by platform name."""
    if platform == "telegram":
        from agclaw.channels.telegram import TelegramChannel

        return TelegramChannel(**kwargs)
    if platform == "discord":
        from agclaw.channels.discord import DiscordChannel

        return DiscordChannel(**kwargs)
    if platform == "slack":
        from agclaw.channels.slack import SlackChannel

        return SlackChannel(**kwargs)
    raise ValueError(f"Unknown channel platform: {platform}")
