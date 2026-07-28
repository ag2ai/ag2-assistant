"""The channel router — the platform-neutral seam between adapters and runtimes.

An adapter normalises an inbound platform message, hands it to the router, and
renders the outcome that comes back. Every decision about what a message means
lives here; adapters keep only platform concerns.
"""

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

from assistant import pairing, peers
from assistant.channels.base import InboundMessage, should_respond

if TYPE_CHECKING:
    from assistant.gateway.core import Gateway  # type-only (runtime import would cycle)

# A bare attachment carries no words, so the model is given a prompt for it.
ATTACHMENT_ONLY_PROMPT = "Here is a file I'm sharing with you."

# Said for a bare attachment whose download brought nothing back.
ATTACHMENT_UNREADABLE = "I sent you a file, but it could not be read."

# Platforms with a command surface. Discord and Slack have none (ADR 0019): they sit
# in their Channel's default profile, and a leading slash there is just words.
COMMAND_PLATFORMS = ("telegram",)

# Said when a message has no profile to land in.
NO_PROFILE = (
    "No profile is set up for this channel yet. "
    "Pick a default profile for it in Settings → Channels."
)

CHOOSE_PROFILE = "Which profile should I use for this conversation?"

# Said to whoever has just paired. Deliberately says nothing about the install yet —
# the next message is the one that gets a profile and a turn.
PAIRED = "You're paired. Send me anything to get started."

# The one thing an unpaired account is ever told, and only when it presents a code
# that was plainly issued to it (ADR 0021).
CODE_EXPIRED = "That pairing code has expired. Generate a new one in Settings → Channels."

# A group's profile is not the group's to change — it is set from the WebUI.
PROFILE_IN_GROUP = "/profile only works in a direct message. Set a group's profile in Settings."

NEW_CHAT = "Started a fresh chat. The one you were in is still in your chat list."
ALREADY_NEW = "You're already in a fresh chat — nothing has been said in it yet."
CONFIRM_CLEAR = "Delete this chat permanently? Its whole transcript goes with it."
CLEARED = "Deleted. Your next message starts a fresh chat."
KEPT = "Left it as it was."
NOTHING_TO_CLEAR = "There's no chat to delete yet — nothing has been said here."
NO_CHAT_YET = "no chat yet"
STALE_OPTION = "That option has expired. Send /help for what I can do."


@dataclass(frozen=True)
class Command:
    """One command a Peer can send, and the one-liner that describes it."""

    name: str
    description: str


COMMANDS = (
    Command("new", "Start a fresh chat"),
    Command("clear", "Delete this chat permanently"),
    Command("status", "Show the profile and chat you're in"),
    Command("profile", "Choose which profile to talk to"),
    Command("help", "List these commands"),
)

# An option token names the picker that offered it; a delete also names the Chat it
# was raised for, so a tap can only ever act on what the user was shown.
PROFILE_TOKEN = "profile:"
CLEAR_TOKEN = "clear:"
KEEP_TOKEN = "keep:"


def unknown_profile(name: str) -> str:
    return f"There is no profile called '{name}'. Send /profile to pick from the list."


def unknown_command(name: str) -> str:
    return f"I don't know the command /{name}. Send /help for what I do know."


def help_text() -> str:
    return "\n".join(f"/{c.name} — {c.description}" for c in COMMANDS)


def status_text(profile: str, title: str, turns: int) -> str:
    return f"Profile: {profile}\nChat: {title}\nSize: {turns} exchanges"


def switched_to(name: str) -> str:
    return f"Now talking to {name}, in a new chat."


def already_in(name: str) -> str:
    return f"Already talking to {name}."


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


@dataclass(frozen=True)
class AvailableProfile:
    """A profile a conversation can be pointed at: its id and the name a user reads."""

    id: str
    name: str


class ProfileDirectory(Protocol):
    """What the router knows about profiles. Every answer is read fresh per message."""

    def available_profiles(self) -> tuple[AvailableProfile, ...]:
        """Every profile a conversation could be pointed at right now."""

    def default_profile(self, platform: str) -> str | None:
        """The Channel's default profile id, or None when it has none available."""

    def gateway_for_profile(self, pid: str) -> "Gateway | None":
        """The running gateway for a profile id, or None when it is not running."""


def spoken_text(outcome: Outcome) -> str | None:
    """The plain text an outcome says back, or None when it says nothing.
    `Choose` is excluded — it needs its options rendered, not just its text."""
    return outcome.text if isinstance(outcome, Reply | Refuse) else None


def parse_command(text: str) -> tuple[str, str] | None:
    """Split ``/name rest`` into (name, rest), or None when this isn't a command.
    A command addressed to a specific bot (``/profile@thebot``) loses the handle."""
    stripped = text.strip()
    if not stripped.startswith("/") or len(stripped) < 2:
        return None
    head, _, rest = stripped[1:].partition(" ")
    name, _, _handle = head.partition("@")
    return name.lower(), rest.strip()


