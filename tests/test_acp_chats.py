"""ACP sessions persist as ordinary Chats (ADR 0034).

Drives real ``ag2.acp`` connections in-process (``ag2.acp.testing.connect``)
against a ``ChatTrackingACPAgent`` wired the same way ``serve.py``'s warm path
wires it, over a ``TestConfig`` agent — no real LLM. Reads back through a
second, independent ``Gateway`` bound to the same profile data dir: the one
the web UI's chat list and transcript endpoints read from, proving these are
real Chats and not a parallel record.
"""

import acp
from ag2 import Agent
from ag2.acp import SessionConfig
from ag2.acp.testing import connect
from ag2.events import ModelRequest
from ag2.testing import TestConfig

from assistant.acp.chats import ChatBackedStorage, ChatTrackingACPAgent
from assistant.gateway.core import Gateway
from assistant.gateway.profile_manager import resolve_active_profile
from assistant.peers import PeerStore
from assistant.profiles import ProfileRegistry
from tests.support.fakes import fake_agent_factory


def _new_profile(paths, name: str = "acp-profile") -> str:
    return ProfileRegistry(paths).create_profile(name, "#336699").id


def _served(paths, pid: str, *, connection_id: str = "acp:test"):
    """A ``ChatTrackingACPAgent`` wired like ``serve.py``'s warm path, over a
    scripted ``TestConfig`` agent. Returns ``(agent, storage, config)``."""
    _, config, _ = resolve_active_profile(pid, paths=paths, env={})
    storage = ChatBackedStorage(paths=paths, data_dir=config.data_dir, profile=pid)
    agent = Agent(name="acp-test", config=TestConfig("42"))
    served = ChatTrackingACPAgent(
        agent,
        name="AG2 Assistant",
        version="0.0.0-test",
        sessions=SessionConfig(storage=storage, retain_history=True),
        chat_storage=storage,
        connection_id=connection_id,
    )
    return served, storage, config


async def _reader_gateway(config) -> Gateway:
    """A second Gateway over the same data dir — what the web UI's chat list reads."""
    gw = Gateway(config=config, memory=False, platform="test", agent_factory=fake_agent_factory())
    await gw.start()
    return gw


async def test_prompt_answer_produces_a_real_chat(paths):
    pid = _new_profile(paths)
    served, _storage, config = _served(paths, pid)

    async with connect(served) as (client, _recorder):
        session = await client.new_session(cwd=".")
        response = await client.prompt(
            session_id=session.session_id,
            prompt=[acp.text_block("what is the answer")],
        )
    assert response.stop_reason == "end_turn"

    gw = await _reader_gateway(config)
    chats = await gw.list_chats()
    assert len(chats) == 1
    chat_id = chats[0]["chat_id"]
    assert chat_id.startswith("acp-")  # scheme: acp-<8 hex>, minted by PeerStore.start_chat

    messages = await gw.transcript(chat_id)
    assert [m["role"] for m in messages] == ["user", "agent"]
    assert messages[0]["text"] == "what is the answer"
    assert messages[1]["text"] == "42"

    peer = PeerStore(paths).get_peer("acp:test", session.session_id)
    assert peer is not None
    assert peer.platform == "acp"
    assert peer.profile == pid
    assert peer.chat == chat_id
    assert peer.chats == [chat_id]


async def test_session_new_alone_creates_no_chat(paths):
    pid = _new_profile(paths)
    served, _storage, config = _served(paths, pid)

    async with connect(served) as (client, _recorder):
        await client.new_session(cwd=".")

    gw = await _reader_gateway(config)
    assert await gw.list_chats() == []
    assert PeerStore(paths).list_peers() == []


async def test_drop_history_keeps_the_chat_and_transcript(paths):
    """One of the three upstream drop_history call sites (sessions.py's
    SessionStore.close, via _discard) — closing a session must detach the live
    stream, never delete the Chat."""
    pid = _new_profile(paths)
    served, storage, config = _served(paths, pid)

    async with connect(served) as (client, _recorder):
        session = await client.new_session(cwd=".")
        await client.prompt(session_id=session.session_id, prompt=[acp.text_block("hi")])
        agent_session = await served.sessions.get(session.session_id)
        stream_id = agent_session.stream_id
        await served.sessions.close(session.session_id)

    # The live stream is gone...
    assert list(await storage.get_history(stream_id)) == []
    # ...but the Chat and its transcript survive.
    gw = await _reader_gateway(config)
    chats = await gw.list_chats()
    assert len(chats) == 1
    messages = await gw.transcript(chats[0]["chat_id"])
    assert [m["role"] for m in messages] == ["user", "agent"]


