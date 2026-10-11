"""Load compacted model context and the complete display event history."""

import hashlib
import json
from collections.abc import Iterable
from dataclasses import dataclass

from ag2.context import StreamId
from ag2.events import BaseEvent
from ag2.events._serialization import qualified_name
from ag2.knowledge import KnowledgeStore
from ag2.knowledge.constants import LOG_PREFIX
from ag2.knowledge.log import EventLogWriter


class ChatEventLog(EventLogWriter):
    """Read current context separately from archived events used for display."""

    async def load_current(self, stream_id: StreamId) -> list[BaseEvent]:
        return await self._load_file(f"{LOG_PREFIX}{stream_id}.jsonl")

    async def replay(
        self,
        stream_id: StreamId,
        current: Iterable[BaseEvent],
        archive: KnowledgeStore | None = None,
    ) -> list[BaseEvent]:
        events = await self.load(stream_id)
        if archive is not None:
            events.extend(await EventLogWriter(archive).load(stream_id))
        events.extend(current)
        unique = {
            (qualified_name(event), json.dumps(event.to_dict(), sort_keys=True, default=str)): event
            for event in events
        }
        return sorted(unique.values(), key=lambda event: event.created_at)

    async def delete(self, stream_id: StreamId, archive: KnowledgeStore | None = None) -> bool:
        """Remove this stream's current log and archived segments from both stores."""
        removed = False
        for store in (self._store, archive):
            if store is None:
                continue
            paths = [f"{LOG_PREFIX}{stream_id}.jsonl"]
            paths.extend(
                f"{LOG_PREFIX}{entry}"
                for entry in await store.list(LOG_PREFIX)
                if entry.startswith(f"{stream_id}.dropped-") and entry.endswith(".jsonl")
            )
            for path in paths:
                if await store.exists(path):
                    await store.delete(path)
                    removed = True
        return removed


def event_cursor(event: BaseEvent) -> str:
    """A stable event identity across live, persisted and compacted history."""
    record = (qualified_name(event), event.to_dict())
    return hashlib.sha256(json.dumps(record, sort_keys=True, default=str).encode()).hexdigest()


@dataclass
class EventPage:
    events: list[BaseEvent]
    before: str | None
    start: str | None


def event_page(
    events: list[BaseEvent], limit: int, *, before: str | None = None, start: str | None = None
) -> EventPage:
    """Select complete turns, with an exclusive cursor for older history."""
    keys = [event_cursor(event) for event in events]
    end = len(events)
    if before is not None:
        end = keys.index(before)
    if start is not None:
        begin = keys.index(start)
    else:
        boundaries = [
            index
            for index, event in enumerate(events[:end])
            if qualified_name(event).rsplit(".", 1)[-1] == "ModelRequest"
        ]
        begin = boundaries[-limit] if len(boundaries) > limit else 0
    selected = events[begin:end]
    first = keys[begin] if selected else None
    return EventPage(selected, first if begin else None, first)
