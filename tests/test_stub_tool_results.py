"""Stubbing old tool results once a chat's prompt cache is cold."""

from ag2.events import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    TextInput,
    ToolCallEvent,
    ToolCallsEvent,
    ToolResult,
    ToolResultEvent,
    ToolResultsEvent,
)

from assistant.config import apply_env_overrides
from assistant.stub_tool_results import (
    STUB_TEMPLATE,
    StubOldToolResultsMiddleware,
    stub_old_tool_results,
)

IDLE = 600
BIG = "x" * 1000


def _at(event, when):
    event.created_at = when
    return event


def _request(when, text="hi"):
    return _at(ModelRequest([TextInput(text)]), when)


def _call(call_id, name="fetch_page", arguments='{"url": "u"}'):
    return ToolCallsEvent([ToolCallEvent(id=call_id, name=name, arguments=arguments)])


def _result(call_id, name="fetch_page", text=BIG):
    return ToolResultsEvent(
        [ToolResultEvent(parent_id=call_id, name=name, result=ToolResult(text))]
    )


def _response(when, text="ok"):
    return _at(ModelResponse(ModelMessage(text)), when)


def _texts(events):
    return [
        part.content
        for event in events
        if isinstance(event, ToolResultsEvent)
        for result in event.results
        for part in result.result.parts
    ]


def _chat(gap):
    """Two turns, each with one 1000-char result, then a new request `gap` seconds later."""
    return [
        _request(0),
        _call("a"),
        _result("a"),
        _response(10),
        _request(20),
        _call("b", "get_task"),
        _result("b", "get_task"),
        _response(30),
        _request(30 + gap),
    ]


def _stub(events, **kwargs):
    settings = {"idle_s": IDLE, "keep_chars": 1000, "min_chars": 300} | kwargs
    return stub_old_tool_results(events, **settings)


def test_warm_chat_is_unchanged():
    events = _chat(gap=IDLE - 1)
    assert _stub(events) == events
    assert _texts(_stub(events)) == [BIG, BIG]


def test_cold_chat_stubs_results_past_the_budget_keeping_the_newest():
    texts = _texts(_stub(_chat(gap=IDLE + 1)))
    assert texts == [STUB_TEMPLATE.format(name="fetch_page", chars=1000), BIG]


def test_budget_keeps_every_result_that_fits():
    assert _texts(_stub(_chat(gap=IDLE + 1), keep_chars=2000)) == [BIG, BIG]


def test_mid_run_call_is_unchanged():
    events = _chat(gap=1)
    events += [_call("c"), _result("c")]
    events[-1].created_at = 30 + IDLE * 5
    assert _stub(events) == events


def test_results_stubbed_at_a_cold_turn_stay_stubbed_while_warm():
    events = _chat(gap=IDLE + 1)
    events += [
        _response(640),
        _request(650),
        _call("c"),
        _result("c"),
        _response(660),
        _request(670),
    ]
    assert _texts(_stub(events))[0].startswith("[tool result omitted")
    assert _texts(_stub(events))[1] == BIG
    assert _texts(_stub(events))[2] == BIG


def test_output_is_deterministic():
    events = _chat(gap=IDLE + 1)
    assert _texts(_stub(events)) == _texts(_stub(events))


def test_prefix_of_a_warm_call_is_the_prefix_of_the_previous_call():
    cold = _chat(gap=IDLE + 1)
    before = _texts(_stub(cold))
    after = _texts(_stub([*cold, _response(640), _request(650), _call("c"), _result("c")]))
    assert after[: len(before)] == before


def test_small_results_are_never_stubbed():
    events = _chat(gap=IDLE + 1)
    events[2] = _result("a", text="short")
    assert _texts(_stub(events, keep_chars=0))[0] == "short"


def test_excluded_tools_are_kept_and_do_not_spend_budget():
    texts = _texts(_stub(_chat(gap=IDLE + 1), keep_chars=0, exclude={"fetch_page"}))
    assert texts[0] == BIG
    assert texts[1] == STUB_TEMPLATE.format(name="get_task", chars=1000)


def test_arguments_and_pairing_are_untouched():
    events = _chat(gap=IDLE + 1)
    out = _stub(events)
    assert [type(e) for e in out] == [type(e) for e in events]
    assert out[1].calls[0].arguments == '{"url": "u"}'
    assert out[2].results[0].parent_id == "a"
    assert out[2].results[0].name == "fetch_page"


def test_source_events_are_not_modified():
    events = _chat(gap=IDLE + 1)
    _stub(events)
    assert _texts(events) == [BIG, BIG]


def test_no_prior_model_response_means_no_stubbing():
    events = [_request(0), _call("a"), _result("a"), _request(10_000)]
    assert _stub(events, keep_chars=0) == events


async def test_middleware_hands_the_stubbed_events_to_the_model_call():
    seen = []

    async def call_next(events, context):
        seen.append(list(events))

    middleware = StubOldToolResultsMiddleware(_request(0), None, idle_s=IDLE, keep_chars=1000)
    await middleware.on_llm_call(call_next, _chat(gap=IDLE + 1), None)
    assert _texts(seen[0])[0].startswith("[tool result omitted")


def test_off_unless_the_env_names_an_idle_threshold(config):
    cfg = config
    assert cfg.memory.stub_tool_results_after_idle_s == 0
    apply_env_overrides(
        cfg,
        {
            "AG2ASSISTANT_TRIM_IDLE_SECONDS": "600",
            "AG2ASSISTANT_TRIM_EXCLUDE_TOOLS": "a, b",
        },
    )
    assert cfg.memory.stub_tool_results_after_idle_s == 600
    assert cfg.memory.stub_keep_result_chars == 30_000
    assert cfg.memory.stub_exclude_tools == ["a", "b"]
