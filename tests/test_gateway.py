"""Tests for the AG2 Assistant gateway and its REST/WebSocket facade.

Unit tests stub the agent so they run without an LLM. Integration tests exercise
the real agent end-to-end. The REST facade is now profile-scoped: the app is built
around a ``ProfileManager`` with one profile and routes live under
``/api/p/{pid}/…`` (see ``conftest.make_profile_app`` / the ``profile_app`` fixture).
"""

import asyncio
import base64

import ag2.testing
import pytest
from ag2.context import ConversationContext
from ag2.events import (
    ModelMessage,
    ModelMessageChunk,
    ModelResponse,
    ToolCallEvent,
    ToolCallsEvent,
)
from ag2.knowledge.constants import LOG_PREFIX
from fastapi.testclient import TestClient
from httpx import ASGITransport, AsyncClient
from starlette.websockets import WebSocketDisconnect

import assistant.onboarding as onboarding
from assistant import codex_auth
from assistant.agent import model_config
from assistant.codex_auth import CodexAuth
from assistant.config import Config, load_config, write_yaml
from assistant.connections import ConnectionStore
from assistant.events import Attachment, TurnCancelled
from assistant.gateway.app import _allowed_origins, _decode_attachments, _origin_ok, create_app
from assistant.gateway.core import Gateway
from assistant.hitl import GatewayAsker, HitlServer
from assistant.hitl.base import Question
from assistant.llm_configs import TYPES, LlmConfigStore
from assistant.memory import PROFILE_PATH, build_profile_store
from assistant.onboarding import STEPS, needs_onboarding
from assistant.permissions import DENY
from assistant.profiles import ProfileRegistry
from assistant.secrets import SecretStore
from tests.support.apps import api, make_manager, make_profile_app, write_codex_session
from tests.support.fakes import (
    FakeAgent,
    FakeReply,
    FakeRunMixin,
    fake_agent_factory,
    fake_title_factory,
)


def _gateway(paths, *, agent=None, memory=False, data_dir=None, **kwargs):
    """A Gateway over the isolated layout whose agent is a fake (no LLM)."""
    config = (
        Config.for_paths(paths) if data_dir is None else Config.for_paths(paths, data_dir=data_dir)
    )
    return Gateway(
        config=config,
        memory=memory,
        agent_factory=fake_agent_factory(agent),
        **kwargs,
    )


class RecordingAsker:
    """An asker that records every question and skips them all — the onboarding
    interview treats "" as a skip, so nothing is written and no .env is touched."""

    def __init__(self):
        self.asked: list[str] = []

    async def ask(self, question, timeout=None):
        self.asked.append(getattr(question, "text", question))
        return ""


@pytest.fixture
def fake_gateway(paths):
    """A Gateway whose agent is a deterministic fake (no LLM, no persistence)."""

    gw = _gateway(paths, persist=False)
    gw._agent = FakeAgent()
    return gw


async def test_send_message_returns_reply(fake_gateway):
    reply = await fake_gateway.send_message("hello", chat_id="s1")
    assert reply == "echo[1]: hello"


async def test_forwarding_events_passes_structured_events_not_transcript(fake_gateway):
    """`_forwarding_events` forwards the agent's structured events verbatim
    (so a voice client folds them with the text reducer) while OMITTING the
    conversation events it renders itself as transcript — and always unsubscribes."""

    captured: dict = {}

    class _Stream:  # mimics MemoryStream's subscribe/unsubscribe + event delivery
        def subscribe(self, fn):
            captured["fn"] = fn
            return "sub-1"

        def unsubscribe(self, sid):
            captured["unsub"] = sid

    forwarded: list = []

    async def on_event(event):
        forwarded.append(event)

    batch = ToolCallsEvent(calls=[ToolCallEvent(id="a", name="write_file", arguments="{}")])

    async def drive():  # stands in for the task driving AgentRun.result()
        await captured["fn"](batch)
        await captured["fn"](ModelMessageChunk(content="spoken words"))  # transcript → omitted
        await captured["fn"](ModelResponse(message=ModelMessage(content="spoken")))  # → omitted
        return FakeReply("done")

    reply = await fake_gateway._forwarding_events(
        _Stream(), asyncio.ensure_future(drive()), on_event
    )

    assert reply.body == "done"
    # only the structured event is forwarded; conversation events are the voice
    # channel's own to render, so they don't double up as folded bubbles.
    assert forwarded == [batch]
    assert captured["unsub"] == "sub-1"  # always unsubscribed


async def test_gateway_auto_onboards_once(paths):
    """First message with an asker triggers the real interview exactly once — the
    universal store is empty, so the gate is open."""

    gw = _gateway(paths, memory=True)  # onboarding only runs when memory is on
    gw._agent = FakeAgent()
    asker = RecordingAsker()
    await gw.send_message("hi", chat_id="s1", asker=asker)
    await gw.send_message("again", chat_id="s1", asker=asker)
    assert asker.asked.count(STEPS[0].question.text) == 1  # onboarded once, not per message


async def test_gateway_skips_onboarding_without_asker(paths):
    """Without an asker there is nothing to interview through, so the gate stays open
    and the NEXT message (with an asker) still runs it."""

    gw = _gateway(paths, memory=True)
    gw._agent = FakeAgent()
    await gw.send_message("hi", chat_id="s1")  # no asker → no onboarding
    asker = RecordingAsker()
    await gw.send_message("again", chat_id="s1", asker=asker)
    assert asker.asked  # still pending, so it ran now


async def test_chat_keeps_multi_turn_history(fake_gateway):
    await fake_gateway.send_message("first", chat_id="s1")
    reply = await fake_gateway.send_message("second", chat_id="s1")
    # The chain grew to length 2, proving history is threaded.
    assert reply == "echo[2]: second"


async def test_chats_are_isolated(fake_gateway):
    await fake_gateway.send_message("a", chat_id="s1")
    await fake_gateway.send_message("b", chat_id="s1")
    reply = await fake_gateway.send_message("c", chat_id="s2")
    # s2 starts a fresh chain (length 1), unaffected by s1.
    assert reply == "echo[1]: c"


async def test_only_the_completed_turn_is_mirrored(fake_gateway):
    """The mirror gets the user's message and the final answer, once — never the
    deltas, tool calls and produce events the turn emitted on the way (ADR 0020)."""
    mirrored: list = []

    async def mirror(chat_id, text, reply, *, origin="", files=()):
        mirrored.append((chat_id, text, reply, origin, files))

    fake_gateway.set_mirror(mirror)
    await fake_gateway.send_message("hello", chat_id="s1", origin="telegram:42")

    assert mirrored == [("s1", "hello", "echo[1]: hello", "telegram:42", ())]


async def test_the_names_of_attached_files_reach_the_mirror(fake_gateway):
    """The mirror names a file instead of carrying it, so it needs what it is called."""
    mirrored: list = []

    async def mirror(chat_id, text, reply, *, origin="", files=()):
        mirrored.append(files)

    fake_gateway.set_mirror(mirror)
    await fake_gateway.send_message("look", chat_id="s1", attachment_names=("report.pdf",))

    assert mirrored == [("report.pdf",)]


async def test_a_mirror_that_fails_does_not_fail_the_turn(fake_gateway):
    """A platform that is down loses the mirror, not the user's answer."""

    async def mirror(*args, **kwargs):
        raise RuntimeError("telegram is down")

    fake_gateway.set_mirror(mirror)
    assert await fake_gateway.send_message("hello", chat_id="s1") == "echo[1]: hello"


def test_status_shape(fake_gateway):
    status = fake_gateway.status()
    assert status["status"] == "ok"
    assert "model" in status
    assert status["chats"] == 0


