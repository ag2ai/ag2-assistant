"""Provider and transport failures through bounded retries and real streamed AG2 turns."""

import asyncio
import json

import httpx
import httpx2
import openai
import pytest
from ag2 import Agent, tool
from ag2.config import OpenAIConfig, OpenAIResponsesConfig
from ag2.events import ModelMessageChunk, ModelResponse, ToolResultsEvent
from ag2.stream import MemoryStream

from assistant.middleware import LLMRetryMiddleware, LLMTimeoutMiddleware, _is_transient
from tests.support.http import InterruptedStream, chat_chunk, response_completed, sse


async def _no_wait(seconds):
    pass


@pytest.mark.parametrize(
    "error",
    [
        ConnectionResetError("connection reset"),
        BrokenPipeError("broken pipe"),
        TimeoutError("timed out"),
        httpx.ConnectError("offline"),
        httpx.ReadError("read failed"),
        httpx.ReadTimeout("read timed out"),
        httpx.RemoteProtocolError("incomplete chunked read"),
        httpx2.ConnectError("offline"),
        httpx2.ReadTimeout("read timed out"),
        httpx2.RemoteProtocolError("incomplete chunked read"),
        openai.APIConnectionError(request=httpx2.Request("POST", "https://provider.test")),
        openai.APITimeoutError(request=httpx2.Request("POST", "https://provider.test")),
    ],
)
async def test_network_failure_recovers_with_the_original_request(error):
    remaining = [error, "Recovered"]
    request = ["original request"]

    async def call(events, context):
        assert events == request
        result = remaining.pop(0)
        if isinstance(result, BaseException):
            raise result
        return result

    middleware = LLMRetryMiddleware(sleep=_no_wait)(None, None)
    assert await middleware.on_llm_call(call, request, None) == "Recovered"
    assert remaining == []


@pytest.mark.parametrize("status", [429, 500, 502, 503, 504, 400, 401, 403, 404, 422])
def test_http_status_overrides_string_provider_code_and_transport_cause(status):
    response = httpx2.Response(status, request=httpx2.Request("POST", "https://provider.test"))
    error = openai.APIStatusError(
        "provider failed", response=response, body={"code": "provider_code"}
    )
    error.__cause__ = httpx2.ConnectError("earlier transport error")
    assert _is_transient(error) is (status in {429, 500, 502, 503, 504})
    http_error = httpx.HTTPStatusError(
        "provider failed",
        request=httpx.Request("GET", "https://provider.test"),
        response=httpx.Response(status),
    )
    assert _is_transient(http_error) is (status in {429, 500, 502, 503, 504})


def test_explicit_transport_causes_are_retryable_and_cycles_terminate():
    wrapper = RuntimeError("provider wrapper")
    wrapper.__cause__ = httpx.RemoteProtocolError("incomplete chunked read")
    assert _is_transient(wrapper)
    wrapper.__cause__ = ValueError("invalid payload")
    assert not _is_transient(wrapper)
    wrapper.__cause__.__cause__ = wrapper
    assert not _is_transient(wrapper)
    wrapper.__cause__ = None
    wrapper.__context__ = httpx.ConnectError("unrelated handled failure")
    assert not _is_transient(wrapper)


def test_local_configuration_errors_are_not_network_failures():
    for error in (
        httpx.LocalProtocolError("invalid header"),
        httpx.UnsupportedProtocol("bad URL"),
        ValueError("Connection error."),
        asyncio.CancelledError(),
    ):
        assert not _is_transient(error)


def test_optional_anthropic_connection_and_timeout_errors():
    anthropic = pytest.importorskip("anthropic")
    request = httpx2.Request("POST", "https://provider.test")
    assert _is_transient(anthropic.APIConnectionError(request=request))
    assert _is_transient(anthropic.APITimeoutError(request=request))


