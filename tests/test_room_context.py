"""Bound room data reaches inference without entering the user transcript or profile."""

import copy
import json
import time
from pathlib import Path
from uuid import UUID

import acp
import pytest
from ag2 import Agent
from ag2.acp import SessionConfig
from ag2.acp.testing import connect
from ag2.config.gemini.mappers import convert_messages as gemini_messages
from ag2.config.openai.mappers import convert_messages, events_to_responses_input
from ag2.context import ConversationContext
from ag2.events import ModelRequest, TextInput, render_for_prompt
from ag2.knowledge import MemoryKnowledgeStore
from ag2.middleware import Middleware
from ag2.stream import MemoryStream
from fast_depends.pydantic import PydanticSerializer

from assistant.acp.chats import ChatBackedStorage, ChatTrackingACPAgent
from assistant.acp.room_context_validation import context_scope, validate_room_context
from assistant.compaction import (
    CHECKPOINT_SCHEMA,
    ContextBudgetError,
    load_checkpoint,
    request_tokens,
)
from assistant.memory import PROFILE_PATH, build_compaction_config, build_knowledge_config
from assistant.room_context import (
    CONTENT_META_KEY,
    PREPARED_META_KEY,
    TASK_META_KEY,
    RoomContextMiddleware,
    RoomContextSession,
    TransientRoomContextInput,
    prepare_room_context,
)
from tests.support.apps import compacting_agent
from tests.support.fakes import CheckpointTestConfig

GOLDEN = json.loads((Path(__file__).parent / "fixtures/room-context-v1.json").read_text())
NOW = 1730000100


def model_text(events):
    return "\n".join(
        "\n".join(part.content for part in event.parts if hasattr(part, "content"))
        if isinstance(event, ModelRequest)
        else render_for_prompt(event)
        for event in events
    )


def fresh_task():
    task = copy.deepcopy(GOLDEN)
    now = int(time.time())
    for value in (task["room_context"], task["room_context_binding"]):
        value.update(observed_at=now, expires_at=now + 1800)
    return task


def blocks(task):
    facts = {
        "task_id": task["id"],
        "room_id": task["channel_id"],
        "source_message_id": task["source_message_id"],
        "access_tier": task["access_tier"],
        "agent_id": task["agent_mxid"],
    }
    ask = acp.text_block(task["task"])
    ask.field_meta = {TASK_META_KEY: facts}
    reference = acp.text_block("DISPLAY TEXT MUST NOT BECOME THE REQUEST")
    reference.field_meta = {
        CONTENT_META_KEY: {"binding": task["room_context_binding"], "context": task["room_context"]}
    }
    return [ask, reference]


def test_validation_checks_bytes_scope_policy_and_exact_shapes():
    emoji = json.loads((Path(__file__).parent / "fixtures/room-context-emoji-v1.json").read_text())
    assert validate_room_context(emoji, now=NOW)
    packet = validate_room_context(copy.deepcopy(GOLDEN), now=NOW)
    assert packet and packet["messages"][0]["body"].endswith("blue fox.")
    for field in ("task_id", "room_id", "access_tier", "anchor_event_id"):
        task = copy.deepcopy(GOLDEN)
        task["room_context"][field] = "wrong"
        assert validate_room_context(task, now=NOW) is None
    for section, field, value in (
        ("room_context", "schema_version", True),
        ("room_context", "extra", True),
        ("room_context_binding", "extra", True),
        ("room_context", "room", {"name": "🦊" * 513}),
        ("room_context", "readme", {"text": "🦊" * 8192, "content_hash": "h", "truncated": False}),
    ):
        task = copy.deepcopy(GOLDEN)
        task[section][field] = value
        assert validate_room_context(task, now=NOW) is None
    for value in ("\ud800", "🦊" * 2049):
        task = copy.deepcopy(GOLDEN)
        task["room_context"]["messages"][0]["body"] = value
        assert validate_room_context(task, now=NOW) is None
    task = copy.deepcopy(GOLDEN)
    task["room_context"]["messages"][0]["body"] = "🦊" * 2048
    assert validate_room_context(task, now=NOW)
    task["room_context"]["messages"][0]["origin_server_ts"] = 9007199254740992
    assert validate_room_context(task, now=NOW) is None
    assert validate_room_context(copy.deepcopy(GOLDEN), agent_id="@wrong", now=NOW) is None
    assert validate_room_context(copy.deepcopy(GOLDEN), now=1730001800) is None
    task = copy.deepcopy(GOLDEN)
    task["room_context"]["visibility"] = task["room_context_binding"]["visibility"] = "mentions"
    assert validate_room_context(task, now=NOW) is None
    task["room_context"]["messages"][0]["selection"] = "mention"
    assert validate_room_context(task, now=NOW)
    assert context_scope(packet) != context_scope({**packet, "status": "denied"})


