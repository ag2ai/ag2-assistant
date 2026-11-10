"""Stub old tool results in the model's view of a chat once its prompt cache has gone cold."""

import copy
from collections.abc import Callable, Collection, Sequence

from ag2.annotations import Context
from ag2.events import (
    BaseEvent,
    DataInput,
    ModelRequest,
    ModelResponse,
    TextInput,
    ToolResult,
    ToolResultEvent,
    ToolResultsEvent,
)
from ag2.middleware.base import BaseMiddleware, LLMCall
from fast_depends.pydantic import PydanticSerializer

_SERIALIZER = PydanticSerializer(
    pydantic_config={"arbitrary_types_allowed": True}, use_fastdepends_errors=False
)
STUB_TEMPLATE = (
    "[tool result omitted to save tokens: {name}, {chars} chars. "
    "Call the tool again if you need it]"
)


def _text_size(result: ToolResultEvent) -> int | None:
    """Characters in a text or data result; None for errors, built-ins and media parts."""
    if type(result) is not ToolResultEvent:
        return None
    size = 0
    for part in result.result.parts:
        if type(part) is TextInput:
            size += len(part.content)
        elif type(part) is DataInput:
            size += len(_SERIALIZER.encode(part.data))
        else:
            return None
    return size or None


def _stub(result: ToolResultEvent, chars: int) -> ToolResultEvent:
    stub = copy.copy(result)
    stub.result = ToolResult(
        STUB_TEMPLATE.format(name=result.name or "tool", chars=chars),
        metadata=result.result.metadata,
    )
    return stub


def stub_old_tool_results(
    events: Sequence[BaseEvent],
    *,
    idle_s: float,
    keep_chars: int,
    min_chars: int = 300,
    exclude: Collection[str] = (),
) -> list[BaseEvent]:
    """The events with tool results older than the newest cold-cache boundary stubbed.

    A boundary is a request sent more than `idle_s` after the previous model response.
    The newest `keep_chars` of results before it stay whole and older ones are stubbed;
    earlier boundaries stub a subset of that, so one pass gives the output a stubbed
    result keeps on every later call.
    """
    events = list(events)
    boundary: int | None = None
    last_response: float | None = None
    for index, event in enumerate(events):
        if isinstance(event, ModelResponse):
            last_response = event.created_at
        elif (
            isinstance(event, ModelRequest)
            and last_response is not None
            and event.created_at - last_response > idle_s
        ):
            boundary = index
    if boundary is None:
        return events
    stubbed: dict[tuple[int, int], int] = {}  # (event index, result index) -> chars
    kept = 0
    for position in range(boundary - 1, -1, -1):
        prior = events[position]
        if not isinstance(prior, ToolResultsEvent):
            continue
        for slot in range(len(prior.results) - 1, -1, -1):
            result = prior.results[slot]
            size = _text_size(result)
            if size is None or size < min_chars or result.name in exclude:
                continue
            kept += size
            if kept > keep_chars:
                stubbed[(position, slot)] = size
    if not stubbed:
        return events
    out: list[BaseEvent] = []
    for position, event in enumerate(events):
        if isinstance(event, ToolResultsEvent) and any(
            (position, slot) in stubbed for slot in range(len(event.results))
        ):
            replaced = copy.copy(event)
            replaced.results = [
                _stub(result, stubbed[(position, slot)]) if (position, slot) in stubbed else result
                for slot, result in enumerate(event.results)
            ]
            event = replaced
        out.append(event)
    return out


class StubOldToolResultsMiddleware(BaseMiddleware):
    """Send the model a stub for each old tool result once a chat's cache has gone cold."""

    def __init__(
        self,
        event: BaseEvent,
        context: Context,
        *,
        trim: Callable[[Sequence[BaseEvent]], list[BaseEvent]],
    ) -> None:
        super().__init__(event, context)
        self._trim = trim

    async def on_llm_call(self, call_next: LLMCall, events: Sequence[BaseEvent], context: Context):
        return await call_next(self._trim(events), context)
