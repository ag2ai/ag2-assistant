"""Host ACP bridge: wire framing, endpoint parsing, server list/run relay, and
the container-side connector. The server resolves adapters on a real search path
holding executable stubs; the byte-relay path is exercised end-to-end against a
stub adapter that echoes its stdin (no real ACP adapter needed)."""

import asyncio
import socket
import sys
from pathlib import Path

import pytest
from ag2.context import ConversationContext
from ag2.stream import MemoryStream

from assistant.coding import bridge_client, bridge_server, detect
from assistant.coding import config as cfgmod
from assistant.coding import session as sessmod
from assistant.coding.bridge_protocol import DEFAULT_PORT, encode_frame, read_frame
from assistant.events import A2UISurface
from tests.support.stubs import write_stub

pytestmark = pytest.mark.asyncio


def _agent(name="claude", label="Claude Code", command=None, available=True):
    return detect.AgentInfo(name, label, command or [], available, "/x" if available else None)


def _bin(tmp_path: Path, *names: str) -> Path:
    """A search-path directory holding real executable stubs for these adapters."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    for name in names:
        write_stub(bin_dir / name)
    return bin_dir


def _echo_adapter(tmp_path: Path, name: str = "claude-agent-acp") -> Path:
    """A real executable adapter stub copying stdin→stdout unbuffered, so the
    server's byte relay can be driven for real."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    script = bin_dir / name
    loop = "import os\nwhile True:\n d=os.read(0,4096)\n if not d: break\n os.write(1,d)"
    script.write_text(f'#!/bin/sh\nexec {sys.executable} -u -c "{loop}"\n')
    script.chmod(0o755)
    return bin_dir


def _closed_port() -> int:
    """A port nothing listens on — dialling it must be refused."""
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


async def _start(server: "bridge_server.BridgeServer"):
    srv = await asyncio.start_server(server.handle, "127.0.0.1", 0)
    port = srv.sockets[0].getsockname()[1]
    return srv, port


# --- framing ---------------------------------------------------------------


async def test_frame_roundtrip_leaves_trailing_bytes():
    reader = asyncio.StreamReader()
    reader.feed_data(encode_frame({"op": "list", "token": "t"}))
    reader.feed_data(b"raw-acp-bytes")  # must stay buffered for the ACP stream
    obj = await read_frame(reader)
    assert obj == {"op": "list", "token": "t"}
    reader.feed_eof()
    assert await reader.read() == b"raw-acp-bytes"


async def test_read_frame_on_closed_connection_raises():
    reader = asyncio.StreamReader()
    reader.feed_eof()
    with pytest.raises(ConnectionError):
        await read_frame(reader)


# --- endpoint parsing ------------------------------------------------------


async def test_bridge_endpoint_unset():
    assert detect.bridge_endpoint({}) is None


async def test_bridge_endpoint_host_port_token():
    ep = detect.bridge_endpoint(
        {
            "AG2ASSISTANT_ACP_BRIDGE": "host.docker.internal:8801",
            "AG2ASSISTANT_ACP_BRIDGE_TOKEN": "sek",
        }
    )
    assert (ep.host, ep.port, ep.token) == ("host.docker.internal", 8801, "sek")


async def test_bridge_endpoint_bare_host_defaults_port():
    ep = detect.bridge_endpoint({"AG2ASSISTANT_ACP_BRIDGE": "myhost"})
    assert (ep.host, ep.port, ep.token) == ("myhost", DEFAULT_PORT, "")


# --- pick ------------------------------------------------------------------


async def test_pick_first_available_and_named():
    inv = [
        _agent("claude"),
        _agent("codex", "Codex", available=False),
        _agent("opencode", "OpenCode"),
    ]
    assert detect.pick(inv, "").name == "claude"  # first available
    assert detect.pick(inv, "opencode").name == "opencode"
    assert detect.pick(inv, "codex") is None  # named but unavailable
    assert detect.pick([], "") is None


# --- server: list ----------------------------------------------------------


async def test_list_returns_inventory(tmp_path):
    """The inventory is what the server really finds on its own search path."""
    bin_dir = _bin(tmp_path, "claude-agent-acp")
    srv, port = await _start(bridge_server.BridgeServer("", search_path=[bin_dir]))
    async with srv:
        agents = await bridge_client.list_agents(detect.BridgeEndpoint("127.0.0.1", port, ""))
    by_name = {a.name: a.available for a in agents}
    assert by_name == {"claude": True, "codex": False, "opencode": False}


async def test_list_token_enforced(tmp_path):
    bin_dir = _bin(tmp_path, "claude-agent-acp")
    srv, port = await _start(bridge_server.BridgeServer("secret", search_path=[bin_dir]))
    async with srv:
        with pytest.raises(ConnectionError):
            await bridge_client.list_agents(detect.BridgeEndpoint("127.0.0.1", port, "wrong"))
        agents = await bridge_client.list_agents(detect.BridgeEndpoint("127.0.0.1", port, "secret"))
    assert [a.name for a in agents if a.available] == ["claude"]


