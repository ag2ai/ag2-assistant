"""ACP listener lifecycle: ``ProfileManager`` boots one server task per
stored ``AcpConnection`` with a port — the same Connection-driven boot ``run`` already
uses for messaging channels — reusing the bound profile's own running gateway agent
rather than building a second ``Gateway`` for it. No real LLM: every profile's agent
is a ``TestConfig``-backed ``ag2.Agent`` (ACP needs a real Agent, not the plain
``FakeAgent``, to wrap in ``ACPAgent``).
"""

import asyncio
import contextlib
import socket

import pytest
from ag2 import Agent as Ag2Agent
from ag2.acp import ACPRemoteConfig
from ag2.testing import TestConfig

from assistant.acp.listeners import (
    UnknownAcpConnection,
    ensure_acp_connection,
    stdio_connection_target,
)
from assistant.connections import ConnectionStore
from assistant.profiles import ProfileRegistry
from tests.support.apps import make_manager
from tests.support.fakes import fake_agent_factory


def _free_port() -> int:
    """A loopback port nothing is listening on yet — released before the caller binds it."""
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


async def _wait_listening(port: int, timeout: float = 5.0) -> None:
    """Poll a raw TCP connect until something is accepting on ``port``."""
    loop = asyncio.get_event_loop()
    deadline = loop.time() + timeout
    while True:
        try:
            _, writer = await asyncio.open_connection("127.0.0.1", port)
        except OSError:
            if loop.time() > deadline:
                raise
            await asyncio.sleep(0.02)
            continue
        writer.close()
        with contextlib.suppress(Exception):
            await writer.wait_closed()
        return


async def _wait_refused(port: int, timeout: float = 5.0) -> None:
    """Poll a raw TCP connect until nothing accepts on ``port`` any more — uvicorn
    releases its listening socket a beat after the serving task is cancelled."""
    loop = asyncio.get_event_loop()
    deadline = loop.time() + timeout
    while True:
        try:
            _, writer = await asyncio.wait_for(
                asyncio.open_connection("127.0.0.1", port), timeout=0.5
            )
        except OSError:
            return
        writer.close()
        with contextlib.suppress(Exception):
            await writer.wait_closed()
        if loop.time() > deadline:
            raise AssertionError(f"port {port} is still accepting connections")
        await asyncio.sleep(0.02)


def _pong_factory():
    """A ``create_agent``-shaped factory building a real, ACP-wrappable Agent that
    always replies "pong" — ``fake_agent_factory``'s plain ``FakeAgent`` lacks the
    surface ``ACPAgent`` needs."""
    return fake_agent_factory(
        agent=lambda config, **kwargs: Ag2Agent(
            name="acp-lifecycle-test", config=TestConfig("pong")
        )
    )


async def test_start_brings_up_a_reachable_listener_and_close_tears_it_down(paths, monkeypatch):
    monkeypatch.setattr("assistant.acp.auth.profile_has_credentials", lambda config, env: True)
    pid = ProfileRegistry(paths).create_profile("Work", "#336699").id
    port = _free_port()
    listener = ConnectionStore(paths).create_acp_connection(pid, port=port, token="s3cret")

    mgr = make_manager(paths, agent_factory=_pong_factory())
    await mgr.start()
    try:
        assert listener.connection.id in mgr.acp_listeners
        assert listener.connection.id not in mgr.acp_listener_errors
        await _wait_listening(port)

        remote = ACPRemoteConfig(
            url=f"ws://127.0.0.1:{port}/acp",
            headers={"Authorization": "Bearer s3cret"},
            expose_tools=False,
            permission_policy="deny",
        )
        client = Ag2Agent(name="lifecycle-client", config=remote)
        reply = await client.ask("hi")
        assert "pong" in str(await reply.content()).lower()
    finally:
        await mgr.close()

    assert mgr.acp_listeners == {}
    await _wait_refused(port)


