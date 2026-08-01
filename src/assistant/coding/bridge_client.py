"""Container-side connector to the host ACP bridge.

:class:`BridgeClient` binds one :class:`~assistant.coding.detect.BridgeEndpoint` and
offers:

  - :meth:`BridgeClient.list_agents` — the daemon's ``list`` inventory (for
    detection), and
  - :meth:`BridgeClient.make_connector` — an ``ag2.acp`` ``ConnectHook`` that opens a
    TCP socket to the daemon, performs the handshake, and wraps the socket streams in
    a ``ClientSideConnection``. This is the *same* connection class the local
    subprocess path uses (``acp.spawn_agent_process``), so the ACP protocol flows
    over the socket unchanged — only the transport differs.

The transport is the bridge's own framed TCP protocol, not HTTP, so the injectable
seam is ``open_connection`` (the ``asyncio.open_connection`` signature).
"""

import asyncio
from contextlib import asynccontextmanager

from assistant.coding.bridge_protocol import encode_frame, read_frame
from assistant.coding.detect import AgentInfo, BridgeEndpoint


async def _close_writer(writer) -> None:
    writer.close()
    try:
        await writer.wait_closed()
    except (ConnectionError, OSError):
        pass


class BridgeClient:
    """Talks to the host bridge daemon listening at ``endpoint``."""

    def __init__(self, endpoint: BridgeEndpoint, *, open_connection=asyncio.open_connection):
        self.endpoint = endpoint
        self._open_connection = open_connection

    async def list_agents(self) -> list[AgentInfo]:
        """Ask the daemon which agents it can drive. Raises on refusal/unreachable."""
        ep = self.endpoint
        reader, writer = await self._open_connection(ep.host, ep.port)
        try:
            writer.write(encode_frame({"op": "list", "token": ep.token}))
            await writer.drain()
            ack = await read_frame(reader)
        finally:
            await _close_writer(writer)
        if not ack.get("ok"):
            raise ConnectionError(ack.get("error", "bridge refused the list request"))
        return [
            AgentInfo(
                name=a["name"],
                label=a["label"],
                command=[],  # the daemon owns the launch command on the host
                available=a["available"],
                path=None,
            )
            for a in ack.get("agents", [])
        ]

    def make_connector(self, agent_name: str, cwd: str):
        """Build a ``ConnectHook`` that runs ``agent_name`` in ``cwd`` via the daemon."""
        from acp import connect_to_agent

        ep = self.endpoint
        open_connection = self._open_connection

        @asynccontextmanager
        async def _connect(client):
            reader, writer = await open_connection(ep.host, ep.port)
            writer.write(
                encode_frame({"op": "run", "token": ep.token, "agent": agent_name, "cwd": cwd})
            )
            await writer.drain()
            ack = await read_frame(reader)
            if not ack.get("ok"):
                await _close_writer(writer)
                raise ConnectionError(ack.get("error", "bridge refused the run request"))
            # From here the socket carries raw ACP JSON-RPC; wrap it exactly as the
            # subprocess path wraps stdin/stdout (writer=input, reader=output).
            conn = connect_to_agent(client, writer, reader)
            try:
                yield conn, None  # no local process — the adapter runs on the host
            finally:
                await conn.close()
                await _close_writer(writer)

        return _connect
