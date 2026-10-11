"""The event bridge: a chat's AG2 stream ⇄ a WebSocket client.

This is the generalized form of the subscriptions we already run piecemeal (tool
chips, voice). For one chat it (1) replays the persisted stream on connect and
(2) forwards every live event via ``stream.subscribe`` — both as the single
`{type, data}` wire shape (`to_wire`). Input messages run through the gateway's
`send_message`, which appends to the *same* stream, so the agent's events flow
straight back out through the subscription. History (replay) and live are one path.
"""

import contextlib
from uuid import UUID

from ag2.stream import MemoryStream

from assistant.gateway.history import event_cursor, event_page
from assistant.gateway.wire import as_drawn, is_binary_event, to_wire


class StreamBridge:
    """Bridges one chat stream to one WebSocket. Construct, ``open()``, feed
    turns via ``run_turn``, and ``close()`` to detach."""

    def __init__(
        self,
        gateway,
        websocket,
        chat_id: str,
        *,
        history_limit: int | None = None,
        history_from: str | None = None,
    ):
        self._gw = gateway
        self._ws = websocket
        self._sid = chat_id
        self._stream: MemoryStream | None = None
        self._sub: UUID | None = None
        self._history_limit = history_limit
        self._history_from = history_from
        self._pending: list | None = []

    async def open(self) -> None:
        """Subscribe before replay and buffer live events until the ready frame."""
        stream: MemoryStream = await self._gw.stream_for(self._sid)
        self._stream = stream
        self._sub = stream.subscribe(self._receive)
        events = await self._gw.replay_events(self._sid)
        ready: dict = {"type": "ready", "chat": self._sid}
        selected = events
        if self._history_limit is not None:
            try:
                page = event_page(events, self._history_limit, start=self._history_from)
            except ValueError:
                page = event_page(events, self._history_limit)
            selected = page.events
            ready["history"] = {"before": page.before, "start": page.start}
            ready["busy"] = self._gw.is_running(self._sid)
        for event in selected:
            await self._forward(event)
        with contextlib.suppress(Exception):
            await self._ws.send_json(ready)
        replayed = {event_cursor(event) for event in events}
        while self._pending:
            pending, self._pending = self._pending, []
            for event in pending:
                if event_cursor(event) not in replayed:
                    await self._forward(event)
        self._pending = None

    async def _receive(self, event) -> None:
        if self._pending is not None:
            self._pending.append(event)
        else:
            await self._forward(event)

    async def _forward(self, event) -> None:
        if is_binary_event(event):
            return  # audio rides its own binary frame, not {type, data}
        with contextlib.suppress(Exception):
            await self._ws.send_json(
                {"event": to_wire(as_drawn(event, self._gw.catalog, self._gw.card_sources))}
            )

    async def run_turn(
        self,
        text: str,
        asker=None,
        attachments=None,
        surface: str = "",
        attachment_names: tuple[str, ...] = (),
        chat_model: str = "",
    ) -> None:
        """Run a user turn; its events flow back out through the subscription.

        ``chat_model`` carries a model the client picked before this Chat existed, for
        the message that creates it to adopt as the Chat's override (ADR 0025)."""
        try:
            await self._gw.send_message(
                text,
                chat_id=self._sid,
                asker=asker,
                attachments=attachments,
                surface=surface,
                attachment_names=attachment_names,
                chat_model=chat_model,
            )
            with contextlib.suppress(Exception):
                await self._ws.send_json({"type": "turn_end", "chat": self._sid})
        except Exception as exc:
            with contextlib.suppress(Exception):
                await self._ws.send_json(
                    {"type": "error", "message": str(exc) or repr(exc), "chat": self._sid}
                )

    def close(self) -> None:
        if self._sub is not None and self._stream is not None:
            with contextlib.suppress(Exception):
                self._stream.unsubscribe(self._sub)
        self._sub = None