async def test_transcript_persists_across_instances(paths, tmp_path):
    """A new Gateway over the same data dir sees prior chats (resumable)."""

    gw = _gateway(paths, data_dir=tmp_path)
    await gw.start()
    await gw.send_message("hello there", chat_id="s1")
    await gw.send_message("again", chat_id="s1")
    await gw.close()

    gw2 = _gateway(paths, data_dir=tmp_path)
    await gw2.start()
    turns = await gw2.transcript("s1")
    assert [m["role"] for m in turns] == ["user", "agent", "user", "agent"]
    assert turns[0]["text"] == "hello there"

    listed = await gw2.list_chats()
    s1 = next(s for s in listed if s["chat_id"] == "s1")
    assert s1["turns"] == 2
    assert s1["preview"] == "hello there"


async def test_replay_and_chat_reads_share_one_sqlite_connection(paths, tmp_path):
    """Hydrating a chat and reading its metadata may happen in the same request burst."""
    from assistant.storage import SerialStore

    first = _gateway(paths, data_dir=tmp_path)
    await first.start()
    await first.send_message("hello", chat_id="s1")
    await first.emit_event("s1", Attachment("/tmp/demo.txt", name="demo.txt"))
    await first.close()

    second = _gateway(paths, data_dir=tmp_path)
    await second.start()
    assert isinstance(second._event_store, SerialStore)

    stream, transcript, chats = await asyncio.gather(
        second.stream_for("s1"), second.transcript("s1"), second.list_chats()
    )
    assert len(await stream.history.get_events()) > 0
    assert transcript[0]["text"] == "hello"
    assert any(chat["chat_id"] == "s1" for chat in chats)
    await second.close()


async def test_delete_chat_removes_transcript_and_event_log(paths, tmp_path):
    """Deleting a chat drops BOTH artifacts — the display transcript AND the AG2
    event log — so it neither lists nor resumes, even on a fresh Gateway."""

    gw = _gateway(paths, data_dir=tmp_path)
    await gw.start()
    await gw.send_message("keep me", chat_id="keep")
    await gw.send_message("delete me", chat_id="gone")
    # both artifacts exist before delete
    assert await gw._event_store.exists(gw._transcript_path("gone"))
    assert await gw._event_store.exists(f"{LOG_PREFIX}gone.jsonl")

    assert await gw.delete_chat("gone") is True
    assert await gw.delete_chat("gone") is False  # idempotent: nothing left to remove

    # gone from the list; both on-disk artifacts removed; other chat untouched
    assert {s["chat_id"] for s in await gw.list_chats()} == {"keep"}
    assert not await gw._event_store.exists(gw._transcript_path("gone"))
    assert not await gw._event_store.exists(f"{LOG_PREFIX}gone.jsonl")
    await gw.close()

    # a fresh Gateway over the same data dir does not resurrect it
    gw2 = _gateway(paths, data_dir=tmp_path)
    await gw2.start()
    assert {s["chat_id"] for s in await gw2.list_chats()} == {"keep"}
    assert await gw2.transcript("gone") == []


# --- in-flight chat stub (bug: a chat mid-turn must be listable so it survives
#     a profile switch, which is a full-page nav that discards local page state) ---


class _SlowAgent(FakeRunMixin):
    """A fake agent whose turn blocks on an event, so a test can observe state while
    a turn is *in flight* (simulating a long agentic turn: web searches etc.)."""

    def __init__(self):
        self.gate = asyncio.Event()
        self.tools = []
        self._counts: dict = {}

    async def ask(self, *msg, stream=None, **kwargs) -> FakeReply:
        await self.gate.wait()  # hold the turn open until the test releases it
        sid = getattr(stream, "id", "default")
        self._counts[sid] = self._counts.get(sid, 0) + 1
        return FakeReply(f"echo[{self._counts[sid]}]: {msg[0]}")


async def _persistent_gateway(paths, tmp_path, agent, **kwargs):

    gw = _gateway(paths, agent=agent, data_dir=tmp_path, **kwargs)
    await gw.start()
    gw._agent = agent
    return gw


async def test_inflight_chat_listed_before_completion(paths, tmp_path):
    """(a) A chat is listed with the user-message preview *while* its (slow) turn
    is still running — the stub is written the instant the message is accepted."""
    slow = _SlowAgent()
    gw = await _persistent_gateway(paths, tmp_path, slow)
    turn = asyncio.create_task(gw.send_message("search the web for X", chat_id="live"))
    try:
        # Let send_message reach the (blocked) agent turn.
        for _ in range(50):
            if await gw._event_store.exists(gw._transcript_path("live")):
                break
            await asyncio.sleep(0.01)

        listed = await gw.list_chats()
        s = next(s for s in listed if s["chat_id"] == "live")
        assert s["preview"] == "search the web for X"  # user message shows immediately
        assert s["turns"] == 0  # no completed exchange yet
        assert s["title"] == ""  # not yet named → drawer falls back to the preview

        # The display transcript already carries the pending user message.
        msgs = await gw.transcript("live")
        assert msgs == [{"role": "user", "text": "search the web for X"}]
    finally:
        slow.gate.set()
        await turn
    await gw.close()


async def test_inflight_stub_completed_in_place_no_duplicate(paths, tmp_path):
    """(b)+(c) After the turn completes the entry has the reply, one turn, a title,
    and the user message is NOT duplicated by the completion write; a second turn
    threads on without duplicating either (multi-turn stub is a no-op)."""

    gw = await _persistent_gateway(
        paths, tmp_path, FakeAgent(), title_factory=fake_title_factory("Named Chat")
    )
    await gw.send_message("first question", chat_id="s1")
    for _ in range(50):  # title generation is fire-and-forget
        listed = await gw.list_chats()
        if next(x for x in listed if x["chat_id"] == "s1")["title"]:
            break
        await asyncio.sleep(0.01)

    msgs = await gw.transcript("s1")
    # exactly one user + one agent — the stub was completed in place, not re-appended.
    assert msgs == [
        {"role": "user", "text": "first question"},
        {"role": "agent", "text": "echo[1]: first question"},
    ]
    listed = await gw.list_chats()
    s1 = next(x for x in listed if x["chat_id"] == "s1")
    assert s1["turns"] == 1
    assert s1["title"] == "Named Chat"

    # Second turn: no stub duplication, history keeps growing normally.
    await gw.send_message("second question", chat_id="s1")
    msgs = await gw.transcript("s1")
    assert [m["text"] for m in msgs] == [
        "first question",
        "echo[1]: first question",
        "second question",
        "echo[2]: second question",
    ]
    listed = await gw.list_chats()
    assert next(x for x in listed if x["chat_id"] == "s1")["turns"] == 2
    await gw.close()


async def test_inflight_chat_stream_replay_returns_user_event(paths, tmp_path):
    """(d) Reopening an in-flight chat mid-turn replays the user message event, so
    the stream bridge shows the history so far and attaches live. Here the user event
    is emitted onto the chat stream before the (blocked) turn, exactly as the WS
    stream path does for a real message; a fresh bridge open() must replay it."""

    slow = _SlowAgent()
    gw = await _persistent_gateway(paths, tmp_path, slow)
    # Emit a marker event onto the chat stream (persisted + replayable), the way the
    # app's stream handler surfaces the user's turn context before running it.
    await gw.emit_event("live", Attachment("/tmp/x.png", name="x.png"))

    turn = asyncio.create_task(gw.send_message("do the slow thing", chat_id="live"))
    try:
        for _ in range(50):
            if await gw._event_store.exists(gw._transcript_path("live")):
                break
            await asyncio.sleep(0.01)

        # A fresh reader (new bridge) replays the persisted stream so far.
        stream = await gw.stream_for("live")
        events = await stream.history.get_events()
        assert any(type(e).__name__ == "Attachment" for e in events)
    finally:
        # Release AND await the turn: a task still running at teardown races the
        # loop shutdown and flakes unrelated tests.
        slow.gate.set()
        await turn
        await turn
    await gw.close()


