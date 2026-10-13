"""Per-LLM-call timeout middleware — the guard against a silently-hung provider.

Covers two layers:
  1. the middleware hook in isolation (a call that never resolves → raises);
  2. the timeout propagating through a real AG2 ``Agent`` turn.

(A third layer used to live here: the old TaskManager executor/runner path
asserting a hung task ends FAILED after MAX_ATTEMPTS. That machinery was
removed by the TaskService v2 rewrite — a run is now one gateway chat turn,
with no separate attempts-loop at that layer — retries live in
LLMRetryMiddleware, covered below. The turn-fails-run-FAILED behavior is now
covered by test_tasks_service.py::test_failed_turn_marks_run_failed.)
"""

import asyncio
import json
from time import monotonic

import httpx
import pytest
from ag2 import Agent, tool
from ag2.config import ModelConfig
from ag2.events import ModelMessage, ModelResponse, ToolCallEvent
from ag2.testing import TestConfig
from openai import APIError, AsyncOpenAI, AsyncStream, BadRequestError, InternalServerError

from assistant.middleware import (
    CitationMarkerMiddleware,
    LLMCallTimeout,
    LLMRetryMiddleware,
    LLMTimeoutMiddleware,
    _is_transient,
    strip_citation_markers,
)


class _HangingClient:
    """An LLM client whose call never resolves (models the incident hang)."""

    async def __call__(self, messages, context, *, tools, response_schema, serializer):
        await asyncio.Event().wait()


class _HangingConfig(ModelConfig):
    """Minimal ModelConfig that hands back a never-resolving client."""

    provider = "gemini"
    model = "hang"

    def copy(self):
        return self

    def create(self):
        return _HangingClient()


async def test_middleware_raises_on_hung_call():
    """A call that never resolves → LLMCallTimeout at the configured seconds."""
    mw = LLMTimeoutMiddleware(0.05)(None, None)

    async def never(events, ctx):
        await asyncio.Event().wait()

    try:
        # Belt-and-braces outer bound so a bug can't hang the suite.
        await asyncio.wait_for(mw.on_llm_call(never, [], None), timeout=1.0)
        raise AssertionError("expected LLMCallTimeout")
    except LLMCallTimeout as exc:
        assert "0.05s" in str(exc)


async def test_middleware_passes_through_a_fast_call():
    """A call that resolves in time is returned untouched."""
    sentinel = object()
    mw = LLMTimeoutMiddleware(1.0)(None, None)

    async def fast(events, ctx):
        return sentinel

    assert await mw.on_llm_call(fast, [], None) is sentinel


async def test_timeout_propagates_through_agent_turn():
    """The timeout fails the whole turn (agent.ask raises), not just the call."""
    agent = Agent("hang-agent", config=_HangingConfig(), middleware=[LLMTimeoutMiddleware(0.1)])
    try:
        await asyncio.wait_for(agent.ask("hi"), timeout=2.0)
        raise AssertionError("expected LLMCallTimeout")
    except LLMCallTimeout as exc:
        assert "timed out" in str(exc)


# --- Retry middleware: transient-only retry with injected (no-real) sleep -----


class _FatalError(Exception):
    """A provider error with a fatal 4xx status → must NOT be retried."""

    code = 400


class _TransientError(Exception):
    """A provider error with a transient status (429) → retryable."""

    code = 429


def _no_sleep():
    """A sleep spy that records requested delays without ever waiting."""
    delays: list[float] = []

    async def sleep(seconds):
        delays.append(seconds)

    return delays, sleep


async def test_retry_recovers_after_transient_timeout():
    """A call that times out once then succeeds returns the success, consuming
    exactly one retry (and one — non-real — sleep)."""
    delays, sleep = _no_sleep()
    sentinel = object()
    calls = 0

    async def flaky(events, ctx):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise LLMCallTimeout("wedged once")
        return sentinel

    mw = LLMRetryMiddleware(2, base_delay=0.01, sleep=sleep)(None, None)
    assert await mw.on_llm_call(flaky, [], None) is sentinel
    assert calls == 2  # one failure + one success
    assert len(delays) == 1  # one backoff sleep, and it was the injected (fake) one


