"""Turn-crash recovery: dangling tool-call repair + HITL-aware turn timeout.

Covers the failure chain where a turn dies mid-tool-call (e.g. the turn timeout
fired while a permission prompt waited for the user): the session history is left
with a ModelResponse carrying tool_calls but no matching ToolResultsEvent, and
every later turn then fails at the provider with "No tool output found for
function call …". The fixes under test:

  1. `repair_dangling_tool_calls(events)` — inserts a synthetic tool result after
     any unanswered tool call, so the history is valid again.
  2. `wait_reply(coro, timeout, hitl_pending=…)` — a turn timeout whose clock
     pauses while a human-in-the-loop prompt is open and restarts once it's
     answered.
  3. Wiring — poisoned sessions are repaired at turn start (gateway chat, which
     task runs also go through), and every blocking asker reports `has_pending()`.
"""

import asyncio

import pytest
from ag2.events import (
    ModelMessage,
    ModelResponse,
    ToolCallEvent,
    ToolCallsEvent,
    ToolResultsEvent,
)
from ag2.events.tool_events import ToolResult, ToolResultEvent

from assistant.gateway.core import Gateway
from assistant.gateway.repair import repair_dangling_tool_calls, wait_reply
from assistant.hitl.base import Question
from assistant.hitl.desktop import HitlServer
from assistant.hitl.gateway import GatewayAsker
from assistant.hitl.inquiry import DurableAsker, InquiryStore, NullAsker
from tests.conftest import FakeAgent


def _response_with_calls(*call_ids: str):
    calls = [ToolCallEvent(id=cid, name="read_file", arguments="{}") for cid in call_ids]
    return ModelResponse(
        message=ModelMessage(content="working on it"), tool_calls=ToolCallsEvent(calls=calls)
    )


def _results_for(*call_ids: str):
    return ToolResultsEvent(
        [
            ToolResultEvent(parent_id=cid, name="read_file", result=ToolResult("ok"))
            for cid in call_ids
        ]
    )


# --- repair_dangling_tool_calls -------------------------------------------------


def test_repair_appends_synthetic_result_for_dangling_call():
    """A tool call with no result gets a synthetic ToolResultsEvent right after it."""
    poisoned = [_response_with_calls("call_1")]
    repaired = repair_dangling_tool_calls(poisoned)

    assert repaired is not None
    assert len(repaired) == 2
    synthetic = repaired[1]
    assert isinstance(synthetic, ToolResultsEvent)
    assert [r.parent_id for r in synthetic.results] == ["call_1"]


def test_repair_returns_none_when_history_valid():
    valid = [_response_with_calls("call_1"), _results_for("call_1")]
    assert repair_dangling_tool_calls(valid) is None


def test_repair_returns_none_for_empty_history():
    assert repair_dangling_tool_calls([]) is None


def test_repair_only_covers_unanswered_calls():
    """Of two calls in one response, only the unanswered one gets a synthetic result."""
    events = [_response_with_calls("call_a", "call_b"), _results_for("call_a")]
    repaired = repair_dangling_tool_calls(events)

    assert repaired is not None
    synthetic = [
        e
        for e in repaired
        if isinstance(e, ToolResultsEvent) and any(r.parent_id == "call_b" for r in e.results)
    ]
    assert len(synthetic) == 1
    # call_a already has its real result; no duplicate synthetic for it
    assert all(r.parent_id == "call_b" for r in synthetic[0].results)


def test_repair_inserts_result_directly_after_its_response():
    """The synthetic result lands right after the dangling response, before later events."""
    later = _response_with_calls()  # a plain assistant reply, no tool calls
    events = [_response_with_calls("call_1"), later]
    repaired = repair_dangling_tool_calls(events)

    assert repaired is not None
    assert isinstance(repaired[1], ToolResultsEvent)
    assert repaired[2] is later


def test_repair_marks_synthetic_result_as_interrupted():
    """The synthetic result tells the model the call was interrupted (not a real output)."""
    repaired = repair_dangling_tool_calls([_response_with_calls("call_1")])
    result = repaired[1].results[0]
    text = " ".join(str(getattr(p, "content", p)) for p in result.result.parts)
    assert "interrupt" in text.lower()


def test_repair_does_not_mutate_the_input_list():
    poisoned = [_response_with_calls("call_1")]
    repair_dangling_tool_calls(poisoned)
    assert len(poisoned) == 1


# --- wait_reply (HITL-aware turn timeout) ---------------------------------------


async def test_wait_reply_returns_result():
    async def quick():
        return "done"

    assert await wait_reply(quick(), timeout=1.0) == "done"


async def test_wait_reply_propagates_exceptions():
    async def boom():
        raise ValueError("nope")

    with pytest.raises(ValueError):
        await wait_reply(boom(), timeout=1.0)


async def test_wait_reply_times_out_and_cancels():
    cancelled = asyncio.Event()

    async def slow():
        try:
            await asyncio.sleep(30)
        except asyncio.CancelledError:
            cancelled.set()
            raise

    with pytest.raises(asyncio.TimeoutError):
        await wait_reply(slow(), timeout=0.1, hitl_pending=lambda: False, slice_seconds=0.02)
    await asyncio.wait_for(cancelled.wait(), timeout=1.0)