# --- REST facade (profile-scoped) ---


def test_chats_rest_endpoints(profile_app):
    client, pid = profile_app
    client.post(api(pid, "/message"), json={"text": "first msg", "chat_id": "u1"})
    chats = client.get(api(pid, "/chats")).json()["chats"]
    assert any(s["chat_id"] == "u1" for s in chats)
    msgs = client.get(api(pid, "/chats/u1")).json()["messages"]
    assert msgs[0]["text"] == "first msg"


def test_rest_message_endpoint(profile_app):
    """The REST facade returns a reply for a posted message (fake agent)."""
    client, pid = profile_app
    health = client.get("/api/health").json()
    assert health["status"] == "ok"

    resp = client.post(api(pid, "/message"), json={"text": "hi there", "chat_id": "u1"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["reply"] == "echo[1]: hi there"
    assert body["chat_id"] == "u1"


def test_unknown_and_archived_profile_status(paths):
    """A prefixed route on an unknown pid 404s; on an archived pid 410s."""

    registry = ProfileRegistry(paths)
    work = registry.create_profile("Work", "#109e91")
    registry.profile_dir(work.id).mkdir(parents=True, exist_ok=True)
    keep = registry.create_profile("Personal", "#f95339")  # so archive isn't the last
    registry.profile_dir(keep.id).mkdir(parents=True, exist_ok=True)

    app = create_app(make_manager(paths))
    with TestClient(app) as client:
        assert client.get(api("ghost", "/chats")).status_code == 404
        assert client.get(api(work.id, "/chats")).status_code == 200
        # archive work (with a replacement default if needed), then it 410s
        client.request("DELETE", f"/api/profiles/{work.id}", json={"new_default": keep.id})
        assert client.get(api(work.id, "/chats")).status_code == 410


def test_mcp_settings_endpoints(profile_app):
    client, pid = profile_app
    assert client.get(api(pid, "/settings")).json()["mcp_servers"] == []

    resp = client.post(
        api(pid, "/settings/mcp"),
        json={
            "name": "local",
            "command": "__missing_mcp_server__",
            "args": "--flag",
            "env": "TOKEN=secret",
        },
    )
    assert resp.status_code == 200
    server = resp.json()["server"]
    assert server["env_keys"] == ["TOKEN"]
    assert "env" not in server

    listed = client.get(api(pid, "/settings")).json()["mcp_servers"]
    assert listed[0]["name"] == "local"
    assert "env" not in listed[0]

    health = client.post(api(pid, "/settings/mcp/local/health"))
    assert health.status_code == 200
    assert health.json()["ok"] is False

    assert client.delete(api(pid, "/settings/mcp/local")).json()["ok"] is True
    assert client.get(api(pid, "/settings")).json()["mcp_servers"] == []
    assert client.delete(api(pid, "/settings/mcp/local")).status_code == 404


def test_focuses_endpoint_saves_appears_in_settings_and_reloads(paths):
    """POST settings/focuses persists the (normalised) focuses, surfaces them in GET
    settings, and reference-swap reloads the runtime so the context line takes effect."""

    registry = ProfileRegistry(paths)
    meta = registry.create_profile("Work", "#109e91")
    registry.profile_dir(meta.id).mkdir(parents=True, exist_ok=True)
    built: list = []
    manager = make_manager(paths, agent_factory=fake_agent_factory(built=built))
    app = create_app(manager)
    with TestClient(app) as client:
        pid = meta.id
        assert client.get(api(pid, "/settings")).json()["focuses"] == []

        built.clear()

        # client sends lowercase slugs; junk is dropped, order kept
        resp = client.post(
            api(pid, "/settings/focuses"),
            json={"focuses": ["Coding", "research", "not a slug!"]},
        )
        assert resp.status_code == 200
        assert resp.json() == {"ok": True, "focuses": ["coding", "research"]}
        assert [cfg.data_dir.name for cfg in built] == [pid]  # context change → reloaded

        assert client.get(api(pid, "/settings")).json()["focuses"] == ["coding", "research"]

        # clearing persists too
        assert (
            client.post(api(pid, "/settings/focuses"), json={"focuses": []}).json()["focuses"] == []
        )
        assert client.get(api(pid, "/settings")).json()["focuses"] == []


def test_reply_timeout_endpoint_saves_appears_in_settings_and_reloads(paths):

    registry = ProfileRegistry(paths)
    meta = registry.create_profile("Work", "#109e91")
    registry.profile_dir(meta.id).mkdir(parents=True, exist_ok=True)
    built: list = []
    manager = make_manager(paths, agent_factory=fake_agent_factory(built=built))
    app = create_app(manager)
    with TestClient(app) as client:
        pid = meta.id
        assert client.get(api(pid, "/settings")).json()["reply_timeout_s"] == 600.0

        built.clear()
        response = client.post(api(pid, "/settings/reply-timeout"), json={"reply_timeout_s": 480})
        assert response.json() == {"ok": True, "reply_timeout_s": 480.0}
        assert [cfg.data_dir.name for cfg in built] == [pid]
        assert client.get(api(pid, "/settings")).json()["reply_timeout_s"] == 480.0

        assert (
            client.post(
                api(pid, "/settings/reply-timeout"), json={"reply_timeout_s": 0}
            ).status_code
            == 422
        )


def test_fs_list_endpoint_lists_subdirs(paths, tmp_path):

    browse = tmp_path / "browse"
    (browse / "alpha").mkdir(parents=True)
    (browse / "beta").mkdir()
    (browse / "file.txt").write_text("x")

    app, _pid = make_profile_app(paths)
    with TestClient(app) as client:
        r = client.get("/api/fs/list", params={"path": str(browse)}).json()
        assert r["ok"] is True
        assert [d["name"] for d in r["dirs"]] == ["alpha", "beta"]

        bad = client.get("/api/fs/list", params={"path": str(browse / "missing")}).json()
        assert bad["ok"] is False


def test_fs_mkdir_creates_subfolder_and_returns_absolute_path(paths, tmp_path):
    """The picker creates one subfolder in the folder it's viewing and gets back an
    ABSOLUTE path — `make_dir` reports a root-relative one, but the picker navigates
    into the result by absolute path."""
    app, _pid = make_profile_app(paths)
    with TestClient(app) as client:
        r = client.post("/api/fs/mkdir", json={"path": str(tmp_path), "name": "reports"})
        assert r.status_code == 200
        assert r.json()["path"] == str(tmp_path / "reports")
        assert (tmp_path / "reports").is_dir()

        # It shows up in the very next listing, so the picker can step into it.
        listed = client.get("/api/fs/list", params={"path": str(tmp_path)}).json()
        assert "reports" in [d["name"] for d in listed["dirs"]]


def test_fs_mkdir_rejects_duplicate_without_clobbering(paths, tmp_path):
    app, _pid = make_profile_app(paths)
    (tmp_path / "taken").mkdir()
    (tmp_path / "taken" / "keep.txt").write_text("still here")
    with TestClient(app) as client:
        r = client.post("/api/fs/mkdir", json={"path": str(tmp_path), "name": "taken"})
        assert r.status_code == 409
        assert r.json()["error"] == "A folder with that name already exists"
    assert (tmp_path / "taken" / "keep.txt").read_text() == "still here"


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("a/b", "Name can't contain slashes"),  # a name, not a path — make_dir would nest
        ("..", "Not a valid folder name"),
        (".hidden", "Names starting with a dot are hidden and won't show here"),
        ("  spaced  ", "Name can't start or end with a space"),
        ("", "Enter a folder name"),
        ("x" * 300, "Name is too long"),
    ],
)
def test_fs_mkdir_rejects_bad_names(paths, tmp_path, name, expected):
    """Every rejection is a 400 carrying a message meant to be shown as-is, and nothing
    is written — notably the over-long name, which used to escape as a 500."""
    app, _pid = make_profile_app(paths)
    before = sorted(p.name for p in tmp_path.iterdir())  # the app puts its data dir here
    with TestClient(app) as client:
        r = client.post("/api/fs/mkdir", json={"path": str(tmp_path), "name": name})
        assert r.status_code == 400
        assert r.json()["error"] == expected
    assert sorted(p.name for p in tmp_path.iterdir()) == before


