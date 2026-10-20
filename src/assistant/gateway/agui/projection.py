"""Project live and stored AG2 events as AG-UI events with stable message ids."""

import json
from collections.abc import Iterable
from typing import Any

from ag2.events import (
    BaseEvent,
    CompactionCompleted,
    CompactionFailed,
    CompactionStarted,
    DataInput,
    ModelMessageChunk,
    ModelRequest,
    ModelResponse,
    ObserverAlert,
    TaskCancelled,
    TaskCompleted,
    TaskExpired,
    TaskFailed,
    TaskStarted,
    TextInput,
    ToolCallsEvent,
    ToolResultEvent,
    ToolResultsEvent,
)
from ag2.events._serialization import qualified_name
from ag_ui.core import (
    ActivitySnapshotEvent,
    CustomEvent,
    Event,
    SubagentErrorEvent,
    SubagentFinishedEvent,
    SubagentStartedEvent,
    TextMessageContentEvent,
    TextMessageEndEvent,
    TextMessageStartEvent,
    ToolCallArgsEvent,
    ToolCallEndEvent,
    ToolCallResultEvent,
    ToolCallStartEvent,
)

from assistant.attachments import INLINED_FILE_PREFIX
from assistant.events import A2UISurface, AssistantEvent
from assistant.gateway.history import event_cursor

A2UI_ACTIVITY_TYPE = "a2ui-surface"

# A button's private instruction to the agent; its own status event is what the user sees.
_PRIVATE_PREFIX = "[[A2UI_ACTION]]"

# AG2 events the thread renders that are not assistant events; sent as CUSTOM.
_RENDERED = (CompactionStarted, CompactionCompleted, CompactionFailed, ObserverAlert)

# Events after which the next model message is named: it follows exactly one of them.
_ANCHORS = (ModelRequest, ToolResultsEvent, ModelResponse)

_TASK_ENDED = (TaskFailed, TaskCancelled, TaskExpired)
_ENDED_STATUS = {TaskFailed: "failed", TaskCancelled: "cancelled", TaskExpired: "failed"}


def _ms(event: BaseEvent) -> int:
    return int(event.created_at * 1000)


def _text_of(value: Any) -> str:
    """The text a tool result carries, whatever nesting its parts use."""
    if isinstance(value, str):
        return value
    if isinstance(value, TextInput):
        return value.content
    if isinstance(value, DataInput):
        return _text_of(value.data)
    if isinstance(value, (list, tuple)):
        return "\n".join(part for item in value if (part := _text_of(item)))
    return json.dumps(value, default=str)


