"""Compaction at the model, Skill runtime and persisted Chat boundaries."""

import asyncio
import json

import pytest
from ag2 import tool
from ag2.compact import CompactionSummary
from ag2.config.openai.events import OpenAIReasoningEvent
from ag2.context import ConversationContext
from ag2.events import (
    CompactionCompleted,
    CompactionFailed,
    CompactionStarted,
    DataInput,
    ImageInput,
    ModelMessage,
    ModelRequest,
    ModelResponse,
    ToolCallEvent,
    ToolCallsEvent,
    ToolResult,
    ToolResultEvent,
    ToolResultsEvent,
    UsageEvent,
)
from ag2.knowledge import MemoryKnowledgeStore
from ag2.middleware import Middleware
from ag2.stream import MemoryStream
from ag2.tools.skills import LocalRuntime, SkillPlugin
from openai.types.responses import ResponseReasoningItem

from assistant.agent import create_agent
from assistant.compaction import (
    CHECKPOINT_SCHEMA,
    CompactionLimits,
    ContextBudgetError,
    SkillCatalogMiddleware,
    SkillResolver,
    checkpoint_root,
    event_ref,
    load_checkpoint,
    request_tokens,
)
from assistant.config import Config
from assistant.gateway.history import ChatEventLog
from assistant.memory import build_compaction_config
from assistant.middleware import LLMRetryMiddleware
from assistant.skills import FilteredSkillRuntime
from tests.support.apps import compacting_agent
from tests.support.fakes import CheckpointTestConfig, ControlledHistory, checkpoint_json


async def test_compacts_before_first_model_call_and_archives_original_history():
    stream = MemoryStream()
    original = []
    for index in range(8):
        original.extend(
            [
                ModelRequest.ensure_request([f"history {index}: " + "x" * 4_000]),
                ModelResponse(ModelMessage(f"reply {index}")),
            ]
        )
    await stream.history.replace(original)
    snapshots = []

    async def observe(messages, context, options):
        if options.get("response_schema") is not CHECKPOINT_SCHEMA:
            snapshots.append(list(await context.stream.history.get_events()))

    config = CheckpointTestConfig("done", before_call=observe)
    knowledge = build_compaction_config(max_tokens=4_000)
    agent = compacting_agent(config, knowledge)
    completed = []
    stream.where(CompactionCompleted).subscribe(lambda event: completed.append(event))
    await agent.ask("Continue; preserve the user's constraints", stream=stream)
    assert original[0] in snapshots[0]
    calls = [
        request
        for request in config.requests
        if request[2].get("response_schema") is not CHECKPOINT_SCHEMA
    ]
    messages, prompt, options = calls[0]
    assert isinstance(messages[0], CompactionSummary)
    assert request_tokens(messages, prompt, options["tools"]) <= 4_000
    assert any("preserve the user's constraints" in str(event) for event in messages)
    current = list(await stream.history.get_events())
    assert original[0] not in current
    assert original[0] in await ChatEventLog(knowledge.store).replay(stream.id, current)
    assert completed[0].llm_calls > 0
    await agent.ask("thanks", stream=stream)
    assert len(completed) == 1


async def test_fixed_prompt_and_schema_overflow_stops_before_provider_call():
    config = CheckpointTestConfig("should not run")
    agent = compacting_agent(
        config, build_compaction_config(max_tokens=1_000), prompt="Required instructions " * 1_000
    )
    with pytest.raises(ContextBudgetError):
        await agent.ask("hello")
    assert config.requests == []


async def test_tool_schema_is_included_in_input_budget():
    @tool(description="Required tool documentation " * 1_000)
    def sample() -> str:
        return "done"

    config = CheckpointTestConfig("should not run")
    agent = compacting_agent(config, build_compaction_config(max_tokens=1_000), tools=[sample])
    with pytest.raises(ContextBudgetError):
        await agent.ask("hello")
    assert config.requests == []