def test_fs_mkdir_rejects_unreadable_parent(paths, tmp_path):
    app, _pid = make_profile_app(paths)
    with TestClient(app) as client:
        r = client.post("/api/fs/mkdir", json={"path": str(tmp_path / "nope"), "name": "x"})
        assert r.status_code == 400
        assert r.json()["error"] == "not a readable directory"


def test_stream_roundtrip(profile_app):
    """The stream WebSocket replays history (ready) then runs a turn (turn_end)."""
    client, pid = profile_app
    with client.websocket_connect(api(pid, "/stream?chat=w1")) as ws:
        assert ws.receive_json()["type"] == "ready"
        ws.send_json({"text": "ping"})
        assert ws.receive_json()["type"] == "turn_end"


def test_stream_unknown_profile_ws_closed(profile_app):
    """A stream WS on an unknown pid is closed before accept (code 4404)."""

    client, _pid = profile_app
    with pytest.raises(WebSocketDisconnect) as exc:
        with client.websocket_connect(api("ghost", "/stream?chat=x")) as ws:
            ws.receive_json()
    assert exc.value.code == 4404


# --- static / redirects ---


def test_root_redirects_to_app(profile_app):
    """/ and unknown paths redirect to the Svelte app at /app."""
    client, _pid = profile_app
    root = client.get("/", follow_redirects=False)
    assert root.status_code == 307 and root.headers["location"] == "/app/"
    other = client.get("/anything", follow_redirects=False)
    assert other.status_code == 307 and other.headers["location"] == "/app/"
    assert client.get("/api/bogus", follow_redirects=False).status_code == 404


def test_favicon_served(profile_app):
    client, _pid = profile_app
    for path in ("/faviconlight.svg", "/favicondark.svg", "/favicon.ico"):
        r = client.get(path)
        assert r.status_code == 200, path
        assert "svg" in r.headers["content-type"]
        assert "<svg" in r.text


# --- HITL: global dispatcher over per-profile registries ---


async def test_hitl_routes_served_by_gateway(paths):
    """The global /hitl page dispatches to the profile whose registry holds the id;
    the profile-scoped /hitl/pending lists that profile's questions."""

    registry = ProfileRegistry(paths)
    meta = registry.create_profile("Test", "#109e91")
    registry.profile_dir(meta.id).mkdir(parents=True, exist_ok=True)
    manager = make_manager(paths)
    app = create_app(manager)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as client:
        # inside the ASGI lifespan the runtime is booted; register on its registry
        async with app.router.lifespan_context(app):
            runtime = manager.get(meta.id)
            req_id, fut = runtime.hitl.register(Question(text="Proceed?", options=["Yes", "No"]))

            listed = (await client.get(api(meta.id, "/hitl/pending"))).json()["pending"]
            assert any(item["id"] == req_id for item in listed)

            page = await client.get(f"/hitl/{req_id}")
            assert page.status_code == 200
            assert "Proceed?" in page.text

            ok = await client.post(f"/hitl/{req_id}/answer", json={"answer": "Yes"})
            assert ok.json()["ok"] is True
            assert fut.result() == "Yes"  # the answer resolved the pending future

            # unknown id → 404
            missing = await client.post("/hitl/nope/answer", json={"answer": "x"})
            assert missing.status_code == 404


def test_decode_attachments():

    data = base64.b64encode(b"hello").decode()
    out = _decode_attachments([{"name": "a.png", "mime": "image/png", "data": data}])
    assert len(out) == 1
    assert _decode_attachments(None) == []
    assert _decode_attachments([{"name": "x.png", "data": ""}]) == []  # empty → skipped


def test_stream_timeout_sends_error_frame(paths):
    """A turn that exceeds the configured reply timeout surfaces an error frame on the stream WS."""

    class _HangAgent(FakeRunMixin):
        tools = []

        async def ask(self, *a, stream=None, **k):
            await asyncio.Event().wait()  # never returns → triggers wait_for timeout

    write_yaml(paths.config_yaml, {"gateway": {"reply_timeout_s": 0.2}})
    app, pid = make_profile_app(paths, agent_factory=fake_agent_factory(_HangAgent()))
    with TestClient(app) as client:
        with client.websocket_connect(api(pid, "/stream?chat=s1")) as ws:
            while ws.receive_json().get("type") != "ready":
                pass
            ws.send_json({"text": "slow"})
            saw_error = False
            for _ in range(5):
                m = ws.receive_json()
                if m.get("type") == "error":
                    saw_error = True
                    break
                if m.get("type") == "turn_end":
                    break
            assert saw_error


def test_stream_cancel_stops_the_turn(paths):
    """A `cancel` frame stops the running turn: AG2 propagates the cancellation into
    the run, a TurnCancelled event comes back out on the stream, and the turn ends."""

    class _HangAgent(FakeRunMixin):
        tools = []

        def __init__(self):
            self.cancelled = False

        async def ask(self, *a, stream=None, **k):
            try:
                await asyncio.Event().wait()  # runs until someone stops it
            except asyncio.CancelledError:
                self.cancelled = True
                raise

    agent = _HangAgent()
    app, pid = make_profile_app(paths, agent_factory=fake_agent_factory(agent))
    with TestClient(app) as client:
        with client.websocket_connect(api(pid, "/stream?chat=s1")) as ws:
            while ws.receive_json().get("type") != "ready":
                pass
            ws.send_json({"text": "something long"})
            ws.send_json({"type": "cancel"})
            saw_cancelled = False
            for _ in range(8):
                m = ws.receive_json()
                if m.get("event", {}).get("type", "").endswith("TurnCancelled"):
                    saw_cancelled = True
                if m.get("type") == "turn_end":
                    break
            assert saw_cancelled
            assert agent.cancelled  # the cancel reached the turn itself, not just the socket


async def test_feed_message_steers_the_running_turn(fake_gateway):
    """A message sent while a turn runs is enqueued onto that run (AG2 drains it before
    the turn's next model call) instead of starting a second turn."""
    started = asyncio.Event()

    class _SteerableAgent(FakeRunMixin):
        tools = []

        def __init__(self):
            self.turns = 0
            self.release = asyncio.Event()

        async def ask(self, *msg, stream=None, **k):
            self.turns += 1
            started.set()
            await self.release.wait()
            return FakeReply("done")

    agent = _SteerableAgent()
    fake_gateway._agent = agent

    turn = asyncio.ensure_future(fake_gateway.send_message("research widgets", chat_id="s1"))
    await asyncio.wait_for(started.wait(), timeout=1)

    assert await fake_gateway.feed_message("focus on 2026", chat_id="s1") is True
    stream = await fake_gateway.stream_for("s1")
    # It lands in the run's inbox — the running turn's next model call drains it.
    assert stream.pending_messages
    assert "focus on 2026" in str(stream.pending_messages[0].parts)

    agent.release.set()
    assert await turn == "done"
    assert agent.turns == 1  # steered the turn in flight; no second one was started


