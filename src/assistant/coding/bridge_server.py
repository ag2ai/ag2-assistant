"""Host-side ACP bridge daemon.

Runs on the HOST (outside Docker), where the coding CLIs and their on-disk
logins live. A containerized ag2-assistant connects over TCP and either lists
the installed agents (``op=list``) or asks the daemon to spawn one and relay its
ACP stdio over the socket (``op=run``).

Security posture:
  - Binds to loopback by default; reachable from a container via
    ``host.docker.internal`` on Docker Desktop.
  - Gates every connection on an optional shared ``token``.
  - The spawned adapter inherits only a small env whitelist (no provider API
    keys) and runs with the requested ``cwd`` — so it uses the CLI's own login
    and stays in the approved folder.
"""

import asyncio
import os
from collections.abc import Sequence
from pathlib import Path

from assistant.coding import detect
from assistant.coding.bridge_protocol import DEFAULT_PORT, encode_frame, read_frame

# Env the adapter subprocess may see — enough to find the CLI's on-disk login,
# deliberately NO provider API keys (auth is the CLI's own login).
_ENV_WHITELIST = ("HOME", "PATH", "SHELL", "TERM", "USER", "LOGNAME", "LANG", "LC_ALL")

_CHUNK = 65536


def _child_env() -> dict:
    return {k: os.environ[k] for k in _ENV_WHITELIST if k in os.environ}


def _agents_payload(search_path: Sequence[Path]) -> list[dict]:
    """Host inventory, mirroring ``detect_agents`` but only wire-relevant fields."""
    return [
        {"name": a.name, "label": a.label, "available": a.available}
        for a in detect.detect_agents(search_path)
    ]


async def _relay(reader, writer, proc) -> None:
    """Pump bytes both ways between the socket and the adapter's stdio until EOF."""

    async def sock_to_proc():
        try:
            while True:
                data = await reader.read(_CHUNK)
                if not data:
                    break
                proc.stdin.write(data)
                await proc.stdin.drain()
        except (ConnectionError, BrokenPipeError):
            pass
        finally:
            if proc.stdin is not None and not proc.stdin.is_closing():
                proc.stdin.close()

    async def proc_to_sock():
        try:
            while True:
                data = await proc.stdout.read(_CHUNK)
                if not data:
                    break
                writer.write(data)
                await writer.drain()
        except (ConnectionError, BrokenPipeError):
            pass

    t1 = asyncio.create_task(sock_to_proc())
    t2 = asyncio.create_task(proc_to_sock())
    try:
        await asyncio.wait({t1, t2}, return_when=asyncio.FIRST_COMPLETED)
    finally:
        for t in (t1, t2):
            t.cancel()
        if proc.returncode is None:
            try:
                proc.terminate()
                await asyncio.wait_for(proc.wait(), timeout=5)
            except (asyncio.TimeoutError, ProcessLookupError):
                try:
                    proc.kill()
                except ProcessLookupError:
                    pass
        writer.close()


class BridgeServer:
    """Handles one connection at a time: authorize → dispatch (list / run)."""

    def __init__(self, token: str = "", *, search_path: Sequence[Path] = ()) -> None:
        self._token = token
        self._search_path = search_path

    def _authorized(self, frame: dict) -> bool:
        return not self._token or frame.get("token") == self._token

    async def _ack(self, writer, obj: dict) -> None:
        writer.write(encode_frame(obj))
        try:
            await writer.drain()
        except ConnectionError:
            pass

    async def handle(self, reader, writer) -> None:
        try:
            frame = await read_frame(reader)
        except (ConnectionError, ValueError):
            writer.close()
            return

        if not self._authorized(frame):
            await self._ack(writer, {"ok": False, "error": "unauthorized"})
            writer.close()
            return

        op = frame.get("op")
        if op == "list":
            await self._ack(writer, {"ok": True, "agents": _agents_payload(self._search_path)})
            writer.close()
            return
        if op == "run":
            await self._run(frame, reader, writer)
            return

        await self._ack(writer, {"ok": False, "error": f"unknown op: {op!r}"})
        writer.close()

    async def _run(self, frame: dict, reader, writer) -> None:
        name = frame.get("agent", "") or ""
        cwd = frame.get("cwd", "") or ""
        info = detect.resolve_agent(name, self._search_path)  # host-side `which`
        if info is None:
            await self._ack(writer, {"ok": False, "error": f"agent not available: {name!r}"})
            writer.close()
            return
        if not cwd or not os.path.isdir(cwd):
            await self._ack(writer, {"ok": False, "error": f"cwd not found on host: {cwd!r}"})
            writer.close()
            return
        try:
            proc = await asyncio.create_subprocess_exec(
                *info.command,
                cwd=cwd,
                env=_child_env(),
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
            )
        except OSError as exc:
            await self._ack(writer, {"ok": False, "error": f"spawn failed: {exc}"})
            writer.close()
            return
        await self._ack(writer, {"ok": True})
        await _relay(reader, writer, proc)


async def serve(
    host: str = "127.0.0.1",
    port: int = DEFAULT_PORT,
    token: str = "",
    *,
    search_path: Sequence[Path] = (),
) -> None:
    """Run the bridge server until cancelled (Ctrl-C)."""
    server = BridgeServer(token, search_path=search_path)
    srv = await asyncio.start_server(server.handle, host, port)
    addrs = ", ".join(str(s.getsockname()) for s in srv.sockets)
    note = " (token required)" if token else " (NO token — bind to loopback only!)"
    print(f"acp-bridge listening on {addrs}{note}")
    print("Agents on this host:")
    for a in detect.detect_agents(search_path):
        print(f"  - {a.label} ({a.name}): {'available' if a.available else 'not installed'}")
    async with srv:
        await srv.serve_forever()