async def test_wait_reply_clock_pauses_while_hitl_pending():
    """A coro slower than the timeout still completes if a HITL prompt was open
    the whole time — waiting on the human must not burn the turn budget."""
    done = asyncio.Event()

    async def slow():
        await asyncio.sleep(0.3)  # 3x the timeout
        done.set()
        return "answered"

    result = await wait_reply(
        slow(), timeout=0.1, hitl_pending=lambda: not done.is_set(), slice_seconds=0.02
    )
    assert result == "answered"


async def test_wait_reply_times_out_after_hitl_resolves():
    """Once no prompt is pending, the clock runs again and the timeout still fires."""
    with pytest.raises(asyncio.TimeoutError):
        await wait_reply(
            asyncio.sleep(30), timeout=0.1, hitl_pending=lambda: False, slice_seconds=0.02
        )


async def test_wait_reply_budget_resets_after_hitl_answer():
    """Budget burned BEFORE the prompt opened must not kill the turn right after
    the user answers — answering restarts the turn budget."""
    prompt_open = asyncio.Event()
    prompt_answered = asyncio.Event()

    async def turn():
        await asyncio.sleep(0.15)  # agent work: most of the 0.2 budget
        prompt_open.set()
        await asyncio.sleep(0.1)  # user thinks (clock paused)
        prompt_answered.set()
        await asyncio.sleep(0.15)  # post-answer work: needs a fresh budget
        return "finished"

    def pending():
        return prompt_open.is_set() and not prompt_answered.is_set()

    result = await wait_reply(turn(), timeout=0.2, hitl_pending=pending, slice_seconds=0.02)
    assert result == "finished"


async def test_wait_reply_pause_has_hard_ceiling():
    """A stuck asker (has_pending never clears) cannot hang the turn forever."""
    with pytest.raises(asyncio.TimeoutError):
        await wait_reply(
            asyncio.sleep(30),
            timeout=0.05,
            hitl_pending=lambda: True,
            slice_seconds=0.01,
            max_pause=0.1,
        )


# --- has_pending across askers -----------------------------------------------------


async def test_gateway_asker_reports_pending_prompt():
    """has_pending() is True exactly while ask() awaits an answer."""
    server = HitlServer()
    asker = GatewayAsker(server)
    assert asker.has_pending() is False

    task = asyncio.create_task(asker.ask(Question(text="allow?", options=["yes", "no"])))
    await asyncio.sleep(0.01)  # let ask() register the question
    assert asker.has_pending() is True

    (req_id,) = [p["id"] for p in server.pending_list()]
    server.answer(req_id, "yes")
    assert await task == "yes"
    assert asker.has_pending() is False


async def test_durable_asker_reports_pending_prompt(tmp_path):
    """DurableAsker — the normal web-chat asker — must pause the turn clock too."""
    store = InquiryStore(path=tmp_path / "inq.db")
    asker = DurableAsker(NullAsker(), store, chat="s1")
    assert asker.has_pending() is False

    task = asyncio.create_task(asker.ask(Question(text="allow?", options=["yes", "no"])))
    await asyncio.sleep(0.05)  # let ask() persist the inquiry
    assert asker.has_pending() is True

    (inq,) = await store.list_pending()
    await store.answer(inq.id, "yes")
    assert await task == "yes"
    assert asker.has_pending() is False


async def test_channel_asker_reports_pending_prompt():
    """Channel askers (Telegram et al.) must pause the turn clock too."""
    pytest.importorskip("telegram")
    from assistant.channels.telegram import TelegramAsker
    from assistant.hitl.channel import PendingAsks

    class _Bot:
        async def send_message(self, *a, **k):
            return None

    pending = PendingAsks()
    asker = TelegramAsker(_Bot(), "42", pending)
    assert asker.has_pending() is False

    task = asyncio.create_task(asker.ask(Question(text="allow?")))
    await asyncio.sleep(0.01)
    assert asker.has_pending() is True

    pending.resolve("42", "yes")
    assert await task == "yes"
    assert asker.has_pending() is False


# --- Gateway / task-controller integration -----------------------------------------


@pytest.fixture
def fake_gateway():
    gw = Gateway(memory=False, persist=False)
    gw._agent = FakeAgent()
    return gw


async def test_send_message_repairs_poisoned_session(fake_gateway):
    """A session whose history ends in a dangling tool call heals on the next turn."""
    stream = await fake_gateway._get_stream("s1")
    await stream.history.replace([_response_with_calls("call_dangling")])

    reply = await fake_gateway.send_message("are you alive?", chat_id="s1")

    assert reply  # the turn ran instead of failing on broken history
    events = list(await stream.history.get_events())
    synthetic = [
        e
        for e in events
        if isinstance(e, ToolResultsEvent)
        and any(r.parent_id == "call_dangling" for r in e.results)
    ]
    assert len(synthetic) == 1
