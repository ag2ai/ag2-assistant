"""Stub old tool results in the model's view of a chat once its prompt cache has gone cold."""

import copy
from collections.abc import Collection, Sequence

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
    """The events with tool results older than a cold-cache boundary replaced by stubs.

    A boundary is a request sent more than `idle_s` after the previous model response.
    At each one the newest `keep_chars` of results stay whole and older ones are stubbed;
    a stubbed result stays stubbed, so the output depends only on the events.
    """
    events = list(events)
    stubbed: set[tuple[int, int]] = set()  # (event index, result index)
    last_response: float | None = None
    for index, event in enumerate(events):
        if isinstance(event, ModelResponse):
            last_response = event.created_at
        elif (
            isinstance(event, ModelRequest)
            and last_response is not None
            and event.created_at - last_response > idle_s
        ):
            kept = 0
            for position in range(index - 1, -1, -1):
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
                        stubbed.add((position, slot))
    if not stubbed:
        return events
    out: list[BaseEvent] = []
    for position, event in enumerate(events):
        if isinstance(event, ToolResultsEvent) and any(
            (position, slot) in stubbed for slot in range(len(event.results))
        ):
            replaced = copy.copy(event)
            replaced.results = [
                _stub(result, _text_size(result) or 0) if (position, slot) in stubbed else result
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
        idle_s: float,
        keep_chars: int = 30_000,
        min_chars: int = 300,
        exclude: Collection[str] = (),
    ) -> None:
        super().__init__(event, context)
        self._idle_s, self._keep_chars, self._min_chars = idle_s, keep_chars, min_chars
        self._exclude = frozenset(exclude)

    async def on_llm_call(self, call_next: LLMCall, events: Sequence[BaseEvent], context: Context):
        return await call_next(
            stub_old_tool_results(
                events,
                idle_s=self._idle_s,
                keep_chars=self._keep_chars,
                min_chars=self._min_chars,
                exclude=self._exclude,
            ),
            context,
        )