async def test_large_tool_output_is_excerpted_between_rounds_and_can_be_read():
    @tool
    def big_output() -> str:
        return "begin " + "x" * 40_000 + " end"

    config = CheckpointTestConfig(ToolCallEvent(name="big_output"), "done")
    knowledge = build_compaction_config(max_tokens=2_000)
    stream = MemoryStream()
    await compacting_agent(config, knowledge, tools=[big_output]).ask("run", stream=stream)
    messages, prompt, options = config.requests[-1]
    assert request_tokens(messages, prompt, options["tools"]) <= 2_000
    assert any("read_compaction_source" in str(event.to_dict()) for event in messages)
    raw = list(await stream.history.get_events())
    result = next(
        event for event in raw if isinstance(event, ToolResultEvent) and event.name == "big_output"
    )
    assert result.result.parts[0].content.endswith(" end")
    source = event_ref(result)
    reader = CheckpointTestConfig(
        ToolCallEvent(
            name="read_compaction_source",
            arguments=json.dumps(
                {
                    "source": source,
                    "offset": 40_000,
                    "max_chars": 64,
                }
            ),
        ),
        "read",
    )
    agent = compacting_agent(reader, knowledge)
    await agent.ask("read the original ending", stream=stream)
    results = [
        event
        for event in await stream.history.get_events()
        if isinstance(event, ToolResultEvent) and event.name == "read_compaction_source"
    ]
    assert " end" in results[-1].result.parts[0].content
    await agent.ask("read", stream=MemoryStream())
    other = [event for event in reader.requests[-1][0] if isinstance(event, ToolResultsEvent)]
    assert "unavailable in this Chat" in str(other)


async def test_excerpt_projection_is_reused_between_rounds_and_turns_without_notices():
    class ImmutableSources(MemoryKnowledgeStore):
        async def write(self, path, content):
            if "/sources/" in path and await self.read(path) is not None:
                raise OSError("An archived source cannot be overwritten")
            await super().write(path, content)

    @tool
    def big_output() -> str:
        return "begin " + "x" * 40_000 + " end"

    @tool
    def small_output() -> str:
        return "small result"

    store = ImmutableSources()
    knowledge = build_compaction_config(max_tokens=2_000, store=store)
    stream = MemoryStream()
    notices = []
    stream.where(CompactionStarted).subscribe(lambda event: notices.append(event))
    stream.where(CompactionCompleted).subscribe(lambda event: notices.append(event))
    config = CheckpointTestConfig(
        ToolCallEvent(name="big_output"),
        ToolCallEvent(name="small_output"),
        ToolCallEvent(name="small_output"),
        "done",
    )
    await compacting_agent(config, knowledge, tools=[big_output, small_output]).ask(
        "run", stream=stream
    )
    next_config = CheckpointTestConfig("next")
    await compacting_agent(next_config, knowledge, tools=[big_output, small_output]).ask(
        "continue", stream=stream
    )
    for messages, prompt, options in [*config.requests, *next_config.requests]:
        assert options.get("response_schema") is not CHECKPOINT_SCHEMA
        assert request_tokens(messages, prompt, options["tools"]) <= 2_000
    raw = list(await stream.history.get_events())
    assert notices == []
    original = next(e for e in raw if isinstance(e, ToolResultEvent) and e.name == "big_output")
    assert original.result.parts[0].content.endswith(" end")
    assert (
        await store.read(checkpoint_root(stream.id) + f"sources/{event_ref(original)}.txt")
        == original.result.parts[0].content
    )