class ChannelRouter:
    """Turns a normalised inbound message into a platform-neutral outcome.

    Built once per install and shared by every adapter (ADR 0019): the runtime a
    message runs on is resolved when the message arrives, not captured at start.
    """

    def __init__(self, directory: ProfileDirectory) -> None:
        self._directory = directory

    def accepts(self, inbound: InboundMessage) -> bool:
        """Whether this message runs a turn — an adapter's gate for showing platform
        feedback before the slow path. An unpaired account never opens it, so no
        placeholder betrays that anything is listening; its message still goes to
        ``handle``, which may pair it."""
        return should_respond(inbound) and self.paired(inbound)

    # ---- who may be served (ADR 0021) ----

    def paired(self, inbound: InboundMessage) -> bool:
        """Whether this account may be served — pinning a pending handle it presents,
        so an invitation becomes an identity on first contact. Adapters call this
        before acting on anything a message implies, taps and answers included."""
        return pairing.is_paired(inbound.platform, inbound.sender_id, inbound.sender_handle)

    def _pair(self, inbound: InboundMessage) -> Outcome:
        """Redeem the code an unpaired account has sent. A code it was never given is
        met with the same silence as anything else it might say."""
        outcome = pairing.redeem(
            inbound.platform, inbound.text, inbound.sender_id, inbound.sender_handle
        )
        if outcome == pairing.PAIRED:
            return Reply(PAIRED)
        if outcome == pairing.EXPIRED:
            return Refuse(CODE_EXPIRED)
        return NOTHING

    # ---- profile selection ----

    def _has_commands(self, inbound: InboundMessage) -> bool:
        """Whether this platform can be asked anything — commands and pickers alike."""
        return inbound.platform in COMMAND_PLATFORMS

    def _by_id(self) -> dict[str, AvailableProfile]:
        return {p.id: p for p in self._directory.available_profiles()}

    def _ask_which_profile(self, by_id: dict[str, AvailableProfile]) -> Choose:
        return Choose(
            CHOOSE_PROFILE,
            tuple(Option(p.name, f"{PROFILE_TOKEN}{p.id}") for p in by_id.values()),
        )

    def _select(self, inbound: InboundMessage, pid: str) -> None:
        """Record this Peer's profile, leaving an unchanged selection alone."""
        peer = peers.get_peer(inbound.platform, inbound.chat_id)
        if peer is not None and peer.profile == pid:
            return  # nothing moved; don't rewrite the registry on every message
        peers.select_profile(inbound.platform, inbound.chat_id, pid, surface=inbound.surface())

    def _current_profile(
        self, inbound: InboundMessage, by_id: dict[str, AvailableProfile]
    ) -> str | None:
        """The profile this Peer is talking to right now, without choosing one for it.
        Its own selection, else the Channel's default, else the sole running profile."""
        peer = peers.get_peer(inbound.platform, inbound.chat_id)
        if peer is not None and peer.profile in by_id:
            return peer.profile
        # The Channel default is a live fallback, never a stored selection: changing it
        # moves every Peer that has not chosen for itself.
        default = self._directory.default_profile(inbound.platform)
        if default in by_id:
            return default
        # Only a platform that can be asked may be placed without asking — the others
        # have their Channel default and no way to correct a selection made for them.
        if len(by_id) == 1 and self._has_commands(inbound):
            return next(iter(by_id))
        return None

    def _switch_to(self, inbound: InboundMessage, profile: AvailableProfile) -> Outcome:
        """Point this Peer at ``profile`` and say what happened. An explicit choice is
        recorded even when it names the profile the Channel default already gives."""
        moved = self._current_profile(inbound, self._by_id()) != profile.id
        self._select(inbound, profile.id)
        return Reply(switched_to(profile.name) if moved else already_in(profile.name))

    def _resolve(self, inbound: InboundMessage) -> str | Outcome:
        """The profile id this message runs in, or the outcome to return instead."""
        by_id = self._by_id()
        current = self._current_profile(inbound, by_id)
        if current is not None:
            if len(by_id) == 1:
                # Place the Peer in the only profile there is, rather than leave it on
                # a fallback that adding a second profile would silently change.
                self._select(inbound, current)
            return current

        if by_id and self._has_commands(inbound) and inbound.is_direct:
            return self._ask_which_profile(by_id)
        return Refuse(NO_PROFILE)

    async def choose(self, inbound: InboundMessage, token: str) -> Outcome:
        """Apply an option token sent back from a `Choose`."""
        if not self.paired(inbound):
            return NOTHING
        if token.startswith(PROFILE_TOKEN):
            profile = self._by_id().get(token.removeprefix(PROFILE_TOKEN))
            if profile is None:
                return Refuse(NO_PROFILE)
            return self._switch_to(inbound, profile)
        if token.startswith(KEEP_TOKEN):
            return Reply(KEPT)
        if token.startswith(CLEAR_TOKEN):
            return await self._delete_chat(inbound, token.removeprefix(CLEAR_TOKEN))
        return Refuse(STALE_OPTION)

    # ---- the Chat a Peer is in ----

    def _attached_chat(self, inbound: InboundMessage) -> str | None:
        peer = peers.get_peer(inbound.platform, inbound.chat_id)
        return peer.chat if peer is not None else None

    def _chat_for(self, inbound: InboundMessage) -> str:
        """The Chat this Peer speaks in, started on first use."""
        return self._attached_chat(inbound) or peers.start_chat(
            inbound.platform, inbound.chat_id, surface=inbound.surface()
        )

    async def _delete_chat(self, inbound: InboundMessage, chat: str) -> Outcome:
        """Delete the Chat the confirmation was raised for, and only that one."""
        if chat != self._attached_chat(inbound):
            return Refuse(STALE_OPTION)
        resolved = self._resolve(inbound)
        if not isinstance(resolved, str):
            return resolved
        gateway = self._directory.gateway_for_profile(resolved)
        if gateway is None:
            return Refuse(NO_PROFILE)
        await gateway.delete_chat(chat)
        peers.forget_chat(chat)
        return Reply(CLEARED)

    # ---- commands ----

    async def _profile_command(self, inbound: InboundMessage, arg: str) -> Outcome:
        if not inbound.is_direct:
            return Refuse(PROFILE_IN_GROUP)

        by_id = self._by_id()
        if not by_id:
            return Refuse(NO_PROFILE)
        if not arg:
            return self._ask_which_profile(by_id)

        match = next(
            (p for p in by_id.values() if arg.casefold() in (p.id.casefold(), p.name.casefold())),
            None,
        )
        if match is None:
            return Refuse(unknown_profile(arg))
        return self._switch_to(inbound, match)

    async def _new_command(self, inbound: InboundMessage, arg: str) -> Outcome:
        if self._attached_chat(inbound) is None:
            return Reply(ALREADY_NEW)
        peers.detach(inbound.platform, inbound.chat_id)
        return Reply(NEW_CHAT)

    async def _clear_command(self, inbound: InboundMessage, arg: str) -> Outcome:
        chat = self._attached_chat(inbound)
        if chat is None:
            return Reply(NOTHING_TO_CLEAR)
        return Choose(
            CONFIRM_CLEAR,
            (Option("Delete it", f"{CLEAR_TOKEN}{chat}"), Option("Keep it", KEEP_TOKEN)),
        )

    async def _status_command(self, inbound: InboundMessage, arg: str) -> Outcome:
        resolved = self._resolve(inbound)
        if not isinstance(resolved, str):
            return resolved
        name = self._by_id()[resolved].name
        chat = self._attached_chat(inbound)
        gateway = self._directory.gateway_for_profile(resolved)
        entry = None
        if chat is not None and gateway is not None:
            listing = await gateway.list_chats()
            entry = next((e for e in listing if e.get("chat_id") == chat), None)
        if entry is None:
            return Reply(status_text(name, NO_CHAT_YET, 0))
        return Reply(
            status_text(
                name, entry.get("title") or entry.get("preview") or "untitled", entry["turns"]
            )
        )

    async def _help_command(self, inbound: InboundMessage, arg: str) -> Outcome:
        return Reply(help_text())

    async def _command(self, inbound: InboundMessage, name: str, arg: str) -> Outcome:
        handlers = {
            "profile": self._profile_command,
            "new": self._new_command,
            "clear": self._clear_command,
            "status": self._status_command,
            "help": self._help_command,
        }
        handler = handlers.get(name)
        if handler is None:
            return Refuse(unknown_command(name))
        return await handler(inbound, arg)

    # ---- messages ----

    async def handle(
        self,
        inbound: InboundMessage,
        *,
        asker=None,
        attachments: list | None = None,
    ) -> Outcome:
        """Run ``inbound`` and return what the adapter should render."""
        if not should_respond(inbound):
            return NOTHING
        if not self.paired(inbound):
            # Everything past here reaches a Profile, so pairing is the first gate —
            # ahead of the command surface, which is just as much of a disclosure.
            return self._pair(inbound)

        if self._has_commands(inbound):
            command = parse_command(inbound.text)
            if command is not None:
                return await self._command(inbound, *command)

        resolved = self._resolve(inbound)
        if not isinstance(resolved, str):
            return resolved
        gateway = self._directory.gateway_for_profile(resolved)
        if gateway is None:
            return Refuse(NO_PROFILE)

        chat_id = self._chat_for(inbound)

        text = inbound.text
        if not text.strip() and inbound.has_attachment:
            # A wordless file speaks for itself, or says it arrived unreadable.
            text = ATTACHMENT_ONLY_PROMPT if attachments else ATTACHMENT_UNREADABLE
        try:
            reply = await gateway.send_message(
                text,
                chat_id=chat_id,
                asker=asker,
                attachments=attachments or [],
            )
        except Exception as exc:  # surface failures to the user
            return Reply(f"Sorry, something went wrong: {exc}")
        return Reply(reply)
