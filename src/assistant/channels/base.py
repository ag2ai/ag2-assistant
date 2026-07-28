"""Channel abstractions — the common surface every messaging platform implements.

A channel adapter normalises an inbound platform message into an `InboundMessage`,
hands it to the `ChannelRouter`, and renders the outcome that comes back in the
platform's format. Adapters keep platform concerns; the router decides.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal, get_args

if TYPE_CHECKING:
    from assistant.channels.router import ChannelRouter  # type-only (would cycle)

# Platforms whose adapters can push an unsolicited message (task-run outcomes
# delivered back to the chat a task came from). Single source of truth: the
# tuple is derived from the Literal, so the two can't drift.
PushChannel = Literal["telegram", "discord", "slack"]
PUSH_CHANNELS: tuple[PushChannel, ...] = get_args(PushChannel)


@dataclass
class InboundMessage:
    """A normalised inbound message, platform-agnostic."""

    text: str
    sender_id: str  # platform user id
    chat_id: str  # platform chat/conversation id
    platform: str  # "telegram", "discord", ...
    is_direct: bool  # True for DMs, False for group/channel
    mentioned: bool = False  # was the bot @mentioned (groups)
    sender_name: str | None = None
    raw: object = field(default=None, repr=False)  # original platform object

    def stable_id(self) -> str:
        """Stable chat id — one isolated conversation per channel chat.

        Named distinctly from ``chat_id`` (the platform's own chat/conversation id,
        above) to avoid shadowing that dataclass field: this is the gateway-facing id
        (``{platform}:{chat_id}``) passed as ``chat_id=`` to ``send_message``."""
        return f"{self.platform}:{self.chat_id}"


def should_respond(msg: InboundMessage) -> bool:
    """Mention-gating: respond to all DMs, but only to @mentions in groups."""
    if not msg.text.strip():
        return False
    return msg.is_direct or msg.mentioned


class Channel(ABC):
    """A messaging-platform adapter driven by the channel router.

    One adapter per platform per install (ADR 0019) — it is never owned by a
    profile, and the router it is handed decides which runtime each message runs on.
    """

    platform: str

    @abstractmethod
    async def start(self, router: "ChannelRouter") -> None:
        """Connect to the platform and begin handling messages."""

    @abstractmethod
    async def stop(self) -> None:
        """Disconnect and clean up."""

    def format_outbound(self, text: str) -> str:
        """Render the agent's reply for this platform. Default: unchanged."""
        return text

    async def notify(self, chat_id: str, text: str) -> None:
        """Push an unsolicited message to a platform chat (task-run outcomes).
        Override per platform; the default says this channel can't push."""
        raise NotImplementedError(f"{self.platform} channel cannot push messages")
