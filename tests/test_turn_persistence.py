"""A turn that ends badly must still keep what it produced.

The regression: `_persist_turn` used to sit in `send_message`'s `else:` clause, so a
turn that raised — a timeout, a provider fault, a dropped connection — was never
written at all. The chat kept only the stub `_ensure_transcript_stub` writes when the
message is accepted, and because the thread renders from the event-log replay (not
the transcript), it opened completely blank while still listing in the sidebar.

The event-log assertions below are the ones that actually catch that: a transcript
alone is what the broken state already had.
"""

import asyncio

import pytest
from ag2.knowledge.constants import LOG_PREFIX

import assistant.gateway.core as core_mod
from assistant.config import Config
from assistant.events import TurnFailed
from assistant.gateway.core import Gateway
from tests.support.fakes import FakeReply, FakeRunMixin


class BoomAgent(FakeRunMixin):
    """Raises instead of replying — the provider fault / timeout case."""

    def __init__(self, exc: BaseException):
        self.tools = []
        self._exc = exc

    async def ask(self, *msg, stream=None, **kwargs):
        raise self._exc


class HangingAgent(FakeRunMixin):
    """Signals that it is mid-turn, then hangs — so a test can cancel it at exactly
    the point a process shutdown would."""

    def __init__(self, running: asyncio.Event):
        self.tools = []
        self._running = running

    async def ask(self, *msg, stream=None, **kwargs) -> FakeReply:
        self._running.set()
        await asyncio.sleep(30)
        return FakeReply("never reached")


async def _gateway(paths, tmp_path, monkeypatch, agent):
    monkeypatch.setattr(core_mod, "create_agent", lambda *a, **k: agent)
    gw = Gateway(config=Config.for_paths(paths, data_dir=tmp_path), memory=False)
    await gw.start()
    return gw


@pytest.mark.parametrize(
    "exc",
    [asyncio.TimeoutError(), RuntimeError("provider exploded")],
    ids=["timeout", "provider-error"],
)
async def test_failed_turn_is_persisted(paths, tmp_path, monkeypatch, exc):
    gw = await _gateway(paths, tmp_path, monkeypatch, BoomAgent(exc))
    try:
        with pytest.raises(type(exc)):
            await gw.send_message("create a task", chat_id="c1")

        # The display transcript completes the stub rather than being left at it.
        assert [m["role"] for m in await gw.transcript("c1")] == ["user", "agent"]

        # ...and the event log exists. Without this the thread renders blank: the
        # sidebar reads the transcript, but the conversation replays from the log.
        assert await gw._event_store.exists(f"{LOG_PREFIX}c1.jsonl")

        # The failure is on the stream, so the thread can say why it stopped.
        events = await (await gw.stream_for("c1")).history.get_events()
        failures = [e for e in events if isinstance(e, TurnFailed)]
        assert len(failures) == 1
        assert failures[0].error  # a real sentence, not empty
    finally:
        await gw.close()


async def test_failed_turn_survives_a_reload(paths, tmp_path, monkeypatch):
    """The point of persisting: a fresh Gateway over the same data dir still has it."""
    gw = await _gateway(paths, tmp_path, monkeypatch, BoomAgent(RuntimeError("boom")))
    with pytest.raises(RuntimeError):
        await gw.send_message("do the thing", chat_id="c1")
    await gw.close()

    gw2 = await _gateway(paths, tmp_path, monkeypatch, BoomAgent(RuntimeError("boom")))
    try:
        events = await (await gw2.stream_for("c1")).history.get_events()
        assert any(isinstance(e, TurnFailed) for e in events)
        assert [m["role"] for m in await gw2.transcript("c1")] == ["user", "agent"]
    finally:
        await gw2.close()


async def test_timeout_failure_reads_as_a_timeout(paths, tmp_path, monkeypatch):
    gw = await _gateway(paths, tmp_path, monkeypatch, BoomAgent(asyncio.TimeoutError()))
    try:
        with pytest.raises(asyncio.TimeoutError):
            await gw.send_message("long job", chat_id="c1")
        events = await (await gw.stream_for("c1")).history.get_events()
        failure = next(e for e in events if isinstance(e, TurnFailed))
        assert "timed out" in failure.error
        # the raw exception text never reaches the chat
        assert "Traceback" not in failure.error
    finally:
        await gw.close()


async def test_shutdown_cancellation_keeps_the_turn(paths, tmp_path, monkeypatch):
    """Cancelled by something other than the user's stop button (a process shutdown):
    the work is still persisted, and the cancellation still propagates."""
    running = asyncio.Event()
    gw = await _gateway(paths, tmp_path, monkeypatch, HangingAgent(running))
    try:
        turn = asyncio.ensure_future(gw.send_message("create a task", chat_id="c1"))
        await asyncio.wait_for(running.wait(), timeout=5)

        turn.cancel()
        with pytest.raises(asyncio.CancelledError):
            await turn

        assert [m["role"] for m in await gw.transcript("c1")] == ["user", "agent"]
        assert await gw._event_store.exists(f"{LOG_PREFIX}c1.jsonl")
    finally:
        await gw.close()


async def test_user_stop_still_marks_the_turn_cancelled(paths, tmp_path, monkeypatch):
    """The existing stop path keeps working — persisted, and reported as a stop
    rather than a failure."""
    from assistant.events import TurnCancelled

    running = asyncio.Event()
    gw = await _gateway(paths, tmp_path, monkeypatch, HangingAgent(running))
    try:
        turn = asyncio.ensure_future(gw.send_message("create a task", chat_id="c1"))
        await asyncio.wait_for(running.wait(), timeout=5)

        await gw.cancel_turn("c1")
        assert await turn == ""

        events = await (await gw.stream_for("c1")).history.get_events()
        assert any(isinstance(e, TurnCancelled) for e in events)
        assert not any(isinstance(e, TurnFailed) for e in events)
    finally:
        await gw.close()