async def test_mixed_tool_output_excerpts_keep_media_data_and_original_text():
    image = ImageInput(data=b"preview", media_type="image/png")
    bounds = DataInput({"width": 1280, "height": 720})
    text = "Measured preview bounds " + "x" * 40_000 + " ending"

    @tool
    def preview() -> ToolResult:
        return ToolResult(text, image, bounds, metadata={"preview": True})

    knowledge = build_compaction_config(max_tokens=4_000)
    config = CheckpointTestConfig(ToolCallEvent(name="preview"), "done")
    stream = MemoryStream()
    await compacting_agent(config, knowledge, tools=[preview]).ask("preview", stream=stream)
    messages = config.requests[-1][0]
    result = next(
        result
        for event in messages
        if isinstance(event, ToolResultsEvent)
        for result in event.results
        if result.name == "preview"
    )
    assert image in result.result.parts
    assert bounds in result.result.parts
    assert result.result.metadata == {"preview": True}
    assert len(result.result.parts[0].content) < len(text)
    original = next(
        event
        for event in await stream.history.get_events()
        if isinstance(event, ToolResultEvent) and event.name == "preview"
    )
    assert original.result.parts[0].content == text
    assert (
        await knowledge.store.read(
            checkpoint_root(stream.id) + f"sources/{event_ref(original)}.txt"
        )
        == text
    )


async def test_checkpoint_uses_native_images_and_bounded_unique_tool_fragments():
    image = ImageInput(data=b"preview image" * 40_000, media_type="image/png")
    text = "Measured bounds " + "x" * 40_000 + " exact original ending"
    call = ToolCallEvent(name="preview")
    result = ToolResultEvent(parent_id=call.id, name="preview", result=ToolResult(text, image))
    original = [
        ModelRequest.ensure_request(["Create a Screen with verified bounds " + "x" * 20_000]),
        ModelResponse(tool_calls=ToolCallsEvent(calls=[call])),
        call,
        result,
        ToolResultsEvent(results=[result]),
        ModelResponse(ModelMessage("Preview completed; layout still needs verification")),
        ModelRequest.ensure_request(["Keep working notes " + "y" * 20_000]),
        ModelResponse(ModelMessage("Notes retained")),
    ]
    stream = MemoryStream()
    await stream.history.replace(original)
    evidence = json.loads(checkpoint_json())
    evidence["completed"] = [
        {"description": "Preview produced; verification is pending", "refs": [event_ref(result)]}
    ]
    config = CheckpointTestConfig("done", checkpoint=json.dumps(evidence))
    knowledge = build_compaction_config(max_tokens=6_000)
    await compacting_agent(config, knowledge).ask(
        "Continue without repeating saved work", stream=stream
    )
    summaries = [
        messages[0]
        for messages, _, options in config.requests
        if options.get("response_schema") is CHECKPOINT_SCHEMA
    ]
    assert sum(part == image for request in summaries for part in request.parts) == 1
    source_text = "\n".join(
        part.content for request in summaries for part in request.parts if hasattr(part, "content")
    )
    assert "__bytes__" not in source_text
    assert "exact original ending" not in source_text
    assert source_text.count("Measured bounds") == 1
    assert event_ref(result) in source_text
    assert source_text.count('"name": "preview"') == 2
    assert "read_compaction_source" in source_text
    assert all(
        request_tokens([request], [], response_schema=CHECKPOINT_SCHEMA) <= 6_000
        for request in summaries
    )
    record = await load_checkpoint(knowledge.store, stream.id)
    assert record.data.completed[0].refs == [event_ref(result)]
    replay = await ChatEventLog(knowledge.store).replay(
        stream.id, await stream.history.get_events()
    )
    assert result in replay
    assert result.result.parts[0].content == text


async def test_summary_keeps_latest_attachment_native_and_opaque_reasoning_archived():
    image = ImageInput(data=b"latest screenshot" * 40_000, media_type="image/png")
    opaque = "encrypted reasoning " * 20_000
    reasoning = OpenAIReasoningEvent(
        "The layout needs verification",
        item=ResponseReasoningItem(
            id="reasoning-1", type="reasoning", summary=[], encrypted_content=opaque
        ),
    )
    original = [ModelRequest.ensure_request(["old " + "x" * 20_000]), reasoning]
    stream = MemoryStream()
    await stream.history.replace(original)
    config = CheckpointTestConfig("done")
    knowledge = build_compaction_config(max_tokens=6_000)
    await compacting_agent(config, knowledge).ask(
        "Continue from this screenshot", image, stream=stream
    )
    summaries = [
        messages[0]
        for messages, _, options in config.requests
        if options.get("response_schema") is CHECKPOINT_SCHEMA
    ]
    assert summaries
    assert all(image in request.parts for request in summaries)
    text = "\n".join(
        part.content for request in summaries for part in request.parts if hasattr(part, "content")
    )
    assert "__bytes__" not in text
    assert "encrypted reasoning" not in text
    assert "The layout needs verification" in text
    assert all(
        request_tokens([request], [], response_schema=CHECKPOINT_SCHEMA) <= 6_000
        for request in summaries
    )
    assert reasoning in await ChatEventLog(knowledge.store).replay(
        stream.id, await stream.history.get_events()
    )
    assert any(
        isinstance(event, ModelRequest) and image in event.parts for event in config.requests[-1][0]
    )


