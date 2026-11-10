"""Stubbing old tool results once a chat's prompt cache is cold."""

import copy
import random
import time
from collections.abc import Collection, Sequence
from dataclasses import dataclass
from functools import partial

import pytest
from ag2.events import (
    BaseEvent,
    DataInput,
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

from assistant import stub_tool_results
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

    middleware = StubOldToolResultsMiddleware(
        _request(0), None, trim=partial(stub_old_tool_results, idle_s=IDLE, keep_chars=1000)
    )
    await middleware.on_llm_call(call_next, _chat(gap=IDLE + 1), None)
    assert _texts(seen[0])[0].startswith("[tool result omitted")


@dataclass
class _Hits:
    items: list[str]


def test_data_results_are_sized_by_their_serialised_text_and_stubbed():
    events = _chat(gap=IDLE + 1)
    data = _Hits(["h" * 400, "i" * 400])
    events[2] = ToolResultsEvent(
        [
            ToolResultEvent(
                parent_id="a", name="duckduckgo_search", result=ToolResult(DataInput(data))
            )
        ]
    )
    out = _stub(events, keep_chars=1000)
    stub = out[2].results[0].result.parts[0].content
    assert stub.startswith("[tool result omitted to save tokens: duckduckgo_search, ")
    kept = _stub(events, keep_chars=5000)[2].results[0].result.parts[0]
    assert isinstance(kept, DataInput)


def test_small_data_results_are_kept():
    events = _chat(gap=IDLE + 1)
    events[2] = ToolResultsEvent(
        [ToolResultEvent(parent_id="a", name="t", result=ToolResult(DataInput(_Hits(["x"]))))]
    )
    assert isinstance(_stub(events, keep_chars=0)[2].results[0].result.parts[0], DataInput)


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


# The first implementation, kept as the oracle the single-pass one must agree with.
def _reference_stub(
    events: Sequence[BaseEvent],
    *,
    idle_s: float,
    keep_chars: int,
    min_chars: int = 300,
    exclude: Collection[str] = (),
) -> list[BaseEvent]:
    """The events with tool results older than a cold-cache boundary replaced by stubs.

    A boundary is a request sent more than `idle_s` after the previous model response.
    At each one the newest `keep_chars` of results stay whole and older ones are stubbed;
    a stubbed result stays stubbed, so the output depends only on the events.
    """
    events = list(events)
    stubbed: set[tuple[int, int]] = set()  # (event index, result index)
    last_response: float | None = None
    for index, event in enumerate(events):
        if isinstance(event, ModelResponse):
            last_response = event.created_at
        elif (
            isinstance(event, ModelRequest)
            and last_response is not None
            and event.created_at - last_response > idle_s
        ):
            kept = 0
            for position in range(index - 1, -1, -1):
                prior = events[position]
                if not isinstance(prior, ToolResultsEvent):
                    continue
                for slot in range(len(prior.results) - 1, -1, -1):
                    result = prior.results[slot]
                    size = stub_tool_results._text_size(result)
                    if size is None or size < min_chars or result.name in exclude:
                        continue
                    kept += size
                    if kept > keep_chars:
                        stubbed.add((position, slot))
    if not stubbed:
        return events
    out: list[BaseEvent] = []
    for position, event in enumerate(events):
        if isinstance(event, ToolResultsEvent) and any(
            (position, slot) in stubbed for slot in range(len(event.results))
        ):
            replaced = copy.copy(event)
            replaced.results = [
                stub_tool_results._stub(result, stub_tool_results._text_size(result) or 0)
                if (position, slot) in stubbed
                else result
                for slot, result in enumerate(event.results)
            ]
            event = replaced
        out.append(event)
    return out


def _mixed_history(seed):
    rng = random.Random(seed)
    events, now = [], 0.0
    for turn in range(rng.randint(2, 14)):
        events.append(_request(now))
        for step in range(rng.randint(0, 3)):
            call_id = f"{turn}-{step}"
            events.append(_call(call_id))
            results = []
            for slot in range(rng.randint(1, 3)):
                name = rng.choice(["fetch_page", "get_task", "duckduckgo_search"])
                payload = rng.choice(
                    [
                        ToolResult("t" * rng.choice([5, 400, 900, 3000])),
                        ToolResult(DataInput(_Hits(["d" * rng.choice([5, 600, 2000])]))),
                    ]
                )
                results.append(
                    ToolResultEvent(parent_id=f"{call_id}.{slot}", name=name, result=payload)
                )
            events.append(ToolResultsEvent(results))
        now += rng.choice([1, 5, 700, 4000])
        events.append(_response(now))
        now += rng.choice([1, 20, 900, 7200])
    events.append(_request(now))
    return events


def _shape(events):
    return [
        [
            (
                r.name,
                r.result.parts[0].content
                if hasattr(r.result.parts[0], "content")
                else repr(r.result.parts[0].data),
            )
            for r in e.results
        ]
        if isinstance(e, ToolResultsEvent)
        else type(e).__name__
        for e in events
    ]


@pytest.mark.parametrize("seed", range(60))
@pytest.mark.parametrize("keep_chars", [0, 1500, 5000])
def test_single_pass_matches_the_reference_on_mixed_histories(seed, keep_chars):
    events = _mixed_history(seed)
    settings = {"idle_s": IDLE, "keep_chars": keep_chars, "min_chars": 300, "exclude": {"get_task"}}
    assert _shape(stub_old_tool_results(events, **settings)) == _shape(
        _reference_stub(events, **settings)
    )


def test_cost_grows_linearly_with_the_number_of_cold_turns():
    events = []
    for turn in range(3000):
        events += [
            _request(turn * 1000.0),
            _call(str(turn)),
            _result(str(turn)),
            _response(turn * 1000.0 + 10),
        ]
    events.append(_request(3000 * 1000.0))
    started = time.perf_counter()
    out = _stub(events, keep_chars=10_000)
    assert time.perf_counter() - started < 1.0
    assert _texts(out)[-1] == BIG
