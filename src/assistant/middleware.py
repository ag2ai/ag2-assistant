"""Bound each LLM call, retry transient failures, and clean provider markup from replies."""

import asyncio
import copy
import json
import logging
import random
import re
from collections.abc import Awaitable, Callable, Mapping, Sequence
from typing import Any

from ag2.annotations import Context
from ag2.events import BaseEvent, ModelRequest, ModelResponse, TextInput
from ag2.middleware.base import BaseMiddleware, LLMCall, MiddlewareFactory

from assistant.compaction import ContextBudgetError
from assistant.room_context import refresh_room_context

logger = logging.getLogger(__name__)

_TRANSIENT_HTTP_STATUS = frozenset({429, 500, 502, 503, 504})
_TRANSIENT_PROVIDER_CODES = frozenset(
    {
        "server_error",
        "internal_error",
        "internal_server_error",
        "overloaded_error",
        "server_overloaded",
        "rate_limit_error",
        "rate_limit_exceeded",
        "service_unavailable",
    }
)
# Match transport base classes without importing optional provider SDKs.
_TRANSIENT_NETWORK_TYPES = frozenset(
    {
        ("openai", "APIConnectionError"),
        ("anthropic", "APIConnectionError"),
        *(
            (module, name)
            for module in ("httpx", "httpx2")
            for name in ("TimeoutException", "NetworkError", "RemoteProtocolError")
        ),
    }
)


class LLMCallTimeout(Exception):
    """Raised when a single LLM call exceeds its configured wall-clock ceiling.

    Propagates out of the agent turn so the task executor's existing failure path
    marks the task FAILED with this message (rather than the turn awaiting a reply
    that never arrives).
    """


class LLMTimeoutMiddleware(MiddlewareFactory):
    """Wrap each LLM call in a wall-clock timeout.

    A ``MiddlewareFactory`` (mirrors AG2's own ``RetryMiddleware``): the Agent
    calls it per turn with ``(event, context)`` to build the per-turn instance.
    """

    def __init__(self, seconds: float) -> None:
        self._seconds = seconds

    def __call__(self, event: "BaseEvent", context: "Context") -> "BaseMiddleware":
        return _LLMTimeoutMiddleware(event, context, seconds=self._seconds)


class _LLMTimeoutMiddleware(BaseMiddleware):
    def __init__(self, event: "BaseEvent", context: "Context", *, seconds: float) -> None:
        super().__init__(event, context)
        self._seconds = seconds

    async def on_llm_call(
        self,
        call_next: LLMCall,
        events: "Sequence[BaseEvent]",
        context: Context,
    ) -> ModelResponse:
        try:
            async with asyncio.timeout(self._seconds):
                return await call_next(events, context)
        except TimeoutError as exc:
            raise LLMCallTimeout(f"LLM call timed out after {self._seconds:g}s") from exc


def _is_transient_provider_error(exc: BaseException) -> bool:
    """Recognize structured error codes in a provider's streaming error payload."""
    codes = [getattr(exc, "code", None), getattr(exc, "type", None)]
    body = getattr(exc, "body", None)
    if isinstance(body, Mapping):
        error = body.get("error", body)
        if isinstance(error, Mapping):
            codes.extend((error.get("code"), error.get("type")))
    return any(isinstance(code, str) and code in _TRANSIENT_PROVIDER_CODES for code in codes)


def _is_transient(exc: BaseException) -> bool:
    """Recognize transient HTTP and network failures, including explicit wrapped causes."""
    seen: set[int] = set()
    while id(exc) not in seen:
        seen.add(id(exc))
        if isinstance(exc, ContextBudgetError):
            return False
        for status in (
            getattr(exc, "status_code", None),
            getattr(exc, "code", None),
            getattr(getattr(exc, "response", None), "status_code", None),
        ):
            if status is None:
                continue
            try:
                status = int(status)
            except (TypeError, ValueError):
                continue
            if 400 <= status <= 599:
                return status in _TRANSIENT_HTTP_STATUS
        if _is_transient_provider_error(exc):
            return True
        if isinstance(exc, (LLMCallTimeout, ConnectionError, TimeoutError)):
            return True
        if any(
            (base.__module__.split(".", 1)[0], base.__name__) in _TRANSIENT_NETWORK_TYPES
            for base in type(exc).__mro__
        ):
            return True
        if exc.__cause__ is None:
            break
        exc = exc.__cause__
    return False