async def test_two_listeners_on_the_same_port_one_serves_one_errors(paths):
    pid = ProfileRegistry(paths).create_profile("Work", "#336699").id
    port = _free_port()
    store = ConnectionStore(paths)
    first = store.create_acp_connection(pid, name="First", port=port)
    second = store.create_acp_connection(pid, name="Second", port=port)

    mgr = make_manager(paths, agent_factory=_pong_factory())
    await mgr.start()  # must complete even though one listener fails
    try:
        started = set(mgr.acp_listeners)
        errored = set(mgr.acp_listener_errors)
        assert started == {first.connection.id}
        assert errored == {second.connection.id}
        assert str(port) in mgr.acp_listener_errors[second.connection.id]
        assert "already in use" in mgr.acp_listener_errors[second.connection.id]
    finally:
        await mgr.close()


async def test_stop_acp_listener_stops_it(paths):
    pid = ProfileRegistry(paths).create_profile("Work", "#336699").id
    port = _free_port()
    listener = ConnectionStore(paths).create_acp_connection(pid, port=port)

    mgr = make_manager(paths, agent_factory=_pong_factory())
    await mgr.start()
    try:
        await _wait_listening(port)

        stopped = await mgr.stop_acp_listener(listener.connection.id)

        assert stopped is True
        assert listener.connection.id not in mgr.acp_listeners
        await _wait_refused(port)
        # stopping again is a no-op, not an error
        assert await mgr.stop_acp_listener(listener.connection.id) is False
    finally:
        await mgr.close()


async def test_archived_profile_records_an_error_and_start_completes(paths):
    registry = ProfileRegistry(paths)
    keep = registry.create_profile("Keep", "#109e91").id
    old = registry.create_profile("Old", "#f95339").id
    listener = ConnectionStore(paths).create_acp_connection(old, port=_free_port())
    registry.archive_profile(old)

    mgr = make_manager(paths, agent_factory=_pong_factory())
    await mgr.start()
    try:
        assert listener.connection.id not in mgr.acp_listeners
        assert "archived" in mgr.acp_listener_errors[listener.connection.id]
        # the unrelated, unarchived profile still boots fine
        assert mgr.get(keep).pid == keep
    finally:
        await mgr.close()


# --- the acp-serve CLI's auto-register helper (tested directly, not the typer command) ---


def test_ensure_acp_connection_registers_when_absent(paths):
    pid = ProfileRegistry(paths).create_profile("Work", "#336699").id
    store = ConnectionStore(paths)

    created = ensure_acp_connection(store, pid, 8802, token="tok")

    assert created.profile == pid
    assert created.port == 8802
    assert store.get_acp_connection(created.connection.id) is not None
    assert store.acp_token_for(created.connection.id) == "tok"


def test_ensure_acp_connection_reuses_an_identical_existing_record(paths):
    pid = ProfileRegistry(paths).create_profile("Work", "#336699").id
    store = ConnectionStore(paths)
    first = ensure_acp_connection(store, pid, 8802)

    again = ensure_acp_connection(store, pid, 8802)

    assert again.connection.id == first.connection.id
    assert len(store.list_acp_connections()) == 1


def test_ensure_acp_connection_registers_a_second_record_for_a_different_port(paths):
    pid = ProfileRegistry(paths).create_profile("Work", "#336699").id
    store = ConnectionStore(paths)
    first = ensure_acp_connection(store, pid, 8802)

    second = ensure_acp_connection(store, pid, 8803)

    assert second.connection.id != first.connection.id
    assert {c.port for c in store.list_acp_connections()} == {8802, 8803}


async def test_restart_bootstraps_every_stored_listener_again(paths):
    """A fresh manager over the same ``paths`` (what a process restart looks like)
    restores every listener the store still names."""
    pid = ProfileRegistry(paths).create_profile("Work", "#336699").id
    port = _free_port()
    listener = ConnectionStore(paths).create_acp_connection(pid, port=port)

    first = make_manager(paths, agent_factory=_pong_factory())
    await first.start()
    await first.close()

    second = make_manager(paths, agent_factory=_pong_factory())
    await second.start()
    try:
        assert listener.connection.id in second.acp_listeners
        await _wait_listening(port)
    finally:
        await second.close()


