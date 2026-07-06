"""Stuck-turn behaviour observers."""

import asyncio
from contextlib import ExitStack

from ag2.context import ConversationContext
from ag2.events import (
    ModelResponse,
    ObserverAlert,
    Severity,
    ToolCallEvent,
    ToolCallsEvent,
)
from ag2.stream import MemoryStream

from assistant.observers import SilenceWatchdog, ToolChurnObserver, build_observers


def _calls(n, name="gmail_search"):
    # Varied arguments (the flail LoopDetector misses because it wants identical args).
    return [ToolCallEvent(name=name, arguments=f'{{"q":{i}}}') for i in range(n)]


async def test_tool_churn_alerts_at_threshold_then_once():
    obs = ToolChurnObserver(threshold=5)
    assert await obs.process(_calls(4), None) is None  # below threshold → quiet
    alert = await obs.process(_calls(1), None)  # 5th call crosses it
    assert isinstance(alert, ObserverAlert)
    assert alert.severity == Severity.WARNING
    assert alert.data["tool_calls"] == 5 and "tool calls" in alert.message
    # already flagged → no repeat alert for the same (still-running) turn
    assert await obs.process(_calls(3), None) is None


async def test_tool_churn_resets_after_a_final_response():
    obs = ToolChurnObserver(threshold=3)
    assert await obs.process(_calls(3), None) is not None  # fires
    # a response with no further tool calls = the turn answered → reset
    assert (
        await obs.process([ModelResponse(message=None, tool_calls=ToolCallsEvent(calls=[]))], None)
        is None
    )
    # a fresh turn can flail (and alert) again
    assert await obs.process(_calls(3), None) is not None


def test_build_observers_includes_loop_churn_and_watchdog():
    from ag2.observers import LoopDetector

    obs = build_observers()
    kinds = {type(o).__name__ for o in obs}
    assert {"LoopDetector", "ToolChurnObserver", "SilenceWatchdog"} <= kinds
    assert any(isinstance(o, LoopDetector) for o in obs)


def test_build_observers_threads_silence_config():
    obs = build_observers(silence_alert_s=42.0, silence_halt_s=99.0)
    wd = next(o for o in obs if isinstance(o, SilenceWatchdog))
    assert wd._alert_s == 42.0 and wd._halt_s == 99.0


# --- SilenceWatchdog -------------------------------------------------------
#
# The watch fires on the ABSENCE of events, so time is virtualised via an
# injected `clock`: we advance a fake monotonic clock and call `_check`
# directly (the same coroutine the poll loop calls), never sleeping for the
# real thresholds.


def _watchdog_stream():
    stream = MemoryStream(id="turn")
    alerts: list = []

    async def sink(event):
        if isinstance(event, ObserverAlert):
            alerts.append(event)

    stream.subscribe(sink)
    return stream, alerts


async def test_silence_watchdog_alerts_critical_once_on_silence():
    stream, alerts = _watchdog_stream()
    now = {"t": 0.0}
    wd = SilenceWatchdog(alert_s=1.0, halt_s=None, clock=lambda: now["t"], poll_interval_s=0.01)
    ctx = ConversationContext(stream=stream)
    with ExitStack() as stack:
        wd.register(stack, ctx)
        await asyncio.sleep(0)  # let the monitor task start
        now["t"] = 0.5
        await wd._check(stream)  # below threshold → quiet
        assert alerts == []
        now["t"] = 1.5
        await wd._check(stream)  # crosses threshold → CRITICAL
        await wd._check(stream)  # still silent → NOT a second alert
    assert len(alerts) == 1
    assert alerts[0].severity == Severity.CRITICAL
    assert alerts[0].source == "silence-watchdog"
    assert "wedged" in alerts[0].message


async def test_silence_watchdog_resets_on_new_event():
    stream, alerts = _watchdog_stream()
    now = {"t": 0.0}
    wd = SilenceWatchdog(alert_s=1.0, halt_s=None, clock=lambda: now["t"], poll_interval_s=0.01)
    ctx = ConversationContext(stream=stream)
    with ExitStack() as stack:
        wd.register(stack, ctx)
        await asyncio.sleep(0)
        now["t"] = 0.9
        # a real event arrives just before the deadline → resets the clock
        await wd._on_event(ModelResponse(message=None), ctx)
        now["t"] = 1.5
        await wd._check(stream)  # silent = 1.5 - 0.9 = 0.6 < 1.0 → quiet
        assert alerts == []
        now["t"] = 2.0
        await wd._check(stream)  # silent = 1.1 ≥ 1.0 → CRITICAL now
    assert len(alerts) == 1 and alerts[0].severity == Severity.CRITICAL


async def test_silence_watchdog_escalates_to_fatal():
    stream, alerts = _watchdog_stream()
    now = {"t": 0.0}
    wd = SilenceWatchdog(alert_s=1.0, halt_s=3.0, clock=lambda: now["t"], poll_interval_s=0.01)
    ctx = ConversationContext(stream=stream)
    with ExitStack() as stack:
        wd.register(stack, ctx)
        await asyncio.sleep(0)
        now["t"] = 1.5
        await wd._check(stream)  # CRITICAL
        now["t"] = 5.0
        await wd._check(stream)  # past halt threshold → FATAL
    sev = [a.severity for a in alerts]
    assert sev == [Severity.CRITICAL, Severity.FATAL]


async def test_silence_watchdog_own_alert_does_not_reset_clock():
    """A fired alert is itself a stream event; it must NOT reset the silence
    clock, or the watchdog could never escalate to FATAL."""
    stream, alerts = _watchdog_stream()
    now = {"t": 0.0}
    wd = SilenceWatchdog(alert_s=1.0, halt_s=3.0, clock=lambda: now["t"], poll_interval_s=0.01)
    ctx = ConversationContext(stream=stream)
    with ExitStack() as stack:
        wd.register(stack, ctx)
        await asyncio.sleep(0)
        now["t"] = 1.5
        await wd._check(stream)  # CRITICAL — and the alert rides the stream
        await asyncio.sleep(0.02)  # let the alert be delivered to _on_event
        # _last must still be 0.0 (the alert didn't reset it), so at t=4 it halts
        now["t"] = 4.0
        await wd._check(stream)
    assert [a.severity for a in alerts] == [Severity.CRITICAL, Severity.FATAL]


async def test_silence_watchdog_disarms_between_turns():
    """After the turn's ExitStack unwinds, the monitor is torn down — an idle
    session is legitimately silent and must not trip the watchdog."""
    stream, alerts = _watchdog_stream()
    now = {"t": 0.0}
    wd = SilenceWatchdog(alert_s=1.0, halt_s=3.0, clock=lambda: now["t"], poll_interval_s=0.01)
    ctx = ConversationContext(stream=stream)
    with ExitStack() as stack:
        wd.register(stack, ctx)
        await asyncio.sleep(0)
    # turn ended → watch disarmed
    assert wd._task is None and wd._sub_id is None
    now["t"] = 100.0
    await asyncio.sleep(0.05)  # long idle gap; no monitor running → no alerts
    assert alerts == []
