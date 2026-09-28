"""Recovery for turns that die mid-flight.

A turn aborted between a model's tool call and the tool's result (a timeout, a
crash, a process kill) leaves the session history with a ``ModelResponse``
carrying ``tool_calls`` but no matching ``ToolResultsEvent``. Providers reject
such a history outright ("No tool output found for function call …"), so one
interrupted turn would poison the session forever.

Two counterparts here:

- :func:`repair_dangling_tool_calls` (and the stream-level
  :func:`repair_stream_history`) heal a broken history by inserting a synthetic
  "interrupted" result after every unanswered call.
- :func:`wait_reply` bounds a turn like ``asyncio.wait_for``, but its clock
  pauses while a human-in-the-loop prompt is open, and restarts once the human
  answers — waiting on the user must not burn the turn budget (the old fixed
  cap expired *before* the prompt's own deny-fallback, killing the turn
  mid-tool-call in the first place).
"""

import asyncio
import contextlib

from ag2.events import ModelResponse, ToolResultsEvent
from ag2.events.tool_events import ToolResult, ToolResultEvent

from assistant.observability import log_suppressed

# Backstop for a stuck `has_pending` (a buggy asker that never resolves): the
# turn clock may pause at most this long in total, so a wedged prompt cannot
# hold the session lock forever. Real prompts resolve far earlier — every asker
# has its own answer timeout (≤ 300s) that ends the pause by answering/denying.
MAX_HITL_PAUSE = 3600.0

_INTERRUPTED_NOTE = (
    "Tool call interrupted: the previous turn was aborted before this call "
    "produced a result. No output is available; call the tool again if the "
    "result is still needed."
)


def repair_dangling_tool_calls(events):
    """Insert synthetic results after tool calls that never got one.

    Returns the repaired list, or ``None`` when the history is already valid
    (so callers can skip a no-op ``history.replace``; the input is never
    mutated). Each synthetic ``ToolResultsEvent`` is placed directly after its
    dangling ``ModelResponse`` to keep the assistant-message → tool-message
    adjacency providers expect.
    """
    events = events if isinstance(events, list) else list(events)
    answered: set[str] = set()
    for event in events:
        if isinstance(event, ToolResultsEvent):
            answered.update(r.parent_id for r in event.results)

    insertions = []  # (index of the dangling response, synthetic results event)
    for i, event in enumerate(events):
        if not (isinstance(event, ModelResponse) and len(event.tool_calls)):
            continue
        dangling = [c for c in event.tool_calls.calls if c.id not in answered]
        if dangling:
            insertions.append(
                (
                    i,
                    ToolResultsEvent(
                        [
                            ToolResultEvent(
                                parent_id=c.id, name=c.name, result=ToolResult(_INTERRUPTED_NOTE)
                            )
                            for c in dangling
                        ]
                    ),
                )
            )

    if not insertions:
        return None
    repaired = list(events)
    for i, synthetic in reversed(insertions):
        repaired.insert(i + 1, synthetic)
    return repaired


async def repair_stream_history(stream, session_id: str = "") -> None:
    """Best-effort dangling-call repair of a live stream's history in place.

    Safe on anything: a stream without a ``history`` (test stubs) is skipped,
    and a repair failure is logged, never raised — a healing pass must not be
    able to fail the turn it protects.
    """
    history = getattr(stream, "history", None)
    if history is None:
        return
    try:
        repaired = repair_dangling_tool_calls(await history.get_events())
        if repaired is not None:
            await history.replace(repaired)
    except Exception as exc:
        log_suppressed("history repair", exc, session_id=session_id or getattr(stream, "id", ""))


async def _cancel_and_reap(task: asyncio.Task) -> None:
    """Cancel `task` and wait it out without raising its errors (they are moot
    once we've decided to abandon the turn) and without swallowing an external
    cancel of ourselves."""
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)


async def wait_reply(
    coro,
    timeout: float,
    hitl_pending=None,
    slice_seconds: float = 1.0,
    max_pause: float = MAX_HITL_PAUSE,
):
    """Await ``coro`` with a timeout whose clock pauses during HITL prompts.

    ``hitl_pending`` is a nullary callable reporting whether a human prompt is
    currently open (see ``PendingGuard.has_pending``): wall-clock spent while
    it returns truthy does not count toward ``timeout``, and once the prompt
    resolves the budget restarts at ``timeout`` — an answer arriving on a
    nearly-spent turn must leave room to act on it. Total paused time is capped
    by ``max_pause``. With no ``hitl_pending`` this is exactly
    ``asyncio.wait_for``. Timeout granularity is ``slice_seconds``. On expiry
    the underlying task is cancelled (and awaited) before
    ``asyncio.TimeoutError`` is raised, so no work leaks past the cap.
    """
    if hitl_pending is None:
        return await asyncio.wait_for(coro, timeout=timeout)

    task = asyncio.ensure_future(coro)
    remaining = float(timeout)
    paused_total = 0.0
    was_pending = False
    try:
        while True:
            pending = bool(hitl_pending())
            if was_pending and not pending:
                remaining = float(timeout)  # the human answered → fresh budget
            was_pending = pending
            paused = pending and paused_total < max_pause

            if remaining <= 0 and not paused:
                await _cancel_and_reap(task)
                raise asyncio.TimeoutError
            try:
                return await asyncio.wait_for(asyncio.shield(task), timeout=slice_seconds)
            except asyncio.TimeoutError:
                if paused:
                    paused_total += slice_seconds
                else:
                    remaining -= slice_seconds
    except asyncio.CancelledError:
        with contextlib.suppress(Exception):
            await _cancel_and_reap(task)
        raise
