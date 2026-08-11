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

from assistant.gateway.wire import is_binary_event, to_wire


class StreamBridge:
    """Bridges one chat stream to one WebSocket. Construct, ``open()``, feed
    turns via ``run_turn``, and ``close()`` to detach."""

    def __init__(self, gateway, websocket, chat_id: str):
        self._gw = gateway
        self._ws = websocket
        self._sid = chat_id
        self._stream: MemoryStream | None = None
        self._sub: UUID | None = None

    async def open(self) -> None:
        """Replay the persisted stream, then subscribe to forward live events."""
        stream: MemoryStream = await self._gw.stream_for(self._sid)
        self._stream = stream
        for event in await stream.history.get_events():
            await self._forward(event)
        with contextlib.suppress(Exception):
            await self._ws.send_json({"type": "ready", "chat": self._sid})
        self._sub = stream.subscribe(self._forward)  # event injected positionally

    async def _forward(self, event) -> None:
        if is_binary_event(event):
            return  # audio rides its own binary frame, not {type, data}
        with contextlib.suppress(Exception):
            await self._ws.send_json({"event": to_wire(event)})

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