class Projector:
    """Feed events in log order; get the AG-UI events each one becomes."""

    def __init__(self) -> None:
        self._anchor = ""
        self._streaming: str | None = None
        self._content: list[str] = []
        self._calls: set[str] = set()
        self._results: set[str] = set()

    def feed(self, event: BaseEvent) -> list[Event]:
        out = self._project(event)
        if isinstance(event, _ANCHORS):
            self._anchor = event_cursor(event)
        return out

    def _project(self, event: BaseEvent) -> list[Event]:
        if isinstance(event, ModelRequest):
            return self._user_message(event)
        if isinstance(event, ModelMessageChunk):
            return self._chunk(event)
        if isinstance(event, ModelResponse):
            return self._response(event)
        if isinstance(event, ToolCallsEvent):
            return self._tool_calls(event)
        if isinstance(event, ToolResultsEvent):
            return [out for result in event.results for out in self._tool_result(result)]
        if isinstance(event, ToolResultEvent):
            return self._tool_result(event)
        if isinstance(event, TaskStarted):
            return [
                SubagentStartedEvent(
                    subagent_run_id=event.task_id,
                    name=event.agent_name,
                    description=event.objective,
                    parent_tool_call_id=event.parent_tool_call_id,
                    timestamp=_ms(event),
                )
            ]
        if isinstance(event, TaskCompleted):
            return [
                SubagentFinishedEvent(
                    subagent_run_id=event.task_id,
                    result=json.loads(json.dumps(event.result, default=str)),
                    timestamp=_ms(event),
                )
            ]
        if isinstance(event, _TASK_ENDED):
            reason = getattr(event, "reason", "") or type(event).__name__
            return [
                SubagentErrorEvent(
                    subagent_run_id=event.task_id,
                    message=str(getattr(event, "error", "") or reason),
                    timestamp=_ms(event),
                    raw_event={"status": _ENDED_STATUS[type(event)]},
                )
            ]
        if isinstance(event, A2UISurface):
            return [
                ActivitySnapshotEvent(
                    message_id=f"surface-{event.surface_id}",
                    activity_type=A2UI_ACTIVITY_TYPE,
                    content=event.to_dict(),
                    replace=True,
                    timestamp=_ms(event),
                )
            ]
        if isinstance(event, (AssistantEvent, *_RENDERED)):
            return [
                CustomEvent(name=qualified_name(event), value=event.to_dict(), timestamp=_ms(event))
            ]
        return []

    def close(self) -> list[Event]:
        """End the model message still streaming, if any (the run is leaving it open)."""
        if self._streaming is None:
            return []
        message_id, self._streaming = self._streaming, None
        self._content.clear()
        return [TextMessageEndEvent(message_id=message_id)]

    def _model_message_id(self) -> str:
        return f"msg-{self._anchor[:16]}"

    def _user_message(self, event: ModelRequest) -> list[Event]:
        text = "\n".join(
            p.content
            for p in event.parts
            if isinstance(p, TextInput) and not p.content.startswith(INLINED_FILE_PREFIX)
        ).strip()
        if not text or text.startswith(_PRIVATE_PREFIX):
            return []
        message_id = f"usr-{event_cursor(event)[:16]}"
        return [
            TextMessageStartEvent(message_id=message_id, role="user", timestamp=_ms(event)),
            TextMessageContentEvent(message_id=message_id, delta=text, timestamp=_ms(event)),
            TextMessageEndEvent(message_id=message_id, timestamp=_ms(event)),
        ]

    def _chunk(self, event: ModelMessageChunk) -> list[Event]:
        if not event.content:
            return []
        out: list[Event] = []
        if self._streaming is None:
            self._streaming = self._model_message_id()
            self._content = []
            out.append(
                TextMessageStartEvent(
                    message_id=self._streaming, role="assistant", timestamp=_ms(event)
                )
            )
        self._content.append(event.content)
        out.append(
            TextMessageContentEvent(
                message_id=self._streaming, delta=event.content, timestamp=_ms(event)
            )
        )
        return out

    def _response(self, event: ModelResponse) -> list[Event]:
        message_id = self._streaming or self._model_message_id()
        content = event.content
        out: list[Event] = []
        stamp = {"created_at": event.created_at}
        if self._streaming is not None and (not content or content == "".join(self._content)):
            out.append(
                TextMessageEndEvent(message_id=message_id, timestamp=_ms(event), raw_event=stamp)
            )
        elif content:
            out += self.close()
            out += [
                TextMessageStartEvent(
                    message_id=message_id, role="assistant", timestamp=_ms(event)
                ),
                TextMessageContentEvent(message_id=message_id, delta=content, timestamp=_ms(event)),
                TextMessageEndEvent(message_id=message_id, timestamp=_ms(event), raw_event=stamp),
            ]
        self._streaming = None
        self._content.clear()
        return out

    def _tool_calls(self, event: ToolCallsEvent) -> list[Event]:
        out: list[Event] = []
        for call in event.calls:
            if call.id in self._calls:
                continue
            self._calls.add(call.id)
            out += [
                ToolCallStartEvent(
                    tool_call_id=call.id, tool_call_name=call.name, timestamp=_ms(call)
                ),
                ToolCallArgsEvent(tool_call_id=call.id, delta=call.arguments, timestamp=_ms(call)),
                ToolCallEndEvent(tool_call_id=call.id, timestamp=_ms(call)),
            ]
        return out

    def _tool_result(self, event: ToolResultEvent) -> list[Event]:
        if event.parent_id in self._results:
            return []
        self._results.add(event.parent_id)
        content = "\n".join(text for part in event.result.parts if (text := _text_of(part)))
        return [
            ToolCallResultEvent(
                message_id=f"res-{event.parent_id}",
                tool_call_id=event.parent_id,
                content=content,
                role="tool",
                timestamp=_ms(event),
            )
        ]


def project(events: Iterable[BaseEvent]) -> list[Event]:
    """A stored Event log (or a page of it, starting at a Turn) as AG-UI events."""
    projector = Projector()
    return [out for event in events for out in projector.feed(event)]
