"""A turn ended by a ``final=True`` tool leaves history the next turn can use."""

from ag2 import tool
from ag2.config.gemini.mappers import convert_messages as gemini_messages
from ag2.config.openai.mappers import convert_messages as openai_messages
from ag2.events import (
    ModelResponse,
    ToolCallEvent,
    ToolCallsEvent,
    ToolResultsEvent,
)
from ag2.events.tool_events import ToolResult
from fast_depends.pydantic import PydanticSerializer

from assistant.agent import create_agent
from assistant.config import Config
from assistant.gateway.core import build_gateway
from tests.support.fakes import ScriptedModels, fake_summary_factory, fake_title_factory

MARKER = "[REPLIED]"


@tool
async def post() -> ToolResult:
    """Post a message."""
    return ToolResult(MARKER, final=True)


@tool
async def side_job() -> str:
    """Do something else."""
    return "side done"


def _agent_with_tools(config, **kwargs):
    extra = [*(kwargs.pop("extra_tools", None) or []), post, side_job]
    return create_agent(config, extra_tools=extra, **kwargs)


async def _two_turns(paths, *called):
    """Run a turn calling ``called``, then a plain one. → the models and the second request."""
    config = Config.for_paths(paths)
    calls = ModelResponse(tool_calls=ToolCallsEvent([ToolCallEvent(name) for name in called]))
    scripts = iter([[calls, "must not be reached"]])
    models = ScriptedModels(lambda _config, _model: next(scripts, ["second reply"]))
    gateway, _ = build_gateway(
        config,
        memory=False,
        persist=False,
        config_factory=lambda: config,
        agent_factory=_agent_with_tools,
        model_factory=models,
        title_factory=fake_title_factory(),
        summary_factory=fake_summary_factory(),
    )
    await gateway.start()
    try:
        assert await gateway.send_message("one", chat_id="chat") == MARKER
        assert len(models.requests) == 1
        assert await gateway.send_message("two", chat_id="chat") == "second reply"
    finally:
        await gateway.close()
    return models.requests[1][3]


def _wrapped_results(messages):
    return [r for e in messages if isinstance(e, ToolResultsEvent) for r in e.results]


async def test_the_next_turn_reads_the_final_result_not_an_interruption(paths):
    messages = await _two_turns(paths, "post")
    results = _wrapped_results(messages)
    assert [r.result.parts[0].content for r in results] == [MARKER]


async def test_a_sibling_of_a_final_call_keeps_its_result(paths):
    messages = await _two_turns(paths, "post", "side_job")
    results = {r.name: r.result.parts[0].content for r in _wrapped_results(messages)}
    assert results == {"post": MARKER, "side_job": "side done"}


async def test_every_mapper_that_reads_only_the_wrapper_answers_the_call(paths):
    messages = await _two_turns(paths, "post")
    serializer = PydanticSerializer()

    parts = [part for c in gemini_messages(messages, serializer) for part in c.parts]
    assert [p.function_response.name for p in parts if p.function_response] == ["post"]

    openai = openai_messages([], messages, serializer)
    assert [m["content"] for m in openai if m["role"] == "tool"][0].count(MARKER) == 1
