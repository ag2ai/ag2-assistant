"""The channel router — the platform-neutral seam between adapters and runtimes.

An adapter normalises an inbound platform message, hands it to the router, and
renders the outcome that comes back. Every decision about what a message means
lives here; adapters keep only platform concerns.
"""

from dataclasses import dataclass
from datetime import datetime
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

# Said when the profile this Peer was talking to has been withdrawn from this surface.
# The message that met it is not run: it was written for that profile, and dropping it
# into another one would put it in the wrong transcript.
PROFILE_WITHDRAWN = "That profile isn't reachable from this conversation anymore."
CHOOSE_INSTEAD = f"{PROFILE_WITHDRAWN} Which one should I use instead?"
NO_PROFILE_HERE = (
    f"{PROFILE_WITHDRAWN} No other profile is either — "
    "make one reachable here in Settings → Profiles."
)

# Said to whoever has just paired. Deliberately says nothing about the install yet —
# the next message is the one that gets a profile and a turn.
PAIRED = "You're paired. Send me anything to get started."

# The one thing an unpaired account is ever told, and only when it presents a code
# that was plainly issued to it (ADR 0021).
CODE_EXPIRED = "That pairing code has expired. Generate a new one in Settings → Channels."

# A group's profile is not the group's to change — it is set from the WebUI.
PROFILE_IN_GROUP = "/profile only works in a direct message. Set a group's profile in Settings."

# Said for `/stop`. A stopped turn keeps the work it already did, as in the browser.
STOPPED = "Stopped. What I'd already done is kept."
NOTHING_RUNNING = "Nothing is running right now."

NEW_CHAT = "Started a fresh chat. The one you were in is still in your chat list."
ALREADY_NEW = "You're already in a fresh chat — nothing has been said in it yet."
CONFIRM_CLEAR = "Delete this chat permanently? Its whole transcript goes with it."
CLEARED = "Deleted. Your next message starts a fresh chat."
KEPT = "Left it as it was."
NOTHING_TO_CLEAR = "There's no chat to delete yet — nothing has been said here."
NO_CHAT_YET = "no chat yet"
STALE_OPTION = "That option has expired. Send /help for what I can do."

# Said when the question a tap or a reply answers has already been resolved elsewhere.
ANSWERED_ELSEWHERE = "That question was already answered."

CHOOSE_CHAT = "Which chat should I pick up?"
NO_CHATS = "There are no chats in this profile yet — send me anything to start one."
CHAT_GONE = "That chat is gone. Send /resume for the ones that are left."

# How much of a Chat a picker and an attach show: enough to tell chats apart and to
# remember where things stood, not a wall of text.
CHATS_OFFERED = 10
TAIL_MESSAGES = 6
TAIL_CHARS = 300

# How files on a message are named to a Peer: their names, never their bytes and never
# their paths (ADR 0020).
FILES_LABEL = "Files:"

# The block of absolute paths the browser appends for the File references a message
# carries (ADR 0012), and the marker a referenced directory ends with.
REFERENCES = "Referenced files:\n"
DIRECTORY_SUFFIX = " (directory — list its contents)"


@dataclass(frozen=True)
class Command:
    """One command a Peer can send, and the one-liner that describes it."""

    name: str
    description: str


COMMANDS = (
    Command("new", "Start a fresh chat"),
    Command("resume", "Pick up an earlier chat"),
    Command("clear", "Delete this chat permanently"),
    Command("stop", "Stop the turn that's running"),
    Command("status", "Show the profile and chat you're in"),
    Command("profile", "Choose which profile to talk to"),
    Command("help", "List these commands"),
)

# An option token names the picker that offered it; a delete also names the Chat it
# was raised for, so a tap can only ever act on what the user was shown.
PROFILE_TOKEN = "profile:"
CLEAR_TOKEN = "clear:"
KEEP_TOKEN = "keep:"
RESUME_TOKEN = "resume:"
# An answer names the Inquiry it resolves and the index of the option that was
# tapped — an index, so a long option label cannot outgrow a platform's token cap.
ANSWER_TOKEN = "answer:"


def unknown_profile(name: str) -> str:
    return f"There is no profile called '{name}'. Send /profile to pick from the list."


