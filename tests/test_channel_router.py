"""Tests for the channel router — the platform-neutral seam (no network, no agent).

Drives the router with normalised inbound messages and asserts the outcome it
returns, plus what reached the gateway underneath.
"""

from assistant import peers
from assistant.channels.base import InboundMessage
from assistant.channels.router import (
    ALREADY_NEW,
    ATTACHMENT_ONLY_PROMPT,
    ATTACHMENT_UNREADABLE,
    COMMANDS,
    NO_PROFILE,
    Ack,
    AvailableProfile,
    ChannelRouter,
    Choose,
    Nothing,
    Option,
    Refuse,
    Reply,
    spoken_text,
)


class FakeGateway:
    """Records send_message calls; returns a canned reply or raises. Keeps a tiny
    chat store so the lifecycle commands have something to report on and delete."""

    def __init__(self, reply: str = "the answer", error: Exception | None = None) -> None:
        self.reply = reply
        self.error = error
        self.calls: list[dict] = []
        self.chats: dict[str, dict] = {}
        self.deleted: list[str] = []

    async def send_message(self, text, chat_id="default", asker=None, attachments=None, **kw):
        self.calls.append(
            {"text": text, "chat_id": chat_id, "asker": asker, "attachments": attachments}
        )
        chat = self.chats.setdefault(chat_id, {"chat_id": chat_id, "title": "", "turns": 0})
        chat["turns"] += 1
        if self.error is not None:
            raise self.error
        return self.reply

    async def list_chats(self) -> list[dict]:
        return list(self.chats.values())

    async def delete_chat(self, chat_id: str) -> bool:
        self.deleted.append(chat_id)
        return self.chats.pop(chat_id, None) is not None


class FakeDirectory:
    """Stands in for the ProfileManager: which profiles are running, which one a
    platform falls back to, and the gateway behind each."""

    def __init__(self, *names, default=None, reply="the answer", error=None) -> None:
        self.gateways = {name: FakeGateway(reply, error) for name in names}
        self.default = default

    def available_profiles(self) -> tuple[AvailableProfile, ...]:
        return tuple(AvailableProfile(name, name.title()) for name in self.gateways)

    def default_profile(self, platform: str) -> str | None:
        return self.default

    def gateway_for_profile(self, pid):
        return self.gateways.get(pid)


def _inbound(
    text="hi",
    *,
    is_direct=True,
    mentioned=False,
    platform="telegram",
    chat_id="c1",
    has_attachment=False,
):
    return InboundMessage(
        text=text,
        sender_id="u1",
        chat_id=chat_id,
        platform=platform,
        is_direct=is_direct,
        mentioned=mentioned,
        has_attachment=has_attachment,
    )


def _router(**kw) -> tuple[ChannelRouter, FakeGateway]:
    """A router over a single running profile — the common case, where a new Peer
    lands in it without being asked."""
    directory = FakeDirectory("work", **kw)
    return ChannelRouter(directory), directory.gateways["work"]


# --- what comes back ---


async def test_dm_returns_the_gateway_reply():
    router, gateway = _router(reply="4")
    outcome = await router.handle(_inbound("what is 2+2?"))
    assert outcome == Reply("4")
    assert gateway.calls[0]["text"] == "what is 2+2?"


async def test_turn_runs_on_the_peers_own_chat():
    """The Chat is the Peer's, and opaque: a Chat id is not a platform address, so
    one conversation can own several over time."""
    router, gateway = _router(default="work")
    await router.handle(_inbound(platform="discord"))
    await router.handle(_inbound("again", platform="discord"))

    chat = gateway.calls[0]["chat_id"]
    assert chat.startswith("discord-") and "c1" not in chat.removeprefix("discord-")
    assert gateway.calls[1]["chat_id"] == chat


async def test_gateway_failure_becomes_a_reply_not_an_exception():
    router, gateway = _router(error=RuntimeError("boom"))
    outcome = await router.handle(_inbound())
    assert isinstance(outcome, Reply)
    assert "boom" in outcome.text


# --- which profile the turn lands in ---


