"""An ACP session's destination and the listener's outbound delivery (ADR 0049).

A client says on ``session/new`` where that session's unsolicited messages belong;
the Peer keeps it, a Task created in the session records it as its origin, and a
task-run outcome reaches the client as an ``_ag2/notify`` extension notification,
queued in a durable outbox while no client is connected.
"""

import asyncio
import json
from types import SimpleNamespace

import acp
import pytest
from ag2.acp.testing import connect

from assistant.acp.outbound import DESTINATION_META_KEY, NOTIFY_METHOD, Outbound, destination_of
from assistant.channels.base import PUSH_CHANNELS
from assistant.peers import PeerStore
from assistant.system_tools import _origin
from tests.support.apps import make_manager
from tests.test_acp_chats import _new_profile, _served

ROOM = "!room:ag2.space"


class FakeClient:
    """The agent-side Client handle: records extension notifications, or fails."""

    def __init__(self, fail: Exception | None = None) -> None:
        self.sent: list[tuple[str, dict]] = []
        self.fail = fail

    async def ext_notification(self, method: str, params: dict) -> None:
        if self.fail is not None:
            raise self.fail
        self.sent.append((method, params))


# --- the destination a session declares ------------------------------------------------


def test_destination_reads_a_string_or_a_chat_id_and_nothing_else():
    assert destination_of({DESTINATION_META_KEY: ROOM}) == ROOM
    assert destination_of({DESTINATION_META_KEY: {"chat_id": ROOM, "platform": "x"}}) == ROOM
    assert destination_of({DESTINATION_META_KEY: {"platform": "x"}}) is None
    assert destination_of({DESTINATION_META_KEY: "  "}) is None
    assert destination_of({}) is None
    assert destination_of(None) is None


def test_acp_is_a_push_platform():
    assert "acp" in PUSH_CHANNELS


def test_a_peer_remembers_its_destination(paths):
    store = PeerStore(paths)
    with pytest.raises(KeyError):
        store.set_destination("cn", "s1", ROOM)
    store.start_chat("cn", "s1", platform="acp")
    assert store.set_destination("cn", "s1", ROOM).destination == ROOM
    assert store.get_peer("cn", "s1").destination == ROOM  # survives the round trip
    assert store.set_destination("cn", "s1", None).destination is None


async def test_session_new_metadata_becomes_the_peers_destination_and_the_tasks_origin(paths):
    pid = _new_profile(paths)
    served, _storage, _config = _served(paths, pid)
    async with connect(served) as (client, _recorder):
        session = await client.new_session(cwd=".", **{DESTINATION_META_KEY: {"chat_id": ROOM}})
        await client.prompt(session_id=session.session_id, prompt=[acp.text_block("hi")])
    peers = PeerStore(paths)
    peer = peers.get_peer("acp:test", session.session_id)
    assert peer is not None and peer.destination == ROOM
    # What create_task records: the listener and the room, not the session id.
    context = SimpleNamespace(stream=SimpleNamespace(id=peer.chat))
    assert _origin(peers, context) == ("acp:test", ROOM)


async def test_a_session_without_a_destination_keeps_the_session_id_as_origin(paths):
    pid = _new_profile(paths)
    served, _storage, _config = _served(paths, pid)
    async with connect(served) as (client, _recorder):
        session = await client.new_session(cwd=".")
        await client.prompt(session_id=session.session_id, prompt=[acp.text_block("hi")])
    peers = PeerStore(paths)
    peer = peers.get_peer("acp:test", session.session_id)
    assert peer is not None and peer.destination is None
    context = SimpleNamespace(stream=SimpleNamespace(id=peer.chat))
    assert _origin(peers, context) == ("acp:test", session.session_id)


# --- outbound: a connected client, else the outbox ---------------------------------------


async def test_notify_reaches_the_newest_connected_client(tmp_path):
    out = Outbound("cn-1", tmp_path / "outbox")
    first, second = FakeClient(), FakeClient()
    out.attach(first)
    out.attach(second)
    assert await out.notify(ROOM, "done") is True
    assert second.sent == [
        (NOTIFY_METHOD, {"destination": ROOM, "text": "done", "connection": "cn-1"})
    ]
    assert first.sent == [] and out.pending() == []


async def test_a_dead_client_is_dropped_and_the_next_one_tried(tmp_path):
    out = Outbound("cn-1", tmp_path / "outbox")
    live, dead = FakeClient(), FakeClient(fail=ConnectionError("closed"))
    out.attach(live)
    out.attach(dead)  # newest, tried first
    assert await out.notify(ROOM, "done") is True
    assert [m for m, _ in live.sent] == [NOTIFY_METHOD]
    assert out.connected == 1