async def test_retry_exhausts_and_propagates_timeout():
    """When every attempt times out, the LLMCallTimeout propagates after the
    retries are spent (total attempts = retries + 1), sleeping only between them."""
    delays, sleep = _no_sleep()
    calls = 0

    async def always_hang(events, ctx):
        nonlocal calls
        calls += 1
        raise LLMCallTimeout("always wedged")

    mw = LLMRetryMiddleware(2, base_delay=0.01, sleep=sleep)(None, None)
    try:
        await mw.on_llm_call(always_hang, [], None)
        raise AssertionError("expected LLMCallTimeout")
    except LLMCallTimeout:
        pass
    assert calls == 3  # 2 retries + the original = 3 attempts
    assert len(delays) == 2  # a sleep before each retry, none after the last


async def test_retry_does_not_retry_fatal_error():
    """A fatal 4xx provider error propagates immediately — no retry, no sleep."""
    delays, sleep = _no_sleep()
    calls = 0

    async def bad_request(events, ctx):
        nonlocal calls
        calls += 1
        raise _FatalError("bad request")

    mw = LLMRetryMiddleware(3, base_delay=0.01, sleep=sleep)(None, None)
    try:
        await mw.on_llm_call(bad_request, [], None)
        raise AssertionError("expected _FatalError")
    except _FatalError:
        pass
    assert calls == 1  # fatal → tried once, never retried
    assert delays == []  # and never slept


async def test_retry_retries_transient_provider_status():
    """A transient provider status (429) is retried like a timeout, recovering
    on a later attempt."""
    delays, sleep = _no_sleep()
    sentinel = object()
    calls = 0

    async def rate_limited_then_ok(events, ctx):
        nonlocal calls
        calls += 1
        if calls < 3:
            raise _TransientError("slow down")
        return sentinel

    mw = LLMRetryMiddleware(3, base_delay=0.01, sleep=sleep)(None, None)
    assert await mw.on_llm_call(rate_limited_then_ok, [], None) is sentinel
    assert calls == 3
    assert len(delays) == 2


def test_is_transient_classifies_by_status():
    """The transient predicate: timeout always; 429/5xx transient; fatal 4xx and
    unstatused errors not. Covers both `.code` and `.status_code` duck-typing."""
    assert _is_transient(LLMCallTimeout("x")) is True
    assert _is_transient(_TransientError("x")) is True
    assert _is_transient(_FatalError("x")) is False
    assert _is_transient(RuntimeError("no status")) is False

    class _ServerError(Exception):
        status_code = 503  # duck-typed via status_code instead of code

    assert _is_transient(_ServerError()) is True


def _stream_error(code=None, error_type=None):
    return APIError(
        "Our servers are currently overloaded. Please try again later.",
        request=httpx.Request("POST", "https://provider.test/v1/responses"),
        body={"code": code, "type": error_type},
    )


@pytest.mark.parametrize(
    ("code", "error_type"),
    [
        ("server_error", None),
        ("internal_error", None),
        ("internal_server_error", None),
        ("server_overloaded", None),
        (None, "overloaded_error"),
        (None, "rate_limit_error"),
        ("rate_limit_exceeded", None),
        ("service_unavailable", None),
    ],
)
def test_streaming_provider_errors_without_http_status_are_transient(code, error_type):
    assert _is_transient(_stream_error(code, error_type))


@pytest.mark.parametrize("code", [None, "invalid_api_key", "context_length_exceeded", "unknown"])
async def test_unknown_or_fatal_streaming_errors_do_not_retry(code):
    error = _stream_error(code)
    delays, sleep = _no_sleep()

    async def fail(events, context):
        raise error

    mw = LLMRetryMiddleware(sleep=sleep)(None, None)
    with pytest.raises(APIError) as raised:
        await mw.on_llm_call(fail, [], None)
    assert raised.value is error
    assert delays == []


@pytest.mark.parametrize(("status", "expected"), [(400, False), (401, False), (503, True)])
def test_http_status_takes_precedence_over_symbolic_provider_code(status, expected):
    response = httpx.Response(
        status, request=httpx.Request("POST", "https://provider.test/v1/responses")
    )
    error_type = InternalServerError if status == 503 else BadRequestError
    error = error_type("provider error", response=response, body={"code": "server_error"})
    assert _is_transient(error) is expected


def test_nested_streaming_error_payload_is_transient():
    error = APIError(
        "stream failed",
        request=httpx.Request("POST", "https://provider.test/v1/responses"),
        body={"error": {"type": "overloaded_error"}},
    )
    assert _is_transient(error)


