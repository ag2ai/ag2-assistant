"""Tests for the channel router — the platform-neutral seam (no network, no agent).

Drives the router with normalised inbound messages and asserts the outcome it
returns, plus what reached the gateway underneath.
"""

import re
from datetime import datetime, timedelta

import pytest

from assistant import pairing, peers
from assistant.channels.base import InboundMessage
from assistant.channels.router import (
    ALREADY_NEW,
    ANSWERED_ELSEWHERE,
    ATTACHMENT_ONLY_PROMPT,
    ATTACHMENT_UNREADABLE,
    CHOOSE_INSTEAD,
    COMMANDS,
    NO_CHATS,
    NO_PROFILE,
    NO_PROFILE_HERE,
    PROFILE_WITHDRAWN,
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

# The account every test below speaks as. Numeric, because a numeric id is what a
# Paired account is ultimately keyed by (ADR 0021).
PAIRED_SENDER = "1001"


class FakeGateway:
    """Records send_message calls; returns a canned reply or raises. Keeps a tiny
    chat store so the lifecycle commands have something to report on and delete."""

    def __init__(self, reply: str = "the answer", error: Exception | None = None) -> None:
        self.reply = reply
        self.error = error
        self.calls: list[dict] = []
        self.chats: dict[str, dict] = {}
        self.transcripts: dict[str, list[dict]] = {}
        self.deleted: list[str] = []
        # inquiry id -> the chat it was raised in, its options, and its answer.
        self.inquiries: dict[str, dict] = {}
        self._mirror = None
        self._questions = None

    def set_mirror(self, mirror) -> None:
        """The callback a completed turn is handed to (the router's mirror)."""
        self._mirror = mirror

    def set_question_mirror(self, questions) -> None:
        """Who this runtime's questions — and their resolutions — are announced to."""
        self._questions = questions

    async def raise_question(self, chat_id: str, text: str, options=(), inquiry="inq-1") -> str:
        """A question a turn in ``chat_id`` has raised, persisted and announced the way
        the durable inquiry store does."""
        self.inquiries[inquiry] = {"chat": chat_id, "options": list(options), "answer": None}
        if self._questions is not None:
            await self._questions.ask(chat_id, inquiry, text, tuple(options))
        return inquiry

    async def answer_inquiry(self, inquiry: str, text: str = "", *, option: int | None = None):
        entry = self.inquiries.get(inquiry)
        if entry is None or entry["answer"] is not None:
            return False  # first answer wins, whichever surface it came from
        if option is not None:
            if not 0 <= option < len(entry["options"]):
                return False
            text = entry["options"][option]
        entry["answer"] = text
        if self._questions is not None:
            await self._questions.retract(entry["chat"], inquiry)
        return True

    def add_chat(self, chat_id: str, title: str, updated: str, messages=()) -> str:
        """A Chat this gateway already holds — a browser one, or one made earlier."""
        self.chats[chat_id] = {
            "chat_id": chat_id,
            "title": title,
            "preview": "",
            "updated": updated,
            "turns": len(messages) // 2,
        }
        self.transcripts[chat_id] = list(messages)
        return chat_id

    async def send_message(
        self, text, chat_id="default", asker=None, attachments=None, origin="", **kw
    ):
        self.calls.append(
            {"text": text, "chat_id": chat_id, "asker": asker, "attachments": attachments}
        )
        chat = self.chats.setdefault(
            chat_id, {"chat_id": chat_id, "title": "", "updated": "", "turns": 0}
        )
        chat["turns"] += 1
        if self.error is not None:
            raise self.error
        self.transcripts.setdefault(chat_id, []).extend(
            [{"role": "user", "text": text}, {"role": "agent", "text": self.reply}]
        )
        if self._mirror is not None:
            await self._mirror(chat_id, text, self.reply, origin=origin)
        return self.reply

    async def list_chats(self) -> list[dict]:
        return list(self.chats.values())

    async def transcript(self, chat_id: str) -> list[dict]:
        return list(self.transcripts.get(chat_id, []))

    async def delete_chat(self, chat_id: str) -> bool:
        self.deleted.append(chat_id)
        self.transcripts.pop(chat_id, None)
        return self.chats.pop(chat_id, None) is not None


class FakeDirectory:
    """Stands in for the ProfileManager: which profiles are running, which one a
    platform falls back to, and the gateway behind each."""

    def __init__(self, *names, default=None, reply="the answer", error=None) -> None:
        self.gateways = {name: FakeGateway(reply, error) for name in names}
        self.default = default
        # profile id → the surfaces it has been withdrawn from (default-allow).
        self.withdrawn: dict[str, set[str]] = {}
        # (platform, chat_id, text) pushed into a platform conversation.
        self.pushed: list[tuple[str, str, str]] = []
        # Questions shown in a platform conversation, and the ones taken back.
        self.asked: list[tuple[str, str, str, Choose]] = []
        self.retracted: list[tuple[str, str, str]] = []

    def withdraw(self, pid: str, *surfaces: str) -> None:
        self.withdrawn.setdefault(pid, set()).update(surfaces)

    def available_profiles(self, surface: str) -> tuple[AvailableProfile, ...]:
        return tuple(
            AvailableProfile(name, name.title())
            for name in self.gateways
            if surface not in self.withdrawn.get(name, ())
        )

    def default_profile(self, platform: str) -> str | None:
        return self.default

    def gateway_for_profile(self, pid):
        return self.gateways.get(pid)

    async def notify_channel(self, platform: str, chat_id: str, text: str) -> None:
        self.pushed.append((platform, chat_id, text))

    async def ask_channel(self, platform: str, chat_id: str, inquiry: str, question) -> None:
        self.asked.append((platform, chat_id, inquiry, question))

    async def retract_channel(self, platform: str, chat_id: str, inquiry: str) -> None:
        self.retracted.append((platform, chat_id, inquiry))


@pytest.fixture(autouse=True)
def _pair_the_sender():
    """A Channel serves nobody but a Paired account (ADR 0021), so the sender every
    test below speaks as is paired up front. The gate itself is exercised in its own
    section, by senders this fixture has not paired."""
    for platform in ("telegram", "discord", "slack"):
        pairing.add_account(platform, PAIRED_SENDER)


def _inbound(
    text="hi",
    *,
    is_direct=True,
    mentioned=False,
    platform="telegram",
    chat_id="c1",
    has_attachment=False,
    sender_id=PAIRED_SENDER,
    sender_handle=None,
):
    return InboundMessage(
        text=text,
        sender_id=sender_id,
        chat_id=chat_id,
        platform=platform,
        is_direct=is_direct,
        mentioned=mentioned,
        has_attachment=has_attachment,
        sender_handle=sender_handle,
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
    assert re.fullmatch(r"discord-[0-9a-f]{8}", chat)
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


# --- channel exposure ---


async def test_a_withdrawn_profile_is_absent_from_the_picker():
    directory = FakeDirectory("work", "home")
    directory.withdraw("home", "telegram:dm")
    outcome = await ChannelRouter(directory).handle(_inbound("/profile"))
    assert isinstance(outcome, Choose)
    assert [opt.token for opt in outcome.options] == ["profile:work"]


async def test_a_withdrawn_profile_cannot_be_selected_by_name():
    directory = FakeDirectory("work", "home")
    directory.withdraw("home", "telegram:dm")
    outcome = await ChannelRouter(directory).handle(_inbound("/profile Home"))
    assert isinstance(outcome, Refuse)
    assert "Home" in outcome.text
    assert peers.get_peer("telegram", "c1") is None


async def test_a_tap_on_a_withdrawn_profile_is_not_honoured():
    """A picker offered before the withdrawal must not still place the Peer there."""
    directory = FakeDirectory("work", "home")
    directory.withdraw("home", "telegram:dm")
    assert await ChannelRouter(directory).choose(_inbound("."), "profile:home") == Refuse(
        NO_PROFILE
    )
    assert peers.get_peer("telegram", "c1") is None


async def test_withdrawing_under_a_live_peer_stops_its_next_message():
    """The same router, no restart: the next message is not run, and never lands in
    another profile — it was written for the one that has gone."""
    directory = FakeDirectory("work", "home")
    router = ChannelRouter(directory)
    await router.handle(_inbound("/profile Home"))

    directory.withdraw("home", "telegram:dm")
    outcome = await router.handle(_inbound("something for home"))
    assert isinstance(outcome, Choose)
    assert outcome.text == CHOOSE_INSTEAD
    assert [opt.token for opt in outcome.options] == ["profile:work"]
    assert directory.gateways["home"].calls == []
    assert directory.gateways["work"].calls == []
    assert peers.get_peer("telegram", "c1").profile == "home"


async def test_a_peer_left_with_nothing_to_choose_is_told_so():
    directory = FakeDirectory("home")
    router = ChannelRouter(directory)
    await router.handle(_inbound("hi"))

    directory.withdraw("home", "telegram:dm")
    assert await router.handle(_inbound("hi again")) == Refuse(NO_PROFILE_HERE)
    assert len(directory.gateways["home"].calls) == 1  # only the message before the withdrawal


async def test_telegram_groups_are_withdrawn_independently_of_direct_messages():
    directory = FakeDirectory("work", "home")
    directory.withdraw("home", "telegram:group")
    router = ChannelRouter(directory)
    peers.select_profile("telegram", "g1", "home", surface="group")
    peers.select_profile("telegram", "c1", "home")

    group = await router.handle(_inbound("hi", chat_id="g1", is_direct=False, mentioned=True))
    assert group == Refuse(PROFILE_WITHDRAWN)  # a group has no picker to offer
    assert isinstance(await router.handle(_inbound("hi")), Reply)  # the DM still lands there


async def test_the_sole_profile_fallback_does_not_reach_a_withdrawn_profile():
    directory = FakeDirectory("work")
    directory.withdraw("work", "telegram:dm")
    assert await ChannelRouter(directory).handle(_inbound("hi")) == Refuse(NO_PROFILE)


async def test_a_withdrawn_channel_default_is_not_smuggled_back():
    directory = FakeDirectory("work", default="work")
    directory.withdraw("work", "discord")
    outcome = await ChannelRouter(directory).handle(_inbound("hi", platform="discord"))
    assert outcome == Refuse(NO_PROFILE)
    assert directory.gateways["work"].calls == []


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


# --- who may reach the bot at all (ADR 0021) ---


UNPAIRED = "2002"


async def test_an_unpaired_account_is_told_nothing_at_all():
    """Not that a profile named 'work' exists, not that the install does."""
    directory = FakeDirectory("work", default="work")
    outcome = await ChannelRouter(directory).handle(_inbound("hi", sender_id=UNPAIRED))
    assert isinstance(outcome, Nothing)
    assert directory.gateways["work"].calls == []


async def test_an_unpaired_account_cannot_enumerate_profiles_with_a_command():
    """Pairing gates commands exactly as it gates ordinary messages."""
    directory = FakeDirectory("work", "home", default="work")
    router = ChannelRouter(directory)
    assert isinstance(await router.handle(_inbound("/profile", sender_id=UNPAIRED)), Nothing)
    assert isinstance(await router.handle(_inbound("/wat", sender_id=UNPAIRED)), Nothing)


async def test_an_unpaired_account_cannot_answer_a_picker():
    directory = FakeDirectory("work", "home")
    outcome = await ChannelRouter(directory).choose(_inbound("", sender_id=UNPAIRED), "work")
    assert isinstance(outcome, Nothing)
    assert peers.get_peer("telegram", "c1") is None


async def test_an_unpaired_account_is_visible_to_an_adapter_as_unpaired():
    """``paired`` is what an adapter checks before acting on anything a message
    implies — a button tap or a typed answer to a running question, not just a turn."""
    router, _ = _router()
    assert router.paired(_inbound("hi", sender_id=UNPAIRED)) is False
    assert router.paired(_inbound("hi")) is True


async def test_the_adapter_gate_stays_shut_for_an_unpaired_account():
    """``accepts`` is what an adapter shows a placeholder on — it must not light up
    for someone who will be answered with silence, even when what they sent is
    code-shaped and so does reach the router."""
    router, _ = _router()
    assert router.accepts(_inbound("hi", sender_id=UNPAIRED)) is False
    assert router.accepts(_inbound("AAAA-1111", sender_id=UNPAIRED)) is False
    assert router.accepts(_inbound("hi")) is True


async def test_a_pairing_code_admits_the_account_that_sends_it():
    code = pairing.issue_code("telegram")
    directory = FakeDirectory("work", default="work")
    router = ChannelRouter(directory)

    outcome = await router.handle(_inbound(code, sender_id=UNPAIRED))
    assert isinstance(outcome, Reply)
    # The code itself is not a turn — it pairs, and the next message is the first one.
    assert directory.gateways["work"].calls == []
    assert isinstance(await router.handle(_inbound("hi", sender_id=UNPAIRED)), Reply)


async def test_an_expired_code_is_reported_as_expired():
    code = pairing.issue_code("telegram", ttl=-1)
    router, _ = _router(default="work")
    outcome = await router.handle(_inbound(code, sender_id=UNPAIRED))
    assert isinstance(outcome, Refuse)
    assert "expired" in outcome.text.lower()


async def test_a_code_that_was_never_issued_is_met_with_silence():
    router, _ = _router(default="work")
    assert isinstance(await router.handle(_inbound("AAAA-1111", sender_id=UNPAIRED)), Nothing)


async def test_an_invited_handle_is_admitted_and_pinned_when_it_first_speaks():
    pairing.add_account("telegram", "@nikita")
    router, gateway = _router(default="work")
    outcome = await router.handle(_inbound("hi", sender_id=UNPAIRED, sender_handle="nikita"))
    assert isinstance(outcome, Reply)
    assert gateway.calls != []
    assert pairing.is_paired("telegram", UNPAIRED) is True


async def test_a_later_holder_of_a_pinned_handle_is_not_admitted():
    pairing.add_account("telegram", "@nikita")
    router, gateway = _router(default="work")
    await router.handle(_inbound("hi", sender_id=UNPAIRED, sender_handle="nikita"))
    outcome = await router.handle(_inbound("hi", sender_id="3003", sender_handle="nikita"))
    assert isinstance(outcome, Nothing)
    assert len(gateway.calls) == 1


async def test_a_platform_with_no_paired_accounts_answers_nobody():
    """The intended failure mode of a token pasted before anyone is paired."""
    directory = FakeDirectory("work", default="work")
    outcome = await ChannelRouter(directory).handle(
        _inbound("hi", platform="discord", sender_id=UNPAIRED)
    )
    assert isinstance(outcome, Nothing)


async def test_revoking_an_account_takes_effect_on_its_next_message():
    router, gateway = _router(default="work")
    assert isinstance(await router.handle(_inbound("hi")), Reply)
    pairing.revoke("telegram", PAIRED_SENDER)
    assert isinstance(await router.handle(_inbound("hi again")), Nothing)
    assert len(gateway.calls) == 1


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


# --- /resume ---


def _ago(**delta) -> str:
    return (datetime.now().astimezone() - timedelta(**delta)).isoformat()


def _resume_tokens(outcome) -> list[str]:
    return [opt.token.removeprefix("resume:") for opt in outcome.options]


async def test_resume_lists_the_chats_of_the_profile_most_recent_first():
    router, gateway = _router()
    gateway.add_chat("web-old", "Last month's taxes", _ago(days=30))
    gateway.add_chat("web-new", "Dinner plans", _ago(minutes=5))

    outcome = await router.handle(_inbound("/resume"))
    assert isinstance(outcome, Choose)
    assert _resume_tokens(outcome) == ["web-new", "web-old"]


async def test_a_resume_entry_shows_a_title_and_a_relative_time():
    router, gateway = _router()
    gateway.add_chat("web-1", "Dinner plans", _ago(hours=3))
    outcome = await router.handle(_inbound("/resume"))
    assert outcome.options[0].label == "Dinner plans · 3h ago"


async def test_a_chat_begun_in_the_browser_is_offered_beside_a_channel_one():
    """The Peer's own Chats and the browser's are the same Chats (ADR 0020)."""
    router, gateway = _router()
    await router.handle(_inbound("hi"))
    mine = gateway.calls[0]["chat_id"]
    gateway.add_chat("web-1", "Dinner plans", _ago(minutes=1))

    outcome = await router.handle(_inbound("/resume"))
    assert set(_resume_tokens(outcome)) == {mine, "web-1"}


async def test_resuming_attaches_the_peer_and_the_next_message_continues_that_chat():
    router, gateway = _router()
    gateway.add_chat(
        "web-1",
        "Dinner plans",
        _ago(hours=2),
        [{"role": "user", "text": "book a table"}, {"role": "agent", "text": "for when?"}],
    )

    outcome = await router.handle(_inbound("/resume"))
    assert isinstance(await router.choose(_inbound(""), outcome.options[0].token), Reply)
    assert peers.get_peer("telegram", "c1").chat == "web-1"

    await router.handle(_inbound("friday"))
    assert gateway.calls[0]["chat_id"] == "web-1"


async def test_attaching_shows_a_header_and_the_tail_of_the_transcript():
    router, gateway = _router()
    gateway.add_chat(
        "web-1",
        "Dinner plans",
        _ago(hours=2),
        [{"role": "user", "text": "book a table"}, {"role": "agent", "text": "for when?"}],
    )

    outcome = await router.choose(_inbound(""), "resume:web-1")
    assert isinstance(outcome, Reply)
    assert "Dinner plans" in outcome.text  # title
    assert "Work" in outcome.text  # profile
    assert "1 exchanges" in outcome.text  # size
    assert "2h ago" in outcome.text  # when it was last touched
    assert "book a table" in outcome.text and "for when?" in outcome.text


async def test_attaching_shows_only_the_tail_of_a_long_transcript():
    router, gateway = _router()
    messages = [{"role": "user", "text": f"turn {i}"} for i in range(40)]
    gateway.add_chat("web-1", "Long one", _ago(minutes=1), messages)

    outcome = await router.choose(_inbound(""), "resume:web-1")
    assert "turn 39" in outcome.text
    assert "turn 0" not in outcome.text


async def test_the_chat_a_peer_leaves_is_unchanged_and_can_be_returned_to():
    router, gateway = _router()
    await router.handle(_inbound("hi"))
    left = gateway.calls[0]["chat_id"]
    gateway.add_chat("web-1", "Dinner plans", _ago(minutes=1))

    await router.choose(_inbound(""), "resume:web-1")
    await router.handle(_inbound("friday"))
    await router.choose(_inbound(""), f"resume:{left}")
    await router.handle(_inbound("back again"))

    assert [call["chat_id"] for call in gateway.calls] == [left, "web-1", left]
    assert gateway.deleted == []


async def test_attaching_creates_no_chat_and_deletes_none():
    router, gateway = _router()
    gateway.add_chat("web-1", "Dinner plans", _ago(minutes=1))
    before = set(gateway.chats)

    assert isinstance(await router.handle(_inbound("/resume")), Choose)
    assert isinstance(await router.choose(_inbound(""), "resume:web-1"), Reply)

    assert set(gateway.chats) == before
    assert gateway.deleted == []
    assert gateway.calls == []


async def test_resume_in_a_profile_with_no_chats_says_so_rather_than_offering_nothing():
    router, gateway = _router()
    outcome = await router.handle(_inbound("/resume"))
    assert outcome == Reply(NO_CHATS)
    assert gateway.calls == []


async def test_resume_offers_only_the_chats_of_the_profile_the_peer_is_in():
    directory = FakeDirectory("work", "home", default="work")
    directory.gateways["home"].add_chat("web-home", "Home things", _ago(minutes=1))
    directory.gateways["work"].add_chat("web-work", "Work things", _ago(minutes=1))
    router = ChannelRouter(directory)

    outcome = await router.handle(_inbound("/resume"))
    assert _resume_tokens(outcome) == ["web-work"]


async def test_no_chat_is_offered_from_a_profile_withdrawn_from_this_surface():
    """Exposure withdraws the Profile's Chats along with the Profile itself."""
    directory = FakeDirectory("home")
    directory.gateways["home"].add_chat("web-1", "Dinner plans", _ago(minutes=1))
    router = ChannelRouter(directory)
    await router.handle(_inbound("hi"))

    directory.withdraw("home", "telegram:dm")
    assert await router.handle(_inbound("/resume")) == Refuse(NO_PROFILE_HERE)
    assert await router.choose(_inbound(""), "resume:web-1") == Refuse(NO_PROFILE_HERE)
    assert peers.get_peer("telegram", "c1").chat != "web-1"


async def test_resuming_a_chat_that_has_since_gone_is_refused():
    router, gateway = _router()
    gateway.add_chat("web-1", "Dinner plans", _ago(minutes=1))
    outcome = await router.handle(_inbound("/resume"))

    gateway.chats.pop("web-1")  # deleted from the browser while the picker was open
    assert isinstance(await router.choose(_inbound(""), outcome.options[0].token), Refuse)
    assert peers.get_peer("telegram", "c1").chat is None


async def test_a_resumed_chat_still_delivers_a_task_outcome_to_the_peer():
    """Attaching makes the Chat the Peer's own, so a task started in it comes back
    here rather than nowhere."""
    router, gateway = _router()
    gateway.add_chat("web-1", "Dinner plans", _ago(minutes=1))
    await router.choose(_inbound(""), "resume:web-1")
    assert peers.peer_for_chat("web-1").chat_id == "c1"


# --- the mirror ---


def _mirroring(*names, **kw) -> tuple[ChannelRouter, FakeDirectory]:
    """A router wired the way a running install is: every gateway hands its completed
    turns back to the router, which pushes them to the Peer attached to that Chat."""
    directory = FakeDirectory(*(names or ("work",)), **kw)
    router = ChannelRouter(directory)
    for gateway in directory.gateways.values():
        gateway.set_mirror(router.mirror)
        gateway.set_question_mirror(router)
    return router, directory


async def _browser_turn(directory: FakeDirectory, chat: str, text: str) -> None:
    """A turn run from the browser — nobody's Peer wrote it."""
    await directory.gateways["work"].send_message(text, chat_id=chat)


async def _resume(router: ChannelRouter, chat: str) -> None:
    await router.choose(_inbound(""), f"resume:{chat}")


async def test_a_browser_turn_reaches_the_peer_attached_to_that_chat():
    """Both halves of it: what was written there, and what the agent answered."""
    router, directory = _mirroring(reply="It's sunny.")
    directory.gateways["work"].add_chat("web-1", "Dinner plans", _ago(minutes=1))
    await _resume(router, "web-1")

    await _browser_turn(directory, "web-1", "what's the weather?")

    assert directory.pushed == [("telegram", "c1", "You: what's the weather?\n\nMe: It's sunny.")]


async def test_a_peer_does_not_get_its_own_message_back():
    router, directory = _mirroring()
    await router.handle(_inbound("hi"))
    assert directory.pushed == []


async def test_a_chat_no_peer_is_attached_to_mirrors_to_nobody():
    router, directory = _mirroring()
    await _browser_turn(directory, "web-1", "just me here")
    assert directory.pushed == []


async def test_a_chat_a_peer_has_left_mirrors_to_nobody():
    """Owning a Chat is not being attached to it — only the attached one mirrors."""
    router, directory = _mirroring()
    gateway = directory.gateways["work"]
    gateway.add_chat("web-1", "Dinner plans", _ago(minutes=2))
    gateway.add_chat("web-2", "Taxes", _ago(minutes=1))
    await _resume(router, "web-1")
    await _resume(router, "web-2")

    await _browser_turn(directory, "web-1", "still there?")

    assert directory.pushed == []


async def test_starting_a_fresh_chat_stops_the_mirror_of_the_one_left_behind():
    router, directory = _mirroring()
    await router.handle(_inbound("hi"))
    left = directory.gateways["work"].calls[0]["chat_id"]
    await router.handle(_inbound("/new"))

    await _browser_turn(directory, left, "anyone?")

    assert directory.pushed == []


async def test_switching_profile_stops_the_mirror_of_the_chat_left_behind():
    """A switch always opens a fresh Chat, so the one left behind goes quiet too."""
    router, directory = _mirroring("work", "home", default="work")
    await router.handle(_inbound("hi"))
    left = directory.gateways["work"].calls[0]["chat_id"]
    await router.handle(_inbound("/profile home"))

    await _browser_turn(directory, left, "anyone?")

    assert directory.pushed == []


async def test_a_wordless_reply_is_not_mirrored_as_an_empty_line():
    router, directory = _mirroring(reply="")
    directory.gateways["work"].add_chat("web-1", "Dinner plans", _ago(minutes=1))
    await _resume(router, "web-1")

    await _browser_turn(directory, "web-1", "hello")

    assert directory.pushed == [("telegram", "c1", "You: hello")]


async def test_a_mirrored_turn_reads_like_a_resumed_one():
    """Same speaker labels as the transcript tail an attach shows, so a conversation
    looks the same however it got to the platform."""
    router, directory = _mirroring(reply="for when?")
    directory.gateways["work"].add_chat(
        "web-1",
        "Dinner plans",
        _ago(minutes=1),
        [{"role": "user", "text": "book a table"}, {"role": "agent", "text": "for when?"}],
    )
    attached = await router.choose(_inbound(""), "resume:web-1")

    await _browser_turn(directory, "web-1", "book a table")

    assert directory.pushed[0][2] in attached.text


# --- questions in the mirror ---


async def _attached() -> tuple[ChannelRouter, FakeGateway, FakeDirectory]:
    """A Peer attached to a Chat that was started in the browser."""
    router, directory = _mirroring()
    gateway = directory.gateways["work"]
    gateway.add_chat("web-1", "Dinner plans", _ago(minutes=1))
    await _resume(router, "web-1")
    return router, gateway, directory


async def test_a_question_from_a_browser_turn_reaches_the_attached_peer():
    """With its options, so the phone shows exactly what the browser shows."""
    router, gateway, directory = await _attached()

    await gateway.raise_question("web-1", "Which table?", ("By the window", "Out back"))

    platform, chat_id, inquiry, question = directory.asked[0]
    assert (platform, chat_id, inquiry) == ("telegram", "c1", "inq-1")
    assert question.text == "Which table?"
    assert [option.label for option in question.options] == ["By the window", "Out back"]


async def test_a_question_in_a_chat_no_peer_is_attached_to_reaches_nobody():
    router, directory = _mirroring()
    await directory.gateways["work"].raise_question("web-1", "Which table?", ("Either",))
    assert directory.asked == []


async def test_tapping_a_mirrored_option_answers_the_question():
    router, gateway, directory = await _attached()
    await gateway.raise_question("web-1", "Which table?", ("By the window", "Out back"))
    question = directory.asked[0][3]

    outcome = await router.choose(_inbound(""), question.options[1].token)

    assert isinstance(outcome, Nothing)
    assert gateway.inquiries["inq-1"]["answer"] == "Out back"


async def test_replying_to_a_mirrored_question_answers_it():
    """A free-text question has no options to tap, so the reply is the answer."""
    router, gateway, _ = await _attached()
    await gateway.raise_question("web-1", "What time?", ())

    outcome = await router.answer(_inbound("eight"), "inq-1", "eight")

    assert isinstance(outcome, Nothing)
    assert gateway.inquiries["inq-1"]["answer"] == "eight"


async def test_answering_in_the_browser_retracts_the_prompt_on_the_platform():
    router, gateway, directory = await _attached()
    await gateway.raise_question("web-1", "Which table?", ("By the window",))

    await gateway.answer_inquiry("inq-1", "By the window")

    assert directory.retracted == [("telegram", "c1", "inq-1")]


async def test_a_question_is_resolved_exactly_once():
    """Both surfaces are showing it; the second one to answer is told it arrived late
    and cannot overwrite what the first one said."""
    router, gateway, directory = await _attached()
    await gateway.raise_question("web-1", "Which table?", ("By the window", "Out back"))
    question = directory.asked[0][3]

    await router.choose(_inbound(""), question.options[0].token)
    late = await router.choose(_inbound(""), question.options[1].token)

    assert late == Refuse(ANSWERED_ELSEWHERE)
    assert gateway.inquiries["inq-1"]["answer"] == "By the window"


async def test_an_unpaired_account_cannot_answer_a_mirrored_question():
    router, gateway, directory = await _attached()
    await gateway.raise_question("web-1", "Which table?", ("By the window",))
    question = directory.asked[0][3]

    outcome = await router.choose(_inbound("", sender_id="9999"), question.options[0].token)

    assert isinstance(outcome, Nothing)
    assert gateway.inquiries["inq-1"]["answer"] is None


async def test_a_question_whose_profile_is_out_of_reach_is_not_answered():
    router, gateway, directory = await _attached()
    await gateway.raise_question("web-1", "Which table?", ("By the window",))
    question = directory.asked[0][3]
    directory.withdraw("work", "telegram:dm")

    assert isinstance(await router.choose(_inbound(""), question.options[0].token), Refuse)
    assert gateway.inquiries["inq-1"]["answer"] is None


async def test_a_tapped_option_that_no_longer_exists_is_refused():
    router, gateway, _ = await _attached()
    await gateway.raise_question("web-1", "Which table?", ("By the window",))

    assert isinstance(await router.choose(_inbound(""), "answer:inq-1:7"), Refuse)
    assert isinstance(await router.choose(_inbound(""), "answer:inq-1:"), Refuse)


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
