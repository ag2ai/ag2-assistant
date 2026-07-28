"""Tests for the channel router — the platform-neutral seam (no network, no agent).

Drives the router with normalised inbound messages and asserts the outcome it
returns, plus what reached the gateway underneath.
"""

from assistant.channels.base import InboundMessage
from assistant.channels.router import (
    NO_PROFILE,
    Ack,
    ChannelRouter,
    Choose,
    Nothing,
    Option,
    Refuse,
    Reply,
    spoken_text,
)


class FakeGateway:
    """Records send_message calls; returns a canned reply or raises."""

    def __init__(self, reply: str = "the answer", error: Exception | None = None) -> None:
        self.reply = reply
        self.error = error
        self.calls: list[dict] = []

    async def send_message(self, text, chat_id="default", asker=None, attachments=None, **kw):
        self.calls.append(
            {"text": text, "chat_id": chat_id, "asker": asker, "attachments": attachments}
        )
        if self.error is not None:
            raise self.error
        return self.reply


def _inbound(text="hi", *, is_direct=True, mentioned=False, platform="telegram") -> InboundMessage:
    return InboundMessage(
        text=text,
        sender_id="u1",
        chat_id="c1",
        platform=platform,
        is_direct=is_direct,
        mentioned=mentioned,
    )


def _router(**kw) -> tuple[ChannelRouter, FakeGateway]:
    gateway = FakeGateway(**kw)
    return ChannelRouter(lambda inbound: gateway), gateway


# --- what comes back ---


async def test_dm_returns_the_gateway_reply():
    router, gateway = _router(reply="4")
    outcome = await router.handle(_inbound("what is 2+2?"))
    assert outcome == Reply("4")
    assert gateway.calls[0]["text"] == "what is 2+2?"


async def test_turn_runs_on_the_peers_own_chat():
    router, gateway = _router()
    await router.handle(_inbound(platform="discord"))
    assert gateway.calls[0]["chat_id"] == "discord:c1"


async def test_gateway_failure_becomes_a_reply_not_an_exception():
    router, gateway = _router(error=RuntimeError("boom"))
    outcome = await router.handle(_inbound())
    assert isinstance(outcome, Reply)
    assert "boom" in outcome.text


# --- which profile the turn lands in ---


async def test_the_profile_is_resolved_per_message():
    """One adapter serves the whole install: the runtime is picked when the message
    arrives, not captured when the channel started."""
    work, home = FakeGateway(reply="from work"), FakeGateway(reply="from home")
    by_chat = {"c1": work, "c2": home}
    router = ChannelRouter(lambda inbound: by_chat.get(inbound.chat_id))

    first = await router.handle(_inbound("hi"))
    second = await router.handle(
        InboundMessage(text="hi", sender_id="u1", chat_id="c2", platform="telegram", is_direct=True)
    )
    assert (first, second) == (Reply("from work"), Reply("from home"))


async def test_no_reachable_profile_is_refused():
    """Nothing to route to (no default profile, or its runtime is gone): say so
    rather than failing silently or raising into the adapter."""
    router = ChannelRouter(lambda inbound: None)
    assert await router.handle(_inbound("hi")) == Refuse(NO_PROFILE)


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


# --- attachments and questions ---


async def test_attachments_reach_the_gateway():
    router, gateway = _router()
    await router.handle(_inbound("look"), attachments=["<input>"])
    assert gateway.calls[0]["attachments"] == ["<input>"]


async def test_a_captionless_attachment_is_dropped_by_the_mention_gate():
    """Today's behaviour, carried through the seam unchanged: the gate rejects a
    message with no words, so a file sent with no caption is never answered."""
    router, gateway = _router()
    assert isinstance(await router.handle(_inbound(""), attachments=["<input>"]), Nothing)
    assert gateway.calls == []


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
