"""The AG-UI projection gives the same thread live as it does when replayed from the log."""

from ag2.events import (
    BaseEvent,
    CompactionStarted,
    ModelMessage,
    ModelMessageChunk,
    ModelRequest,
    ModelResponse,
    TaskCompleted,
    TaskStarted,
    TextInput,
    ToolCallEvent,
    ToolCallsEvent,
    ToolResult,
    ToolResultEvent,
    ToolResultsEvent,
    UsageEvent,
)
from ag_ui.core import Event, EventType

from assistant.events import A2UISurface, TaskCreated
from assistant.gateway.agui import A2UI_ACTIVITY_TYPE, Projector, project


def _user(text: str) -> ModelRequest:
    return ModelRequest([TextInput(text)])


def _call(call_id: str, name: str = "search") -> ToolCallEvent:
    return ToolCallEvent(name, id=call_id, arguments='{"q": "x"}')


def _result(call_id: str, text: str) -> ToolResultsEvent:
    return ToolResultsEvent(
        [ToolResultEvent(parent_id=call_id, name="search", result=ToolResult([TextInput(text)]))]
    )


def _live_turns() -> list[BaseEvent]:
    """Two requests and a tool round, with the streamed chunks a live client sees."""
    calls = ToolCallsEvent([_call("c1")])
    return [
        _user("find it"),
        ModelMessageChunk("Let me "),
        ModelMessageChunk("look."),
        ModelResponse(ModelMessage("Let me look."), tool_calls=calls),
        calls,
        _result("c1", "found"),
        ModelMessageChunk("It is "),
        ModelMessageChunk("here."),
        ModelResponse(ModelMessage("It is here.")),
        _user("thanks"),
        ModelResponse(ModelMessage("You are welcome.")),
        UsageEvent(),
    ]


def _persisted(events: list[BaseEvent]) -> list[BaseEvent]:
    """What the Event log keeps: transient events (stream chunks) are not written."""
    return [event for event in events if not getattr(type(event), "__transient__", False)]


def _fold(events: list[Event]) -> dict:
    """The thread an AG-UI client builds: messages, tool calls, and everything else."""
    messages: dict[str, dict] = {}
    tools: dict[str, dict] = {}
    rest: list[str] = []
    for event in events:
        kind = event.type
        if kind == EventType.TEXT_MESSAGE_START:
            assert event.message_id not in messages, "a message was opened twice"
            messages[event.message_id] = {"role": event.role, "text": "", "open": True}
        elif kind == EventType.TEXT_MESSAGE_CONTENT:
            assert messages[event.message_id]["open"], "content outside an open message"
            messages[event.message_id]["text"] += event.delta
        elif kind == EventType.TEXT_MESSAGE_END:
            assert messages[event.message_id]["open"], "a message was closed twice"
            messages[event.message_id]["open"] = False
        elif kind == EventType.TOOL_CALL_START:
            tools[event.tool_call_id] = {"name": event.tool_call_name, "args": "", "result": None}
        elif kind == EventType.TOOL_CALL_ARGS:
            tools[event.tool_call_id]["args"] += event.delta
        elif kind == EventType.TOOL_CALL_RESULT:
            tools[event.tool_call_id]["result"] = event.content
        elif kind != EventType.TOOL_CALL_END:
            rest.append(str(kind))
    assert all(not m["open"] for m in messages.values()), "a message was left open"
    return {"messages": messages, "tools": tools, "rest": rest}


def test_replay_gives_the_same_thread_as_live():
    live = _live_turns()
    replayed = _fold(project(_persisted(live)))
    streamed = Projector()
    folded = _fold([out for event in live for out in streamed.feed(event)])

    assert folded == replayed
    assert [m["text"] for m in replayed["messages"].values()] == [
        "find it",
        "Let me look.",
        "It is here.",
        "thanks",
        "You are welcome.",
    ]
    assert replayed["tools"] == {"c1": {"name": "search", "args": '{"q": "x"}', "result": "found"}}


def test_a_streamed_message_is_one_message_not_two():
    streamed = Projector()
    events = [out for event in _live_turns()[:4] for out in streamed.feed(event)]

    starts = [e for e in events if e.type == EventType.TEXT_MESSAGE_START and e.role == "assistant"]
    assert len(starts) == 1


def test_ids_are_the_same_every_time_and_differ_between_messages():
    log = _persisted(_live_turns())
    ids = list(_fold(project(log))["messages"])
    assert ids == list(_fold(project(log))["messages"])
    assert len(set(ids)) == len(ids)


def test_a_page_starting_at_a_turn_keeps_that_turns_ids():
    log = _persisted(_live_turns())
    whole = _fold(project(log))["messages"]
    page = _fold(project(log[log.index(next(e for e in log if _is_request(e, "thanks"))) :]))[
        "messages"
    ]

    assert all(whole[message_id] == message for message_id, message in page.items())
    assert len(page) == 2


def _is_request(event: BaseEvent, text: str) -> bool:
    return isinstance(event, ModelRequest) and any(
        isinstance(p, TextInput) and p.content == text for p in event.parts
    )


def test_a_button_instruction_is_not_shown_as_a_user_message():
    assert project([_user("[[A2UI_ACTION]] do it")]) == []


def test_a_surface_becomes_an_a2ui_activity():
    surface = A2UISurface("s1", component={"type": "Text"}, data={"a": 1})

    (activity,) = project([surface])

    assert activity.type == EventType.ACTIVITY_SNAPSHOT
    assert activity.activity_type == A2UI_ACTIVITY_TYPE
    assert activity.content["component"] == {"type": "Text"}


def test_assistant_events_ride_as_custom_and_telemetry_is_dropped():
    created = TaskCreated("t1", title="Report")

    events = project(
        [created, UsageEvent(), CompactionStarted(agent="a", strategy="s", event_count=3)]
    )

    assert [e.type for e in events] == [EventType.CUSTOM, EventType.CUSTOM]
    assert events[0].value["task_id"] == "t1"


def test_a_delegation_is_a_subagent_run():
    started = TaskStarted(task_id="d1", agent_name="researcher", objective="look")
    done = TaskCompleted(
        task_id="d1", agent_name="researcher", objective="look", result="ok", task_stream="s"
    )

    started_out, done_out = project([started, done])

    assert started_out.type == EventType.SUBAGENT_STARTED
    assert done_out.type == EventType.SUBAGENT_FINISHED
    assert done_out.subagent_run_id == "d1"
