"""The channel router — the platform-neutral seam between adapters and runtimes.

An adapter normalises an inbound platform message, hands it to the router, and
renders the outcome that comes back. Every decision about what a message means
lives here; adapters keep only platform concerns.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Protocol

from ag2.events import ToolCallsEvent

from assistant import connections
from assistant import pairing as pairing_mod
from assistant.channels.base import InboundMessage, should_respond
from assistant.observability import log_suppressed
from assistant.pairing import PairingStore
from assistant.paths import Paths
from assistant.peers import Peer, PeerStore

if TYPE_CHECKING:
    from assistant.gateway.core import Gateway  # type-only (runtime import would cycle)

# A bare attachment carries no words, so the model is given a prompt for it.
ATTACHMENT_ONLY_PROMPT = "Here is a file I'm sharing with you."

# Said for a bare attachment whose download brought nothing back.
ATTACHMENT_UNREADABLE = "I sent you a file, but it could not be read."

# Platforms with a command surface. Discord and Slack have none (ADR 0022): they sit
# in their Channel's default profile, and a leading slash there is just words.
COMMAND_PLATFORMS = ("telegram",)

# Said when a message has no profile to land in.
NO_PROFILE = (
    "No profile is set up for this channel yet. "
    "Pick a default profile for it in Settings → Channels."
)

CHOOSE_PROFILE = "Which profile should I use for this conversation?"

# Said, without running the message, when the profile this Peer talked to has been
# withdrawn from this surface.
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

# How many models a picker offers — the same bound the chat picker keeps, so a long
# model list never buries the conversation in buttons.
MODELS_OFFERED = CHATS_OFFERED

# --- the model a Chat runs on (ADR 0025) ---

CHOOSE_MODEL = "Which model should this chat run on?"
USE_DEFAULT = "Use default"
NO_MODELS = "No models are set up yet — add one in Settings → Models."
# Said when "Use default" is picked before there is a Chat: whatever was being held for
# the next one is dropped, and that Chat will be born inheriting like any other.
PENDING_CLEARED = "Your next chat will run on the default model."
MODEL_GONE = "That model is gone. Send /model to pick from the ones that are left."

# What the browser's switcher says on a row it will not let you pick
# (web/src/components/ModelSwitcherView.svelte); a tap here is told the same thing.
MODEL_NOT_READY = "Not ready — add a key or sign in via Settings"
# How the picker marks such a row, since an inline button cannot be greyed out: the
# refusal's own first words, so the warning and the tap say the same thing.
NOT_READY_SUFFIX = "(not ready)"

# How `/status` marks a model the Chat never chose — it runs on whatever is Active, so
# a later switch moves it.
INHERITED_MODEL = "(default)"
# How `/status` marks a Pending override: a model held for the Chat the next message
# starts, not for one that exists.
PENDING_MODEL = "(for your next chat)"

# A model picker in a group would publish the install's model names to a room nobody
# in the conversation owns.
MODEL_IN_GROUP = "/model only works in a direct message. Set a group chat's model in the browser."

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
    Command("model", "Choose the model this chat runs on"),
    Command("help", "List these commands"),
)

# An option token names the picker that offered it; a delete also names the Chat it
# was raised for, so a tap can only ever act on what the user was shown.
PROFILE_TOKEN = "profile:"
CLEAR_TOKEN = "clear:"
KEEP_TOKEN = "keep:"
RESUME_TOKEN = "resume:"
# A model option carries the configuration it offers; the bare token is "Use default",
# which clears the Chat's override rather than naming a model.
MODEL_TOKEN = "model:"
# An answer names the Inquiry it resolves and the index of the option that was
# tapped — an index, so a long option label cannot outgrow a platform's token cap.
ANSWER_TOKEN = "answer:"


def unknown_profile(name: str) -> str:
    return f"There is no profile called '{name}'. Send /profile to pick from the list."


def unknown_model(name: str) -> str:
    return f"There is no model called '{name}'. Send /model to pick from the list."


def model_label(model: dict) -> str:
    """One row in the `/model` picker: the model's name, marked when tapping it would
    be refused — the browser greys such a row out, and a button cannot be."""
    return f"{model['name']} {NOT_READY_SUFFIX}" if not model.get("ready", True) else model["name"]


def model_set(name: str) -> str:
    return f"This chat now runs on {name}."


def model_pending(name: str) -> str:
    """Said when a model is chosen with no Chat to write it to: the next message starts
    a Chat already on it. Deliberately says "next chat", not "this one"."""
    return f"Your next chat will run on {name}."


def model_cleared(name: str) -> str:
    """Said when a Chat gives up its own model: what it drops, and what it lands on —
    which is the default here, the Task's model in a Run's thread."""
    return "This chat no longer has a model of its own" + (
        f" — it runs on {name}." if name else "."
    )


def unknown_command(name: str) -> str:
    return f"I don't know the command /{name}. Send /help for what I do know."


def help_text() -> str:
    return "\n".join(f"/{c.name} — {c.description}" for c in COMMANDS)


def status_text(profile: str, title: str, turns: int, model: str = "") -> str:
    """Where a Peer stands: its profile, Chat and size, plus the model it runs on when
    there is one to name."""
    lines = [f"Profile: {profile}", f"Chat: {title}", f"Size: {turns} exchanges"]
    return "\n".join([*lines, f"Model: {model}"] if model else lines)


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


# --- the Tool trace: the tools a Peer's own turn called, as that Peer reads them ---

# What a line carries when its call has no argument to be marked by.
TRACE_MARKER = "•"

# What a trace carries while its turn is still in flight.
TRACE_WORKING = "⏳ Working…"

# How many of the most recent calls a trace shows. A bound, because the trace lives
# in one editable message with a per-message cap.
TRACE_LINES = 12

# The argument a call is previewed by, most telling first, and the mark its *shape*
# earns — a category, never a per-tool table, so a tool an MCP server or a skill
# contributed is marked as sensibly as one this repo ships.
PREVIEW_ICONS = {
    "path": "📄",
    "query": "🔍",
    "name": "🧩",
    "url": "🌐",
    "file": "📄",
    "command": "⚙️",
}
PREVIEW_CHARS = 40


@dataclass(frozen=True)
class ToolCall:
    """One call in a Tool trace: the tool's name and the arguments it was given."""

    name: str
    arguments: dict


