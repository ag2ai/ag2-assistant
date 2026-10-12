"""Internal streams (task runs) must not surface in the Chats list."""

import asyncio

import pytest
from ag2.context import ConversationContext
from ag2.events import (
    CompactionCompleted,
    CompactionFailed,
    CompactionStarted,
    ModelMessage,
    ModelMessageChunk,
    ModelResponse,
)

from assistant.config import Config
from assistant.gateway.core import Gateway, is_internal_stream
from assistant.gateway.stream_bridge import StreamBridge
from tests.support.fakes import FakeReply, FakeRunMixin, fake_agent_factory, fake_title_factory


def test_run_and_task_streams_are_internal():
    assert is_internal_stream("task-run:run_abc") is True
    assert is_internal_stream("task:task_abc") is True  # legacy records
    assert is_internal_stream("web-123") is False
    assert is_internal_stream("telegram:42") is False


class _CompactingAgent(FakeRunMixin):
    """Emit a reply and hold compaction open until released."""

    def __init__(self, terminal):
        self.terminal = terminal
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def ask(self, *msg, stream, **kwargs):
        context = ConversationContext(stream=stream)
        await context.send(ModelMessageChunk("reply"))
        await context.send(ModelResponse(message=ModelMessage("reply")))
        before = list(await stream.history.get_events())
        await context.send(
            CompactionStarted(agent="ag2-assistant", strategy="test", event_count=len(before))
        )
        self.started.set()
        await self.release.wait()
        if isinstance(self.terminal, CompactionCompleted):
            await stream.history.replace(before)
        await context.send(self.terminal)
        return FakeReply("reply")


class _WebSocket:
    def __init__(self):
        self.sent = []

    async def send_json(self, frame):
        self.sent.append(frame)


@pytest.mark.parametrize(
    "terminal",
    [
        CompactionCompleted(
            agent="ag2-assistant", strategy="test", events_before=2, events_after=1
        ),
        CompactionFailed(
            agent="ag2-assistant", strategy="test", error_type="ValueError", error="Unavailable"
        ),
    ],
)
async def test_compaction_replays_while_running_and_after_restart(paths, terminal):
    agent = _CompactingAgent(terminal)
    config = Config.for_paths(paths)

    def gateway():
        return Gateway(
            config,
            memory=False,
            agent_factory=fake_agent_factory(agent),
            title_factory=fake_title_factory("Test chat"),
        )

    gw = gateway()
    await gw.start()
    turn = asyncio.create_task(gw.send_message("hello", chat_id="compacting"))
    try:
        await asyncio.wait_for(agent.started.wait(), 5)
        ws = _WebSocket()
        bridge = StreamBridge(gw, ws, "compacting")
        await bridge.open()
        replay = [frame["event"]["type"] for frame in ws.sent if "event" in frame]
        assert any(name.endswith("CompactionStarted") for name in replay)
        assert not any(name.endswith("ModelMessageChunk") for name in replay)
        agent.release.set()
        assert await turn == "reply"
        assert any(
            frame.get("event", {}).get("type", "").endswith(type(terminal).__name__)
            for frame in ws.sent
        )
        bridge.close()
    finally:
        agent.release.set()
        await turn
        await gw.close()

    resumed = gateway()
    await resumed.start()
    try:
        ws = _WebSocket()
        bridge = StreamBridge(resumed, ws, "compacting")
        await bridge.open()
        replay = [frame["event"]["type"] for frame in ws.sent if "event" in frame]
        assert any(name.endswith(type(terminal).__name__) for name in replay)
        bridge.close()
    finally:
        await resumed.close()