async def test_real_acp_keeps_packet_and_derived_outputs_out_of_profile_and_stored_request(paths):
    config = CheckpointTestConfig("Derived answer mentions blue fox")
    profile = MemoryKnowledgeStore()
    knowledge = build_knowledge_config(
        store=profile,
        aggregate_config=CheckpointTestConfig("PROFILE MUST NOT CHANGE"),
        every_n_turns=1,
    )
    await profile.write(PROFILE_PATH, "Original profile")
    agent = Agent(
        "room-test",
        config=config,
        knowledge=knowledge,
        middleware=[Middleware(RoomContextMiddleware)],
    )
    storage = ChatBackedStorage(paths=paths, data_dir=paths.root / "data", profile="test")
    served = ChatTrackingACPAgent(
        agent,
        sessions=SessionConfig(storage=storage, retain_history=True),
        chat_storage=storage,
        connection_id="room-test",
    )
    async with connect(served) as (client, _recorder):
        session = await client.new_session(cwd=".")
        result = await client.prompt(session_id=session.session_id, prompt=blocks(fresh_task()))
        assert result.stop_reason == "end_turn"
        # A later reply may still derive from room-specific assistant output.
        await client.prompt(session_id=session.session_id, prompt=[acp.text_block("continue")])
    inputs = config.requests[0][0]
    text = model_text(inputs)
    assert "blue fox" in text and GOLDEN["task"] in text
    assert "DISPLAY TEXT MUST NOT BECOME" not in text
    assert await profile.read(PROFILE_PATH) == "Original profile"
    history = list(await storage.get_history(UUID(session.session_id)))
    requests = [event for event in history if isinstance(event, ModelRequest)]
    assert [model_text([event]) for event in requests] == [GOLDEN["task"], "continue"]
    assert any(isinstance(event, RoomContextSession) for event in history)
    serialized = "\n".join(json.dumps(event.to_dict()) for event in history)
    assert "Launch target: Friday" not in serialized
    assert "room_context_binding" not in serialized
    restarted_storage = ChatBackedStorage(paths=paths, data_dir=paths.root / "data", profile="test")
    restarted_agent = Agent(
        "room-test",
        config=CheckpointTestConfig("restarted derived answer"),
        knowledge=knowledge,
        middleware=[Middleware(RoomContextMiddleware)],
    )
    restarted = ChatTrackingACPAgent(
        restarted_agent,
        sessions=SessionConfig(storage=restarted_storage, retain_history=True),
        chat_storage=restarted_storage,
        connection_id="room-test",
    )
    async with connect(restarted) as (client, _recorder):
        await client.load_session(session_id=session.session_id, cwd=".")
        await client.prompt(
            session_id=session.session_id, prompt=[acp.text_block("continue after restart")]
        )
    assert await profile.read(PROFILE_PATH) == "Original profile"


async def test_invalid_or_payload_only_acp_context_never_replaces_the_actual_ask(paths):
    config = CheckpointTestConfig("ok")
    agent = Agent("test", config=config, middleware=[Middleware(RoomContextMiddleware)])
    storage = ChatBackedStorage(paths=paths, data_dir=paths.root / "data", profile="test")
    served = ChatTrackingACPAgent(
        agent, sessions=SessionConfig(storage=storage), chat_storage=storage, connection_id="test"
    )
    task = fresh_task()
    task["room_context"]["room_id"] = "!wrong"
    async with connect(served) as (client, _recorder):
        session = await client.new_session(cwd=".")
        await client.prompt(session_id=session.session_id, prompt=blocks(task))
        before = len(config.requests)
        await client.prompt(session_id=session.session_id, prompt=[blocks(task)[1]])
        assert len(config.requests) == before
    text = model_text(config.requests[0][0])
    assert GOLDEN["task"] in text and "unavailable" in text
    assert "blue fox" not in text