def call_preview(arguments: dict) -> tuple[str, str]:
    """What a call is marked and named by: the mark its first preferred argument earns
    and that argument clipped. The generic marker and no text when there is none, or
    when it is structured data — which is omitted rather than serialised."""
    for key, icon in PREVIEW_ICONS.items():
        if key not in arguments:
            continue
        value = arguments[key]
        if not isinstance(value, str) or not value.strip():
            return TRACE_MARKER, ""
        text = " ".join(value.split())
        return icon, (f"{text[:PREVIEW_CHARS].rstrip()}…" if len(text) > PREVIEW_CHARS else text)
    return TRACE_MARKER, ""


def tool_line(call: ToolCall) -> str:
    """One call as a line: what was called, and what it was about when that is short."""
    icon, preview = call_preview(call.arguments)
    return f"{icon} {call.name}" + (f' "{preview}"' if preview else "")


def earlier_calls(count: int) -> str:
    """What a trace says about the calls it had to drop to stay within its bound."""
    return f"… {count} earlier {'call' if count == 1 else 'calls'} not shown"


def tool_trace(calls: tuple[ToolCall, ...], *, working: bool) -> str:
    """A turn's tool calls as one block of plain text — the Telegram counterpart of the
    browser's chips. Empty for a turn that called nothing, which then shows no trace."""
    if not calls:
        return ""
    shown = calls[-TRACE_LINES:]
    lines = [TRACE_WORKING] if working else []
    if len(calls) > len(shown):
        lines.append(earlier_calls(len(calls) - len(shown)))
    lines.extend(tool_line(call) for call in shown)
    return "\n".join(lines)


def call_arguments(call) -> dict:
    """A tool call's arguments as a mapping — empty when they parse to anything else."""
    try:
        arguments = call.serialized_arguments
    except Exception:
        return {}
    return arguments if isinstance(arguments, dict) else {}


