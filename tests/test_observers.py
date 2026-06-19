"""Stuck-turn behaviour observers."""

from autogen.beta.events import (
    ModelResponse,
    ObserverAlert,
    Severity,
    ToolCallEvent,
    ToolCallsEvent,
)

from assistant.observers import ToolChurnObserver, build_observers


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


def test_build_observers_includes_loop_and_churn():
    from autogen.beta.observers import LoopDetector

    obs = build_observers()
    kinds = {type(o).__name__ for o in obs}
    assert "LoopDetector" in kinds and "ToolChurnObserver" in kinds
    assert any(isinstance(o, LoopDetector) for o in obs)
