"""Deliver ACP outcomes to declared receivers through a durable, ordered outbox."""

import asyncio
import contextlib
import json
import logging
import os
import re
import tempfile
import time
from pathlib import Path
from typing import Any

log = logging.getLogger("assistant.acp.outbound")

# The extension method the notification travels as; the SDK adds the underscore.
NOTIFY_METHOD = "ag2/notify"
# The ``_meta`` key a client sets on ``session/new`` to say where that session's
# unsolicited messages belong: a string, or a mapping with ``chat_id``.
DESTINATION_META_KEY = "ag2/destination"
# A client declares notification delivery on ``initialize`` with this ``_meta`` key.
RECEIVE_META_KEY = "ag2/receive"

_SAFE = re.compile(r"[^A-Za-z0-9._-]+")


def destination_of(meta: Any) -> str | None:
    """The destination a session's ``_meta`` declares, or None."""
    if not isinstance(meta, dict):
        return None
    value = meta.get(DESTINATION_META_KEY)
    if isinstance(value, dict):
        value = value.get("chat_id")
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def wants_delivery(meta: Any) -> bool:
    """Whether ``initialize``'s ``_meta`` declares the client a receiver."""
    return isinstance(meta, dict) and meta.get(RECEIVE_META_KEY) is True


class Outbound:
    """One listener's outbound side: the clients it can reach and what waits for one."""

    def __init__(self, connection_id: str, outbox_dir: Path) -> None:
        self.connection_id = connection_id
        self._path = outbox_dir / f"{_SAFE.sub('_', connection_id) or 'listener'}.json"
        self._clients: list[Any] = []
        self._lock = asyncio.Lock()
        self._flush_task: asyncio.Task[int] | None = None

    # ---- clients -------------------------------------------------------------

    def attach(self, client: Any) -> None:
        """Register a declared receiver and schedule delivery of queued outcomes."""
        if client in self._clients:
            return
        self._clients.append(client)
        if self.pending() and (self._flush_task is None or self._flush_task.done()):
            self._flush_task = asyncio.create_task(self.flush())
            self._flush_task.add_done_callback(self._flushed)

    def detach(self, client: Any) -> None:
        if client in self._clients:
            self._clients.remove(client)

    @property
    def connected(self) -> int:
        return len(self._clients)

    def _flushed(self, task: asyncio.Task[int]) -> None:
        if not task.cancelled() and (error := task.exception()) is not None:
            log.error("acp outbound %s: flush failed: %s", self.connection_id, error)

    async def aclose(self) -> None:
        """Detach receivers and stop background delivery, retaining unsent outcomes."""
        self._clients.clear()
        if self._flush_task is not None:
            self._flush_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._flush_task
            self._flush_task = None

    # ---- delivery ------------------------------------------------------------

    async def notify(self, destination: str, text: str) -> bool:
        """Persist an outcome and drain deliveries; return whether the outbox is empty."""
        params = {"destination": destination, "text": text, "connection": self.connection_id}
        self._queue(params)
        await self.flush()
        return not self.pending()

    async def flush(self) -> int:
        """Deliver queued outcomes in order to the connected receivers."""
        async with self._lock:
            return await self._drain()

    async def _drain(self) -> int:
        sent = 0
        while entries := self.pending():
            if not await self._send(_wire(entries[0])):
                break
            # This drain removes entries; concurrent notifiers only append.
            self._write(self.pending()[1:])
            sent += 1
        return sent

    async def _send(self, params: dict) -> bool:
        while self._clients:
            client = self._clients[-1]
            try:
                await client.ext_notification(NOTIFY_METHOD, params)
                return True
            except Exception as exc:
                log.info("acp outbound %s: client dropped: %s", self.connection_id, exc)
                self.detach(client)
        return False

    # ---- the outbox ----------------------------------------------------------

    def pending(self) -> list[dict]:
        try:
            data = json.loads(self._path.read_text())
        except (OSError, ValueError):
            return []
        return (
            [entry for entry in data if isinstance(entry, dict)] if isinstance(data, list) else []
        )

    def _queue(self, params: dict) -> None:
        self._write([*self.pending(), {**params, "queued_at": time.time()}])

    def _write(self, entries: list[dict]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=self._path.parent, prefix=".outbox-", suffix=".json")
        try:
            with os.fdopen(fd, "w") as handle:
                handle.write(json.dumps(entries, indent=2))
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp, self._path)
        except BaseException:
            os.unlink(tmp)
            raise


def _wire(params: dict) -> dict:
    """What travels: the outbox's own bookkeeping stays on disk."""
    return {k: v for k, v in params.items() if k != "queued_at"}
