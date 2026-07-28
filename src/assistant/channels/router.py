"""The channel router — the platform-neutral seam between adapters and runtimes.

An adapter normalises an inbound platform message, hands it to the router, and
renders the outcome that comes back. Every decision about what a message means
lives here; adapters keep only platform concerns.
"""

from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

from assistant.channels.base import InboundMessage, should_respond

if TYPE_CHECKING:
    from assistant.gateway.core import Gateway  # type-only (runtime import would cycle)

# A bare attachment carries no words, so the model is given a prompt for it.
ATTACHMENT_ONLY_PROMPT = "Here is a file I'm sharing with you."

# Said when a message has no profile to land in — the channel's default profile is
# unset, or the profile it names is no longer running.
NO_PROFILE = (
    "No profile is set up for this channel yet. "
    "Pick a default profile for it in Settings → Channels."
)


@dataclass(frozen=True)
class Reply:
    """Text to send back to the conversation the message came from."""

    text: str


@dataclass(frozen=True)
class Option:
    """One choice in a `Choose`: the label a user reads, and the token they send back."""

    label: str
    token: str


@dataclass(frozen=True)
class Choose:
    """A prompt answered by picking one of the options."""

    text: str
    options: tuple[Option, ...]


@dataclass(frozen=True)
class Refuse:
    """The message will not be handled, and why."""

    text: str


@dataclass(frozen=True)
class Ack:
    """The message was taken, with nothing to say back."""


@dataclass(frozen=True)
class Nothing:
    """No response at all — the adapter stays silent."""


Outcome = Reply | Choose | Refuse | Ack | Nothing

NOTHING = Nothing()


def spoken_text(outcome: Outcome) -> str | None:
    """The plain text an outcome says back, or None when it says nothing.
    `Choose` is excluded — it needs its options rendered, not just its text."""
    return outcome.text if isinstance(outcome, Reply | Refuse) else None


class ChannelRouter:
    """Turns a normalised inbound message into a platform-neutral outcome.

    Built once per install and shared by every adapter (ADR 0019): the runtime a
    message runs on is resolved when the message arrives, not captured at start.
    """

    def __init__(self, resolve_gateway: "Callable[[InboundMessage], Gateway | None]") -> None:
        self._resolve_gateway = resolve_gateway

    def accepts(self, inbound: InboundMessage) -> bool:
        """Whether this message will be handled at all — an adapter's gate for
        showing platform feedback before the slow path."""
        return should_respond(inbound)

    async def handle(
        self,
        inbound: InboundMessage,
        *,
        asker=None,
        attachments: list | None = None,
    ) -> Outcome:
        """Run ``inbound`` and return what the adapter should render."""
        if not self.accepts(inbound):
            return NOTHING

        gateway = self._resolve_gateway(inbound)
        if gateway is None:
            return Refuse(NO_PROFILE)

        text = inbound.text or (ATTACHMENT_ONLY_PROMPT if attachments else "")
        try:
            reply = await gateway.send_message(
                text,
                chat_id=inbound.stable_id(),
                asker=asker,
                attachments=attachments or [],
            )
        except Exception as exc:  # surface failures to the user
            return Reply(f"Sorry, something went wrong: {exc}")
        return Reply(reply)
