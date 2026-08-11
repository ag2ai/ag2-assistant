"""Shared persistence helpers for AG2 Assistant's JSON-in-SQLite stores.

Both the task store and the inquiry store keep one JSON doc per record in a
SQLite knowledge store. SQLite isn't safe for concurrent multi-coroutine access
and these stores are hit by many tasks at once, so every op funnels through one
in-process lock (`SerialStore`).
"""

import asyncio
import uuid
from datetime import datetime

from ag2.knowledge import ChangeCallback, ChangeSubscription, KnowledgeStore


class SerialStore:
    """Serialises all ops on an inner KnowledgeStore with one in-process lock.

    Covers the whole ``KnowledgeStore`` surface, so no op can reach the inner
    SQLite store outside the lock."""

    def __init__(self, inner: KnowledgeStore) -> None:
        self._inner = inner
        self._lock = asyncio.Lock()

    async def write(self, path: str, content: str) -> None:
        async with self._lock:
            return await self._inner.write(path, content)

    async def read(self, path: str) -> str | None:
        async with self._lock:
            return await self._inner.read(path)

    async def list(self, path: str = "/") -> list[str]:
        async with self._lock:
            return await self._inner.list(path)

    async def exists(self, path: str) -> bool:
        async with self._lock:
            return await self._inner.exists(path)

    async def delete(self, path: str) -> None:
        async with self._lock:
            return await self._inner.delete(path)

    async def append(self, path: str, content: str) -> int:
        async with self._lock:
            return await self._inner.append(path, content)

    async def read_range(self, path: str, start: int, end: int | None = None) -> str:
        async with self._lock:
            return await self._inner.read_range(path, start, end)

    async def on_change(self, path: str, callback: ChangeCallback) -> ChangeSubscription:
        async with self._lock:
            return await self._inner.on_change(path, callback)


def now_iso() -> str:
    """Local, timezone-aware ISO timestamp."""
    return datetime.now().astimezone().isoformat()


def new_id(prefix: str) -> str:
    """A short, unique id with the given prefix (e.g. 'task' -> 'task-ab12…')."""
    return f"{prefix}-" + uuid.uuid4().hex[:12]
