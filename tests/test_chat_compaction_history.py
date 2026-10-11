"""Compaction bounds model context while keeping the complete client history."""

import pytest
from ag2 import Agent
from ag2.compact import CompactionSummary
from ag2.events import (
    CompactionCompleted,
    ModelMessage,
    ModelRequest,
    ModelResponse,
    estimated_tokens,
    is_conversational,
)
from ag2.knowledge import SqliteKnowledgeStore
from ag2.knowledge.log import EventLogWriter
from ag2.stream import MemoryStream
from ag2.testing import TestConfig

from assistant.config import Config
from assistant.events import A2UISurface
from assistant.gateway.core import Gateway
from assistant.gateway.stream_bridge import StreamBridge
from assistant.memory import build_compaction_config, build_knowledge_config
from tests.support.fakes import fake_agent_factory


class _WebSocket:
    def __init__(self):
        self.sent = []

    async def send_json(self, frame):
        self.sent.append(frame)


@pytest.mark.parametrize("profile_memory", [False, True])
async def test_large_history_compacts_below_budget_then_skips_small_turn(tmp_path, profile_memory):
    stream = MemoryStream()
    before = []
    for index in range(8):
        before.extend(
            [
                ModelRequest.ensure_request([f"turn {index}: " + "x" * 4_000]),
                ModelResponse(ModelMessage(f"reply {index}")),
            ]
        )
    await stream.history.replace(before)
    knowledge = (
        build_knowledge_config(
            store_path=tmp_path / "profile.db",
            aggregate_config=TestConfig("profile"),
            compact=True,
            compact_max_tokens=4_000,
        )
        if profile_memory
        else build_compaction_config(max_tokens=4_000)
    )
    agent = Agent("chat", config=TestConfig("short reply"), knowledge=knowledge)
    completed = []

    async def collect(event: CompactionCompleted):
        completed.append(event)

    stream.where(CompactionCompleted).subscribe(collect)

    await agent.ask("continue", stream=stream)
    history = list(await stream.history.get_events())
    assert any(isinstance(event, CompactionSummary) for event in history)
    assert sum(estimated_tokens(e, 4) for e in history if is_conversational(e)) < 4_000
    assert len(completed) == 1

    await agent.ask("thanks", stream=stream)
    assert len(completed) == 1
    if profile_memory:
        knowledge.store.close()


async def test_replay_restores_archived_messages_and_cards_without_expanding_context(paths):
    config = Config.for_paths(paths)
    config.data_dir.mkdir(parents=True, exist_ok=True)
    archive = SqliteKnowledgeStore(str(config.data_dir / "profile.db"))
    old = [
        ModelRequest.ensure_request(["old question"]),
        ModelResponse(ModelMessage("same reply")),
        A2UISurface("old-card", component={"Text": {"text": "Calendar"}}),
    ]
    recent = [
        CompactionSummary(summary="Earlier calendar discussion", event_count=len(old)),
        ModelRequest.ensure_request(["new question"]),
        ModelResponse(ModelMessage("same reply")),
    ]
    archive_writer = EventLogWriter(archive)
    await archive_writer.persist_dropped("c1", old)
    await archive_writer.persist_dropped("c1", old)
    archive.close()

    for _ in range(2):
        gw = Gateway(config, memory=False, agent_factory=fake_agent_factory())
        await gw.start()
        try:
            if not await gw._event_store.exists("/log/c1.jsonl"):
                await gw._writer.persist("c1", recent)
                await gw._writer.persist_dropped("c1", old[:1])
            stream = await gw.stream_for("c1")
            assert [type(e) for e in await stream.history.get_events()] == [type(e) for e in recent]

            for _ in range(2):
                ws = _WebSocket()
                bridge = StreamBridge(gw, ws, "c1")
                await bridge.open()
                bridge.close()
                events = [frame["event"] for frame in ws.sent if "event" in frame]
                assert len(events) == len(old) + len(recent)
                assert [event["type"].rsplit(".", 1)[-1] for event in events] == [
                    type(event).__name__ for event in [*old, *recent]
                ]
                assert [
                    event["data"] for event in events if event["type"].endswith("ModelResponse")
                ] == [old[1].to_dict(), recent[2].to_dict()]
                assert any(event["data"].get("surface_id") == "old-card" for event in events)
            assert len(list(await stream.history.get_events())) == len(recent)
        finally:
            await gw.close()


async def test_delete_chat_removes_its_archived_segments_only(paths):
    config = Config.for_paths(paths)
    config.data_dir.mkdir(parents=True, exist_ok=True)
    archive = SqliteKnowledgeStore(str(config.data_dir / "profile.db"))
    archive_writer = EventLogWriter(archive)
    old = [ModelRequest.ensure_request(["old question"])]
    await archive_writer.persist_dropped("c1", old)
    await archive_writer.persist_dropped("c2", old)
    archive.close()
    gw = Gateway(config, memory=False, agent_factory=fake_agent_factory())
    await gw.start()
    try:
        await gw._writer.persist("c1", [CompactionSummary(summary="old", event_count=1)])
        assert await gw.delete_chat("c1") is True
        assert await gw.replay_events("c1") == []
        assert len(await gw.replay_events("c2")) == 1
    finally:
        await gw.close()
