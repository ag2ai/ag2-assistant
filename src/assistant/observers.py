"""Behaviour observers — AG2-native guards on the agent's event stream.

Observers watch the stream and emit `ObserverAlert`s, which ride the same stream
the GUI projects (so they surface as a note in the thread and persist in the
event log). Two families here:

**Stuck-turn (event-driven):**

- `NativeToolLoopDetector` (AG2's `LoopDetector`, narrowed to our own tools): the
  same tool called repeatedly with *identical* arguments — a tight retry loop.
- `ToolChurnObserver`: a turn that makes *many* tool calls without producing an
  answer — the flailing case (e.g. 20+ varied searches) the LoopDetector misses
  because the arguments differ each time.

**Wedged-turn (silence-driven):**

- `SilenceWatchdog`: fires when NO event has been seen for a while during an
  active turn — the case the event-driven observers structurally *cannot* catch,
  because they react to events and a hung LLM call produces none. Emits a
  CRITICAL alert (and, past a harder threshold, a FATAL alert → `HaltEvent`).

Alerts surface + persist on the stream. When the agent has an `AlertPolicy` wired
(the assistant's memory/compaction agents do), non-fatal alerts are also injected
into the model and FATAL alerts halt the turn deterministically.
"""

import asyncio
import time
from collections.abc import Callable
from contextlib import AsyncExitStack, ExitStack

from ag2.annotations import Context
from ag2.context import ConversationContext
from ag2.events import (
    BaseEvent,
    BuiltinToolCallEvent,
    ModelResponse,
    ObserverAlert,
    Severity,
    ToolCallEvent,
)
from ag2.observers import BaseObserver, LoopDetector

try:  # ObserverStarted/Completed bracket a turn; used to scope silence to in-turn.
    from ag2.events import ObserverCompleted, ObserverStarted  # local: version-guarded ag2 events
except ImportError:  # pragma: no cover - older AG2 without the lifecycle events
    ObserverStarted = ObserverCompleted = ()  # type: ignore[assignment,misc]
from ag2.watch import EventWatch

# A turn that calls this many tools without answering is treated as stuck/flailing.
_CHURN_THRESHOLD = 20

# Default silence thresholds (seconds). Overridable per-instance / via config.
_SILENCE_ALERT_S = 300.0
_SILENCE_HALT_S = 900.0


class NativeToolLoopDetector(LoopDetector):
    """`LoopDetector`, narrowed to tools *this* agent calls.

    AG2's detector keys a call on `(name, arguments)` and warns on three
    identical ones in a row. That identity only holds for our own tools, where
    `arguments` is the real JSON payload. ACP forwards the *inner* CLI agent's
    tool calls onto the same stream as `BuiltinToolCallEvent`s, mapped from the
    protocol's `tool_call` update as `(title, rawInput)` — and `rawInput` is
    optional in the ACP schema while `title` is only a human-readable label. An
    agent that sends `title="Terminal"` with no `rawInput` collapses every shell
    command onto the key `("Terminal", "{}")`, so three *unrelated* commands read
    as a loop.

    Repairing the identity isn't an option: the only reliably unique field is
    `toolCallId`, which is fresh per call and would disable the detector. So we
    skip delegated calls entirely — a wedged ACP session is the coding
    provider's own timeouts to catch (see `coding/acp_provider.py`), not ours.

    `ToolChurnObserver` deliberately still counts these: its threshold is a
    volume of calls, not their identity, so a delegation that burns 20+ tool
    calls without answering is worth flagging however it was spawned.
    """

    async def process(self, events: list[BaseEvent], ctx: Context) -> ObserverAlert | None:
        own = [e for e in events if not isinstance(e, BuiltinToolCallEvent)]
        if not own:
            return None
        return await super().process(own, ctx)


class ToolChurnObserver(BaseObserver):
    """Warn when one turn makes too many tool calls without producing a final
    answer — a flailing/stuck turn (e.g. the 27 varied Gmail/Drive searches that
    `LoopDetector` misses because each call's arguments differ).

    Counts `ToolCallEvent`s across the turn and resets when the turn produces a
    `ModelResponse` with no further tool calls (a final answer). Fires once per
    turn (until reset) at the threshold.
    """

    def __init__(self, threshold: int = _CHURN_THRESHOLD, *, name: str = "tool-churn") -> None:
        super().__init__(name, watch=EventWatch((ToolCallEvent, ModelResponse)))
        self._threshold = threshold
        self._count = 0
        self._flagged = False

    async def process(self, events: list[BaseEvent], ctx: Context) -> ObserverAlert | None:
        for event in events:
            if isinstance(event, ToolCallEvent):
                self._count += 1
                if self._count >= self._threshold and not self._flagged:
                    self._flagged = True
                    return ObserverAlert(
                        source=self.name,
                        severity=Severity.WARNING,
                        message=(
                            f"This turn has made {self._count} tool calls without "
                            "producing an answer — it may be stuck or flailing."
                        ),
                        data={"tool_calls": self._count},
                    )
            elif isinstance(event, ModelResponse):
                # A response with no further tool calls ends the turn → reset.
                if not (event.tool_calls and event.tool_calls.calls):
                    self._count = 0
                    self._flagged = False
        return None