async def test_transient_context_is_budgeted_and_not_compacted_into_checkpoint_sources():
    task = fresh_task()
    _cleaned, prepared = prepare_room_context(blocks(task))
    stream = MemoryStream()
    source = []
    for i in range(8):
        source.append(ModelRequest.ensure_request([f"history {i} " + "x" * 4000]))
    await stream.history.replace(source)
    config = CheckpointTestConfig("done")
    knowledge = build_compaction_config(max_tokens=4000)
    agent = compacting_agent(config, knowledge, middleware=[Middleware(RoomContextMiddleware)])
    variables = {"acp.meta": {PREPARED_META_KEY: prepared}}
    context = ConversationContext(stream, variables=variables)
    # ACP executor uses the same preparation/execution path with an explicit context.
    event = ModelRequest.ensure_request([task["task"]])
    client = await agent._prepare_turn(event, context, None)
    await agent._execute(event, context=context, client=client)
    ordinary = [
        request
        for request in config.requests
        if request[2].get("response_schema") is not CHECKPOINT_SCHEMA
    ]
    messages, prompt, options = ordinary[0]
    assert "blue fox" in model_text(messages)
    assert request_tokens(messages, prompt, options["tools"]) <= 4000
    references = [event for event in messages if isinstance(event, TransientRoomContextInput)]
    ordinary_events = [
        event for event in messages if not isinstance(event, TransientRoomContextInput)
    ]
    reference_chars = sum(len(part.content) for event in references for part in event.parts)
    assert (
        request_tokens(messages, prompt, options["tools"])
        - request_tokens(ordinary_events, prompt, options["tools"])
        >= reference_chars // 4
    )

    record = await load_checkpoint(knowledge.store, stream.id)
    assert record is not None
    assert "blue fox" not in record.model_dump_json()
    assert any(isinstance(event, RoomContextSession) for event in await stream.history.get_events())
    summaries = [
        request
        for request in config.requests
        if request[2].get("response_schema") is CHECKPOINT_SCHEMA
    ]
    assert all("blue fox" not in str(request[0]) for request in summaries)


async def test_oversized_reference_cannot_bypass_the_model_input_budget():
    task = fresh_task()
    task["room_context"]["readme"]["text"] = "reference data " * 500
    _cleaned, prepared = prepare_room_context(blocks(task))
    stream = MemoryStream()
    context = ConversationContext(stream, variables={"acp.meta": {PREPARED_META_KEY: prepared}})
    config = CheckpointTestConfig("should not fit")
    agent = compacting_agent(
        config,
        build_compaction_config(max_tokens=1000),
        middleware=[Middleware(RoomContextMiddleware)],
    )
    event = ModelRequest.ensure_request([task["task"]])
    client = await agent._prepare_turn(event, context, None)
    with pytest.raises(ContextBudgetError):
        await agent._execute(event, context=context, client=client)


def test_optional_empty_coverage_and_identifier_controls_follow_the_shared_contract():
    for key in ("cursor", "reason"):
        task = copy.deepcopy(GOLDEN)
        task["room_context"]["coverage"][key] = ""
        assert validate_room_context(task, now=NOW)
    for control in ("\r", "\n", "\t", "\x00", "\x7f"):
        task = copy.deepcopy(GOLDEN)
        task["room_context"]["messages"][0]["sender"] = "@sender" + control
        assert validate_room_context(task, now=NOW) is None
        task = copy.deepcopy(GOLDEN)
        task["room_context"]["policy_revision"] = task["room_context_binding"][
            "policy_revision"
        ] = "policy" + control
        assert validate_room_context(task, now=NOW) is None
    for key in ("members", "messages"):
        task = copy.deepcopy(GOLDEN)
        task["room_context"][key] = None
        assert validate_room_context(task, now=NOW) is None


def test_transient_reference_reaches_provider_payload_as_user_data():
    reference = TransientRoomContextInput([TextInput("ROOM_REFERENCE_SENTINEL")])
    ask = ModelRequest.ensure_request(["ACTUAL_ASK_SENTINEL"])
    serializer = PydanticSerializer(
        pydantic_config={"arbitrary_types_allowed": True}, use_fastdepends_errors=False
    )
    for payload in (
        convert_messages(["operator guidance"], [reference, ask], serializer),
        events_to_responses_input([reference, ask], serializer),
        [part.model_dump() for part in gemini_messages([reference, ask], serializer)],
    ):
        serialized = json.dumps(payload)
        assert "ROOM_REFERENCE_SENTINEL" in serialized
        assert "ACTUAL_ASK_SENTINEL" in serialized
        assert (
            next(part for part in payload if "ROOM_REFERENCE_SENTINEL" in str(part))["role"]
            == "user"
        )