@pytest.mark.parametrize("memory", [False, True])
async def test_configured_summary_size_limit_keeps_source_on_overflow(paths, memory):
    config = Config.for_paths(
        paths,
        memory={"compact_max_tokens": 6_000, "compact_summary_max_tokens": 128},
    )
    model = CheckpointTestConfig("unreachable", checkpoint=checkpoint_json(goal="x" * 600))
    store = MemoryKnowledgeStore()
    agent = create_agent(
        config,
        memory=memory,
        skills=False,
        capabilities=[],
        compact=True,
        knowledge_store=store,
        compaction_store=store,
        model_factory=lambda config, model_name=None: model,
    )
    original = ModelRequest.ensure_request(["old " + "x" * 40_000])
    stream = MemoryStream()
    await stream.history.replace([original])
    with pytest.raises(ContextBudgetError, match="Checkpoint exceeds"):
        await agent.ask("continue", stream=stream)
    assert original in await stream.history.get_events()
    assert await load_checkpoint(store, stream.id) is None


async def test_compaction_timeout_is_explicit_and_not_retried_as_a_provider_timeout():
    async def stall(messages, context, options):
        if options.get("response_schema") is CHECKPOINT_SCHEMA:
            await asyncio.Event().wait()

    config = CheckpointTestConfig("unreachable", before_call=stall)
    limits = CompactionLimits(max_tokens=3_000, timeout_s=0.05)
    knowledge = build_compaction_config(limits=limits)
    stream = MemoryStream()
    original = ModelRequest.ensure_request(["old " + "x" * 20_000])
    await stream.history.replace([original])
    failed, completed = [], []
    stream.where(CompactionFailed).subscribe(lambda event: failed.append(event))
    stream.where(CompactionCompleted).subscribe(lambda event: completed.append(event))
    agent = compacting_agent(
        config,
        knowledge,
        middleware=[LLMRetryMiddleware(retries=2, base_delay=0)],
    )
    with pytest.raises(ContextBudgetError, match="timed out after 0.05 seconds"):
        await agent.ask("continue", stream=stream)
    events = list(await stream.history.get_events())
    assert len(failed) == 1
    assert failed[0].error_type == "TimeoutError"
    assert failed[0].error == "A compaction model call timed out after 0.05 seconds"
    assert completed == []
    assert original in events
    assert await load_checkpoint(knowledge.store, stream.id) is None
    assert len(config.requests) == 1
    provider = CheckpointTestConfig("unreachable", checkpoint=TimeoutError("Provider timed out"))
    retry_agent = compacting_agent(
        provider, knowledge, middleware=[LLMRetryMiddleware(retries=2, base_delay=0)]
    )
    with pytest.raises(ContextBudgetError, match="Provider timed out"):
        await retry_agent.ask("retry on a new Turn", stream=stream)
    assert len(failed) == 2
    assert failed[-1].error == "Provider timed out"
    assert len(provider.requests) == 1