class ToolTrace:
    """Accumulates one turn's tool calls and reports the whole trace to the adapter's
    ``progress`` callback, which owns when and how it is shown. Best-effort: a trace
    the adapter cannot deliver never fails a turn."""

    def __init__(self, progress) -> None:
        self._progress = progress
        self._calls: list[ToolCall] = []

    async def __call__(self, event) -> None:
        """Take one of the turn's events, tracing only the batch tool-call one — the
        per-provider singular event duplicates it."""
        if not isinstance(event, ToolCallsEvent):
            return
        self._calls.extend(
            ToolCall(getattr(call, "name", "") or "", call_arguments(call)) for call in event.calls
        )
        await self._report(working=True)

    async def settle(self) -> None:
        """Report the finished trace once, without the working marker."""
        if self._calls:
            await self._report(working=False, final=True)

    async def _report(self, *, working: bool, final: bool = False) -> None:
        try:
            await self._progress(tool_trace(tuple(self._calls), working=working), final=final)
        except Exception as exc:
            log_suppressed("channel tool trace", exc)


def peer_key(connection: str, chat_id: str) -> str:
    """How a Peer is named when a turn records which conversation wrote it."""
    return f"{connection}:{chat_id}"


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

    def default_profile(self, connection: str) -> str | None:
        """The Connection's default profile id, or None when it has none available."""

    def gateway_for_profile(self, pid: str) -> "Gateway | None":
        """The running gateway for a profile id, or None when it is not running."""

    async def notify_channel(self, connection: str, chat_id: str, text: str) -> None:
        """Push a message into a conversation through the Connection it arrived on."""

    async def ask_channel(
        self, connection: str, chat_id: str, inquiry: str, question: Choose
    ) -> None:
        """Show a question, with its options, in a conversation on that Connection."""

    async def retract_channel(self, connection: str, chat_id: str, inquiry: str) -> None:
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

    Built once per install and shared by every adapter (ADR 0022): the runtime a
    message runs on is resolved when the message arrives, not captured at start.
    """

    def __init__(self, directory: ProfileDirectory, paths: Paths) -> None:
        self._directory = directory
        self._peers = PeerStore(paths)
        self._pairing = PairingStore(paths)

    def accepts(self, inbound: InboundMessage) -> bool:
        """Whether this message runs a turn — an adapter's gate for showing platform
        feedback. An unpaired account never opens it; its message still goes to ``handle``."""
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
        """Whether this account may be served by the Connection it wrote to, pinning any
        pending handle it presents. Adapters call this before acting on a message."""
        return self._pairing.is_paired(inbound.connection, inbound.sender_id, inbound.sender_handle)

    def _pair(self, inbound: InboundMessage) -> Outcome:
        """Redeem the code an unpaired account has sent, against the Connection it sent
        it to; an unknown code is met with silence."""
        outcome = self._pairing.redeem(
            inbound.connection, inbound.text, inbound.sender_id, inbound.sender_handle
        )
        if outcome == pairing_mod.PAIRED:
            return Reply(PAIRED)
        if outcome == pairing_mod.EXPIRED:
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
        """Record this Peer's profile and the account speaking, leaving an unchanged
        selection alone."""
        peer = self._peers.get_peer(inbound.connection, inbound.chat_id)
        if peer is not None and peer.profile == pid and peer.sender == inbound.sender_id:
            return  # nothing moved; don't rewrite the registry on every message
        self._peers.select_profile(
            inbound.connection,
            inbound.chat_id,
            pid,
            platform=inbound.platform,
            surface=inbound.surface(),
            sender=inbound.sender_id,
        )

    def _current_profile(
        self, inbound: InboundMessage, by_id: dict[str, AvailableProfile]
    ) -> str | None:
        """The profile this Peer is talking to right now, without choosing one for it.
        Its own selection, else the Channel's default, else the sole running profile."""
        peer = self._peers.get_peer(inbound.connection, inbound.chat_id)
        if peer is not None and peer.profile in by_id:
            return peer.profile
        # The Connection default is a live fallback, never a stored selection: changing
        # it moves every Peer that has not chosen for itself.
        default = self._directory.default_profile(inbound.connection)
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
            self._peers.detach(inbound.connection, inbound.chat_id)
        self._select(inbound, profile.id)
        return Reply(switched_to(profile.name) if moved else already_in(profile.name))

    def _withdrawn(self, inbound: InboundMessage, by_id: dict[str, AvailableProfile]) -> bool:
        """Whether this Peer's own selection is out of reach from this surface."""
        peer = self._peers.get_peer(inbound.connection, inbound.chat_id)
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
            # Pin rather than leave the Peer on a fallback the install could move
            # under it. A group is pinned always: it is re-pointed only from the WebUI.
            if len(by_id) == 1 or not inbound.is_direct:
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
            if not inbound.is_direct:
                # No picker is ever offered in a group, so a token arriving from one is
                # stale or forwarded; the pin holds either way.
                return Refuse(PROFILE_IN_GROUP)
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
        if token.startswith(MODEL_TOKEN):
            return await self._choose_model(inbound, token.removeprefix(MODEL_TOKEN))
        if token.startswith(ANSWER_TOKEN):
            inquiry, _, index = token.removeprefix(ANSWER_TOKEN).partition(":")
            if not index.isdigit():
                return Refuse(STALE_OPTION)
            return await self._answer(inbound, inquiry, option=int(index))
        return Refuse(STALE_OPTION)

    # ---- the Chat a Peer is in ----

    def _runtime(self, inbound: InboundMessage) -> "tuple[str, Gateway] | Outcome":
        """The profile id this Peer talks to and the gateway behind it, or the outcome
        to return instead. Every Chat operation goes through here."""
        resolved = self._resolve(inbound)
        if not isinstance(resolved, str):
            return resolved
        gateway = self._directory.gateway_for_profile(resolved)
        if gateway is None:
            return Refuse(NO_PROFILE)
        return resolved, gateway

    def _exposed_to(self, peer: Peer) -> bool:
        """Whether the profile this Peer chose is still reachable from its surface — the
        same rule `_withdrawn` applies inbound, on the side no message passes through."""
        if peer.profile is None:
            return True
        surface = connections.surface_key(peer.connection, peer.platform, peer.surface)
        return peer.profile in {p.id for p in self._directory.available_profiles(surface)}

    def _reachable(self, peer: Peer) -> bool:
        """Whether anything may be pushed into this Peer unasked: its account is Paired to
        the Connection (ADR 0021) and its profile exposed there (ADR 0022)."""
        return bool(peer.sender) and (
            self._pairing.is_paired(peer.connection, peer.sender) and self._exposed_to(peer)
        )

    def _attached_chat(self, inbound: InboundMessage) -> str | None:
        peer = self._peers.get_peer(inbound.connection, inbound.chat_id)
        return peer.chat if peer is not None else None

    def _chat_for(self, inbound: InboundMessage) -> str:
        """The Chat this Peer speaks in, started on first use."""
        return self._attached_chat(inbound) or self._peers.start_chat(
            inbound.connection,
            inbound.chat_id,
            platform=inbound.platform,
            surface=inbound.surface(),
            sender=inbound.sender_id,
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
        self._peers.attach(
            inbound.connection,
            inbound.chat_id,
            chat,
            platform=inbound.platform,
            surface=inbound.surface(),
            sender=inbound.sender_id,
        )
        header = attached_header(self._by_id(inbound)[resolved].name, entry)
        tail = transcript_tail(await gateway.transcript(chat))
        return Reply(f"{header}\n\n{tail}" if tail else header)

    async def mirror(
        self, chat: str, text: str, reply: str, *, origin: str = "", files: tuple[str, ...] = ()
    ) -> None:
        """Send a completed turn in ``chat`` to the Peer Attached to it (ADR 0020).
        ``origin`` names the conversation that ran it, which is not sent it back;
        ``files`` are the names of the files attached, which are named, never carried."""
        peer = self._peers.attached_to(chat)
        if peer is None or peer_key(peer.connection, peer.chat_id) == origin:
            return
        if not self._reachable(peer):
            return
        body = mirrored_turn(text, reply, files)
        if body:
            await self._directory.notify_channel(peer.connection, peer.chat_id, body)

    async def push(self, connection: str, chat_id: str, text: str) -> None:
        """Push a task-run outcome into the conversation the task was started from,
        through the same gates a mirrored turn passes."""
        peer = self._peers.get_peer(connection, chat_id)
        if peer is None or not self._reachable(peer):
            return
        await self._directory.notify_channel(connection, chat_id, text)

    async def ask(self, chat: str, inquiry: str, text: str, options: tuple[str, ...]) -> None:
        """Show a question raised in ``chat`` to the Peer Attached to it, carrying the
        same options the browser offers so either surface can resolve it (ADR 0020)."""
        peer = self._peers.attached_to(chat)
        if peer is None or not self._reachable(peer):
            return
        question = Choose(
            text,
            tuple(
                Option(label, f"{ANSWER_TOKEN}{inquiry}:{index}")
                for index, label in enumerate(options)
            ),
        )
        await self._directory.ask_channel(peer.connection, peer.chat_id, inquiry, question)

    async def retract(self, chat: str, inquiry: str) -> None:
        """Take back a question the Attached Peer was shown, once it has been resolved
        — on that platform, in the browser, or anywhere else."""
        peer = self._peers.attached_to(chat)
        if peer is None:
            return
        await self._directory.retract_channel(peer.connection, peer.chat_id, inquiry)

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
        self._peers.forget_chat(chat)
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

    # ---- the model the Attached Chat runs on (ADR 0025) ----

    def _direct_gateway(self, inbound: InboundMessage):
        """The Gateway for a `/model` request, or the Outcome that refuses it: the
        command is direct-only, and needs a running profile behind it."""
        if not inbound.is_direct:
            return Refuse(MODEL_IN_GROUP)
        runtime = self._runtime(inbound)
        return runtime[1] if isinstance(runtime, tuple) else runtime

    async def _choose_model(self, inbound: InboundMessage, cid: str) -> Outcome:
        """Apply a model tapped in the picker — the empty id being "Use default"."""
        gateway = self._direct_gateway(inbound)
        if isinstance(gateway, Outcome):
            return gateway
        if not cid:
            return await self._set_model(inbound, gateway, None)
        # Only a model that is still offered can be tapped onto a Chat.
        match = self._model_by_id(gateway, cid)
        if match is None:
            return Refuse(MODEL_GONE)
        return await self._set_model(inbound, gateway, match)

    async def _set_model(self, inbound: InboundMessage, gateway, model: dict | None) -> Outcome:
        """Set the Attached Chat's override to ``model``, or clear it for None, and say
        what the Chat runs on now. With no Chat Attached the choice is held on the Peer
        as a Pending override instead, for the Chat the next message starts.

        A model that cannot run is refused, held choice and Attached one alike."""
        if model is not None and not model.get("ready", True):
            return Refuse(MODEL_NOT_READY)
        chat = self._attached_chat(inbound)
        if chat is None:
            self._peers.set_pending_model(
                inbound.connection, inbound.chat_id, model["id"] if model else ""
            )
            return Reply(model_pending(model["name"]) if model else PENDING_CLEARED)
        if not await gateway.update_chat(chat, model=model["id"] if model else ""):
            return Refuse(CHAT_GONE)
        if model is not None:
            return Reply(model_set(model["name"]))
        return Reply(model_cleared(await self._effective_model_name(gateway, chat)))

    @staticmethod
    def _model_by_id(gateway, cid: str) -> dict | None:
        """The configured Text model with this id, or None when it is no longer offered."""
        return next((m for m in gateway.text_models() if m["id"] == cid), None)

    async def _effective_model_name(self, gateway, chat: str) -> str:
        """What this Chat runs on now, named as the user knows it. Resolved by the
        gateway, which is the only place the whole chain (env pin, Task, Active) lives."""
        effective = await gateway.effective_model(chat)
        # An env pin names a model that was never a configuration, so it names itself.
        return (self._model_by_id(gateway, effective) or {}).get("name") or effective

    async def _chat_model_line(self, gateway, chat: str) -> str:
        """What `/status` says this Chat runs on: the model itself, marked a default
        when the Chat has no override of its own and merely follows what is Active."""
        named = await self._effective_model_name(gateway, chat)
        if not named or await gateway.chat_model(chat):
            return named
        return f"{named} {INHERITED_MODEL}"

    def _held_model_line(self, inbound: InboundMessage, gateway) -> str:
        """What `/status` says about a Pending override, or nothing when none is held.
        Read, never taken: only a message may spend what the Peer is holding."""
        peer = self._peers.get_peer(inbound.connection, inbound.chat_id)
        held = peer.pending_model if peer is not None else None
        if not held:
            return ""
        # A model deleted since it was chosen still names its id rather than nothing.
        named = (self._model_by_id(gateway, held) or {}).get("name") or held
        return f"{named} {PENDING_MODEL}"

    async def _model_command(self, inbound: InboundMessage, arg: str) -> Outcome:
        gateway = self._direct_gateway(inbound)
        if isinstance(gateway, Outcome):
            return gateway
        models = gateway.text_models()
        if not arg:
            if not models:
                return Reply(NO_MODELS)
            return Choose(
                CHOOSE_MODEL,
                (
                    Option(USE_DEFAULT, MODEL_TOKEN),
                    *(
                        Option(model_label(m), f"{MODEL_TOKEN}{m['id']}")
                        for m in models[:MODELS_OFFERED]
                    ),
                ),
            )

        match = next(
            (m for m in models if arg.casefold() in (m["id"].casefold(), m["name"].casefold())),
            None,
        )
        if match is None:
            return Refuse(unknown_model(arg))
        return await self._set_model(inbound, gateway, match)

    async def _new_command(self, inbound: InboundMessage, arg: str) -> Outcome:
        fresh = self._attached_chat(inbound) is None
        # Detach even when there is nothing to detach from: starting over also drops a
        # model held for the Chat that never happened (ADR 0025).
        self._peers.detach(inbound.connection, inbound.chat_id)
        return Reply(ALREADY_NEW if fresh else NEW_CHAT)

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
        if chat is None:
            # The only branch a Pending override is reported on: a held model and an
            # Attached Chat never coexist, so there is nothing here to adjudicate.
            held = self._held_model_line(inbound, gateway)
            return Reply(status_text(name, NO_CHAT_YET, 0, held))
        entry = next((e for e in await gateway.list_chats() if e.get("chat_id") == chat), None)
        if entry is None:
            return Reply(status_text(name, NO_CHAT_YET, 0))
        model = await self._chat_model_line(gateway, chat)
        return Reply(status_text(name, chat_title(entry), entry["turns"], model))

    async def _help_command(self, inbound: InboundMessage, arg: str) -> Outcome:
        return Reply(help_text())

    async def _command(self, inbound: InboundMessage, name: str, arg: str) -> Outcome:
        handlers = {
            "profile": self._profile_command,
            "model": self._model_command,
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
        progress=None,
    ) -> Outcome:
        """Run ``inbound`` and return what the adapter should render. ``progress`` is an
        optional async ``(text, *, final) -> None`` given this turn's Tool trace as it
        grows and once more when it ends; an adapter passing none gets today's turn."""
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

        # Taken before the Chat is resolved: attaching to one drops what the Peer holds.
        chat_model = self._peers.take_pending_model(inbound.connection, inbound.chat_id)
        chat_id = self._chat_for(inbound)

        text = inbound.text
        if not text.strip() and inbound.has_attachment:
            # A wordless file speaks for itself, or says it arrived unreadable.
            text = ATTACHMENT_ONLY_PROMPT if attachments else ATTACHMENT_UNREADABLE

        # Sent while a turn is in flight? Feed that turn instead of queueing a second
        # one behind it — the answer comes back in the first message's place.
        if await gateway.feed_message(text, chat_id, attachments or []):
            return Ack()

        trace = ToolTrace(progress)
        try:
            reply = await gateway.send_message(
                text,
                chat_id=chat_id,
                chat_model=chat_model,
                asker=asker,
                attachments=attachments or [],
                origin=peer_key(inbound.connection, inbound.chat_id),
                # Wired only for an adapter that asked to trace; the rest run the
                # unforwarded path.
                on_event=trace if progress is not None else None,
            )
        except Exception as exc:  # surface failures to the user
            return Reply(f"Sorry, something went wrong: {exc}")
        finally:
            # A turn that ends any way at all — answered, stopped, failed — leaves its
            # trace settled behind it (ADR 0018).
            await trace.settle()
        # A stopped turn comes back with nothing to say; say nothing rather than an
        # empty reply, so the adapter drops the placeholder instead of leaving it.
        return Reply(reply) if reply else NOTHING
