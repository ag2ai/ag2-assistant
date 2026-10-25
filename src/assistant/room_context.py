"""Transient room reference data for ACP turns."""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from ag2.acp.executor import META_VARIABLE
from ag2.events import BaseEvent, ModelRequest, TextInput
from ag2.middleware import BaseMiddleware

from assistant.acp.room_context_validation import (
    ROOM_CONTEXT_GUIDANCE,
    render_room_context,
    validate_room_context,
)

CONTENT_META_KEY = "ag2/room_context"
TASK_META_KEY = "ag2/task"
PREPARED_META_KEY = "assistant/transient-room-context"


@dataclass(frozen=True)
class PreparedRoomContext:
    task: dict | None = None
    agent_id: str = ""


class RoomContextSession(BaseEvent):
    """Content-free marker excluding this conversation from passive profile learning."""

    __conversational__ = False


class TransientRoomContextInput(ModelRequest):
    """Model-only reference data excluded from durable conversation history."""

    __transient__ = True


def _meta(block: Any) -> dict:
    meta = getattr(block, "field_meta", None)
    return meta if isinstance(meta, dict) else {}


def prepare_room_context(prompt: list) -> tuple[list, PreparedRoomContext | None]:
    """Remove marked data blocks and bind them to the separately supplied task facts."""
    marked = [block for block in prompt if CONTENT_META_KEY in _meta(block)]
    if not marked:
        return prompt, None
    cleaned = [block for block in prompt if CONTENT_META_KEY not in _meta(block)]
    prepared = PreparedRoomContext()
    if len(marked) != 1 or not prompt or prompt[0] in marked:
        return cleaned, prepared
    outer = _meta(prompt[0]).get(TASK_META_KEY)
    value = _meta(marked[0]).get(CONTENT_META_KEY)
    if (
        not isinstance(outer, dict)
        or not isinstance(value, dict)
        or set(value) != {"binding", "context"}
    ):
        return cleaned, prepared
    if (
        not {"task_id", "room_id", "access_tier", "source_message_id"}
        <= outer.keys()
        <= {"task_id", "room_id", "access_tier", "source_message_id", "agent_id"}
    ):
        return cleaned, prepared
    task = {
        "id": outer["task_id"],
        "channel_id": outer["room_id"],
        "access_tier": outer["access_tier"],
        "source_message_id": outer["source_message_id"],
        "room_context_binding": value["binding"],
        "room_context": value["context"],
    }
    agent_id = outer.get("agent_id") or ""
    if not validate_room_context(task, agent_id=agent_id):
        return cleaned, prepared
    return cleaned, PreparedRoomContext(task=task, agent_id=agent_id)


def refresh_room_context(events: Sequence[BaseEvent], context) -> list[BaseEvent]:
    """Replace transient references with a freshly validated view for one provider attempt."""
    outgoing = [event for event in events if not isinstance(event, TransientRoomContextInput)]
    meta = context.variables.get(META_VARIABLE, {}) if context is not None else {}
    prepared = meta.get(PREPARED_META_KEY) if isinstance(meta, dict) else None
    if not isinstance(prepared, PreparedRoomContext):
        return outgoing
    packet = (
        validate_room_context(prepared.task, agent_id=prepared.agent_id) if prepared.task else None
    )
    text = (
        render_room_context(packet)
        if packet
        else (
            "AG2 Space room context unavailable: optional packet did not validate for this turn. "
            + ROOM_CONTEXT_GUIDANCE
        )
    )
    return [TransientRoomContextInput([TextInput(text)]), *outgoing]


class RoomContextMiddleware(BaseMiddleware):
    async def on_turn(self, call_next, event, context):
        meta = context.variables.get(META_VARIABLE, {})
        prepared = meta.get(PREPARED_META_KEY) if isinstance(meta, dict) else None
        if isinstance(prepared, PreparedRoomContext):
            await context.send(RoomContextSession())
        return await call_next(event, context)

    async def on_llm_call(self, call_next, events, context):
        return await call_next(refresh_room_context(events, context), context)