@pytest.mark.parametrize(
    "checkpoint",
    [
        "not JSON",
        checkpoint_json(needed_skills=["invented"]),
        RuntimeError("summarizer failed"),
    ],
)
async def test_invalid_checkpoint_keeps_originals_and_reports_failure(checkpoint):
    stream = MemoryStream()
    original = [ModelRequest.ensure_request(["old " + "x" * 20_000])]
    await stream.history.replace(original)
    config = CheckpointTestConfig("unreachable", checkpoint=checkpoint)
    knowledge = build_compaction_config(max_tokens=3_000)
    failures = []
    stream.where(CompactionFailed).subscribe(lambda event: failures.append(event))
    with pytest.raises((ValueError, RuntimeError)):
        await compacting_agent(config, knowledge).ask("continue", stream=stream)
    assert original[0] in await stream.history.get_events()
    assert await load_checkpoint(knowledge.store, stream.id) is None
    assert len(failures) == 1
    assert all(
        options.get("response_schema") is CHECKPOINT_SCHEMA for _, _, options in config.requests
    )


async def test_committed_checkpoint_recovers_when_turn_fails_before_log_materialization():
    store = MemoryKnowledgeStore()
    knowledge = build_compaction_config(max_tokens=3_000, store=store)
    stream = MemoryStream()
    original = [ModelRequest.ensure_request(["old " + "x" * 20_000])]
    await stream.history.replace(original)
    writer = ChatEventLog(store)
    await writer.persist(stream.id, original)
    config = CheckpointTestConfig(RuntimeError("provider failed"))
    with pytest.raises(RuntimeError, match="provider failed"):
        await compacting_agent(config, knowledge).ask("continue", stream=stream)
    assert original[0] in await stream.history.get_events()
    assert await load_checkpoint(store, stream.id) is not None
    restarted = MemoryStream(id=stream.id)
    await restarted.history.replace(await writer.load_current(stream.id))
    fresh = CheckpointTestConfig("recovered")
    await compacting_agent(fresh, build_compaction_config(max_tokens=3_000, store=store)).ask(
        "resume", stream=restarted
    )
    assert isinstance(fresh.requests[0][0][0], CompactionSummary)
    assert original[0] in await writer.replay(stream.id, await restarted.history.get_events())
    assert await writer.delete(stream.id)
    assert await store.list(checkpoint_root(stream.id)) == []


async def test_two_chats_share_strategy_without_sharing_checkpoint_state():
    knowledge = build_compaction_config(max_tokens=3_000)
    streams = [MemoryStream(), MemoryStream()]
    for index, stream in enumerate(streams):
        await stream.history.replace(
            [ModelRequest.ensure_request([f"chat {index}: " + "x" * 20_000])]
        )
    agent = compacting_agent(CheckpointTestConfig("default"), knowledge)
    models = [
        CheckpointTestConfig("done", checkpoint=checkpoint_json(goal=f"goal {index}"))
        for index in range(2)
    ]
    await asyncio.gather(
        *(
            agent.ask("continue", stream=stream, config=model)
            for stream, model in zip(streams, models, strict=True)
        )
    )
    for index, stream in enumerate(streams):
        record = await load_checkpoint(knowledge.store, stream.id)
        assert record.data.goal == f"goal {index}"


async def test_needed_skill_is_restored_verbatim_and_refreshed_through_held_runtime(tmp_path):
    root = tmp_path / "skills"
    skill = root / "plan"
    skill.mkdir(parents=True)
    path = skill / "SKILL.md"
    path.write_text("---\nname: plan\ndescription: Make a plan\n---\nAlways verify the rollback.")
    runtime = FilteredSkillRuntime(LocalRuntime(str(root)), lambda skill: True, snapshot=True)
    plugin = SkillPlugin(runtime)
    plugin.add_middleware(Middleware(SkillCatalogMiddleware, resolver=SkillResolver([runtime])))
    stream = MemoryStream()
    await runtime.read("plan", ConversationContext(stream))
    context = ConversationContext(stream)
    await context.send(ModelRequest.ensure_request(["old " + "x" * 30_000]))
    config = CheckpointTestConfig("done", checkpoint=checkpoint_json(needed_skills=["plan"]))
    knowledge = build_compaction_config(max_tokens=4_000)
    agent = compacting_agent(config, knowledge, plugins=[plugin])
    await agent.ask("continue planning", stream=stream)
    assert "Always verify the rollback." in json.dumps(
        [e.to_dict() for e in config.requests[-1][0]]
    )
    record = await load_checkpoint(knowledge.store, stream.id)
    assert record.skills[0].name == "plan"
    path.write_text("---\nname: plan\ndescription: Make a plan\n---\nNew version: verify twice.")
    await agent.ask("continue", stream=stream)
    assert "New version: verify twice." in json.dumps([e.to_dict() for e in config.requests[-1][0]])