@pytest.mark.parametrize("retries", [0, 2])
async def test_network_retry_budget_propagates_the_last_error(retries):
    remaining = [httpx.ConnectError(f"failure {index}") for index in range(retries + 1)]
    final = remaining[-1]

    async def call(events, context):
        raise remaining.pop(0)

    with pytest.raises(httpx.ConnectError) as failure:
        await LLMRetryMiddleware(retries, sleep=_no_wait)(None, None).on_llm_call(call, [], None)
    assert failure.value is final
    assert remaining == []


async def test_cancellation_interrupts_retry_backoff():
    sleeping = asyncio.Event()

    async def sleep(seconds):
        sleeping.set()
        await asyncio.Event().wait()

    async def call(events, context):
        raise httpx.ConnectError("offline")

    task = asyncio.create_task(
        LLMRetryMiddleware(sleep=sleep)(None, None).on_llm_call(call, [], None)
    )
    await asyncio.wait_for(sleeping.wait(), timeout=1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


@pytest.mark.parametrize("provider", ["chat", "responses"])
async def test_interrupted_provider_stream_recovers_without_reexecuting_tools(tmp_path, provider):
    marker = tmp_path / "marker.txt"

    @tool
    def commit_marker() -> str:
        """Append a durable marker."""
        with marker.open("a") as file:
            file.write("saved\n")
        return "Saved marker"

    if provider == "chat":
        first = chat_chunk(
            calls=[
                {
                    "index": 0,
                    "id": "call-1",
                    "type": "function",
                    "function": {"name": "commit_marker", "arguments": "{}"},
                }
            ]
        )
        partial = chat_chunk(content="Partial answer")
        recovered = chat_chunk(content="Recovered answer") + b"data: [DONE]\n\n"
        config_type = OpenAIConfig
    else:
        item = {
            "type": "function_call",
            "id": "fc-1",
            "call_id": "call-1",
            "name": "commit_marker",
            "arguments": "{}",
            "status": "completed",
        }
        first = sse(
            {
                "type": "response.output_item.done",
                "sequence_number": 1,
                "output_index": 0,
                "item": item,
            }
        ) + response_completed([item])
        delta = {
            "type": "response.output_text.delta",
            "sequence_number": 1,
            "output_index": 0,
            "content_index": 0,
            "item_id": "msg-1",
        }
        partial = sse({**delta, "delta": "Partial answer"})
        recovered = sse({**delta, "delta": "Recovered answer"}) + response_completed([])
        config_type = OpenAIResponsesConfig
    requests = []

    def respond(request):
        requests.append(json.loads(request.content))
        if len(requests) == 2:
            return httpx2.Response(
                200,
                headers={"content-type": "text/event-stream"},
                stream=InterruptedStream(partial),
            )
        return httpx2.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content=first if len(requests) == 1 else recovered,
        )

    stream = MemoryStream()
    events = []

    async def collect(event):
        events.append(event)

    stream.subscribe(collect)
    async with httpx2.AsyncClient(transport=httpx2.MockTransport(respond)) as client:
        config = config_type(
            model="test-model", api_key="test", streaming=True, max_retries=0, http_client=client
        )
        agent = Agent(
            "retry-test",
            config=config,
            tools=[commit_marker],
            middleware=[
                LLMRetryMiddleware(sleep=_no_wait),
                LLMTimeoutMiddleware(1),
            ],
        )
        reply = await asyncio.wait_for(
            agent.ask("Commit once and answer", stream=stream), timeout=5
        )
        assert await reply.content() == "Recovered answer"
    assert marker.read_text() == "saved\n"
    assert len(requests) == 3
    assert requests[1] == requests[2]
    assert [event.content for event in events if isinstance(event, ModelMessageChunk)] == [
        "Partial answer",
        "Recovered answer",
    ]
    assert sum(isinstance(event, ToolResultsEvent) for event in events) == 1
    responses = [event for event in events if isinstance(event, ModelResponse)]
    assert len(responses) == 2
    assert responses[-1].message.content == "Recovered answer"