async def test_the_profile_is_resolved_per_message():
    """One adapter serves the whole install: the runtime is picked when the message
    arrives, not captured when the channel started."""
    directory = FakeDirectory("work", "home")
    directory.gateways["work"].reply = "from work"
    directory.gateways["home"].reply = "from home"
    peers.select_profile("telegram", "c1", "work")
    peers.select_profile("telegram", "c2", "home")
    router = ChannelRouter(directory)

    first = await router.handle(_inbound("hi", chat_id="c1"))
    second = await router.handle(_inbound("hi", chat_id="c2"))
    assert (first, second) == (Reply("from work"), Reply("from home"))


async def test_a_settled_peer_is_not_rewritten_on_every_message():
    """Resolving a profile reads the registry; it only writes when something moved."""
    router, _ = _router()
    await router.handle(_inbound("hi"))
    before = peers.get_peer("telegram", "c1")
    await router.handle(_inbound("again"))
    assert peers.get_peer("telegram", "c1") == before


async def test_the_only_profile_is_chosen_without_asking():
    router, gateway = _router()
    assert isinstance(await router.handle(_inbound("hi")), Reply)
    assert gateway.calls != []
    assert peers.get_peer("telegram", "c1").profile == "work"


async def test_a_new_peer_facing_several_profiles_is_asked_to_choose():
    """Nothing is processed until it has chosen."""
    directory = FakeDirectory("work", "home")
    outcome = await ChannelRouter(directory).handle(_inbound("hi"))
    assert isinstance(outcome, Choose)
    assert {opt.token for opt in outcome.options} == {"profile:work", "profile:home"}
    assert directory.gateways["work"].calls == []
    assert peers.get_peer("telegram", "c1") is None


async def test_the_channel_default_answers_before_anyone_is_asked():
    """Ticket 03's per-Channel default profile stays the fallback."""
    directory = FakeDirectory("work", "home", default="home")
    outcome = await ChannelRouter(directory).handle(_inbound("hi"))
    assert isinstance(outcome, Reply)
    assert directory.gateways["home"].calls != []


async def test_a_peers_own_profile_beats_the_channel_default():
    directory = FakeDirectory("work", "home", default="home")
    peers.select_profile("telegram", "c1", "work")
    await ChannelRouter(directory).handle(_inbound("hi"))
    assert directory.gateways["home"].calls == []
    assert directory.gateways["work"].calls != []


async def test_a_peer_whose_profile_is_gone_is_asked_again():
    peers.select_profile("telegram", "c1", "archived-one")
    directory = FakeDirectory("work", "home")
    assert isinstance(await ChannelRouter(directory).handle(_inbound("hi")), Choose)


async def test_no_reachable_profile_is_refused():
    """Nothing to route to (no profile is running): say so rather than failing
    silently or raising into the adapter."""
    router = ChannelRouter(FakeDirectory())
    assert await router.handle(_inbound("hi")) == Refuse(NO_PROFILE)


async def test_a_platform_without_commands_is_never_placed_without_a_default():
    """The default profile is the only mode Discord and Slack have (ADR 0019). Placing
    one in the sole profile would record a selection it has no command to correct, and
    that selection would then outrank the default the operator later sets."""
    for platform in ("discord", "slack"):
        directory = FakeDirectory("work")
        outcome = await ChannelRouter(directory).handle(_inbound("hi", platform=platform))
        assert outcome == Refuse(NO_PROFILE)
        assert peers.get_peer(platform, "c1") is None


async def test_a_platform_without_commands_is_refused_rather_than_asked():
    """Discord and Slack have no command surface, so they cannot answer a Choose —
    they sit in their Channel's default profile or nowhere."""
    directory = FakeDirectory("work", "home")
    for platform in ("discord", "slack"):
        outcome = await ChannelRouter(directory).handle(_inbound("hi", platform=platform))
        assert outcome == Refuse(NO_PROFILE)


# --- /profile ---


async def test_profile_with_no_argument_offers_a_picker():
    directory = FakeDirectory("work", "home", default="work")
    outcome = await ChannelRouter(directory).handle(_inbound("/profile"))
    assert isinstance(outcome, Choose)
    assert [opt.label for opt in outcome.options] == ["Work", "Home"]


async def test_profile_with_a_name_switches_directly():
    directory = FakeDirectory("work", "home", default="work")
    outcome = await ChannelRouter(directory).handle(_inbound("/profile Home"))
    assert isinstance(outcome, Reply)
    assert peers.get_peer("telegram", "c1").profile == "home"


