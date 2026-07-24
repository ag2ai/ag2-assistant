"""Wire framing for the ACP host bridge (container ⇄ host daemon).

One newline-delimited JSON *control frame* opens every connection; the daemon
replies with one newline-delimited JSON *ack*:

  - ``{"op": "list", "token": …}`` → ack carries the agent inventory, then close.
  - ``{"op": "run", "token": …, "agent": …, "cwd": …}`` → ack ``{"ok": true}``,
    after which the socket carries raw ACP JSON-RPC in both directions (the daemon
    relays it byte-for-byte to/from the spawned adapter's stdio).

Keeping this format in one module is the single source of truth for both sides.
"""

import json

DEFAULT_PORT = 8801


def encode_frame(obj: dict) -> bytes:
    """Serialize one control frame / ack as a single newline-terminated JSON line."""
    return (json.dumps(obj, separators=(",", ":")) + "\n").encode("utf-8")


async def read_frame(reader) -> dict:
    """Read exactly one newline-delimited JSON frame from an ``asyncio`` reader.

    Leaves any bytes after the newline buffered on the reader untouched, so the
    same stream can carry raw ACP traffic immediately after the handshake.
    """
    line = await reader.readline()
    if not line:
        raise ConnectionError("bridge connection closed before a frame was received")
    return json.loads(line.decode("utf-8"))
