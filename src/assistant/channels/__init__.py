"""AG2 Assistant channel adapters — messaging-platform frontends to the gateway."""

from collections.abc import Mapping

from assistant.channels.base import Channel, InboundMessage, should_respond
from assistant.channels.discord import DiscordChannel
from assistant.channels.slack import SlackChannel
from assistant.channels.telegram import TelegramChannel
from assistant.profiles import CHANNEL_TOKEN_ENVS

__all__ = [
    "Channel",
    "InboundMessage",
    "should_respond",
    "get_channel",
    "channel_token_kwargs",
    "CHANNEL_TOKEN_KWARGS",
]

# Token env var name → the constructor kwarg that carries it. The adapters never
# read the environment: whoever builds one hands the tokens over explicitly.
CHANNEL_TOKEN_KWARGS = {
    "TELEGRAM_BOT_TOKEN": "token",
    "DISCORD_BOT_TOKEN": "token",
    "SLACK_BOT_TOKEN": "bot_token",
    "SLACK_APP_TOKEN": "app_token",
}


def channel_token_kwargs(platform: str, env: Mapping[str, str]) -> dict[str, str]:
    """This platform's tokens as constructor kwargs, read from a resolved env."""
    return {CHANNEL_TOKEN_KWARGS[name]: env.get(name, "") for name in CHANNEL_TOKEN_ENVS[platform]}


def get_channel(platform: str, **kwargs) -> Channel:
    """Construct a channel adapter by platform name."""
    if platform == "telegram":
        return TelegramChannel(**kwargs)
    if platform == "discord":
        return DiscordChannel(**kwargs)
    if platform == "slack":
        return SlackChannel(**kwargs)
    raise ValueError(f"Unknown channel platform: {platform}")