async def test_choosing_the_profile_you_are_already_in_is_not_a_switch():
    """It still pins the choice — a later change to the Channel default must not
    move a conversation the user placed by hand."""
    directory = FakeDirectory("work", "home", default="work")
    router = ChannelRouter(directory)
    assert await router.handle(_inbound("/profile Work")) == Reply("Already talking to Work.")

    directory.default = "home"
    await router.handle(_inbound("hi"))
    assert directory.gateways["home"].calls == []
    assert directory.gateways["work"].calls != []


async def test_an_unknown_profile_name_is_reported_not_guessed():
    directory = FakeDirectory("work", "home")
    outcome = await ChannelRouter(directory).handle(_inbound("/profile Hoem"))
    assert isinstance(outcome, Refuse)
    assert "Hoem" in outcome.text
    assert peers.get_peer("telegram", "c1") is None


async def test_switching_profile_leaves_the_chat_it_came_from_untouched():
    directory = FakeDirectory("work", "home", default="work")
    router = ChannelRouter(directory)
    await router.handle(_inbound("hello work"))
    first_chat = directory.gateways["work"].calls[0]["chat_id"]

    await router.handle(_inbound("/profile Home"))
    await router.handle(_inbound("hello home"))
    await router.handle(_inbound("/profile Work"))
    await router.handle(_inbound("hello again"))

    chats = [call["chat_id"] for call in directory.gateways["work"].calls]
    assert chats == [first_chat, chats[1]]
    assert chats[1] != first_chat


async def test_flipping_between_profiles_creates_no_chats():
    """The Chat is materialised by the first message, so a Peer that only ever
    switches never reaches a gateway at all."""
    directory = FakeDirectory("work", "home", default="work")
    router = ChannelRouter(directory)
    for name in ("Home", "Work", "Home"):
        assert isinstance(await router.handle(_inbound(f"/profile {name}")), Reply)
    assert directory.gateways["work"].calls == []
    assert directory.gateways["home"].calls == []


async def test_profile_is_refused_in_a_group():
    directory = FakeDirectory("work", "home", default="work")
    outcome = await ChannelRouter(directory).handle(
        _inbound("/profile", is_direct=False, mentioned=True)
    )
    assert isinstance(outcome, Refuse)
    assert directory.gateways["work"].calls == []


async def test_a_platform_without_commands_treats_a_slash_as_words():
    """Discord and Slack expose no commands, so nothing is intercepted there."""
    directory = FakeDirectory("work", default="work")
    await ChannelRouter(directory).handle(_inbound("/profile", platform="discord"))
    assert directory.gateways["work"].calls[0]["text"] == "/profile"


async def test_an_unrecognised_command_is_reported_not_sent_to_the_agent():
    directory = FakeDirectory("work", default="work")
    outcome = await ChannelRouter(directory).handle(_inbound("/wat"))
    assert isinstance(outcome, Refuse)
    assert "/help" in outcome.text
    assert directory.gateways["work"].calls == []


# --- answering a Choose ---


async def test_choosing_an_option_selects_that_profile():
    directory = FakeDirectory("work", "home")
    router = ChannelRouter(directory)
    outcome = await router.handle(_inbound("hi"))
    token = next(opt.token for opt in outcome.options if opt.label == "Home")

    assert isinstance(await router.choose(_inbound(""), token), Reply)
    assert peers.get_peer("telegram", "c1").profile == "home"


async def test_choosing_a_profile_that_has_since_gone_is_refused():
    directory = FakeDirectory("work", "home")
    outcome = await ChannelRouter(directory).choose(_inbound(""), "profile:gone")
    assert isinstance(outcome, Refuse)
    assert peers.get_peer("telegram", "c1") is None


async def test_an_option_from_no_known_picker_is_refused():
    """Tokens are namespaced per picker: a stale tap can't land in the wrong one."""
    directory = FakeDirectory("work")
    assert isinstance(await ChannelRouter(directory).choose(_inbound(""), "work"), Refuse)


# --- /new ---


