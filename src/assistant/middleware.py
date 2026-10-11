"""Bound each LLM call and retry transient provider or transport failures with backoff."""

import asyncio
import copy
import random
from collections.abc import Awaitable, Callable, Sequence
from typing import Any

from ag2.annotations import Context
from ag2.events import BaseEvent, ModelRequest, ModelResponse, TextInput
from ag2.middleware.base import BaseMiddleware, LLMCall, MiddlewareFactory

_TRANSIENT_HTTP_STATUS = frozenset({429, 500, 502, 503, 504})
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


def _is_transient(exc: BaseException) -> bool:
    """Recognize transient HTTP and network failures, including explicit wrapped causes."""
    seen: set[int] = set()
    while id(exc) not in seen:
        seen.add(id(exc))
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
    """Retry a failed LLM call a few times, with backoff, before propagating.

    ``retries`` is the number of *re-tries* (so total attempts = ``retries + 1``).
    Only transient failures (see ``_is_transient``) are retried; a fatal error
    propagates on the first occurrence. Backoff is exponential from ``base_delay``
    (capped at ``max_delay``) with a little jitter. ``sleep`` is injectable so
    tests exercise the retry path without real waits.
    """

    def __init__(
        self,
        retries: int = 2,
        *,
        base_delay: float = 1.0,
        max_delay: float = 30.0,
        predicate: Callable[[BaseException], bool] = _is_transient,
        sleep: Callable[[float], Awaitable[Any]] = asyncio.sleep,
    ) -> None:
        self._retries = max(0, retries)
        self._base_delay = base_delay
        self._max_delay = max_delay
        self._predicate = predicate
        self._sleep = sleep

    def __call__(self, event: "BaseEvent", context: "Context") -> "BaseMiddleware":
        return _LLMRetryMiddleware(
            event,
            context,
            retries=self._retries,
            base_delay=self._base_delay,
            max_delay=self._max_delay,
            predicate=self._predicate,
            sleep=self._sleep,
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
    ) -> None:
        super().__init__(event, context)
        self._retries = retries
        self._base_delay = base_delay
        self._max_delay = max_delay
        self._predicate = predicate
        self._sleep = sleep

    async def on_llm_call(
        self,
        call_next: LLMCall,
        events: "Sequence[BaseEvent]",
        context: Context,
    ) -> ModelResponse:
        attempt = 0
        while True:
            try:
                return await call_next(events, context)
            except Exception as exc:
                # Out of retries, or a non-transient error → let it propagate so
                # the timeout/executor failure path handles it as before.
                if attempt >= self._retries or not self._predicate(exc):
                    raise
                await self._sleep(self._backoff(attempt))
                attempt += 1

    def _backoff(self, attempt: int) -> float:
        """Exponential backoff (base * 2**attempt), capped, with ±25% jitter."""
        delay = min(self._base_delay * (2**attempt), self._max_delay)
        return delay * (1 + random.uniform(-0.25, 0.25))


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