async def test_manager_listener_installs_owner_side_approvals_once(paths):
    """The shared runtime agent gets the approvals middleware exactly once,
    surviving a stop/start cycle without stacking duplicates — and Gateway turns keep
    their own per-turn manager (setdefault, not override)."""
    from assistant.acp.approvals import _OwnerApprovalMiddleware

    pid = ProfileRegistry(paths).create_profile("Work", "#336699").id
    port = _free_port()
    listener = ConnectionStore(paths).create_acp_connection(pid, port=port)

    mgr = make_manager(paths, agent_factory=_pong_factory())
    await mgr.start()
    try:
        cid = listener.connection.id
        agent = mgr.get(pid).require_gateway().require_agent()

        def count() -> int:
            return sum(
                1
                for m in agent.middleware
                if getattr(getattr(m, "middleware", m), "cls", None) is _OwnerApprovalMiddleware
            )

        assert count() == 1
        assert await mgr.stop_acp_listener(cid)
        ok, reason = await mgr.start_acp_listener(cid)
        assert ok, reason
        assert count() == 1
    finally:
        await mgr.close()


async def test_manager_listener_sessions_persist_as_chats(paths, monkeypatch):
    """A conversation through a manager-booted listener lands as a Chat whose Peer is
    attributed to the stored Connection's real id."""
    from assistant.peers import PeerStore

    monkeypatch.setattr("assistant.acp.auth.profile_has_credentials", lambda config, env: True)
    pid = ProfileRegistry(paths).create_profile("Work", "#336699").id
    port = _free_port()
    listener = ConnectionStore(paths).create_acp_connection(pid, port=port, token="s3cret")

    mgr = make_manager(paths, agent_factory=_pong_factory())
    await mgr.start()
    try:
        await _wait_listening(port)
        remote = ACPRemoteConfig(
            url=f"ws://127.0.0.1:{port}/acp",
            headers={"Authorization": "Bearer s3cret"},
            expose_tools=False,
            permission_policy="deny",
        )
        client = Ag2Agent(name="chats-client", config=remote)
        reply = await client.ask("hi there")
        assert "pong" in str(await reply.content()).lower()
    finally:
        await mgr.close()

    peers = [
        p
        for p in PeerStore(paths).list_peers()
        if p.platform == "acp" and p.connection == listener.connection.id
    ]
    assert peers, "no Peer attributed to the stored listener's Connection id"
    assert peers[0].profile == pid
    assert peers[0].chat, "Peer has no Chat attached"


# --- the acp (stdio) CLI's --connection resolver (tested directly, not the typer command) ---


def test_stdio_connection_target_resolves_a_listener_by_id(paths):
    pid = ProfileRegistry(paths).create_profile("Work", "#336699").id
    store = ConnectionStore(paths)
    listener = store.create_acp_connection(pid, name="Desktop", port=8802)

    assert stdio_connection_target(store, listener.connection.id) == (pid, listener.connection.id)


def test_stdio_connection_target_resolves_a_listener_by_display_name(paths):
    pid = ProfileRegistry(paths).create_profile("Work", "#336699").id
    store = ConnectionStore(paths)
    listener = store.create_acp_connection(pid, name="Desktop", port=8802)

    assert stdio_connection_target(store, "Desktop") == (pid, listener.connection.id)


def test_stdio_connection_target_accepts_a_portless_record(paths):
    """A stdio listener is exactly a record with no port, which ``acp-serve`` refuses."""
    pid = ProfileRegistry(paths).create_profile("Work", "#336699").id
    store = ConnectionStore(paths)
    listener = store.create_acp_connection(pid, name="Editor")

    assert listener.port is None
    assert stdio_connection_target(store, "Editor") == (pid, listener.connection.id)


def test_stdio_connection_target_rejects_an_unknown_connection(paths):
    with pytest.raises(UnknownAcpConnection, match="no-such-listener"):
        stdio_connection_target(ConnectionStore(paths), "no-such-listener")