# --- server: run relay -----------------------------------------------------


async def test_run_relays_stdio(tmp_path):
    bin_dir = _echo_adapter(tmp_path)
    srv, port = await _start(bridge_server.BridgeServer("", search_path=[bin_dir]))
    async with srv:
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        writer.write(encode_frame({"op": "run", "agent": "claude", "cwd": str(tmp_path)}))
        await writer.drain()
        ack = await read_frame(reader)
        assert ack["ok"] is True
        writer.write(b"hello acp\n")
        await writer.drain()
        echoed = await asyncio.wait_for(reader.readline(), timeout=5)
        writer.close()
    assert echoed == b"hello acp\n"


async def test_run_rejects_missing_cwd(tmp_path):
    bin_dir = _echo_adapter(tmp_path)
    srv, port = await _start(bridge_server.BridgeServer("", search_path=[bin_dir]))
    async with srv:
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        writer.write(encode_frame({"op": "run", "agent": "claude", "cwd": "/nope/nope"}))
        await writer.drain()
        ack = await read_frame(reader)
        writer.close()
    assert ack["ok"] is False and "cwd not found" in ack["error"]


async def test_run_rejects_unknown_agent(tmp_path):
    """Nothing on the server's search path → every run is refused."""
    srv, port = await _start(bridge_server.BridgeServer("", search_path=[_bin(tmp_path)]))
    async with srv:
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        writer.write(encode_frame({"op": "run", "agent": "nope", "cwd": "/tmp"}))
        await writer.drain()
        ack = await read_frame(reader)
        writer.close()
    assert ack["ok"] is False and "not available" in ack["error"]


# --- connector -------------------------------------------------------------


async def test_connector_raises_on_refusal():
    async def handle(reader, writer):
        await read_frame(reader)
        writer.write(encode_frame({"ok": False, "error": "nope"}))
        await writer.drain()
        writer.close()

    srv = await asyncio.start_server(handle, "127.0.0.1", 0)
    port = srv.sockets[0].getsockname()[1]
    connector = bridge_client.make_connector(
        detect.BridgeEndpoint("127.0.0.1", port, ""), "claude", "/tmp"
    )
    async with srv:
        with pytest.raises(ConnectionError):
            async with connector(object()):
                pass


# --- config ----------------------------------------------------------------


async def test_build_config_sets_connect_only_with_endpoint():
    info = _agent(command=[])
    with_ep = cfgmod.build_config(info, "/dir", endpoint=detect.BridgeEndpoint("h", 1, ""))
    assert with_ep._connect is not None
    assert cfgmod.build_config(info, "/dir")._connect is None


# --- session mode selection ------------------------------------------------


def _ctx():
    stream = MemoryStream(id="s")
    surfaces: list = []

    async def collect(event):
        if isinstance(event, A2UISurface):
            surfaces.append(event)

    stream.subscribe(collect)
    return ConversationContext(stream=stream), surfaces


class _PM:
    def __init__(self, allow=True):
        self.allow = allow
        self.checked: list = []

    async def check(self, target):
        self.checked.append(str(target))
        return self.allow


async def test_session_uses_bridge_when_configured(tmp_path):
    """With a bridge, the session takes its inventory from a REAL bridge server
    over a real socket — the local search path is deliberately empty."""
    work = tmp_path / "work"
    work.mkdir()
    host_bin = _echo_adapter(tmp_path)
    srv, port = await _start(bridge_server.BridgeServer("", search_path=[host_bin]))
    ctx, surfaces = _ctx()
    pm = _PM()
    calls: list = []

    async def runner(config, task, context):
        calls.append(config)
        (work / "hello.txt").write_text("hi")
        return "done"

    async with srv:
        out = await sessmod.run_coding_session(
            context=ctx,
            directory=str(work),
            task="t",
            pm=pm,
            runner=runner,
            search_path=[],
            bridge=detect.BridgeEndpoint("127.0.0.1", port, ""),
        )
    assert calls and calls[0]._connect is not None  # bridge connector wired onto the config
    assert pm.checked == [str(work)]
    assert "hello.txt" in out
    assert surfaces and surfaces[-1].component["status"] == "done"


async def test_session_bridge_unreachable_is_reported(tmp_path):
    ctx, _ = _ctx()
    out = await sessmod.run_coding_session(
        context=ctx,
        directory=str(tmp_path),
        task="t",
        pm=_PM(),
        runner=None,
        bridge=detect.BridgeEndpoint("127.0.0.1", _closed_port(), ""),
    )
    assert "host coding bridge" in out and "acp-bridge" in out