class LLMRetryMiddleware(MiddlewareFactory):
    """Retry transient failures up to ``retries`` times with capped exponential waits."""

    def __init__(
        self,
        retries: int = 2,
        *,
        base_delay: float = 1.0,
        max_delay: float = 30.0,
        predicate: Callable[[BaseException], bool] = _is_transient,
        sleep: Callable[[float], Awaitable[Any]] = asyncio.sleep,
        jitter: Callable[[], float] = random.random,
    ) -> None:
        self._retries = max(0, retries)
        self._base_delay = base_delay
        self._max_delay = max_delay
        self._predicate = predicate
        self._sleep = sleep
        self._jitter = jitter

    def __call__(self, event: "BaseEvent", context: "Context") -> "BaseMiddleware":
        return _LLMRetryMiddleware(
            event,
            context,
            retries=self._retries,
            base_delay=self._base_delay,
            max_delay=self._max_delay,
            predicate=self._predicate,
            sleep=self._sleep,
            jitter=self._jitter,
        )


class _LLMRetryMiddleware(BaseMiddleware):
    def __init__(
        self,
        event: "BaseEvent",
        context: "Context",
        *,
        retries: int,
        base_delay: float,
        max_delay: float,
        predicate: Callable[[BaseException], bool],
        sleep: Callable[[float], Awaitable[Any]],
        jitter: Callable[[], float],
    ) -> None:
        super().__init__(event, context)
        self._retries = retries
        self._base_delay = base_delay
        self._max_delay = max_delay
        self._predicate = predicate
        self._sleep = sleep
        self._jitter = jitter

    async def on_llm_call(
        self,
        call_next: LLMCall,
        events: "Sequence[BaseEvent]",
        context: Context,
    ) -> ModelResponse:
        attempt = 0
        while True:
            try:
                return await call_next(refresh_room_context(events, context), context)
            except Exception as exc:
                if attempt >= self._retries or not self._predicate(exc):
                    raise
                delay = self._backoff(attempt)
                logger.warning(
                    "LLM call failed (%s); retry %d/%d in %.2fs",
                    type(exc).__name__,
                    attempt + 1,
                    self._retries,
                    delay,
                )
                await self._sleep(delay)
                attempt += 1

    def _backoff(self, attempt: int) -> float:
        """Double the wait per retry, add up to 25% jitter, and cap the final delay."""
        delay = min(self._base_delay * (2**attempt), self._max_delay)
        return min(delay * (1 + self._jitter() * 0.25), self._max_delay)


class ACPInstructionsMiddleware(MiddlewareFactory):
    """Include resolved assistant instructions in ACP's text-only prompt transport."""

    def __call__(self, event: BaseEvent, context: Context) -> BaseMiddleware:
        return _ACPInstructionsMiddleware(event, context)


class _ACPInstructionsMiddleware(BaseMiddleware):
    async def on_llm_call(
        self, call_next: LLMCall, events: Sequence[BaseEvent], context: Context
    ) -> ModelResponse:
        outgoing = list(events)
        for index in range(len(outgoing) - 1, -1, -1):
            if isinstance(request := outgoing[index], ModelRequest):
                held = copy.copy(request)
                instructions = "Current assistant instructions:\n" + "\n\n".join(context.prompt)
                held.parts = [TextInput(content=instructions), *request.parts]
                outgoing[index] = held
                break
        return await call_next(outgoing, context)


# OpenAI search citations such as "\ue200cite\ue202turn5view4\ue201", with the space before them.
_CITATION_MARKER = re.compile("[ \t]*\ue200[^\ue200\ue201]*\ue201|[\ue200-\ue202]")


def strip_citation_markers(text: str) -> str:
    """`text` without provider citation markers."""
    return _CITATION_MARKER.sub("", text)


def _strip_strings(value: Any) -> Any:
    if isinstance(value, str):
        return strip_citation_markers(value)
    if isinstance(value, list):
        return [_strip_strings(item) for item in value]
    if isinstance(value, dict):
        return {key: _strip_strings(item) for key, item in value.items()}
    return value


class CitationMarkerMiddleware(MiddlewareFactory):
    """Remove provider citation markers from reply text and tool-call arguments."""

    def __call__(self, event: BaseEvent, context: Context) -> BaseMiddleware:
        return _CitationMarkerMiddleware(event, context)


class _CitationMarkerMiddleware(BaseMiddleware):
    async def on_llm_call(
        self, call_next: LLMCall, events: Sequence[BaseEvent], context: Context
    ) -> ModelResponse:
        response = await call_next(events, context)
        if response.message is not None:
            response.message.content = strip_citation_markers(response.message.content)
        for call in response.tool_calls.calls:
            try:
                arguments = json.loads(call.arguments or "{}")
            except ValueError:
                continue
            if (cleaned := _strip_strings(arguments)) != arguments:
                call.arguments = json.dumps(cleaned, ensure_ascii=False)
                call.serialized_arguments = cleaned
        return response
