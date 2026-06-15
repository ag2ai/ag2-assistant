"""Shared persistence helpers for AGClaw's JSON-in-SQLite stores.

Both the task store and the inquiry store keep one JSON doc per record in a
SQLite knowledge store. SQLite isn't safe for concurrent multi-coroutine access
and these stores are hit by many tasks at once, so every op funnels through one
in-process lock (`SerialStore`).
"""

import asyncio
import uuid
from datetime import datetime


class SerialStore:
    """Serialises all ops on an inner KnowledgeStore with one in-process lock."""

    def __init__(self, inner) -> None:
        self._inner = inner
        self._lock = asyncio.Lock()

    async def write(self, path, data):
        async with self._lock:
            return await self._inner.write(path, data)

    async def read(self, path):
        async with self._lock:
            return await self._inner.read(path)

    async def list(self, prefix):
        async with self._lock:
            return await self._inner.list(prefix)

    async def exists(self, path):
        async with self._lock:
            return await self._inner.exists(path)

    async def delete(self, path):
        async with self._lock:
            return await self._inner.delete(path)


def now_iso() -> str:
    """Local, timezone-aware ISO timestamp."""
    return datetime.now().astimezone().isoformat()


def new_id(prefix: str) -> str:
    """A short, unique id with the given prefix (e.g. 'task' -> 'task-ab12…')."""
    return f"{prefix}-" + uuid.uuid4().hex[:12]
