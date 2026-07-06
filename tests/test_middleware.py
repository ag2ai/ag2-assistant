"""Per-LLM-call timeout middleware — the guard against a silently-hung provider.

Covers three layers:
  1. the middleware hook in isolation (a call that never resolves → raises);
  2. the timeout propagating through a real AG2 ``Agent`` turn;
  3. the executor/runner path: a task whose agent hangs ends FAILED with the
     timeout message (the incident's failure mode, now surfaced not swallowed).
"""

import asyncio

from ag2 import Agent
from ag2.config import ModelConfig, ModelProvider

from assistant.middleware import (
    LLMCallTimeout,
    LLMRetryMiddleware,
    LLMTimeoutMiddleware,
    _is_transient,
)


class _HangingClient:
    """An LLM client whose call never resolves (models the incident hang)."""

    async def __call__(self, messages, context, *, tools, response_schema, serializer):
        await asyncio.Event().wait()


class _HangingConfig(ModelConfig):
    """Minimal ModelConfig that hands back a never-resolving client."""

    provider = ModelProvider.GEMINI
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


async def test_executor_path_marks_task_failed_when_all_attempts_time_out(tmp_path):
    """The executor/runner failure path under the resilience semantics: an agent
    whose LLM call hangs on EVERY attempt consumes each attempt (a crash, not
    instant death) and only then ends the task FAILED — carrying the timeout
    message and the crash flavour. This reproduces the incident's hang chain
    (agent.ask timeout → run_task → RuntimeError → recorded crash) while asserting
    the new "a crash = one attempt" behaviour: the loop runs MAX_ATTEMPTS times.
    """
    from ag2.context import ConversationContext
    from ag2.stream import MemoryStream
    from ag2.tools.subagents.run_task import run_task

    from assistant.tasks import DeliverableStatus, TaskManager, TaskStatus, TaskStore

    store = TaskStore(path=tmp_path / "tasks.db")
    t = await store.create("hung research")
    d = await store.add_deliverable(t.id, "notes")

    attempts = 0

    async def executor(task_id, mgr, asker):
        nonlocal attempts
        attempts += 1
        # Mirror the real executor: run a subagent via run_task and raise its error.
        agent = Agent("worker", config=_HangingConfig(), middleware=[LLMTimeoutMiddleware(0.1)])
        stream = MemoryStream(id=f"{task_id}:work")
        parent = ConversationContext(stream=MemoryStream(id=f"{task_id}:parent"))
        result = await run_task(
            agent, "do the work", parent_context=parent, stream=stream, task_id=task_id
        )
        if not result.completed:
            raise RuntimeError(str(result.error or "subagent failed"))
        # (unreached on the hang path) satisfy the deliverable
        await store.set_deliverable_status(task_id, d["id"], DeliverableStatus.PRODUCED)

    mgr = TaskManager(store, executor)
    await mgr.submit(t.id)
    await asyncio.wait_for(mgr.wait(t.id), timeout=5.0)

    got = await store.get(t.id)
    assert got.status == TaskStatus.FAILED
    assert "timed out" in got.error
    assert "crashed" in got.error  # crash-flavoured, not a plain "not met"
    assert attempts == TaskManager.MAX_ATTEMPTS  # each hang consumed one attempt


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