async def test_new_starts_a_fresh_chat_and_leaves_the_old_one_alone():
    router, gateway = _router()
    await router.handle(_inbound("first"))
    left = gateway.calls[0]["chat_id"]

    assert isinstance(await router.handle(_inbound("/new")), Reply)
    await router.handle(_inbound("second"))

    assert gateway.calls[1]["chat_id"] != left
    assert left in gateway.chats  # still there, still listed
    assert gateway.deleted == []


async def test_new_in_a_chat_nothing_was_said_in_says_so():
    """There is nothing to leave behind, so it doesn't claim to have left one."""
    router, gateway = _router()
    assert await router.handle(_inbound("/new")) == Reply(ALREADY_NEW)
    assert gateway.calls == []


# --- /clear ---


async def test_clear_asks_before_deleting_anything():
    router, gateway = _router()
    await router.handle(_inbound("hi"))
    outcome = await router.handle(_inbound("/clear"))
    assert isinstance(outcome, Choose)
    assert gateway.deleted == []


async def test_confirming_clear_deletes_the_chat_the_peer_is_in():
    router, gateway = _router()
    await router.handle(_inbound("hi"))
    chat = gateway.calls[0]["chat_id"]

    outcome = await router.handle(_inbound("/clear"))
    confirm = next(opt.token for opt in outcome.options if opt.token.startswith("clear:"))
    assert isinstance(await router.choose(_inbound(""), confirm), Reply)

    assert gateway.deleted == [chat]
    assert peers.get_peer("telegram", "c1").chat is None
    assert peers.peer_for_chat(chat) is None


async def test_declining_clear_leaves_the_chat_untouched():
    router, gateway = _router()
    await router.handle(_inbound("hi"))
    chat = gateway.calls[0]["chat_id"]

    outcome = await router.handle(_inbound("/clear"))
    decline = next(opt.token for opt in outcome.options if opt.token.startswith("keep:"))
    assert isinstance(await router.choose(_inbound(""), decline), Reply)

    assert gateway.deleted == []
    assert peers.get_peer("telegram", "c1").chat == chat


async def test_the_message_after_a_cleared_chat_starts_a_new_one():
    router, gateway = _router()
    await router.handle(_inbound("hi"))
    outcome = await router.handle(_inbound("/clear"))
    confirm = next(opt.token for opt in outcome.options if opt.token.startswith("clear:"))
    await router.choose(_inbound(""), confirm)

    await router.handle(_inbound("hello again"))
    assert gateway.calls[1]["chat_id"] != gateway.calls[0]["chat_id"]


async def test_clear_with_nothing_to_delete_says_so():
    router, gateway = _router()
    outcome = await router.handle(_inbound("/clear"))
    assert isinstance(outcome, Reply)
    assert gateway.deleted == []


async def test_a_confirmation_only_deletes_the_chat_it_was_raised_for():
    """The picker can outlive the Chat it was shown in — a stale tap must not take
    whatever Chat the Peer has moved to since."""
    router, gateway = _router()
    await router.handle(_inbound("hi"))
    outcome = await router.handle(_inbound("/clear"))
    confirm = next(opt.token for opt in outcome.options if opt.token.startswith("clear:"))

    await router.handle(_inbound("/new"))
    await router.handle(_inbound("somewhere else"))

    assert isinstance(await router.choose(_inbound(""), confirm), Refuse)
    assert gateway.deleted == []


# --- /status ---


async def test_status_reports_the_profile_the_chat_and_its_size():
    router, gateway = _router()
    await router.handle(_inbound("hi"))
    gateway.chats[gateway.calls[0]["chat_id"]]["title"] = "Tax questions"

    outcome = await router.handle(_inbound("/status"))
    assert isinstance(outcome, Reply)
    assert "Work" in outcome.text
    assert "Tax questions" in outcome.text
    assert "1" in outcome.text


async def test_status_before_the_first_message_says_there_is_no_chat_yet():
    router, _ = _router()
    outcome = await router.handle(_inbound("/status"))
    assert isinstance(outcome, Reply)
    assert "Work" in outcome.text


# --- /help ---


async def test_every_listed_command_is_one_the_router_answers():
    """`/help` and the menu are built from COMMANDS, so nothing there can be a name
    the router refuses."""
    router, _ = _router()
    for command in COMMANDS:
        assert not isinstance(await router.handle(_inbound(f"/{command.name}")), Refuse)


