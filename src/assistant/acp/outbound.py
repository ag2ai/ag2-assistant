"""Outbound delivery from an ACP listener to its clients.

ACP has no agent-initiated message outside a prompt, so a task-run outcome for a
session has no protocol route back on its own. The listener sends one as an
extension notification, ``_ag2/notify``, on a client connection it still holds;
with no client connected the message waits in a durable outbox and goes out when
the next *receiver* binds.

Not every connection is a receiver. A client that only probes the listener (a
worker's startup check: ``initialize`` and nothing else) has nowhere to put a
notification and would drop it on the floor, so handing it the outbox would lose
every outcome queued while that worker was down. A client that can route
``_ag2/notify`` onward says so on ``initialize`` with ``_meta["ag2/receive"] =
true``; only such a client is attached here, flushed to, and sent live messages.
"""

import asyncio
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
# The ``_meta`` key a client sets to ``true`` on ``initialize`` to say it takes
# ``_ag2/notify`` and delivers it: the outbox is flushed to such a client and to
# no other. A probe that does not say so is left out, and the outbox waits.
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

    # ---- clients -------------------------------------------------------------

    def attach(self, client: Any) -> None:
        """A receiver bound to the listener; anything waiting goes to it.

        Callers attach only a client that declared itself with ``RECEIVE_META_KEY``
        (see :func:`wants_delivery`): a probe never comes through here.
        """
        if client in self._clients:
            return
        self._clients.append(client)
        if self.pending():
            asyncio.get_running_loop().create_task(self.flush(client))

    def detach(self, client: Any) -> None:
        if client in self._clients:
            self._clients.remove(client)

    @property
    def connected(self) -> int:
        return len(self._clients)

    # ---- delivery ------------------------------------------------------------

    async def notify(self, destination: str, text: str) -> bool:
        """Send one message for ``destination``, or queue it. True when a client took it."""
        params = {"destination": destination, "text": text, "connection": self.connection_id}
        if await self._send(params):
            return True
        self._queue(params)
        log.info("acp outbound %s: no client connected, message queued", self.connection_id)
        return False

    async def flush(self, client: Any) -> int:
        """Deliver the outbox to ``client`` in order; stop at the first failure."""
        sent = 0
        for params in self.pending():
            try:
                await client.ext_notification(NOTIFY_METHOD, _wire(params))
            except Exception as exc:
                log.warning("acp outbound %s: flush stopped: %s", self.connection_id, exc)
                self.detach(client)
                break
            sent += 1
        if sent:
            self._write(self.pending()[sent:])
        return sent

    async def _send(self, params: dict) -> bool:
        # Newest client first: it is the one most likely still open.
        for client in list(reversed(self._clients)):
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