async def test_is_running_tells_a_turn_in_flight_from_an_idle_chat(fake_gateway):
    """What a channel asks before showing a placeholder a fed message would not fill."""
    started = asyncio.Event()

    class _SlowAgent(FakeRunMixin):
        tools = []

        def __init__(self):
            self.release = asyncio.Event()

        async def ask(self, *msg, stream=None, **k):
            started.set()
            await self.release.wait()
            return FakeReply("done")

    agent = _SlowAgent()
    fake_gateway._agent = agent
    assert fake_gateway.is_running("s3") is False

    turn = asyncio.ensure_future(fake_gateway.send_message("go", chat_id="s3"))
    await asyncio.wait_for(started.wait(), timeout=1)
    assert fake_gateway.is_running("s3") is True

    agent.release.set()
    await turn
    assert fake_gateway.is_running("s3") is False


async def test_feed_message_is_false_when_nothing_is_running(fake_gateway):
    """Idle chat → the caller runs the message as a new turn instead."""
    assert await fake_gateway.feed_message("hello", chat_id="idle") is False
    stream = await fake_gateway.stream_for("idle")
    assert not stream.pending_messages  # nothing left stranded in the inbox


async def test_cancelled_turn_keeps_what_it_produced(fake_gateway):
    """Stopping a turn keeps the events it already put on the stream, and marks the stop."""

    started = asyncio.Event()

    class _WorkingAgent(FakeRunMixin):
        tools = []

        async def ask(self, *msg, stream=None, **k):

            await ConversationContext(stream=stream).send(
                ModelResponse(message=ModelMessage(content="partial work"))
            )
            started.set()
            await asyncio.Event().wait()

    fake_gateway._agent = _WorkingAgent()

    turn = asyncio.ensure_future(fake_gateway.send_message("do it", chat_id="s2"))
    await asyncio.wait_for(started.wait(), timeout=1)
    assert await fake_gateway.cancel_turn("s2") is True
    assert await turn == ""

    events = await (await fake_gateway.stream_for("s2")).history.get_events()
    assert any(getattr(e, "message", None) and "partial work" in e.message.content for e in events)
    assert isinstance(events[-1], TurnCancelled)
    assert await fake_gateway.cancel_turn("s2") is False  # nothing in flight now


async def test_gateway_asker_timeout_denies():

    asker = GatewayAsker(HitlServer(), timeout=0.05)
    answer = await asker.ask(Question(text="?", options=["Allow once", "Deny"]))
    assert answer == DENY  # unanswered prompt fails safe


@pytest.mark.integration
async def test_gateway_real_agent_multiturn_and_isolation():
    """End-to-end with the real agent: multi-turn recall + chat isolation."""

    gw = Gateway(memory=False)
    await gw.start()
    try:
        await gw.send_message("My codeword is KIWI-7. Acknowledge in one sentence.", chat_id="s1")
        recall = await gw.send_message("What is my codeword? One word.", chat_id="s1")
        assert "KIWI-7" in recall.upper()

        other = await gw.send_message(
            "What is my codeword? If unknown, reply exactly UNKNOWN.",
            chat_id="s2",
        )
        assert "KIWI-7" not in other.upper()
    finally:
        await gw.close()


@pytest.mark.integration
async def test_conversation_resumes_across_restart(tmp_path):
    """A brand-new Gateway over the same data dir keeps full conversation context."""

    cfg = load_config()
    cfg.data_dir = tmp_path  # isolate the chat store

    gw1 = Gateway(config=cfg, memory=False)
    await gw1.start()
    await gw1.send_message("My lucky number is 7. Acknowledge.", chat_id="resume-1")
    await gw1.close()  # simulate shutdown

    gw2 = Gateway(config=cfg, memory=False)
    await gw2.start()
    recall = await gw2.send_message(
        "What is my lucky number? Reply with just the digit.", chat_id="resume-1"
    )
    assert "7" in recall
    await gw2.close()


# --- cross-origin guard (defends a localhost gateway from malicious web pages) ---


def test_origin_ok_unit():
    """The same-origin rule: no-Origin and same host:port pass; others don't."""

    assert _origin_ok(None, "127.0.0.1:8800")  # non-browser caller
    assert _origin_ok("http://127.0.0.1:8800", "127.0.0.1:8800")  # same-origin
    assert _origin_ok("http://127.0.0.1:8800/", "127.0.0.1:8800")  # trailing slash
    assert not _origin_ok("http://evil.example", "127.0.0.1:8800")  # other site
    assert not _origin_ok("http://127.0.0.1:9999", "127.0.0.1:8800")  # other port


def test_origin_allowlist_env():
    """AG2ASSISTANT_ALLOWED_ORIGINS adds extra accepted origins for proxied demos."""

    allowed = _allowed_origins({"AG2ASSISTANT_ALLOWED_ORIGINS": "https://demo.example, http://foo"})
    assert allowed == {"https://demo.example", "http://foo"}
    assert _origin_ok("https://demo.example", "127.0.0.1:8800", allowed)
    assert not _origin_ok("https://other.example", "127.0.0.1:8800", allowed)


def test_cross_origin_requests_rejected(paths):
    """Cross-origin REST and WebSocket attempts are refused; same-origin works."""

    app, pid = make_profile_app(paths)
    with TestClient(app) as client:  # TestClient's Host is "testserver"
        assert client.get("/api/health").status_code == 200  # no Origin → ok
        assert (
            client.get("/api/health", headers={"origin": "http://testserver"})
        ).status_code == 200  # same-origin → ok
        assert (
            client.get("/api/health", headers={"origin": "http://evil.example"})
        ).status_code == 403  # cross-origin → rejected

        # cross-origin WebSocket handshake is closed before accept()
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect(
                api(pid, "/stream?chat=x"), headers={"origin": "http://evil.example"}
            ) as ws:
                ws.receive_json()

        # same-origin WebSocket still connects and replays history
        with client.websocket_connect(
            api(pid, "/stream?chat=y"), headers={"origin": "http://testserver"}
        ) as ws:
            assert ws.receive_json()["type"] == "ready"


# --- POST /api/identity: seed the universal doc from web onboarding ---------- #
#
# The web onboarding "About you" step posts identity answers here; the endpoint
# seeds the shared universal "who the user is" doc via the SAME identity_document
# helper the CLI interview uses (format parity), and is seed-only: it refuses to
# clobber an existing doc and no-ops on an all-empty payload. Seeding it is what
# keeps the in-chat interview from firing for a web-onboarded user.


def _identity_app(paths):

    return create_app(make_manager(paths))


def test_identity_endpoint_seeds_when_empty(paths):

    app = _identity_app(paths)
    with TestClient(app) as client:
        r = client.post(
            "/api/identity",
            json={"name": "Mark", "location": "Sydney", "hours": "9-5", "style": "concise"},
        )
        assert r.status_code == 200
        assert r.json() == {"ok": True, "seeded": True}
        doc = client.get("/api/memory").json()["text"]
        assert "Name: Mark" in doc
        assert "Location: Sydney" in doc
        assert "Usual working hours: 9-5" in doc
        assert "Prefers answers that are concise." in doc


def test_identity_endpoint_refuses_to_clobber_existing_doc(paths):

    app = _identity_app(paths)
    with TestClient(app) as client:
        client.post(
            "/api/memory", json={"text": "# User profile\n\n## About the user\n- Name: Ada\n"}
        )
        r = client.post("/api/identity", json={"name": "Mark"})
        assert r.status_code == 200
        body = r.json()
        assert body["seeded"] is False and body["reason"] == "exists"
        # the pre-existing doc is untouched
        assert "Name: Ada" in client.get("/api/memory").json()["text"]
        assert "Name: Mark" not in client.get("/api/memory").json()["text"]


def test_identity_endpoint_noops_when_all_empty(paths):

    app = _identity_app(paths)
    with TestClient(app) as client:
        r = client.post(
            "/api/identity", json={"name": "", "location": "  ", "hours": "", "style": ""}
        )
        assert r.status_code == 200
        body = r.json()
        assert body["seeded"] is False and body["reason"] == "empty"
        assert client.get("/api/memory").json()["text"].strip() == ""


