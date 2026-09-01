"""ACP chats surface their origin in the ordinary chat list, and the
owner can close a live session from the gateway — a kill switch that drops the
client but keeps the Chat (ADR 0034).

Drives a real ``ag2.acp`` session in-process (``ag2.acp.testing.connect``)
against a ``ChatTrackingACPAgent``, wired the same way tests/test_acp_chats.py
does, then reads it back through the ACTUAL gateway HTTP routes (a running
``create_app`` over the same data dir) — proving the origin join and the close
endpoint work over the real API surface, not a second, parallel record.
"""

import acp
import pytest
from ag2 import Agent
from ag2.acp import SessionConfig
from ag2.acp.testing import connect
from ag2.testing import TestConfig
from fastapi.testclient import TestClient

from assistant.acp.chats import ChatBackedStorage, ChatTrackingACPAgent
from assistant.connections import ConnectionStore
from assistant.gateway.profile_manager import resolve_active_profile
from tests.support.apps import api, make_profile_app


def _served(paths, pid: str, *, connection_id: str = "acp:test"):
    """A ``ChatTrackingACPAgent`` wired like ``serve.py``'s warm path, over a
    scripted ``TestConfig`` agent, writing into the SAME data dir the running
    gateway app below reads from."""
    _, config, _ = resolve_active_profile(pid, paths=paths, env={})
    storage = ChatBackedStorage(paths=paths, data_dir=config.data_dir, profile=pid)
    agent = Agent(name="acp-chat-route-test", config=TestConfig("42"))
    return ChatTrackingACPAgent(
        agent,
        name="AG2 Assistant",
        version="0.0.0-test",
        sessions=SessionConfig(storage=storage),
        chat_storage=storage,
        connection_id=connection_id,
    )


@pytest.fixture
def app_over(paths):
    """A running gateway app + its profile id over ``paths``, persisted so its
    chat list reads the same ``chats.db`` an ACP session below writes into."""
    app, pid = make_profile_app(paths, persist=True)
    with TestClient(app) as client:
        yield client, pid


async def test_list_and_transcript_carry_origin_for_a_live_acp_chat(app_over, paths):
    client, pid = app_over
    listener = ConnectionStore(paths).create_acp_connection(pid, name="Space")
    served = _served(paths, pid, connection_id=listener.connection.id)

    async with connect(served) as (acp_client, _recorder):
        session = await acp_client.new_session(cwd=".")
        await acp_client.prompt(session_id=session.session_id, prompt=[acp.text_block("hi")])

        rows = client.get(api(pid, "/chats")).json()["chats"]
        assert len(rows) == 1
        row = rows[0]
        chat_id = row["chat_id"]
        assert row["origin_platform"] == "acp"
        assert row["origin_name"] == "Space"
        assert row["origin_live"] is True

        transcript = client.get(api(pid, f"/chats/{chat_id}")).json()
        assert transcript["origin_platform"] == "acp"
        assert transcript["origin_name"] == "Space"
        assert transcript["origin_live"] is True

        r = client.post(api(pid, f"/chats/{chat_id}/acp/close"))
        assert r.status_code == 200, r.text
        assert r.json() == {"ok": True}

        # The session is gone: the client can no longer prompt it.
        with pytest.raises(Exception):  # noqa: B017 - the SDK's own protocol error
            await acp_client.prompt(session_id=session.session_id, prompt=[acp.text_block("again")])

    # The Chat survives the close — only the live badge drops.
    rows = client.get(api(pid, "/chats")).json()["chats"]
    assert len(rows) == 1
    assert rows[0]["chat_id"] == chat_id
    assert rows[0]["origin_name"] == "Space"
    assert rows[0]["origin_live"] is False
    transcript = client.get(api(pid, f"/chats/{chat_id}")).json()
    assert [m["role"] for m in transcript["messages"]] == ["user", "agent"]


async def test_close_is_404_when_the_chat_is_not_acp(app_over):
    client, pid = app_over
    r = client.post(api(pid, "/chats/not-a-real-chat/acp/close"))
    assert r.status_code == 404


async def test_close_is_409_once_the_session_has_already_ended(app_over, paths):
    client, pid = app_over
    served = _served(paths, pid)

    async with connect(served) as (acp_client, _recorder):
        session = await acp_client.new_session(cwd=".")
        await acp_client.prompt(session_id=session.session_id, prompt=[acp.text_block("hi")])
        chat_id = client.get(api(pid, "/chats")).json()["chats"][0]["chat_id"]

        first = client.post(api(pid, f"/chats/{chat_id}/acp/close"))
        assert first.status_code == 200, first.text
        second = client.post(api(pid, f"/chats/{chat_id}/acp/close"))
        assert second.status_code == 409


async def test_origin_name_falls_back_to_the_raw_connection_id_when_unregistered(app_over, paths):
    client, pid = app_over
    served = _served(paths, pid, connection_id="acp:unregistered")

    async with connect(served) as (acp_client, _recorder):
        session = await acp_client.new_session(cwd=".")
        await acp_client.prompt(session_id=session.session_id, prompt=[acp.text_block("hi")])

    row = client.get(api(pid, "/chats")).json()["chats"][0]
    assert row["origin_platform"] == "acp"
    assert row["origin_name"] == "acp:unregistered"


def test_a_plain_chat_carries_no_origin_fields(app_over):
    client, pid = app_over
    r = client.post(api(pid, "/message"), json={"text": "hello", "chat_id": "abc123"})
    assert r.status_code == 200, r.text

    row = client.get(api(pid, "/chats")).json()["chats"][0]
    assert "origin_platform" not in row
    assert "origin_name" not in row
    assert "origin_live" not in row

    transcript = client.get(api(pid, "/chats/abc123")).json()
    assert "origin_platform" not in transcript
    assert "origin_name" not in transcript
    assert "origin_live" not in transcript