async def test_cancelled_summarization_keeps_source_history_and_usage():
    started, release = asyncio.Event(), asyncio.Event()

    async def pause(messages, context, options):
        if options.get("response_schema") is CHECKPOINT_SCHEMA:
            started.set()
            await release.wait()

    stream = MemoryStream()
    original = ModelRequest.ensure_request(["old " + "x" * 20_000])
    await stream.history.replace([original])
    config = CheckpointTestConfig("done", before_call=pause)
    knowledge = build_compaction_config(max_tokens=3_000)
    task = asyncio.create_task(compacting_agent(config, knowledge).ask("continue", stream=stream))
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert original in await stream.history.get_events()
    assert await load_checkpoint(knowledge.store, stream.id) is None


async def test_compaction_timeout_applies_to_each_summarizer_call():
    async def slow(messages, context, options):
        if options.get("response_schema") is CHECKPOINT_SCHEMA:
            await asyncio.sleep(0.15)

    config = CheckpointTestConfig("done", before_call=slow)
    limits = CompactionLimits(max_tokens=3_000, timeout_s=0.25)
    stream = MemoryStream()
    await stream.history.replace([ModelRequest.ensure_request(["old " + "x" * 40_000])])
    completed = []
    stream.where(CompactionCompleted).subscribe(lambda event: completed.append(event))
    reply = await compacting_agent(config, build_compaction_config(limits=limits)).ask(
        "continue", stream=stream
    )
    assert reply.body == "done"
    assert completed[0].llm_calls >= 2


async def test_summarizer_call_limit_preserves_history():
    stream = MemoryStream()
    original = ModelRequest.ensure_request(["old " + "x" * 40_000])
    await stream.history.replace([original])
    config = CheckpointTestConfig("done")
    knowledge = build_compaction_config(limits=CompactionLimits(max_tokens=3_000, max_calls=1))
    with pytest.raises(ContextBudgetError, match="model-call limit"):
        await compacting_agent(config, knowledge).ask("continue", stream=stream)
    assert original in await stream.history.get_events()
    usage = [e for e in await stream.history.get_events() if isinstance(e, UsageEvent)]
    assert len(usage) == 1 and usage[0].kind == "compaction"


async def test_summary_validation_rejects_invented_evidence():
    data = json.loads(checkpoint_json())
    data["completed"] = [{"description": "Published", "refs": ["invented-source"]}]
    config = CheckpointTestConfig("unreachable", checkpoint=json.dumps(data))
    stream = MemoryStream()
    original = ModelRequest.ensure_request(["old " + "x" * 20_000])
    await stream.history.replace([original])
    knowledge = build_compaction_config(max_tokens=3_000)
    with pytest.raises(ValueError, match="unknown evidence"):
        await compacting_agent(config, knowledge).ask("continue", stream=stream)
    assert await load_checkpoint(knowledge.store, stream.id) is None
    assert original in await stream.history.get_events()


async def test_failed_source_write_does_not_activate_checkpoint(tmp_path):
    store = ControlledHistory(tmp_path / "history.db")
    config = CheckpointTestConfig("unreachable")
    stream = MemoryStream()
    original = ModelRequest.ensure_request(["old " + "x" * 20_000])
    await stream.history.replace([original])
    knowledge = build_compaction_config(max_tokens=3_000, store=store)
    store.fail = True
    try:
        with pytest.raises(OSError, match="unavailable"):
            await compacting_agent(config, knowledge).ask("continue", stream=stream)
        assert await load_checkpoint(store, stream.id) is None
        assert original in await stream.history.get_events()
    finally:
        store.inner.close()


