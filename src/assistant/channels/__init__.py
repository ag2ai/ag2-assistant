"""AG2 Assistant channel adapters — messaging-platform frontends to the gateway."""

import importlib

from assistant.channels.base import Channel, InboundMessage, should_respond
from assistant.channels.router import AvailableProfile, ChannelRouter, Choose, Outcome

# Adapter module per platform; each imports its SDK, so it loads on first use only.
_ADAPTERS = {
    "telegram": ("assistant.channels.telegram", "TelegramChannel"),
    "discord": ("assistant.channels.discord", "DiscordChannel"),
    "slack": ("assistant.channels.slack", "SlackChannel"),
}

# Token env var name → the constructor kwarg that carries it. The adapters never
# read the environment: a Connection stores its tokens under these names and hands
# them over explicitly.
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


def _adapter_class(platform: str) -> type[Channel]:
    """The adapter class for a platform, importing its module on first use."""
    try:
        module_name, class_name = _ADAPTERS[platform]
    except KeyError:
        raise ValueError(f"Unknown channel platform: {platform}") from None
    return getattr(importlib.import_module(module_name), class_name)


def get_channel(platform: str, **kwargs) -> Channel:
    """Construct a channel adapter by platform name, from the token(s) passed in."""
    return _adapter_class(platform)(**kwargs)


def __getattr__(name: str):
    """``assistant.channels.DiscordChannel`` etc. still resolve, imported on demand."""
    for module_name, class_name in _ADAPTERS.values():
        if name == class_name:
            return getattr(importlib.import_module(module_name), class_name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
