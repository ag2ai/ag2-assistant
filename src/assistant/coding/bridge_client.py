"""Container-side connector to the host ACP bridge.

Turns a :class:`~assistant.coding.detect.BridgeEndpoint` into:

  - :func:`list_agents` — the daemon's ``list`` inventory (for detection), and
  - :func:`make_connector` — an ``ag2.acp`` ``ConnectHook`` that opens a TCP
    socket to the daemon, performs the handshake, and wraps the socket streams in
    a ``ClientSideConnection``. This is the *same* connection class the local
    subprocess path uses (``acp.spawn_agent_process``), so the ACP protocol flows
    over the socket unchanged — only the transport differs.
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


async def list_agents(ep: BridgeEndpoint) -> list[AgentInfo]:
    """Ask the host daemon which agents it can drive. Raises on refusal/unreachable."""
    reader, writer = await asyncio.open_connection(ep.host, ep.port)
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


def make_connector(ep: BridgeEndpoint, agent_name: str, cwd: str):
    """Build a ``ConnectHook`` that runs ``agent_name`` in ``cwd`` via the daemon."""
    from acp import connect_to_agent

    @asynccontextmanager
    async def _connect(client):
        reader, writer = await asyncio.open_connection(ep.host, ep.port)
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