@pytest.mark.parametrize("jitter", [0.0, 1.0])
async def test_streaming_error_backoff_doubles_and_caps_before_exhaustion(jitter):
    timeline = []
    error = _stream_error("server_error")

    async def fail(events, context):
        timeline.append("call")
        raise error

    async def sleep(delay):
        timeline.append(delay)

    mw = LLMRetryMiddleware(5, base_delay=1, max_delay=5, sleep=sleep, jitter=lambda: jitter)(
        None, None
    )
    with pytest.raises(APIError) as raised:
        await mw.on_llm_call(fail, [], None)
    assert raised.value is error
    factor = 1 + jitter * 0.25
    assert timeline == [
        "call",
        factor,
        "call",
        2 * factor,
        "call",
        4 * factor,
        "call",
        5,
        "call",
        5,
        "call",
    ]


class _RetryConfig(ModelConfig):
    """Model configuration with an injected scripted client."""

    provider = "openai"
    model = "test"

    def __init__(self, client):
        self.client = client

    def copy(self):
        return self

    def create(self):
        return self.client


async def test_streaming_error_recovers_through_agent_after_real_exponential_waits():
    times = []

    async def client(messages, context, *, tools, response_schema, serializer):
        times.append(monotonic())
        if len(times) < 3:
            payload = json.dumps(
                {"error": {"code": "server_error", "message": "Our servers are overloaded."}}
            )
            response = httpx.Response(
                200,
                content=f"data: {payload}\n\n".encode(),
                headers={"Content-Type": "text/event-stream"},
                request=httpx.Request("POST", "https://provider.test/v1/responses"),
            )
            async with AsyncOpenAI(api_key="test") as sdk:
                async for _ in AsyncStream(cast_to=object, response=response, client=sdk):
                    pass
        return ModelResponse(message=ModelMessage("recovered"))

    agent = Agent(
        "retry-agent",
        config=_RetryConfig(client),
        middleware=[
            LLMRetryMiddleware(2, base_delay=0.01, jitter=lambda: 0.0),
            LLMTimeoutMiddleware(0.1),
        ],
    )
    response = await asyncio.wait_for(agent.ask("hi"), timeout=2)
    assert response.body == "recovered"
    assert len(times) == 3
    assert times[1] - times[0] >= 0.01
    assert times[2] - times[1] >= 0.02


async def test_retry_gets_a_fresh_timeout_window_through_agent():
    attempts = 0

    async def client(messages, context, *, tools, response_schema, serializer):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            await asyncio.Event().wait()
        await asyncio.sleep(0.02)
        return ModelResponse(message=ModelMessage("recovered"))

    agent = Agent(
        "timeout-retry-agent",
        config=_RetryConfig(client),
        middleware=[
            LLMRetryMiddleware(1, base_delay=0.01, jitter=lambda: 0.0),
            LLMTimeoutMiddleware(0.1),
        ],
    )
    response = await asyncio.wait_for(agent.ask("hi"), timeout=2)
    assert response.body == "recovered"
    assert attempts == 2


async def test_cancel_during_backoff_stops_before_the_next_provider_call():
    sleeping = asyncio.Event()
    attempts = 0

    async def fail(events, context):
        nonlocal attempts
        attempts += 1
        raise _stream_error("server_error")

    async def sleep(delay):
        sleeping.set()
        await asyncio.Event().wait()

    mw = LLMRetryMiddleware(sleep=sleep)(None, None)
    task = asyncio.create_task(mw.on_llm_call(fail, [], None))
    try:
        await asyncio.wait_for(sleeping.wait(), timeout=1)
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert attempts == 1


# --- Citation markers: provider markup stripped from replies and tool arguments ---


def test_strip_citation_markers_removes_marker_and_leading_space():
    text = "Friday, not Monday. citeturn5view4turn1view0\nNext"
    assert strip_citation_markers(text) == "Friday, not Monday.\nNext"


async def test_citation_markers_never_reach_tools_or_the_reply():
    """A tool receives clean arguments and the final reply is clean."""
    received: list[list[str]] = []

    @tool
    def brief(sections: list[str]) -> str:
        """Draw a brief."""
        received.append(sections)
        return "drawn"

    marked = json.dumps({"sections": ["Up 1%. citeturn1view0"]})
    config = TestConfig(ToolCallEvent(name="brief", arguments=marked), "Done citeturn5view4.")
    agent = Agent(
        "cite-agent", config=config, tools=[brief], middleware=[CitationMarkerMiddleware()]
    )
    reply = await agent.ask("brief me")
    assert received == [["Up 1%."]]
    assert reply.body == "Done."