async def test_with_no_client_the_message_waits_in_the_outbox(tmp_path):
    out = Outbound("cn-1", tmp_path / "outbox")
    assert await out.notify(ROOM, "first") is False
    assert await out.notify(ROOM, "second") is False
    pending = out.pending()
    assert [p["text"] for p in pending] == ["first", "second"]
    assert all(p["destination"] == ROOM and "queued_at" in p for p in pending)
    raw = json.loads((tmp_path / "outbox" / "cn-1.json").read_text())
    assert len(raw) == 2  # durable: on disk, not in memory


async def test_the_outbox_is_flushed_in_order_when_a_client_connects(tmp_path):
    out = Outbound("cn-1", tmp_path / "outbox")
    await out.notify(ROOM, "first")
    await out.notify(ROOM, "second")
    client = FakeClient()
    assert await out.flush(client) == 2
    assert [p["text"] for _, p in client.sent] == ["first", "second"]
    assert out.pending() == []
    # A flush that fails midway keeps what was not sent.
    await out.notify(ROOM, "third")
    assert await out.flush(FakeClient(fail=ConnectionError("closed"))) == 0
    assert [p["text"] for p in out.pending()] == ["third"]


async def test_an_unreadable_outbox_reads_as_empty(tmp_path):
    (tmp_path / "outbox").mkdir()
    (tmp_path / "outbox" / "cn-1.json").write_text("{not json")
    assert Outbound("cn-1", tmp_path / "outbox").pending() == []


# --- the manager routes an ACP listener's outcomes to its outbound side -------------------


async def test_the_manager_delivers_a_listeners_outcome_through_its_outbound(paths, tmp_path):
    manager = make_manager(paths)
    out = Outbound("acp-1", tmp_path / "outbox")
    client = FakeClient()
    out.attach(client)
    manager.acp_outbound["acp-1"] = out
    await manager.notify_channel("acp-1", ROOM, "✅ nightly: 3 PRs reviewed")
    assert client.sent == [
        (
            NOTIFY_METHOD,
            {"destination": ROOM, "text": "✅ nightly: 3 PRs reviewed", "connection": "acp-1"},
        )
    ]
    with pytest.raises(RuntimeError):  # anything else still needs a running Channel
        await manager.notify_channel("cn-unknown", ROOM, "hi")


# --- end to end: a supervised listener, a real WebSocket client, a queued outcome ---------


class _WsClient:
    """The ACP client side as agent-connect would implement it: records every
    extension notification it is sent."""

    def __init__(self) -> None:
        self.notified: list[tuple[str, dict]] = []
        self.arrived = asyncio.Event()

    async def ext_notification(self, method: str, params: dict) -> None:
        self.notified.append((method, params))
        self.arrived.set()

    async def session_update(self, session_id: str, update, **kwargs) -> None:
        return None

    async def request_permission(self, session_id: str, tool_call, options, **kwargs):
        return {"outcome": {"outcome": "cancelled"}}


async def test_a_queued_outcome_reaches_the_next_websocket_client_to_bind(paths):
    """The task service pushes an outcome while nobody is connected: it waits in the
    outbox. A client that then dials in, as agent-connect does, receives it as
    ``_ag2/notify`` right after the handshake, and a later push arrives live."""
    from acp import connect_to_agent
    from acp.ws.client import create_websocket_stream

    from assistant.profiles import ProfileRegistry
    from tests.test_acp_serve_ws import _wait_listening
    from tests.test_gateway_acp import _free_port, _pong_factory

    pid = ProfileRegistry(paths).create_profile("Work", "#109e91").id
    manager = make_manager(paths, agent_factory=_pong_factory())
    await manager.start()
    try:
        port = _free_port()
        token = "listener-token"  # noqa: S105 - test fixture
        cid = manager._connections.create_acp_connection(pid, port=port, token=token).connection.id
        ok, reason = await manager.start_acp_listener(cid)
        assert ok, reason
        await _wait_listening("127.0.0.1", port)

        # Pushed with no client connected: queued, durably.
        await manager.notify_channel(cid, ROOM, "✅ nightly: done")
        assert [p["text"] for p in manager.acp_outbound[cid].pending()] == ["✅ nightly: done"]

        ws_client = _WsClient()
        transport = await create_websocket_stream(
            f"ws://127.0.0.1:{port}/acp", headers={"Authorization": f"Bearer {token}"}
        )
        conn = connect_to_agent(ws_client, transport)
        try:
            await conn.initialize(protocol_version=1)
            await asyncio.wait_for(ws_client.arrived.wait(), 5)
            assert ws_client.notified == [
                (
                    NOTIFY_METHOD,
                    {"destination": ROOM, "text": "✅ nightly: done", "connection": cid},
                )
            ]
            assert manager.acp_outbound[cid].pending() == []
            # And a push while it is connected goes straight through.
            ws_client.arrived.clear()
            await manager.notify_channel(cid, ROOM, "✅ nightly: again")
            await asyncio.wait_for(ws_client.arrived.wait(), 5)
            assert ws_client.notified[-1][1]["text"] == "✅ nightly: again"
        finally:
            await conn.close()
    finally:
        await manager.close()