async def test_help_lists_every_command():
    router, gateway = _router()
    outcome = await router.handle(_inbound("/help"))
    assert isinstance(outcome, Reply)
    for command in COMMANDS:
        assert f"/{command.name}" in outcome.text
        assert command.description in outcome.text
    assert gateway.calls == []


# --- who gets answered ---


async def test_group_without_a_mention_is_ignored():
    router, gateway = _router()
    outcome = await router.handle(_inbound(is_direct=False, mentioned=False))
    assert isinstance(outcome, Nothing)
    assert gateway.calls == []


async def test_group_with_a_mention_is_answered():
    router, _ = _router(reply="hello")
    outcome = await router.handle(_inbound(is_direct=False, mentioned=True))
    assert outcome == Reply("hello")


async def test_blank_message_with_no_attachment_is_ignored():
    router, gateway = _router()
    assert isinstance(await router.handle(_inbound("   ")), Nothing)
    assert gateway.calls == []


def test_accepts_gates_platform_feedback_the_same_way():
    router, _ = _router()
    assert router.accepts(_inbound()) is True
    assert router.accepts(_inbound(is_direct=False, mentioned=False)) is False
    assert router.accepts(_inbound("   ")) is False
    assert router.accepts(_inbound("", has_attachment=True)) is True


# --- attachments and questions ---


async def test_attachments_reach_the_gateway():
    router, gateway = _router()
    await router.handle(_inbound("look"), attachments=["<input>"])
    assert gateway.calls[0]["attachments"] == ["<input>"]


async def test_a_captionless_attachment_is_answered():
    """Inverted from the drop this seam used to pin: a wordless file is something to
    answer, and the model is given a prompt for it rather than an empty message."""
    router, gateway = _router()
    outcome = await router.handle(
        _inbound("", has_attachment=True),
        attachments=["<input>"],
    )
    assert isinstance(outcome, Reply)
    assert gateway.calls[0]["text"] == ATTACHMENT_ONLY_PROMPT
    assert gateway.calls[0]["attachments"] == ["<input>"]


async def test_a_captionless_attachment_in_a_group_still_needs_a_mention():
    router, gateway = _router()
    ignored = await router.handle(
        _inbound("", is_direct=False, has_attachment=True), attachments=["<input>"]
    )
    assert isinstance(ignored, Nothing)
    assert gateway.calls == []

    answered = await router.handle(
        _inbound("", is_direct=False, mentioned=True, has_attachment=True),
        attachments=["<input>"],
    )
    assert isinstance(answered, Reply)
    assert gateway.calls[0]["text"] == ATTACHMENT_ONLY_PROMPT


async def test_a_wordless_file_that_could_not_be_read_says_so():
    """The gate lets a wordless file through on the inbound fact, so the download can
    still come back empty (too large, unsupported). The turn says that rather than
    claiming a file the model never got."""
    router, gateway = _router()
    outcome = await router.handle(_inbound("", has_attachment=True), attachments=[])
    assert isinstance(outcome, Reply)
    assert gateway.calls[0]["text"] == ATTACHMENT_UNREADABLE


async def test_a_captioned_attachment_sends_the_caption_as_the_message():
    router, gateway = _router()
    await router.handle(_inbound("what is this?", has_attachment=True), attachments=["<input>"])
    assert gateway.calls[0]["text"] == "what is this?"


async def test_asker_is_bound_to_the_turn():
    router, gateway = _router()
    asker = object()
    await router.handle(_inbound(), asker=asker)
    assert gateway.calls[0]["asker"] is asker


# --- what an adapter renders an outcome as ---


def test_a_reply_and_a_refusal_are_both_spoken():
    assert spoken_text(Reply("the answer")) == "the answer"
    assert spoken_text(Refuse("you are not paired with this bot")) == (
        "you are not paired with this bot"
    )


def test_silent_outcomes_speak_nothing():
    assert spoken_text(Ack()) is None
    assert spoken_text(Nothing()) is None


def test_a_choice_is_not_spoken_as_bare_text():
    """Its options have to be rendered as buttons, so it is never sent as text alone."""
    choose = Choose("which profile?", (Option("Work", "p1"), Option("Home", "p2")))
    assert spoken_text(choose) is None