async def test_identity_seed_disables_interview_gate(paths):
    """After the endpoint seeds the universal store, the in-chat interview gate is
    closed — a web-onboarded user's first chat won't trigger it."""

    user_store_path = paths.root / "user.db"
    assert await needs_onboarding(user_store_path) is True  # fresh install: gate open

    app = _identity_app(paths)
    with TestClient(app) as client:
        assert client.post("/api/identity", json={"location": "Sydney"}).json()["seeded"] is True

    assert await needs_onboarding(user_store_path) is False  # gate now closed


async def test_identity_document_endpoint_parity(paths):
    """The endpoint's stored doc is byte-identical to run_onboarding's for the same
    answers — both go through identity_document, the single formatter."""

    answers = {"name": "Ada", "location": "London", "hours": "9am–6pm", "style": "Short & direct"}

    app = _identity_app(paths)
    with TestClient(app) as client:
        client.post("/api/identity", json=answers)
        endpoint_doc = client.get("/api/memory").json()["text"]

    # run_onboarding writes to a separate store; compare its stored doc.
    class _Asker:
        def __init__(self, vals):
            self._vals = list(vals)

        async def ask(self, q, timeout=None):
            return self._vals.pop(0)

    cli_store = paths.root / "cli_user.db"
    await onboarding.run_onboarding(
        _Asker(["Ada", "London", "9am–6pm", "Short & direct"]),
        user_store_path=cli_store,
        env_path=paths.root / ".env",
    )
    cli_doc = await build_profile_store(cli_store).read(PROFILE_PATH)
    assert endpoint_doc == cli_doc


# ---- System health endpoint (the status-dot source, GET /health) ---------------


def test_profile_health_ok_and_down(profile_app, paths):
    """The cheap health aggregate: healthy when the agent is up and the configured
    provider has a key; 'down' (agent can't run) when the key is missing. The dot
    reads `overall`; the panel reads `checks`."""

    client, pid = profile_app

    # A real key for the configured provider + faked agent alive → core signals green.
    key = SecretStore(paths).create_secret("K", "sk-gemini-1", provider="gemini", default=True)
    body = client.get(api(pid, "/health")).json()
    assert body["overall"] == "ok"
    ids = {c["id"] for c in body["checks"]}
    assert ids == {"agent", "provider", "mcp", "channels", "google", "scheduler"}
    agent = next(c for c in body["checks"] if c["id"] == "agent")
    assert agent["state"] == "ok"
    # MCP is config-only here (no probe, no servers configured) → off.
    mcp = next(c for c in body["checks"] if c["id"] == "mcp")
    assert mcp["state"] == "off" and mcp["servers"] == []

    # Drop the provider key → the configured provider (gemini) has no key → down.
    SecretStore(paths).delete_secret(key["id"])
    body = client.get(api(pid, "/health")).json()
    assert body["overall"] == "down"
    provider = next(c for c in body["checks"] if c["id"] == "provider")
    assert provider["state"] == "down"


def test_profile_health_warns_on_channel_error(profile_app, paths):
    """A messaging channel that defaults to this profile and failed to start (start
    error recorded) rolls the overall up to 'warn' — auxiliary, so amber not red."""

    client, pid = profile_app

    SecretStore(paths).create_secret("K", "sk-gemini-1", provider="gemini", default=True)
    # Point the discord Connection's default at this profile and record a start error.
    connection = ConnectionStore(paths).create_connection(
        "discord", tokens={"DISCORD_BOT_TOKEN": "bad"}
    )
    ProfileRegistry(paths).set_connection_default(connection.id, pid)
    client.app.state.profiles.channel_errors[connection.id] = "invalid bot token"

    body = client.get(api(pid, "/health")).json()
    assert body["overall"] == "warn"
    channels = next(c for c in body["checks"] if c["id"] == "channels")
    assert channels["state"] == "warn"
    assert any(it["platform"] == "discord" and it["error"] for it in channels["items"])


# ---- Named LLM configurations (global /api/llm-configs) ------------------------