class SilenceWatchdog:
    """Fire when the stream goes silent mid-turn — the *absence* of events.

    The event-driven observers (`LoopDetector`, `ToolChurnObserver`) react to
    events, so a hung LLM call — which emits nothing — slips past them entirely.
    This watchdog closes that gap: it resets a timer on every stream event and,
    if `alert_s` seconds pass with no new event during an active turn, emits a
    CRITICAL `ObserverAlert`. If `halt_s` is set and elapses, it escalates to a
    FATAL alert (which an `AlertPolicy`-equipped agent turns into a `HaltEvent`,
    terminating the wedged turn deterministically).

    Scoping to *in-turn* silence is structural, not heuristic: this observer is
    registered per-turn (its monitor loop starts on `register` and is torn down
    when the turn's `ExitStack`/`AsyncExitStack` unwinds), so between turns it
    isn't running at all — a legitimately idle session never trips it.

    The watchdog ignores its own alerts and the observer-lifecycle events when
    resetting the timer, so a fired CRITICAL alert doesn't keep the clock alive
    and block the later FATAL escalation.

    `poll_interval_s` and `clock` are injectable so tests can drive silence in
    milliseconds without real long sleeps.
    """

    def __init__(
        self,
        alert_s: float = _SILENCE_ALERT_S,
        halt_s: float | None = _SILENCE_HALT_S,
        *,
        name: str = "silence-watchdog",
        poll_interval_s: float = 5.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.name = name
        self._alert_s = alert_s
        self._halt_s = halt_s if (halt_s and halt_s > 0) else None
        self._poll = poll_interval_s
        self._clock = clock
        self._ctx: Context | None = None
        self._sub_id = None
        self._task: asyncio.Task | None = None
        self._last = 0.0
        self._alerted = False
        self._halted = False

    # --- Observer protocol -------------------------------------------------
    def register(self, stack: "ExitStack | AsyncExitStack", context: Context) -> None:
        self._ctx = context
        self._last = self._clock()
        self._alerted = False
        self._halted = False
        self._sub_id = context.stream.subscribe(self._on_event)
        self._task = asyncio.ensure_future(self._monitor(context.stream))
        stack.callback(self._disarm)

    def _disarm(self) -> None:
        if self._task is not None:
            self._task.cancel()
            self._task = None
        if self._ctx is not None and self._sub_id is not None:
            self._ctx.stream.unsubscribe(self._sub_id)
        self._sub_id = None
        self._ctx = None

    # --- timer + monitor ---------------------------------------------------
    async def _on_event(self, event: BaseEvent, ctx: Context) -> None:
        # A real event → the turn is making progress; reset the silence clock.
        # Ignore our OWN alerts and the lifecycle brackets so a fired alert can't
        # keep the clock alive (which would block escalation to FATAL/halt).
        if isinstance(event, ObserverAlert) and event.source == self.name:
            return
        if ObserverStarted and isinstance(event, (ObserverStarted, ObserverCompleted)):
            return
        self._last = self._clock()
        self._alerted = False
        self._halted = False

    async def _monitor(self, stream) -> None:
        while True:
            try:
                await asyncio.sleep(self._poll)
            except asyncio.CancelledError:
                return
            await self._check(stream)

    async def _check(self, stream) -> None:
        """One silence check — exposed for tests to drive without sleeping."""
        silent = self._clock() - self._last
        ctx = ConversationContext(stream=stream)
        if self._halt_s is not None and silent >= self._halt_s and not self._halted:
            self._halted = True
            self._alerted = True  # a FATAL supersedes any pending CRITICAL
            await ctx.send(
                ObserverAlert(
                    source=self.name,
                    severity=Severity.FATAL,
                    message=(
                        f"No activity for {silent:.0f}s — the turn is wedged. Halting execution."
                    ),
                    data={"silent_s": round(silent, 1), "threshold_s": self._halt_s},
                )
            )
            return
        if silent >= self._alert_s and not self._alerted:
            self._alerted = True
            await ctx.send(
                ObserverAlert(
                    source=self.name,
                    severity=Severity.CRITICAL,
                    message=(
                        f"No activity for {silent:.0f}s — the turn may be wedged "
                        "(an LLM call or tool may have hung)."
                    ),
                    data={"silent_s": round(silent, 1), "threshold_s": self._alert_s},
                )
            )


def build_observers(
    silence_alert_s: float = _SILENCE_ALERT_S,
    silence_halt_s: float | None = _SILENCE_HALT_S,
) -> list:
    """The stuck-turn observer set wired onto the assistant's agents.

    Adds the `SilenceWatchdog` for wedged (silent) turns alongside the two
    event-driven stuck-turn guards.
    """
    return [
        NativeToolLoopDetector(),
        ToolChurnObserver(),
        SilenceWatchdog(alert_s=silence_alert_s, halt_s=silence_halt_s),
    ]
