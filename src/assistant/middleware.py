"""AG2 middleware — provider-agnostic guards wrapped around the agent loop.

Two guards, composed so the retry wraps the timeout (each retry gets a fresh
timeout window):

**Per-LLM-call timeout** (`LLMTimeoutMiddleware`). Provider SDKs (Gemini in
particular, on a streaming request) can hang a call indefinitely — no error, no
timeout — leaving the turn awaiting a reply that never comes and a task stuck
"running" forever. AG2's trigger-driven observers can't fire on the *absence* of
events, so this wraps each LLM call in ``asyncio.timeout`` and raises when the
wall clock runs out, turning a silent hang into a clean turn failure.

Why middleware and not a provider timeout: ``GeminiConfig`` exposes only an
``http_client`` (an ``httpx.AsyncClient``), i.e. transport-level read/connect
timeouts — which don't cleanly cover a *streaming* call that stalls between
chunks, and are Gemini-specific anyway. The ``on_llm_call`` hook wraps the whole
call (streaming included) and is provider-agnostic, so it's the primary guard.

**In-place retry** (`LLMRetryMiddleware`). AG2 ships a ``RetryMiddleware`` whose
``retry_on`` tuple is the extension hook, but it has *no backoff* (it loops the
call immediately) and can only match on exception *type* — so it can't tell a
retryable 429/503 from a fatal 400. We mirror its style (a ``MiddlewareFactory``
building a per-turn instance, looping ``call_next``) but add: exponential backoff
(injectable so tests don't sleep) and a transient-only predicate — retry a
``LLMCallTimeout`` unconditionally and a Gemini provider error only when its HTTP
status is transient (429 / 500 / 502 / 503 / 504), never a fatal 4xx like a bad
request. Wired OUTSIDE the timeout in ``agent.py`` so each attempt gets a fresh
timeout window.
"""

import asyncio
import random
from collections.abc import Awaitable, Callable, Sequence
from typing import Any

from ag2.annotations import Context
from ag2.events import BaseEvent, ModelResponse
from ag2.middleware.base import BaseMiddleware, LLMCall, MiddlewareFactory

# HTTP statuses worth another try: rate limit + the transient 5xx family. A fatal
# 4xx (400 bad request, 401/403 auth, 404) is NOT here — retrying it just burns
# the same error. Mirrors what a provider SDK's own retry policy treats as
# transient; kept as data so it's obvious and testable.
_TRANSIENT_HTTP_STATUS = frozenset({429, 500, 502, 503, 504})


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
    """True if `exc` is a transient LLM-call failure worth retrying.

    Always retryable: our own ``LLMCallTimeout`` (a wedged call — the incident's
    failure mode, and a fresh attempt often succeeds). Provider errors are matched
    by HTTP status: a Gemini ``google.genai.errors.APIError`` carries a ``.code``
    (429/5xx → transient; a fatal 4xx → not). Duck-typed on the ``code`` attribute
    so we don't hard-import the Gemini SDK (other providers surface a comparable
    ``status_code``); anything without a recognisable transient status is treated
    as fatal and propagates immediately.
    """
    if isinstance(exc, LLMCallTimeout):
        return True
    status = getattr(exc, "code", None)
    if status is None:
        status = getattr(exc, "status_code", None)
    if status is None:
        return False
    try:
        return int(status) in _TRANSIENT_HTTP_STATUS
    except (TypeError, ValueError):
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