def unknown_command(name: str) -> str:
    return f"I don't know the command /{name}. Send /help for what I do know."


def help_text() -> str:
    return "\n".join(f"/{c.name} — {c.description}" for c in COMMANDS)


def status_text(profile: str, title: str, turns: int) -> str:
    return f"Profile: {profile}\nChat: {title}\nSize: {turns} exchanges"


def relative_time(stamp: str) -> str:
    """How long ago an ISO timestamp was, in one short phrase."""
    try:
        then = datetime.fromisoformat(stamp)
    except ValueError:
        return "recently"
    minutes = int((datetime.now().astimezone() - then).total_seconds() // 60)
    if minutes < 1:
        return "just now"
    if minutes < 60:
        return f"{minutes}m ago"
    if minutes < 60 * 24:
        return f"{minutes // 60}h ago"
    return f"{minutes // (60 * 24)}d ago"


def chat_title(entry: dict) -> str:
    """What a Chat is called in a list or a header, falling back to its opening line."""
    return entry.get("title") or entry.get("preview") or "untitled"


def chat_label(entry: dict) -> str:
    """One line in the `/resume` picker: what the chat is about, and how stale it is."""
    return f"{chat_title(entry)} · {relative_time(entry.get('updated', ''))}"


def attached_header(profile: str, entry: dict) -> str:
    """What a Peer is shown about the Chat it has just attached to."""
    return (
        f"{status_text(profile, chat_title(entry), entry['turns'])}\n"
        f"Last active: {relative_time(entry.get('updated', ''))}"
    )


def speaker(role: str) -> str:
    """How a message's author is labelled in anything a Peer reads."""
    return "You" if role == "user" else "Me"


def fold_references(text: str) -> tuple[str, tuple[str, ...]]:
    """A sent message split into the words it says and the names of the Files its
    trailing block references. Text without a well-formed block passes through, so
    prose that merely mentions the marker stays prose."""
    at = text.rfind(f"\n{REFERENCES}")
    start = at + 1 if at != -1 else (0 if text.startswith(REFERENCES) else -1)
    if start == -1:
        return text, ()
    names = []
    for line in text[start + len(REFERENCES) :].splitlines():
        if not line.strip():
            continue
        if not line.startswith("- "):
            return text, ()
        path = line[2:].strip().removesuffix(DIRECTORY_SUFFIX).strip()
        names.append(path.rsplit("/", 1)[-1])
    if not names:
        return text, ()
    return text[:start].rstrip(), tuple(names)


def said(text: str, files: tuple[str, ...] = (), *, clip: int = 0) -> str:
    """One message as a Peer reads it: the words, clipped when asked, followed by the
    names of the files on it — the ones attached and the ones referenced."""
    body, referenced = fold_references(text)
    body = body.strip()
    if clip and len(body) > clip:
        body = f"{body[:clip].rstrip()}…"
    named = (*files, *referenced)
    return "\n".join(
        part for part in (body, f"{FILES_LABEL} {', '.join(named)}" if named else "") if part
    )


def transcript_tail(messages: list[dict]) -> str:
    """The last few turns, speaker-labelled and clipped, as one block of text."""
    return "\n\n".join(
        f"{speaker(message.get('role') or '')}: {said(message.get('text') or '', clip=TAIL_CHARS)}"
        for message in messages[-TAIL_MESSAGES:]
    )


def mirrored_turn(text: str, reply: str, files: tuple[str, ...] = ()) -> str:
    """A completed turn as the Attached Peer reads it — the same speaker labels a
    resumed transcript shows, unclipped (the adapter splits what is too long)."""
    return "\n\n".join(
        f"{speaker(role)}: {body}"
        for role, body in (("user", said(text, files)), ("agent", reply.strip()))
        if body
    )


def peer_key(platform: str, chat_id: str) -> str:
    """How a Peer is named when a turn records which conversation wrote it."""
    return f"{platform}:{chat_id}"


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

    def available_profiles(self, surface: str) -> tuple[AvailableProfile, ...]:
        """Every profile a conversation on ``surface`` could be pointed at right now —
        the ones withdrawn from it are not among them."""

    def default_profile(self, platform: str) -> str | None:
        """The Channel's default profile id, or None when it has none available."""

    def gateway_for_profile(self, pid: str) -> "Gateway | None":
        """The running gateway for a profile id, or None when it is not running."""

    async def notify_channel(self, platform: str, chat_id: str, text: str) -> None:
        """Push a message into a platform conversation through its live Channel."""

    async def ask_channel(
        self, platform: str, chat_id: str, inquiry: str, question: Choose
    ) -> None:
        """Show a question, with its options, in a platform conversation."""

    async def retract_channel(self, platform: str, chat_id: str, inquiry: str) -> None:
        """Take back a question shown there — it has been resolved."""


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

    def steers(self, inbound: InboundMessage) -> bool:
        """Whether this message will be fed into the turn already running in this
        Peer's Chat rather than starting one — the adapter's gate for acknowledging it
        instead of showing a placeholder the running turn's answer will not land in."""
        if not self.accepts(inbound):
            return False
        if self._has_commands(inbound) and parse_command(inbound.text) is not None:
            return False
        chat = self._attached_chat(inbound)
        runtime = self._runtime(inbound)
        return chat is not None and isinstance(runtime, tuple) and runtime[1].is_running(chat)

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

    def _by_id(self, inbound: InboundMessage) -> dict[str, AvailableProfile]:
        """The profiles reachable from this conversation's surface — the single place
        every picker and name lookup reads, so a withdrawal is absent from all of them."""
        return {p.id: p for p in self._directory.available_profiles(inbound.exposure_surface())}

    def _ask_which_profile(
        self, by_id: dict[str, AvailableProfile], text: str = CHOOSE_PROFILE
    ) -> Choose:
        return Choose(
            text,
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
        moved = self._current_profile(inbound, self._by_id(inbound)) != profile.id
        if moved:
            # A Chat cannot cross Profiles, so leaving one always leaves its Chat —
            # including for a Peer that was riding the Channel default rather than a
            # selection of its own.
            peers.detach(inbound.platform, inbound.chat_id)
        self._select(inbound, profile.id)
        return Reply(switched_to(profile.name) if moved else already_in(profile.name))

    def _withdrawn(self, inbound: InboundMessage, by_id: dict[str, AvailableProfile]) -> bool:
        """Whether this Peer's own selection is out of reach from this surface."""
        peer = peers.get_peer(inbound.platform, inbound.chat_id)
        return peer is not None and peer.profile is not None and peer.profile not in by_id

    def _unreachable(self, inbound: InboundMessage, by_id: dict[str, AvailableProfile]) -> Outcome:
        """Say the chosen profile is out of reach, offering what remains where it can be
        offered. Never falls back to another profile — this message was written for the
        one that has gone."""
        if not by_id:
            return Refuse(NO_PROFILE_HERE)
        if self._has_commands(inbound) and inbound.is_direct:
            return self._ask_which_profile(by_id, CHOOSE_INSTEAD)
        return Refuse(PROFILE_WITHDRAWN)

    def _resolve(self, inbound: InboundMessage) -> str | Outcome:
        """The profile id this message runs in, or the outcome to return instead."""
        by_id = self._by_id(inbound)
        if self._withdrawn(inbound, by_id):
            return self._unreachable(inbound, by_id)
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
            profile = self._by_id(inbound).get(token.removeprefix(PROFILE_TOKEN))
            if profile is None:
                return Refuse(NO_PROFILE)
            return self._switch_to(inbound, profile)
        if token.startswith(KEEP_TOKEN):
            return Reply(KEPT)
        if token.startswith(CLEAR_TOKEN):
            return await self._delete_chat(inbound, token.removeprefix(CLEAR_TOKEN))
        if token.startswith(RESUME_TOKEN):
            return await self._attach_chat(inbound, token.removeprefix(RESUME_TOKEN))
        if token.startswith(ANSWER_TOKEN):
            inquiry, _, index = token.removeprefix(ANSWER_TOKEN).partition(":")
            if not index.isdigit():
                return Refuse(STALE_OPTION)
            return await self._answer(inbound, inquiry, option=int(index))
        return Refuse(STALE_OPTION)

    # ---- the Chat a Peer is in ----

    def _runtime(self, inbound: InboundMessage) -> "tuple[str, Gateway] | Outcome":
        """The profile id this Peer talks to and the gateway behind it, or the outcome
        to return instead. Every Chat operation goes through here, so a profile out of
        reach from this surface takes its Chats with it."""
        resolved = self._resolve(inbound)
        if not isinstance(resolved, str):
            return resolved
        gateway = self._directory.gateway_for_profile(resolved)
        if gateway is None:
            return Refuse(NO_PROFILE)
        return resolved, gateway

    def _attached_chat(self, inbound: InboundMessage) -> str | None:
        peer = peers.get_peer(inbound.platform, inbound.chat_id)
        return peer.chat if peer is not None else None

    def _chat_for(self, inbound: InboundMessage) -> str:
        """The Chat this Peer speaks in, started on first use."""
        return self._attached_chat(inbound) or peers.start_chat(
            inbound.platform, inbound.chat_id, surface=inbound.surface()
        )

    async def _chats_in_profile(self, inbound: InboundMessage) -> "list[dict] | Outcome":
        """Every Chat of this Peer's profile, most recently touched first — the ones
        begun in the browser among them (ADR 0020)."""
        runtime = self._runtime(inbound)
        if not isinstance(runtime, tuple):
            return runtime
        listing = await runtime[1].list_chats()
        return sorted(listing, key=lambda entry: entry.get("updated") or "", reverse=True)

    async def _attach_chat(self, inbound: InboundMessage, chat: str) -> Outcome:
        """Attach the Peer to a Chat it was offered, and show it where things stood.
        Pure navigation: nothing is created, and the Chat left behind is untouched."""
        runtime = self._runtime(inbound)
        if not isinstance(runtime, tuple):
            return runtime
        resolved, gateway = runtime
        entry = next((e for e in await gateway.list_chats() if e.get("chat_id") == chat), None)
        if entry is None:
            return Refuse(CHAT_GONE)
        peers.attach(inbound.platform, inbound.chat_id, chat, surface=inbound.surface())
        header = attached_header(self._by_id(inbound)[resolved].name, entry)
        tail = transcript_tail(await gateway.transcript(chat))
        return Reply(f"{header}\n\n{tail}" if tail else header)

    async def mirror(
        self, chat: str, text: str, reply: str, *, origin: str = "", files: tuple[str, ...] = ()
    ) -> None:
        """Send a completed turn in ``chat`` to the Peer Attached to it (ADR 0020).

        Nothing goes anywhere when no Peer is attached, and a Peer never gets back
        the turn it wrote itself — ``origin`` names the conversation that ran it.
        ``files`` are the names of the files attached to the message; they are named,
        never carried.
        """
        peer = peers.attached_to(chat)
        if peer is None or peer_key(peer.platform, peer.chat_id) == origin:
            return
        body = mirrored_turn(text, reply, files)
        if body:
            await self._directory.notify_channel(peer.platform, peer.chat_id, body)

    async def ask(self, chat: str, inquiry: str, text: str, options: tuple[str, ...]) -> None:
        """Show a question raised in ``chat`` to the Peer Attached to it, carrying the
        same options the browser offers so either surface can resolve it (ADR 0020)."""
        peer = peers.attached_to(chat)
        if peer is None:
            return
        question = Choose(
            text,
            tuple(
                Option(label, f"{ANSWER_TOKEN}{inquiry}:{index}")
                for index, label in enumerate(options)
            ),
        )
        await self._directory.ask_channel(peer.platform, peer.chat_id, inquiry, question)

    async def retract(self, chat: str, inquiry: str) -> None:
        """Take back a question the Attached Peer was shown, once it has been resolved
        — on that platform, in the browser, or anywhere else."""
        peer = peers.attached_to(chat)
        if peer is None:
            return
        await self._directory.retract_channel(peer.platform, peer.chat_id, inquiry)

    async def answer(self, inbound: InboundMessage, inquiry: str, text: str) -> Outcome:
        """Resolve a mirrored question with what this conversation replied."""
        return await self._answer(inbound, inquiry, text=text)

    async def _answer(
        self, inbound: InboundMessage, inquiry: str, *, text: str = "", option: int | None = None
    ) -> Outcome:
        """Resolve an Inquiry from this Peer, by tapped option or by replied text. The
        store keeps first-answer-wins, so a second surface is told it arrived late."""
        if not self.paired(inbound):
            return NOTHING
        runtime = self._runtime(inbound)
        if not isinstance(runtime, tuple):
            return runtime
        if await runtime[1].answer_inquiry(inquiry, text, option=option):
            return NOTHING
        return Refuse(ANSWERED_ELSEWHERE)

    async def _delete_chat(self, inbound: InboundMessage, chat: str) -> Outcome:
        """Delete the Chat the confirmation was raised for, and only that one."""
        if chat != self._attached_chat(inbound):
            return Refuse(STALE_OPTION)
        runtime = self._runtime(inbound)
        if not isinstance(runtime, tuple):
            return runtime
        await runtime[1].delete_chat(chat)
        peers.forget_chat(chat)
        return Reply(CLEARED)

    # ---- commands ----

    async def _profile_command(self, inbound: InboundMessage, arg: str) -> Outcome:
        if not inbound.is_direct:
            return Refuse(PROFILE_IN_GROUP)

        by_id = self._by_id(inbound)
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

    async def _resume_command(self, inbound: InboundMessage, arg: str) -> Outcome:
        listing = await self._chats_in_profile(inbound)
        if not isinstance(listing, list):
            return listing
        if not listing:
            return Reply(NO_CHATS)
        return Choose(
            CHOOSE_CHAT,
            tuple(
                Option(chat_label(entry), f"{RESUME_TOKEN}{entry['chat_id']}")
                for entry in listing[:CHATS_OFFERED]
            ),
        )

    async def _stop_command(self, inbound: InboundMessage, arg: str) -> Outcome:
        runtime = self._runtime(inbound)
        if not isinstance(runtime, tuple):
            return runtime
        chat = self._attached_chat(inbound)
        if chat is None or not await runtime[1].cancel_turn(chat):
            return Reply(NOTHING_RUNNING)
        return Reply(STOPPED)

    async def _status_command(self, inbound: InboundMessage, arg: str) -> Outcome:
        runtime = self._runtime(inbound)
        if not isinstance(runtime, tuple):
            return runtime
        resolved, gateway = runtime
        name = self._by_id(inbound)[resolved].name
        chat = self._attached_chat(inbound)
        entry = None
        if chat is not None:
            entry = next((e for e in await gateway.list_chats() if e.get("chat_id") == chat), None)
        if entry is None:
            return Reply(status_text(name, NO_CHAT_YET, 0))
        return Reply(status_text(name, chat_title(entry), entry["turns"]))

    async def _help_command(self, inbound: InboundMessage, arg: str) -> Outcome:
        return Reply(help_text())

    async def _command(self, inbound: InboundMessage, name: str, arg: str) -> Outcome:
        handlers = {
            "profile": self._profile_command,
            "new": self._new_command,
            "resume": self._resume_command,
            "clear": self._clear_command,
            "stop": self._stop_command,
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

        runtime = self._runtime(inbound)
        if not isinstance(runtime, tuple):
            return runtime
        gateway = runtime[1]

        chat_id = self._chat_for(inbound)

        text = inbound.text
        if not text.strip() and inbound.has_attachment:
            # A wordless file speaks for itself, or says it arrived unreadable.
            text = ATTACHMENT_ONLY_PROMPT if attachments else ATTACHMENT_UNREADABLE

        # Sent while a turn is in flight? Feed that turn instead of queueing a second
        # one behind it — the answer comes back in the first message's place.
        if await gateway.feed_message(text, chat_id, attachments or []):
            return Ack()

        try:
            reply = await gateway.send_message(
                text,
                chat_id=chat_id,
                asker=asker,
                attachments=attachments or [],
                origin=peer_key(inbound.platform, inbound.chat_id),
            )
        except Exception as exc:  # surface failures to the user
            return Reply(f"Sorry, something went wrong: {exc}")
        # A stopped turn comes back with nothing to say; say nothing rather than an
        # empty reply, so the adapter drops the placeholder instead of leaving it.
        return Reply(reply) if reply else NOTHING