async def test_multiple_compactions_preserve_unknown_action_and_do_not_repeat_it(tmp_path):
    sent = tmp_path / "sent.txt"

    @tool
    def send_once() -> str:
        sent.write_text((sent.read_text() if sent.exists() else "") + "sent\n")
        return "Request sent; acknowledgement unavailable."

    stream = MemoryStream()
    knowledge = build_compaction_config(max_tokens=3_000)
    first = CheckpointTestConfig(ToolCallEvent(name="send_once"), "acknowledgement unknown")
    await compacting_agent(first, knowledge, tools=[send_once]).ask("send once", stream=stream)
    result = next(
        e
        for e in await stream.history.get_events()
        if isinstance(e, ToolResultEvent) and e.name == "send_once"
    )
    data = json.loads(checkpoint_json("Finish without repeating the send"))
    data["constraints"] = ["Send exactly once", "Do not retry an unknown acknowledgement"]
    data["unknown_outcomes"] = [
        {"description": "Delivery acknowledgement is unknown", "refs": [event_ref(result)]}
    ]
    config = CheckpointTestConfig("continue", checkpoint=json.dumps(data))
    agent = compacting_agent(config, knowledge, tools=[send_once])
    for index in range(2):
        await ConversationContext(stream).send(
            ModelRequest.ensure_request([f"Working notes {index}: " + "x" * 20_000])
        )
        await agent.ask("Continue without retrying the send", stream=stream)
        record = await load_checkpoint(knowledge.store, stream.id)
        assert record.data.unknown_outcomes[0].description == "Delivery acknowledgement is unknown"
        assert record.data.constraints == [
            "Send exactly once",
            "Do not retry an unknown acknowledgement",
        ]
    assert sent.read_text() == "sent\n"


async def test_inbox_correction_arriving_during_summary_survives_materialization():
    started, release = asyncio.Event(), asyncio.Event()
    paused = False

    async def pause(messages, context, options):
        nonlocal paused
        if options.get("response_schema") is CHECKPOINT_SCHEMA and not paused:
            paused = True
            started.set()
            await release.wait()

    stream = MemoryStream()
    await stream.history.replace([ModelRequest.ensure_request(["old " + "x" * 20_000])])
    config = CheckpointTestConfig("first reply", "corrected reply", before_call=pause)
    knowledge = build_compaction_config(max_tokens=3_000)
    agent = compacting_agent(config, knowledge)
    async with agent.run("continue", stream=stream) as run:
        task = asyncio.create_task(run.result())
        await asyncio.wait_for(started.wait(), timeout=2)
        run.enqueue("Correction: use the new destination")
        release.set()
        await task
    calls = [
        messages
        for messages, _, options in config.requests
        if options.get("response_schema") is not CHECKPOINT_SCHEMA
    ]
    assert any(
        "Correction: use the new destination" in json.dumps([e.to_dict() for e in call])
        for call in calls
    )
    assert any(
        "Correction: use the new destination" in json.dumps(e.to_dict())
        for e in await stream.history.get_events()
    )


async def test_new_event_before_checkpoint_marker_is_not_mistaken_for_covered_history():
    stream = MemoryStream()
    await stream.history.replace([ModelRequest.ensure_request(["old " + "x" * 20_000])])
    knowledge = build_compaction_config(max_tokens=3_000)
    config = CheckpointTestConfig("done")
    agent = compacting_agent(config, knowledge)
    await agent.ask("continue", stream=stream)
    current = list(await stream.history.get_events())
    external = ModelResponse(ModelMessage("Work retained from another process"))
    await stream.history.replace([external, *current])
    await agent.ask("review all completed work", stream=stream)
    messages = config.requests[-1][0]
    assert any(
        "Work retained from another process" in json.dumps(event.to_dict()) for event in messages
    )
