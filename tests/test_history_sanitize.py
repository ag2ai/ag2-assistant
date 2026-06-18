"""Interim guard: a loaded history that compaction left mid tool-cycle (orphaned
function-response, or a window starting before any user turn) is repaired before
resume, so Gemini doesn't 400 on function call/response adjacency.
"""

from autogen.beta.compact import CompactionSummary
from autogen.beta.events import (
    ModelRequest,
    ModelResponse,
    ToolCallEvent,
    ToolCallsEvent,
    ToolResultEvent,
)

from assistant.gateway.core import sanitize_history


def _user(text="hi"):
    return ModelRequest(parts=[])


def _model_call(call_id):
    return ModelResponse(tool_calls=ToolCallsEvent(calls=[ToolCallEvent(id=call_id, name="search")]))


def test_drops_orphan_result_mid_conversation():
    u = _user()
    mr = _model_call("c1")
    good = ToolResultEvent(parent_id="c1", name="search")     # paired
    orphan = ToolResultEvent(parent_id="gone", name="search")  # call compacted away
    out = sanitize_history([u, mr, good, orphan])
    assert orphan not in out
    assert out == [u, mr, good]


def test_strips_leading_halfcycle_keeps_summary_and_from_first_user():
    # mirrors the real bug: summary + an orphaned tool half-cycle, THEN the
    # conversation's first user turn. Everything before the user turn (except the
    # summary) is dropped; the rest is kept.
    summary = CompactionSummary(summary="earlier context", event_count=12)
    lead_call = ToolCallsEvent(calls=[ToolCallEvent(id="x", name="search")])
    lead_result = ToolResultEvent(parent_id="x", name="search")
    u = _user()
    mr = _model_call("c2")
    paired = ToolResultEvent(parent_id="c2", name="search")
    out = sanitize_history([summary, lead_call, lead_result, u, mr, paired])
    assert out == [summary, u, mr, paired]


def test_no_user_turn_keeps_only_summary():
    # a degenerate window (single giant turn whose user start was compacted) →
    # keep just the summary; resume will append the new user message.
    summary = CompactionSummary(summary="ctx", event_count=30)
    out = sanitize_history([
        summary,
        ToolCallsEvent(calls=[ToolCallEvent(id="x", name="s")]),
        ToolResultEvent(parent_id="x", name="s"),
        ModelResponse(message=None),
    ])
    assert out == [summary]


def test_clean_history_passthrough():
    assert sanitize_history([]) == []
    clean = [_user(), ModelResponse(message=None)]
    assert sanitize_history(clean) == clean
