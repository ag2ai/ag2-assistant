"""Channel abstractions — the common surface every messaging platform implements.

A channel adapter normalises an inbound platform message into an `InboundMessage`,
hands it to the `ChannelRouter`, and renders the outcome that comes back in the
platform's format. Adapters keep platform concerns; the router decides.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal, get_args

from assistant import connections

if TYPE_CHECKING:
    from assistant.channels.router import ChannelRouter, Choose  # type-only (would cycle)

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
    connection: str = ""  # the Connection this message arrived on
    mentioned: bool = False  # was the bot @mentioned (groups)
    has_attachment: bool = False  # did the message carry a file (caption may be empty)
    sender_name: str | None = None
    sender_handle: str | None = None  # platform @handle, when the platform exposes one
    raw: object = field(default=None, repr=False)  # original platform object

    def surface(self) -> str:
        """Which surface of its platform this conversation is — "dm" or "group"."""
        return "dm" if self.is_direct else "group"

    def exposure_surface(self) -> str:
        """The surface a Profile's Channel exposure is read for this message — the
        Connection's own, so two bots of one platform are exposed independently."""
        return connections.surface_key(self.connection, self.platform, self.surface())


def should_respond(msg: InboundMessage) -> bool:
    """Mention-gating: respond to all DMs, but only to @mentions in groups.
    A wordless message counts when it carries a file, and is gated the same way."""
    if not msg.text.strip() and not msg.has_attachment:
        return False
    return msg.is_direct or msg.mentioned


class Channel(ABC):
    """A messaging-platform adapter driven by the channel router.

    One adapter per Connection (ADR 0022) — it is never owned by a profile, and the
    router it is handed decides which runtime each message runs on. The adapter knows
    its own Connection id and stamps it on every message it normalises.
    """

    platform: str
    connection: str
    # The router `start` was handed; None until then.
    _router: "ChannelRouter | None" = None

    @abstractmethod
    async def start(self, router: "ChannelRouter") -> None:
        """Connect to the platform and begin handling messages."""

    def _require_router(self) -> "ChannelRouter":
        """The router this adapter was started with."""
        if self._router is None:
            raise RuntimeError(f"{self.platform} channel is not started")
        return self._router

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

    async def ask(self, chat_id: str, inquiry: str, question: "Choose") -> None:
        """Show a question mirrored from another surface, with its options as buttons,
        keyed by ``inquiry`` so it can be taken back (ADR 0020)."""
        raise NotImplementedError(f"{self.platform} channel cannot show a question")

    async def retract(self, chat_id: str, inquiry: str) -> None:
        """Take back a question shown here — it has been resolved elsewhere."""
        raise NotImplementedError(f"{self.platform} channel cannot show a question")
