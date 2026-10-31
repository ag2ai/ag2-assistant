"""Send-time stamps added to user messages at the provider boundary."""

from datetime import datetime, timezone

from ag2.compact import CompactionSummary
from ag2.events import ModelMessage, ModelRequest, ModelResponse, TextInput
from ag2.knowledge import MemoryKnowledgeStore
from ag2.stream import MemoryStream

from assistant.compaction import RetainedSkillRequest, event_ref, stamp_requests
from assistant.gateway.history import ChatEventLog
from assistant.memory import build_compaction_config
from tests.support.apps import compacting_agent
from tests.support.fakes import CheckpointTestConfig


def at(hour: int, minute: int, second: int = 0) -> float:
    return datetime(2026, 3, 5, hour, minute, second, tzinfo=timezone.utc).timestamp()


def request(text: str, created_at: float, kind=ModelRequest) -> ModelRequest:
    event = kind.ensure_request([text])
    event.created_at = created_at
    return event


def texts(events) -> list[list[str]]:
    return [
        [part.content for part in event.parts if isinstance(part, TextInput)]
        for event in events
        if isinstance(event, ModelRequest)
    ]


def test_a_stamp_appears_only_when_the_minute_changes():
    events = [
        request("one", at(23, 14, 5)),
        ModelResponse(ModelMessage("reply")),
        request("two", at(23, 14, 50)),
        request("three", at(23, 15, 1)),
    ]
    assert texts(stamp_requests(events)) == [
        ["one", "[Sent: Thu 5 Mar 2026 23:14 UTC]"],
        ["two"],
        ["three", "[Sent: Thu 5 Mar 2026 23:15 UTC]"],
    ]


def test_stamping_leaves_the_stored_event_as_it_was():
    event = request("one", at(23, 14))
    ref = event_ref(event)
    stamp_requests([event])
    assert event_ref(event) == ref
    assert texts([event]) == [["one"]]


def test_a_retained_skill_request_is_neither_stamped_nor_compared():
    events = [
        request("skill text", at(23, 59), kind=RetainedSkillRequest),
        request("one", at(23, 14)),
        request("two", at(23, 14, 30)),
    ]
    assert texts(stamp_requests(events)) == [
        ["skill text"],
        ["one", "[Sent: Thu 5 Mar 2026 23:14 UTC]"],
        ["two"],
    ]


def test_the_first_message_after_a_checkpoint_gets_a_stamp():
    summary = CompactionSummary(summary="{}", event_count=4)
    assert texts(stamp_requests([summary, request("kept", at(23, 14))])) == [
        ["kept", "[Sent: Thu 5 Mar 2026 23:14 UTC]"]
    ]


async def test_earlier_messages_render_identically_on_every_request():
    stream = MemoryStream()
    knowledge = build_compaction_config(max_tokens=50_000)
    config = CheckpointTestConfig("first", "second")
    agent = compacting_agent(config, knowledge)
    await agent.ask("one", stream=stream)
    await agent.ask("two", stream=stream)
    first, second = config.requests[0][0], config.requests[1][0]
    assert texts(second)[0] == texts(first)[0]
    assert texts(first)[0][-1].startswith("[Sent: ")


async def test_stored_history_never_carries_a_stamp():
    stream = MemoryStream()
    knowledge = build_compaction_config(max_tokens=50_000)
    await compacting_agent(CheckpointTestConfig("done"), knowledge).ask("one", stream=stream)
    assert texts(await stream.history.get_events()) == [["one"]]


async def test_a_stamp_survives_a_reload_through_the_chat_log():
    store = MemoryKnowledgeStore()
    log = ChatEventLog(store)
    events = [request("one", at(23, 14, 5)), ModelResponse(ModelMessage("reply"))]
    await log.persist("chat", events)
    assert texts(stamp_requests(await log.load_current("chat"))) == texts(stamp_requests(events))