def test_llm_configs_crud_use_delete_and_key_secrecy(profile_app, paths):
    """Create/update/use/delete named configs; the raw key of a referenced Secret is
    never echoed (only its view with a hint), and deleting a config leaves the
    Secret in place (they're independent entities)."""

    client, pid = profile_app

    # empty install
    r = client.get("/api/llm-configs").json()
    assert r["configs"] == [] and r["active"] is None and r["env_override"] is None
    # Every config type, including ones no config uses yet (the template grid).
    assert set(r["provider_deps"]) == set(TYPES)
    assert r["provider_deps"]["gemini"]["ok"] is True
    assert r["provider_deps"]["ollama"]["extra"] == "ollama"

    # create a Secret, then a local-server config referencing it + activate
    sid = client.post("/api/secrets", json={"name": "Local key", "value": "sk-secret-1234"}).json()[
        "secret"
    ]["id"]
    r = client.post(
        "/api/llm-configs",
        json={
            "name": "Local",
            "type": "openai",
            "model": "gemma-4",
            "base_url": "http://192.168.0.55:8080/v1",
            "secret_id": sid,
            "activate": True,
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    cid = body["config"]["id"]
    assert body["ok"] is True and body["active"] == cid
    # raw key never echoed — only the Secret's view with a hint
    assert body["config"]["secret"] == {"id": sid, "name": "Local key", "hint": "…1234"}
    assert "sk-secret-1234" not in r.text

    g = client.get("/api/llm-configs").json()
    assert g["active"] == cid
    assert g["configs"][0]["base_url"] == "http://192.168.0.55:8080/v1"
    assert g["configs"][0]["secret_id"] == sid
    assert "sk-secret-1234" not in client.get("/api/llm-configs").text
    # the honest key labels: its referenced Secret wins; the shared env slot is
    # reported too
    entry = g["configs"][0]
    assert entry["key_source"] == "secret"
    assert entry["shared_key"]["env"] == "OPENAI_API_KEY"
    assert entry["shared_key"]["set"] is False  # the isolated install has no key

    # update keeping the secret_id reference → reference kept, model changed
    r = client.post(
        f"/api/llm-configs/{cid}",
        json={
            "name": "Local",
            "type": "openai",
            "model": "gemma-5",
            "base_url": "http://192.168.0.55:8080/v1",
            "secret_id": sid,
        },
    )
    assert r.status_code == 200
    g = client.get("/api/llm-configs").json()
    assert g["configs"][0]["model"] == "gemma-5"
    assert g["configs"][0]["secret_id"] == sid  # untouched

    # add a second config, then delete the ACTIVE first one: allowed, and active moves
    # to the remaining config (no "switch first" dance).
    r2 = client.post("/api/llm-configs", json={"name": "G", "type": "gemini", "model": "gemini-x"})
    cid2 = r2.json()["config"]["id"]
    assert client.get("/api/llm-configs").json()["active"] == cid  # first is still active

    assert client.delete(f"/api/llm-configs/{cid}").status_code == 200
    assert SecretStore(paths).get_secret(sid) is not None  # the Secret survives its referrer
    assert client.get("/api/llm-configs").json()["active"] == cid2  # active moved on

    # unknown ids → 404
    assert client.post("/api/llm-configs/c_ghost/use").status_code == 404
    assert client.delete("/api/llm-configs/c_ghost").status_code == 404
    assert (
        client.post(
            "/api/llm-configs/c_ghost", json={"name": "x", "type": "gemini", "model": "m"}
        ).status_code
        == 404
    )


def test_llm_config_dry_construct_rejects_bad_options(profile_app):
    """A typo'd advanced kwarg fails the dry-construct (400 + the constructor's
    message) and nothing is persisted."""
    client, pid = profile_app
    r = client.post(
        "/api/llm-configs",
        json={"name": "Bad", "type": "openai", "model": "m", "options": {"bogus_kwarg": 1}},
    )
    assert r.status_code == 400
    assert "bogus_kwarg" in r.json()["error"]
    assert client.get("/api/llm-configs").json()["configs"] == []  # not saved


def test_llm_config_env_override_surfaced(paths):
    """When AG2ASSISTANT_MODEL / _LLM_PROVIDER is set (they pin the model in
    resolve_config), GET reports it so the UI can show the 'pinned by env' banner."""
    env = {"AG2ASSISTANT_LLM_PROVIDER": "openai", "AG2ASSISTANT_MODEL": "gpt-x"}
    manager = make_manager(paths, env=env)
    with TestClient(create_app(manager)) as client:
        assert client.get("/api/llm-configs").json()["env_override"] == {
            "provider": "openai",
            "model": "gpt-x",
        }
        # and the pin really reached the runtime's config
        client.post("/api/profiles", json={"name": "Work", "accent": "#109e91"})
        runtime = client.app.state.profiles.get("work")
        assert (runtime.config.llm.provider, runtime.config.llm.model) == ("openai", "gpt-x")


class LlmProbe:
    """The injected stand-in for the app's ``model_config`` probe: drives the /test
    round-trip through the REAL ``ag2.Agent`` while replacing only the LLM client with
    an ``ag2.testing.TestClient``.

    ``model_config`` still runs for real (its built config lands in ``captured`` for
    assertions); a ``TestConfig`` then stands in as the agent's config so ``.create()``
    yields the canned client instead of a network one. Set ``client`` to hand the agent
    a raising/hanging double."""

    def __init__(self, *, reply="PONG"):
        self.reply = reply
        self.client = None
        self.captured: dict = {}

    def __call__(self, config):
        self.captured["config"] = model_config(config)
        probe = self

        class _Cfg(ag2.testing.TestConfig):
            def create(self):
                if probe.client is not None:
                    return probe.client
                return ag2.testing.TestClient(probe.reply)

        return _Cfg()


def test_llm_config_test_endpoint_pong_and_failures(profile_app_factory):
    """The /test endpoint runs a real PONG round-trip (LLM client canned via
    ag2.testing.TestClient, real Agent): a reply → {ok, reply, latency_ms}; any
    exception or a timeout → 502 {ok:false, error}."""
    probe = LlmProbe()
    client, _pid = profile_app_factory(llm_probe=probe, llm_probe_timeout_s=0.2)
    entry = client.post(
        "/api/llm-configs", json={"name": "G", "type": "gemini", "model": "gemini-x"}
    ).json()["config"]

    r = client.post(f"/api/llm-configs/{entry['id']}/test")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True and body["reply"] == "PONG"
    assert isinstance(body["latency_ms"], int)

    class _Boom(ag2.testing.TestClient):
        async def __call__(self, messages, context, **k):
            raise RuntimeError("nope-boom")

    probe.client = _Boom()
    r = client.post(f"/api/llm-configs/{entry['id']}/test")
    assert r.status_code == 502
    assert "nope-boom" in r.json()["error"]

    # a wedged call trips the (tiny, injected) timeout → 502
    class _Hang(ag2.testing.TestClient):
        async def __call__(self, messages, context, **k):
            await asyncio.sleep(5)
            return await super().__call__(messages, context, **k)

    probe.client = _Hang()
    r = client.post(f"/api/llm-configs/{entry['id']}/test")
    assert r.status_code == 502

    # unknown id → 404
    assert client.post("/api/llm-configs/c_ghost/test").status_code == 404


def test_llm_config_draft_test_endpoint(profile_app_factory, paths):
    """POST /api/llm-configs/test pings an UNSAVED editor draft: nothing persisted,
    a typed api_key is used for the call, a blank one resolves the draft's
    ``secret_id`` reference, and validation errors come back as 400 (the literal
    "test" segment must not be captured by the /{cid} update route)."""
    probe = LlmProbe()
    client, _pid = profile_app_factory(llm_probe=probe)
    captured = probe.captured

    # pure draft (no id): tested and NOT saved
    r = client.post(
        "/api/llm-configs/test",
        json={
            "name": "Draft",
            "type": "openai",
            "model": "gemma-4",
            "base_url": "http://h:8080/v1",
            "api_key": "sk-draft-key-1",
        },
    )
    assert r.status_code == 200 and r.json()["ok"] is True
    assert LlmConfigStore(paths).list_configs() == []  # nothing persisted
    assert getattr(captured["config"], "api_key", None) == "sk-draft-key-1"  # draft key used

    # a draft referencing a Secret with no typed key: the Secret's value is sent
    sid = client.post("/api/secrets", json={"name": "Stored", "value": "sk-stored-key-2"}).json()[
        "secret"
    ]["id"]
    r = client.post(
        "/api/llm-configs/test",
        json={
            "name": "E",
            "type": "openai",
            "model": "m",
            "base_url": "http://h/v1",
            "secret_id": sid,
        },
    )
    assert r.status_code == 200
    assert getattr(captured["config"], "api_key", None) == "sk-stored-key-2"

    # a bad draft (unknown type) → 400 with the validator's message, not a 404 from
    # the update route misparsing "test" as a config id
    r = client.post("/api/llm-configs/test", json={"name": "X", "type": "nope", "model": "m"})
    assert r.status_code == 400
    assert "type must be one of" in r.json()["error"]


def test_llm_config_probe_carries_host_bridge(profile_app_factory):
    """The /test probe's Config must carry the install's ACP host bridge.

    ``Config.for_paths`` defaults every non-path field, so the bridge that reaches a
    real turn through ``apply_env_overrides`` is absent unless the route copies it.
    Without it a Docker probe spawns the adapter locally — inside an image that ships
    none — and reports a bare "[Errno 2]" instead of testing the host CLI."""
    seen: dict = {}

    class _Probe(LlmProbe):
        def __call__(self, config):
            seen["config"] = config
            return super().__call__(config)

    client, _pid = profile_app_factory(
        llm_probe=_Probe(),
        env={
            "AG2ASSISTANT_ACP_BRIDGE": "host.docker.internal:8801",
            "AG2ASSISTANT_ACP_BRIDGE_TOKEN": "shared-secret",
        },
    )

    r = client.post(
        "/api/llm-configs/test",
        json={"name": "Claude Code", "type": "claude_code", "model": ""},
    )
    assert r.status_code == 200, r.json()
    assert seen["config"].acp_bridge == "host.docker.internal:8801"
    assert seen["config"].acp_bridge_token == "shared-secret"


def test_llm_config_subscription_entry_view_signed_in(profile_app, paths):
    """An openai_subscription config's row/chip need the live ChatGPT sign-in state and
    a 'subscription' key_source so the UI can label it honestly without a 2nd fetch.
    Endpoint fields are stripped for this type (codex_auth owns the endpoint)."""

    client, pid = profile_app
    entry = client.post(
        "/api/llm-configs",
        json={
            "name": "Sub",
            "type": "openai_subscription",
            "model": "gpt-5.5",
            "base_url": "http://sneaky/v1",  # must be ignored/stripped
            "activate": True,
        },
    ).json()["config"]
    assert entry["key_source"] == "subscription"
    assert entry["base_url"] == ""

    # a real signed-in session on disk, read live by the app's CodexAuth
    write_codex_session(paths)
    assert CodexAuth(paths).status()["signed_in"] is True
    assert client.get("/api/llm-configs").json()["configs"][0]["signed_in"] is True

    assert CodexAuth(paths).logout() is True  # signing out is observable too
    assert client.get("/api/llm-configs").json()["configs"][0]["signed_in"] is False


def test_llm_config_subscription_draft_test_routes_to_backend(profile_app_factory, paths):
    """Testing a subscription draft flows through model_config's subscription branch:
    the probe carries auth_mode=subscription, so the built client points at the ChatGPT
    backend with the codex token and server-side storage disabled."""
    write_codex_session(paths, access_token="TOK", account_id="acc")
    probe = LlmProbe()
    client, _pid = profile_app_factory(llm_probe=probe)
    captured = probe.captured
    r = client.post(
        "/api/llm-configs/test",
        json={"name": "Sub", "type": "openai_subscription", "model": "gpt-5.5"},
    )
    assert r.status_code == 200 and r.json()["ok"] is True
    cfg = captured["config"]
    assert type(cfg).__name__ == "OpenAIResponsesConfig"
    assert cfg.base_url == codex_auth.BACKEND_BASE
    assert cfg.api_key == "TOK"
    assert cfg.store is False


def test_secrets_crud_endpoints(profile_app, paths):
    """POST/GET/POST-{sid}/DELETE /api/secrets: safe views only (raw value never
    echoed), 409 + existing on a duplicate value, 404s, delete-always-succeeds."""
    client, pid = profile_app
    r = client.post(
        "/api/secrets",
        json={"name": "Work", "value": "sk-w-1234", "provider": "openai", "default": True},
    )
    assert r.status_code == 200
    view = r.json()["secret"]
    sid = view["id"]
    assert view["hint"] == "…1234" and view["default"] is True
    assert "sk-w-1234" not in r.text
    # unique by value → 409 pointing at the existing secret
    r = client.post("/api/secrets", json={"name": "Other", "value": "sk-w-1234"})
    assert r.status_code == 409
    assert r.json()["existing"]["id"] == sid
    # bad input → 400
    assert client.post("/api/secrets", json={"name": "", "value": "x"}).status_code == 400
    # list
    r = client.get("/api/secrets")
    assert r.json()["secrets"][0]["used_by"] == []
    assert "sk-w-1234" not in r.text
    # update: rename; unknown id → 404
    r = client.post(f"/api/secrets/{sid}", json={"name": "Renamed"})
    assert r.status_code == 200 and r.json()["secret"]["name"] == "Renamed"
    assert client.post("/api/secrets/s_missing", json={"name": "X"}).status_code == 404
    # delete: ok, then 404
    assert client.delete(f"/api/secrets/{sid}").status_code == 200
    assert client.delete(f"/api/secrets/{sid}").status_code == 404
    # POST /api/secrets/key still routes to the provider-key handler (not /{sid})
    r = client.post("/api/secrets/key", json={"provider": "openai", "value": "sk-ob-1"})
    assert r.status_code == 200

    assert SecretStore(paths).default_secret("openai")["hint"] == "…" + "sk-ob-1"[-4:]


def test_llm_config_secret_reference_flow(profile_app):
    """Configs reference Secrets by id: the view carries {secret, secret_missing},
    key_source says 'secret', used_by names the config, deleting the Secret
    degrades the config honestly, and deleting the config leaves the Secret."""
    client, pid = profile_app
    sid = client.post("/api/secrets", json={"name": "K", "value": "sk-k-9999"}).json()["secret"][
        "id"
    ]
    r = client.post(
        "/api/llm-configs",
        json={"name": "GPT", "type": "openai", "model": "gpt-4o", "secret_id": sid},
    )
    assert r.status_code == 200
    view = r.json()["config"]
    cid = view["id"]
    assert view["secret"] == {"id": sid, "name": "K", "hint": "…9999"}
    assert view["secret_id"] == sid and view["secret_missing"] is False
    assert view["key_source"] == "secret"
    assert "sk-k-9999" not in r.text
    assert client.get("/api/secrets").json()["secrets"][0]["used_by"] == ["GPT"]
    # deleting the secret → dangling reference reported honestly
    client.delete(f"/api/secrets/{sid}")
    view = client.get("/api/llm-configs").json()["configs"][0]
    assert view["secret"] is None and view["secret_missing"] is True
    assert view["key_source"] in ("shared", "none")
    # deleting the config never deletes a Secret (they're independent)
    sid2 = client.post("/api/secrets", json={"name": "K2", "value": "sk-k2-9999"}).json()["secret"][
        "id"
    ]
    client.post(
        f"/api/llm-configs/{cid}",
        json={"name": "GPT", "type": "openai", "model": "gpt-4o", "secret_id": sid2},
    )
    client.delete(f"/api/llm-configs/{cid}")
    assert client.get("/api/secrets").json()["secrets"][0]["id"] == sid2


# ---- ACP model-session teardown on reload/close ---------------------------------


class _FakeAcpConfig:
    def __init__(self):
        self.closed = 0

    async def aclose(self):
        self.closed += 1


class _FakeAgentWithConfig:
    def __init__(self, config):
        self.config = config


async def test_reload_closes_acp_sessions(fake_gateway):
    cfg = _FakeAcpConfig()
    fake_gateway._agent = _FakeAgentWithConfig(cfg)
    fake_gateway._model_agents["c_x"] = _FakeAgentWithConfig(cfg)
    await fake_gateway.reload()
    # Both cached agents shared one config; aclose is idempotent so >=1 is the
    # contract (dedup by id() keeps it at exactly 1).
    assert cfg.closed == 1


async def test_close_closes_acp_sessions(fake_gateway):
    cfg = _FakeAcpConfig()
    fake_gateway._agent = _FakeAgentWithConfig(cfg)
    await fake_gateway.close()
    assert cfg.closed == 1


def test_llm_configs_expose_provider_builtin_tools(profile_app):
    """The list ships the per-type availability (ids only — the labels are the
    web's), and a config round-trips the switches the user chose."""
    client, _pid = profile_app

    r = client.get("/api/llm-configs").json()
    # Every type, not just the configured ones — the form renders a type before
    # any config uses it.
    assert set(r["builtin_tools_by_type"]) == set(TYPES)
    assert r["builtin_tools_by_type"]["gemini"] == ["web_search", "web_fetch", "code_execution"]
    assert r["builtin_tools_by_type"]["openai_responses"] == ["web_search", "code_execution"]
    # Chat Completions, Ollama and the CLI-login types map none.
    for ctype in ("openai", "openai_subscription", "ollama", "claude_code", "codex"):
        assert r["builtin_tools_by_type"][ctype] == []

    body = client.post(
        "/api/llm-configs",
        json={
            "name": "G",
            "type": "gemini",
            "model": "gemini-3.6-flash",
            "builtin_tools": {"web_search": {}},
        },
    ).json()
    assert body["config"]["builtin_tools"] == {"web_search": {}}
    assert client.get("/api/llm-configs").json()["configs"][0]["builtin_tools"] == {
        "web_search": {}
    }


def test_saving_an_unsupported_builtin_strips_it_rather_than_failing(profile_app):
    client, _pid = profile_app
    r = client.post(
        "/api/llm-configs",
        json={
            "name": "R",
            "type": "openai_responses",
            "model": "gpt-5",
            "builtin_tools": {"web_search": {}, "web_fetch": {}},
        },
    )
    assert r.status_code == 200, r.text
    assert r.json()["config"]["builtin_tools"] == {"web_search": {}}


def test_an_anthropic_config_saved_without_the_field_keeps_the_native_fetcher(profile_app):
    """A client that never sends builtin_tools (or a config written before the
    feature) must not silently lose the fetcher it had — see llm_configs."""
    pytest.importorskip("anthropic")  # the save dry-constructs the provider config
    client, _pid = profile_app
    r = client.post(
        "/api/llm-configs", json={"name": "A", "type": "anthropic", "model": "claude-x"}
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["config"]["builtin_tools"] == {"web_fetch": {}}
    # An explicit empty object is the user's choice and is honoured.
    off = client.post(
        f"/api/llm-configs/{body['config']['id']}",
        json={"name": "A", "type": "anthropic", "model": "claude-x", "builtin_tools": {}},
    ).json()
    assert off["config"]["builtin_tools"] == {}