async def test_reconnect_creates_a_second_chat(paths):
    pid = _new_profile(paths)
    served, _storage, config = _served(paths, pid)

    async def one_turn(text: str) -> str:
        async with connect(served) as (client, _recorder):
            session = await client.new_session(cwd=".")
            await client.prompt(session_id=session.session_id, prompt=[acp.text_block(text)])
            return session.session_id

    first_session = await one_turn("first")
    second_session = await one_turn("second")
    assert first_session != second_session

    gw = await _reader_gateway(config)
    chats = await gw.list_chats()
    assert len(chats) == 2
    chat_ids = {c["chat_id"] for c in chats}

    peers = PeerStore(paths).list_peers()
    assert {p.chat_id for p in peers} == {first_session, second_session}
    assert {p.chat for p in peers} == chat_ids
    for peer in peers:
        assert peer.platform == "acp"
        assert peer.profile == pid
        assert peer.chats == [peer.chat]  # each ACP session births exactly one Chat


async def test_turns_mirror_onto_the_gateway_stream(paths):
    """The web UI replays and live-streams a chat from the GATEWAY's stream, which
    ACP turns never touch on their own — the storage's ``mirror`` seam feeds it
    (one ModelRequest and one final ModelResponse per turn, tagged with the Chat)."""
    from ag2.events import ModelRequest, ModelResponse

    pid = _new_profile(paths)
    _, config, _ = resolve_active_profile(pid, paths=paths, env={})
    mirrored: list[tuple[str, object]] = []

    async def mirror(chat_id: str, event) -> None:
        mirrored.append((chat_id, event))

    storage = ChatBackedStorage(paths=paths, data_dir=config.data_dir, profile=pid, mirror=mirror)
    served = ChatTrackingACPAgent(
        Agent(name="acp-test", config=TestConfig("42")),
        name="AG2 Assistant",
        version="0.0.0-test",
        sessions=SessionConfig(storage=storage),
        chat_storage=storage,
        connection_id="acp:test",
    )
    async with connect(served) as (client, _recorder):
        session = await client.new_session(cwd=".")
        await client.prompt(session_id=session.session_id, prompt=[acp.text_block("hello")])

    kinds = [type(e).__name__ for _, e in mirrored]
    assert kinds == ["ModelRequest", "ModelResponse"]
    chat_ids = {cid for cid, _ in mirrored}
    assert len(chat_ids) == 1 and next(iter(chat_ids)).startswith("acp-")
    req, resp = mirrored[0][1], mirrored[1][1]
    assert isinstance(req, ModelRequest) and isinstance(resp, ModelResponse)
    assert resp.content == "42"


async def test_session_loads_into_a_storage_that_never_served_it(paths):
    """The stdio shape: every turn runs in a fresh process, so the storage that
    answers ``session/load`` is never the one that wrote the history.

    A second ``ChatBackedStorage`` over the same profile data dir stands in for
    that new process. Upstream refuses any id whose ``get_history`` reads back
    empty, so a memory-only slice would fail the load outright.
    """
    pid = _new_profile(paths)
    first, _first_storage, config = _served(paths, pid)

    async with connect(first) as (client, _recorder):
        session = await client.new_session(cwd=".")
        session_id = session.session_id
        await client.prompt(session_id=session_id, prompt=[acp.text_block("remember me")])

    # A brand-new agent + storage pair: nothing of the first is carried over.
    second, second_storage, _ = _served(paths, pid)
    async with connect(second) as (client, _recorder):
        await client.load_session(session_id=session_id, cwd=".")
        await client.prompt(session_id=session_id, prompt=[acp.text_block("still there?")])
        agent_session = await second.sessions.get(session_id)

    replayed = list(await second_storage.get_history(agent_session.stream_id))
    assert [type(e).__name__ for e in replayed] == [
        "ModelRequest",
        "ModelResponse",
        "ModelRequest",
        "ModelResponse",
    ]
    assert _request_texts(replayed) == ["remember me", "still there?"]

    # Both turns land in the one Chat the first connection created.
    gw = await _reader_gateway(config)
    chats = await gw.list_chats()
    assert len(chats) == 1
    messages = await gw.transcript(chats[0]["chat_id"])
    assert [m["role"] for m in messages] == ["user", "agent", "user", "agent"]


def _request_texts(events) -> list[str]:
    return [
        "\n".join(p.content for p in e.parts if getattr(p, "content", None))
        for e in events
        if isinstance(e, ModelRequest)
    ]
