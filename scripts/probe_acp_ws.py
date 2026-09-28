#!/usr/bin/env python3
"""Drive a running listener's ACP WebSocket door the way a remote Client would.

Checks the three things a per-turn dialer depends on, and prints every frame:

1. ``initialize`` — the door answers and advertises ``loadSession``
2. a turn on a fresh session, then the socket closes
3. ``session/load`` on a SECOND socket — the shape a Client that dials per turn
   uses, and the one that fails when history is not durable
4. a bad bearer — refused at the WebSocket upgrade, before any ACP frame

Usage:

    python3 scripts/probe_acp_ws.py --connection "AG2 Space Assistant"
    python3 scripts/probe_acp_ws.py --url ws://127.0.0.1:8802 --token SECRET

``--connection`` reads the port and token from the stored ACP listener, so it
needs this install's data dir; ``--url``/``--token`` need nothing. Exits non-zero
on the first failed check.
"""

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

try:
    from acp.ws.client import create_websocket_stream
except ImportError:  # pragma: no cover - depends on the SDK's optional extra
    sys.exit(
        "This probe needs the ACP SDK's WebSocket transport.\n"
        "Install it with:  pip install 'agent-client-protocol[http]'"
    )


class Dial:
    """One WebSocket connection, driven at the JSON-RPC level.

    Deliberately hand-rolled rather than using the SDK's client: the point is to
    see what the door actually puts on the wire, including frames a typed client
    would have swallowed.
    """

    def __init__(self, transport, label: str) -> None:
        self._t = transport
        self._label = label
        self._id = 0

    async def call(self, method: str, params: dict, timeout: float = 180.0) -> dict:
        self._id += 1
        rid = self._id
        await self._t.send({"jsonrpc": "2.0", "id": rid, "method": method, "params": params})
        print(f"  {self._label} >>> {method}")
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        while True:
            message = await asyncio.wait_for(self._t.receive(), deadline - loop.time())
            if message is None:
                return {"error": {"code": 0, "message": "socket closed"}}
            if message.get("method") == "session/update":
                update = message["params"]["update"]
                text = (update.get("content") or {}).get("text", "")
                print(f"  {self._label} <<< {update.get('sessionUpdate')}: {text[:140]!r}")
                continue
            if message.get("id") == rid:
                payload = message.get("result", message.get("error"))
                print(f"  {self._label} <<< {json.dumps(payload)[:220]}")
                return message

    async def close(self) -> None:
        await self._t.close()


async def dial(url: str, token: str, label: str) -> Dial:
    transport = await create_websocket_stream(url, headers={"Authorization": f"Bearer {token}"})
    connection = Dial(transport, label)
    reply = await connection.call(
        "initialize", {"protocolVersion": 1, "clientCapabilities": {"fs": {}}}
    )
    result = reply["result"]
    info = result["agentInfo"]
    print(f"  {label} === {info['name']} {info['version']}")
    if not result["agentCapabilities"].get("loadSession"):
        raise SystemExit("FAIL: the door does not advertise loadSession")
    return connection


def stored_listener(name: str) -> tuple[str, str]:
    """``(url, token)`` for a stored ACP listener, by id or display name."""
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
    from assistant.connections import ConnectionStore
    from assistant.paths import Paths

    store = ConnectionStore(Paths.from_env(os.environ, Path.home()))
    listener = store.get_acp_connection(name) or next(
        (c for c in store.list_acp_connections() if c.connection.name == name), None
    )
    if listener is None:
        raise SystemExit(f"FAIL: no stored ACP listener named {name!r}")
    if listener.port is None:
        raise SystemExit(f"FAIL: listener {name!r} has no port (stdio only)")
    return f"ws://127.0.0.1:{listener.port}", store.acp_token_for(listener.connection.id)


async def run(url: str, token: str, cwd: str) -> None:
    secret = "8802-OK"

    print(f"url: {url}\ntoken: {'present' if token else 'EMPTY'}\n")

    print("--- dial 1: new session, one turn, then disconnect ---")
    first = await dial(url, token, "d1")
    opened = await first.call("session/new", {"cwd": cwd, "mcpServers": []})
    session_id = opened["result"]["sessionId"]
    await first.call(
        "session/prompt",
        {
            "sessionId": session_id,
            "prompt": [{"type": "text", "text": f"My probe code is {secret}. Just acknowledge."}],
        },
    )
    await first.close()

    print("\n--- dial 2: a SECOND socket loads that session ---")
    second = await dial(url, token, "d2")
    loaded = await second.call(
        "session/load", {"sessionId": session_id, "cwd": cwd, "mcpServers": []}
    )
    if "error" in loaded:
        raise SystemExit(
            f"FAIL: session/load was refused — {loaded['error']}\n"
            "A per-turn dialer cannot keep a conversation against this build."
        )
    answer = await second.call(
        "session/prompt",
        {"sessionId": session_id, "prompt": [{"type": "text", "text": "What is my probe code?"}]},
    )
    await second.close()
    if "error" in answer:
        raise SystemExit(f"FAIL: the resumed turn errored — {answer['error']}")

    print("\n--- bad bearer: must be refused at the upgrade ---")
    try:
        rogue = await create_websocket_stream(url, headers={"Authorization": "Bearer wrong"})
    except Exception as exc:  # noqa: BLE001 - any refusal is the pass condition
        print(f"  refused: {type(exc).__name__}: {str(exc)[:160]}")
    else:
        await rogue.close()
        raise SystemExit("FAIL: a bad bearer was accepted at the upgrade")

    print("\nPASS: dial, resume across sockets, and bearer refusal all behaved.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--connection", help="stored ACP listener id or display name")
    parser.add_argument("--url", help="ws://host:port of the listener")
    parser.add_argument("--token", default="", help="bearer the listener expects")
    parser.add_argument("--cwd", default=".", help="cwd to open sessions with")
    args = parser.parse_args()

    if args.connection:
        url, token = stored_listener(args.connection)
    elif args.url:
        url, token = args.url, args.token
    else:
        parser.error("give either --connection or --url")

    asyncio.run(run(url, token, args.cwd))


if __name__ == "__main__":
    main()
