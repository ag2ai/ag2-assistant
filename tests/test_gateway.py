"""Tests for the AG2 Assistant gateway and its REST/WebSocket facade.

Unit tests stub the agent so they run without an LLM. Integration tests exercise
the real agent end-to-end. The REST facade is now profile-scoped: the app is built
around a ``ProfileManager`` with one profile and routes live under
``/api/p/{pid}/…`` (see ``conftest.make_profile_app`` / the ``profile_app`` fixture).
"""

import asyncio

import pytest

from tests.conftest import FakeAgent, FakeReply, api, make_profile_app, use_fake_agent


@pytest.fixture
def fake_gateway(monkeypatch):
    """A Gateway whose agent is a deterministic fake (no LLM, no persistence)."""
    from assistant.gateway.core import Gateway

    gw = Gateway(memory=False, persist=False)
    gw._agent = FakeAgent()
    return gw


async def test_send_message_returns_reply(fake_gateway):
    reply = await fake_gateway.send_message("hello", session_id="s1")
    assert reply == "echo[1]: hello"


async def test_forwarding_events_passes_structured_events_not_transcript(fake_gateway):
    """`_ask_forwarding_events` forwards the agent's structured events verbatim
    (so a voice client folds them with the text reducer) while OMITTING the
    conversation events it renders itself as transcript — and always unsubscribes."""
    from ag2.events import (
        ModelMessage,
        ModelMessageChunk,
        ModelResponse,
        ToolCallEvent,
        ToolCallsEvent,
    )

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

    async def ask_coro():
        await captured["fn"](batch)
        await captured["fn"](ModelMessageChunk(content="spoken words"))  # transcript → omitted
        await captured["fn"](ModelResponse(message=ModelMessage(content="spoken")))  # → omitted
        return FakeReply("done")

    reply = await fake_gateway._ask_forwarding_events(_Stream(), ask_coro(), on_event)

    assert reply.body == "done"
    # only the structured event is forwarded; conversation events are the voice
    # channel's own to render, so they don't double up as folded bubbles.
    assert forwarded == [batch]
    assert captured["unsub"] == "sub-1"  # always unsubscribed


async def test_gateway_auto_onboards_once(fake_gateway, monkeypatch):
    """First message with an asker triggers onboarding exactly once."""
    import assistant.onboarding as onboarding

    calls = {"check": 0, "run": 0}

    async def fake_needs(*a, **k):
        calls["check"] += 1
        return True

    async def fake_run(asker, *a, **k):
        calls["run"] += 1
        return {}

    monkeypatch.setattr(onboarding, "needs_onboarding", fake_needs)
    monkeypatch.setattr(onboarding, "run_onboarding", fake_run)
    fake_gateway._memory = True  # onboarding only runs when memory is on

    class _Asker:
        async def ask(self, q, timeout=None):
            return "x"

    asker = _Asker()
    await fake_gateway.send_message("hi", session_id="s1", asker=asker)
    await fake_gateway.send_message("again", session_id="s1", asker=asker)
    assert calls["run"] == 1  # onboarded once, not on every message


async def test_gateway_skips_onboarding_without_asker(fake_gateway, monkeypatch):
    import assistant.onboarding as onboarding

    async def boom(*a, **k):
        raise AssertionError("should not be called without an asker")

    monkeypatch.setattr(onboarding, "needs_onboarding", boom)
    await fake_gateway.send_message("hi", session_id="s1")  # no asker → no onboarding


async def test_session_keeps_multi_turn_history(fake_gateway):
    await fake_gateway.send_message("first", session_id="s1")
    reply = await fake_gateway.send_message("second", session_id="s1")
    # The chain grew to length 2, proving history is threaded.
    assert reply == "echo[2]: second"


async def test_sessions_are_isolated(fake_gateway):
    await fake_gateway.send_message("a", session_id="s1")
    await fake_gateway.send_message("b", session_id="s1")
    reply = await fake_gateway.send_message("c", session_id="s2")
    # s2 starts a fresh chain (length 1), unaffected by s1.
    assert reply == "echo[1]: c"


def test_status_shape(fake_gateway):
    status = fake_gateway.status()
    assert status["status"] == "ok"
    assert "model" in status
    assert status["sessions"] == 0


async def test_transcript_persists_across_instances(tmp_path, monkeypatch):
    """A new Gateway over the same data dir sees prior sessions (resumable)."""
    import assistant.gateway.core as core_mod
    from assistant.config import Config
    from assistant.gateway.core import Gateway

    monkeypatch.setattr(core_mod, "create_agent", lambda *a, **k: FakeAgent())

    gw = Gateway(config=Config(data_dir=tmp_path), memory=False)
    await gw.start()
    await gw.send_message("hello there", session_id="s1")
    await gw.send_message("again", session_id="s1")
    await gw.close()

    gw2 = Gateway(config=Config(data_dir=tmp_path), memory=False)
    await gw2.start()
    turns = await gw2.transcript("s1")
    assert [m["role"] for m in turns] == ["user", "agent", "user", "agent"]
    assert turns[0]["text"] == "hello there"

    listed = await gw2.list_sessions()
    s1 = next(s for s in listed if s["session_id"] == "s1")
    assert s1["turns"] == 2
    assert s1["preview"] == "hello there"


async def test_delete_session_removes_transcript_and_event_log(tmp_path, monkeypatch):
    """Deleting a chat drops BOTH artifacts — the display transcript AND the AG2
    event log — so it neither lists nor resumes, even on a fresh Gateway."""
    from ag2.knowledge.constants import LOG_PREFIX

    import assistant.gateway.core as core_mod
    from assistant.config import Config
    from assistant.gateway.core import Gateway

    monkeypatch.setattr(core_mod, "create_agent", lambda *a, **k: FakeAgent())

    gw = Gateway(config=Config(data_dir=tmp_path), memory=False)
    await gw.start()
    await gw.send_message("keep me", session_id="keep")
    await gw.send_message("delete me", session_id="gone")
    # both artifacts exist before delete
    assert await gw._event_store.exists(gw._transcript_path("gone"))
    assert await gw._event_store.exists(f"{LOG_PREFIX}gone.jsonl")

    assert await gw.delete_session("gone") is True
    assert await gw.delete_session("gone") is False  # idempotent: nothing left to remove

    # gone from the list; both on-disk artifacts removed; other session untouched
    assert {s["session_id"] for s in await gw.list_sessions()} == {"keep"}
    assert not await gw._event_store.exists(gw._transcript_path("gone"))
    assert not await gw._event_store.exists(f"{LOG_PREFIX}gone.jsonl")
    await gw.close()

    # a fresh Gateway over the same data dir does not resurrect it
    gw2 = Gateway(config=Config(data_dir=tmp_path), memory=False)
    await gw2.start()
    assert {s["session_id"] for s in await gw2.list_sessions()} == {"keep"}
    assert await gw2.transcript("gone") == []


# --- in-flight session stub (bug: a chat mid-turn must be listable so it survives
#     a profile switch, which is a full-page nav that discards local page state) ---


class _SlowAgent:
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


async def _persistent_gateway(tmp_path, monkeypatch, agent):
    import assistant.gateway.core as core_mod
    from assistant.config import Config
    from assistant.gateway.core import Gateway

    monkeypatch.setattr(core_mod, "create_agent", lambda *a, **k: agent)
    gw = Gateway(config=Config(data_dir=tmp_path), memory=False)
    await gw.start()
    gw._agent = agent
    return gw


async def test_inflight_session_listed_before_completion(tmp_path, monkeypatch):
    """(a) A session is listed with the user-message preview *while* its (slow) turn
    is still running — the stub is written the instant the message is accepted."""
    slow = _SlowAgent()
    gw = await _persistent_gateway(tmp_path, monkeypatch, slow)
    turn = asyncio.create_task(gw.send_message("search the web for X", session_id="live"))
    try:
        # Let send_message reach the (blocked) agent turn.
        for _ in range(50):
            if await gw._event_store.exists(gw._transcript_path("live")):
                break
            await asyncio.sleep(0.01)

        listed = await gw.list_sessions()
        s = next(s for s in listed if s["session_id"] == "live")
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


async def test_inflight_stub_completed_in_place_no_duplicate(tmp_path, monkeypatch):
    """(b)+(c) After the turn completes the entry has the reply, one turn, a title,
    and the user message is NOT duplicated by the completion write; a second turn
    threads on without duplicating either (multi-turn stub is a no-op)."""
    import assistant.title as title_mod

    async def fake_title(config, user_text, reply_text):
        return "Named Chat"

    monkeypatch.setattr(title_mod, "generate_title", fake_title)

    gw = await _persistent_gateway(tmp_path, monkeypatch, FakeAgent())
    await gw.send_message("first question", session_id="s1")
    for _ in range(50):  # title generation is fire-and-forget
        listed = await gw.list_sessions()
        if next(x for x in listed if x["session_id"] == "s1")["title"]:
            break
        await asyncio.sleep(0.01)

    msgs = await gw.transcript("s1")
    # exactly one user + one agent — the stub was completed in place, not re-appended.
    assert msgs == [
        {"role": "user", "text": "first question"},
        {"role": "agent", "text": "echo[1]: first question"},
    ]
    listed = await gw.list_sessions()
    s1 = next(x for x in listed if x["session_id"] == "s1")
    assert s1["turns"] == 1
    assert s1["title"] == "Named Chat"

    # Second turn: no stub duplication, history keeps growing normally.
    await gw.send_message("second question", session_id="s1")
    msgs = await gw.transcript("s1")
    assert [m["text"] for m in msgs] == [
        "first question",
        "echo[1]: first question",
        "second question",
        "echo[2]: second question",
    ]
    listed = await gw.list_sessions()
    assert next(x for x in listed if x["session_id"] == "s1")["turns"] == 2
    await gw.close()


async def test_inflight_session_stream_replay_returns_user_event(tmp_path, monkeypatch):
    """(d) Reopening an in-flight session mid-turn replays the user message event, so
    the stream bridge shows the history so far and attaches live. Here the user event
    is emitted onto the session stream before the (blocked) turn, exactly as the WS
    stream path does for a real message; a fresh bridge open() must replay it."""
    from assistant.events import Attachment  # any persisted, replayable session event

    slow = _SlowAgent()
    gw = await _persistent_gateway(tmp_path, monkeypatch, slow)
    # Emit a marker event onto the session stream (persisted + replayable), the way the
    # app's stream handler surfaces the user's turn context before running it.
    await gw.emit_event("live", Attachment("/tmp/x.png", name="x.png"))

    turn = asyncio.create_task(gw.send_message("do the slow thing", session_id="live"))
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


def test_sessions_rest_endpoints(profile_app):
    client, pid = profile_app
    client.post(api(pid, "/message"), json={"text": "first msg", "session_id": "u1"})
    sessions = client.get(api(pid, "/sessions")).json()["sessions"]
    assert any(s["session_id"] == "u1" for s in sessions)
    msgs = client.get(api(pid, "/sessions/u1")).json()["messages"]
    assert msgs[0]["text"] == "first msg"


def test_rest_message_endpoint(profile_app):
    """The REST facade returns a reply for a posted message (fake agent)."""
    client, pid = profile_app
    health = client.get("/api/health").json()
    assert health["status"] == "ok"

    resp = client.post(api(pid, "/message"), json={"text": "hi there", "session_id": "u1"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["reply"] == "echo[1]: hi there"
    assert body["session_id"] == "u1"


def test_unknown_and_archived_profile_status(monkeypatch):
    """A prefixed route on an unknown pid 404s; on an archived pid 410s."""
    from fastapi.testclient import TestClient

    from assistant import profiles
    from assistant.gateway.app import create_app
    from assistant.gateway.profile_manager import ProfileManager

    use_fake_agent(monkeypatch)
    work = profiles.create_profile("Work", "teal")
    profiles.profile_dir(work.id).mkdir(parents=True, exist_ok=True)
    keep = profiles.create_profile("Personal", "coral")  # so archive isn't the last
    profiles.profile_dir(keep.id).mkdir(parents=True, exist_ok=True)

    app = create_app(ProfileManager(memory=False, persist=False))
    with TestClient(app) as client:
        assert client.get(api("ghost", "/sessions")).status_code == 404
        assert client.get(api(work.id, "/sessions")).status_code == 200
        # archive work (with a replacement default if needed), then it 410s
        client.request("DELETE", f"/api/profiles/{work.id}", json={"new_default": keep.id})
        assert client.get(api(work.id, "/sessions")).status_code == 410


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


def test_project_folder_endpoint_seeds_readonly_repo_files(tmp_path, monkeypatch):
    """POST settings/project-folder persists the folder AND seeds a `repo-files`
    MCP scoped to it with exactly the 7 read tools (no write/edit/delete reaches the agent)."""
    from fastapi.testclient import TestClient

    proj = tmp_path / "project"
    proj.mkdir()

    use_fake_agent(monkeypatch)
    app, pid = make_profile_app()
    with TestClient(app) as client:
        before = client.get(api(pid, "/settings")).json()
        assert before["project_folder"] == ""
        assert before["mcp_servers"] == []
        # the picker's start roots are advertised for the UI
        assert set(before["fs"]) == {"home", "cwd", "workspace"}

        resp = client.post(api(pid, "/settings/project-folder"), json={"path": str(proj)})
        assert resp.status_code == 200
        assert resp.json()["project_folder"] == str(proj.resolve())

        s = client.get(api(pid, "/settings")).json()
        assert s["project_folder"] == str(proj.resolve())
        server = next(x for x in s["mcp_servers"] if x["name"] == "repo-files")
        assert server["command"] == "npx"
        assert server["args"] == [
            "-y",
            "@modelcontextprotocol/server-filesystem",
            str(proj.resolve()),
        ]
        # read-only by whitelist — exactly the 7 read tools, nothing that mutates
        assert server["allowed_tools"] == [
            "read_file",
            "read_multiple_files",
            "list_directory",
            "directory_tree",
            "search_files",
            "get_file_info",
            "list_allowed_directories",
        ]
        assert not any(
            "write" in t or "edit" in t or "delete" in t for t in server["allowed_tools"]
        )

        # a non-directory is rejected (and seeds nothing)
        bad = client.post(api(pid, "/settings/project-folder"), json={"path": str(proj / "nope")})
        assert bad.status_code == 400


def test_focuses_endpoint_saves_appears_in_settings_and_reloads(monkeypatch):
    """POST settings/focuses persists the (normalised) focuses, surfaces them in GET
    settings, and reference-swap reloads the runtime so the context line takes effect."""
    from fastapi.testclient import TestClient

    from assistant.gateway.app import create_app
    from assistant.gateway.profile_manager import ProfileManager
    from assistant.profiles import create_profile, profile_dir

    use_fake_agent(monkeypatch)
    meta = create_profile("Work", "teal")
    profile_dir(meta.id).mkdir(parents=True, exist_ok=True)
    manager = ProfileManager(memory=False, persist=False)
    app = create_app(manager)
    with TestClient(app) as client:
        pid = meta.id
        assert client.get(api(pid, "/settings")).json()["focuses"] == []

        reloaded: list[str] = []
        orig = manager.reload

        async def spy(p):
            reloaded.append(p)
            return await orig(p)

        monkeypatch.setattr(manager, "reload", spy)

        # client sends lowercase slugs; junk is dropped, order kept
        resp = client.post(
            api(pid, "/settings/focuses"),
            json={"focuses": ["Coding", "research", "not a slug!"]},
        )
        assert resp.status_code == 200
        assert resp.json() == {"ok": True, "focuses": ["coding", "research"]}
        assert reloaded == [pid]  # context change → runtime reloaded

        assert client.get(api(pid, "/settings")).json()["focuses"] == ["coding", "research"]

        # clearing persists too
        assert (
            client.post(api(pid, "/settings/focuses"), json={"focuses": []}).json()["focuses"] == []
        )
        assert client.get(api(pid, "/settings")).json()["focuses"] == []


def test_fs_list_endpoint_lists_subdirs(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    (tmp_path / "alpha").mkdir()
    (tmp_path / "beta").mkdir()
    (tmp_path / "file.txt").write_text("x")

    use_fake_agent(monkeypatch)
    app, _pid = make_profile_app()
    with TestClient(app) as client:
        r = client.get("/api/fs/list", params={"path": str(tmp_path)}).json()
        assert r["ok"] is True
        assert [d["name"] for d in r["dirs"]] == ["alpha", "beta"]

        bad = client.get("/api/fs/list", params={"path": str(tmp_path / "missing")}).json()
        assert bad["ok"] is False


def test_stream_roundtrip(profile_app):
    """The stream WebSocket replays history (ready) then runs a turn (turn_end)."""
    client, pid = profile_app
    with client.websocket_connect(api(pid, "/stream?session=w1")) as ws:
        assert ws.receive_json()["type"] == "ready"
        ws.send_json({"text": "ping"})
        assert ws.receive_json()["type"] == "turn_end"


def test_stream_unknown_profile_ws_closed(profile_app):
    """A stream WS on an unknown pid is closed before accept (code 4404)."""
    from starlette.websockets import WebSocketDisconnect

    client, _pid = profile_app
    with pytest.raises(WebSocketDisconnect) as exc:
        with client.websocket_connect(api("ghost", "/stream?session=x")) as ws:
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


async def test_hitl_routes_served_by_gateway(monkeypatch):
    """The global /hitl page dispatches to the profile whose registry holds the id;
    the profile-scoped /hitl/pending lists that profile's questions."""
    from httpx import ASGITransport, AsyncClient

    from assistant import profiles
    from assistant.gateway.app import create_app
    from assistant.gateway.profile_manager import ProfileManager
    from assistant.hitl.base import Question

    use_fake_agent(monkeypatch)
    meta = profiles.create_profile("Test", "teal")
    profiles.profile_dir(meta.id).mkdir(parents=True, exist_ok=True)
    manager = ProfileManager(memory=False, persist=False)
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
    import base64

    from assistant.gateway.app import _decode_attachments

    data = base64.b64encode(b"hello").decode()
    out = _decode_attachments([{"name": "a.png", "mime": "image/png", "data": data}])
    assert len(out) == 1
    assert _decode_attachments(None) == []
    assert _decode_attachments([{"name": "x.png", "data": ""}]) == []  # empty → skipped


def test_stream_timeout_sends_error_frame(monkeypatch):
    """A turn that exceeds REPLY_TIMEOUT surfaces an error frame on the stream WS."""
    from fastapi.testclient import TestClient

    import assistant.gateway.core as core_mod

    class _HangAgent:
        tools = []

        async def ask(self, *a, stream=None, **k):
            await asyncio.Event().wait()  # never returns → triggers wait_for timeout

    use_fake_agent(monkeypatch, lambda *a, **k: _HangAgent())
    monkeypatch.setattr(core_mod, "REPLY_TIMEOUT", 0.2)

    app, pid = make_profile_app()
    with TestClient(app) as client:
        with client.websocket_connect(api(pid, "/stream?session=s1")) as ws:
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


async def test_gateway_asker_timeout_denies():
    from assistant.hitl import GatewayAsker, HitlServer
    from assistant.hitl.base import Question
    from assistant.permissions import DENY

    asker = GatewayAsker(HitlServer(), timeout=0.05)
    answer = await asker.ask(Question(text="?", options=["Allow once", "Deny"]))
    assert answer == DENY  # unanswered prompt fails safe


@pytest.mark.integration
async def test_gateway_real_agent_multiturn_and_isolation():
    """End-to-end with the real agent: multi-turn recall + session isolation."""
    from assistant.gateway.core import Gateway

    gw = Gateway(memory=False)
    await gw.start()
    try:
        await gw.send_message(
            "My codeword is KIWI-7. Acknowledge in one sentence.", session_id="s1"
        )
        recall = await gw.send_message("What is my codeword? One word.", session_id="s1")
        assert "KIWI-7" in recall.upper()

        other = await gw.send_message(
            "What is my codeword? If unknown, reply exactly UNKNOWN.",
            session_id="s2",
        )
        assert "KIWI-7" not in other.upper()
    finally:
        await gw.close()


@pytest.mark.integration
async def test_conversation_resumes_across_restart(tmp_path):
    """A brand-new Gateway over the same data dir keeps full conversation context."""
    from assistant.config import load_config
    from assistant.gateway.core import Gateway

    cfg = load_config()
    cfg.data_dir = tmp_path  # isolate the session store

    gw1 = Gateway(config=cfg, memory=False)
    await gw1.start()
    await gw1.send_message("My lucky number is 7. Acknowledge.", session_id="resume-1")
    await gw1.close()  # simulate shutdown

    gw2 = Gateway(config=cfg, memory=False)
    await gw2.start()
    recall = await gw2.send_message(
        "What is my lucky number? Reply with just the digit.", session_id="resume-1"
    )
    assert "7" in recall
    await gw2.close()


# --- cross-origin guard (defends a localhost gateway from malicious web pages) ---


def test_origin_ok_unit(monkeypatch):
    """The same-origin rule: no-Origin and same host:port pass; others don't."""
    from assistant.gateway.app import _origin_ok

    monkeypatch.delenv("AG2ASSISTANT_ALLOWED_ORIGINS", raising=False)
    assert _origin_ok(None, "127.0.0.1:8800")  # non-browser caller
    assert _origin_ok("http://127.0.0.1:8800", "127.0.0.1:8800")  # same-origin
    assert _origin_ok("http://127.0.0.1:8800/", "127.0.0.1:8800")  # trailing slash
    assert not _origin_ok("http://evil.example", "127.0.0.1:8800")  # other site
    assert not _origin_ok("http://127.0.0.1:9999", "127.0.0.1:8800")  # other port


def test_origin_allowlist_env(monkeypatch):
    """AG2ASSISTANT_ALLOWED_ORIGINS adds extra accepted origins for proxied demos."""
    from assistant.gateway.app import _origin_ok

    monkeypatch.setenv("AG2ASSISTANT_ALLOWED_ORIGINS", "https://demo.example, http://foo")
    assert _origin_ok("https://demo.example", "127.0.0.1:8800")
    assert not _origin_ok("https://other.example", "127.0.0.1:8800")


def test_cross_origin_requests_rejected(monkeypatch):
    """Cross-origin REST and WebSocket attempts are refused; same-origin works."""
    from fastapi.testclient import TestClient
    from starlette.websockets import WebSocketDisconnect

    monkeypatch.delenv("AG2ASSISTANT_ALLOWED_ORIGINS", raising=False)
    use_fake_agent(monkeypatch)

    app, pid = make_profile_app()
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
                api(pid, "/stream?session=x"), headers={"origin": "http://evil.example"}
            ) as ws:
                ws.receive_json()

        # same-origin WebSocket still connects and replays history
        with client.websocket_connect(
            api(pid, "/stream?session=y"), headers={"origin": "http://testserver"}
        ) as ws:
            assert ws.receive_json()["type"] == "ready"


# --- POST /api/identity: seed the universal doc from web onboarding ---------- #
#
# The web onboarding "About you" step posts identity answers here; the endpoint
# seeds the shared universal "who the user is" doc via the SAME identity_document
# helper the CLI interview uses (format parity), and is seed-only: it refuses to
# clobber an existing doc and no-ops on an all-empty payload. Seeding it is what
# keeps the in-chat interview from firing for a web-onboarded user.


def _identity_app():
    from assistant.gateway.app import create_app
    from assistant.gateway.profile_manager import ProfileManager

    return create_app(ProfileManager(memory=False, persist=False))


def test_identity_endpoint_seeds_when_empty():
    from fastapi.testclient import TestClient

    app = _identity_app()
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


def test_identity_endpoint_refuses_to_clobber_existing_doc():
    from fastapi.testclient import TestClient

    app = _identity_app()
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


def test_identity_endpoint_noops_when_all_empty():
    from fastapi.testclient import TestClient

    app = _identity_app()
    with TestClient(app) as client:
        r = client.post(
            "/api/identity", json={"name": "", "location": "  ", "hours": "", "style": ""}
        )
        assert r.status_code == 200
        body = r.json()
        assert body["seeded"] is False and body["reason"] == "empty"
        assert client.get("/api/memory").json()["text"].strip() == ""


async def test_identity_seed_disables_interview_gate():
    """After the endpoint seeds the universal store, the in-chat interview gate is
    closed — a web-onboarded user's first chat won't trigger it."""
    from fastapi.testclient import TestClient

    from assistant.config import load_config
    from assistant.onboarding import needs_onboarding

    user_store_path = load_config().root_dir / "user.db"
    assert await needs_onboarding(user_store_path) is True  # fresh install: gate open

    app = _identity_app()
    with TestClient(app) as client:
        assert client.post("/api/identity", json={"location": "Sydney"}).json()["seeded"] is True

    assert await needs_onboarding(user_store_path) is False  # gate now closed


async def test_identity_document_endpoint_parity():
    """The endpoint's stored doc is byte-identical to run_onboarding's for the same
    answers — both go through identity_document, the single formatter."""
    from fastapi.testclient import TestClient

    from assistant import onboarding
    from assistant.config import load_config
    from assistant.memory import PROFILE_PATH, build_profile_store

    answers = {"name": "Ada", "location": "London", "hours": "9am–6pm", "style": "Short & direct"}

    app = _identity_app()
    with TestClient(app) as client:
        client.post("/api/identity", json=answers)
        endpoint_doc = client.get("/api/memory").json()["text"]

    # run_onboarding writes to a separate store; compare its stored doc.
    class _Asker:
        def __init__(self, vals):
            self._vals = list(vals)

        async def ask(self, q, timeout=None):
            return self._vals.pop(0)

    cli_store = load_config().root_dir / "cli_user.db"
    await onboarding.run_onboarding(
        _Asker(["Ada", "London", "9am–6pm", "Short & direct"]),
        user_store_path=cli_store,
        env_path=load_config().root_dir / ".env",
    )
    cli_doc = await build_profile_store(cli_store).read(PROFILE_PATH)
    assert endpoint_doc == cli_doc


# ---- System health endpoint (the status-dot source, GET /health) ---------------


def _fake_key_status(*, present: bool):
    """A secrets.status() stand-in: all three providers set (or not), plus ollama."""
    flag = {"set": present, "hint": "…key" if present else ""}
    return {
        "openai": dict(flag),
        "gemini": dict(flag),
        "anthropic": dict(flag),
        "ollama": {"set": False, "base_url": "http://localhost:11434"},
    }


def test_profile_health_ok_and_down(profile_app, monkeypatch):
    """The cheap health aggregate: healthy when the agent is up and the configured
    provider has a key; 'down' (agent can't run) when the key is missing. The dot
    reads `overall`; the panel reads `checks`."""
    import assistant.secrets as secrets

    client, pid = profile_app

    # Provider key present + faked agent alive → all core signals green.
    monkeypatch.setattr(secrets, "status", lambda: _fake_key_status(present=True))
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
    monkeypatch.setattr(secrets, "status", lambda: _fake_key_status(present=False))
    body = client.get(api(pid, "/health")).json()
    assert body["overall"] == "down"
    provider = next(c for c in body["checks"] if c["id"] == "provider")
    assert provider["state"] == "down"


def test_profile_health_warns_on_channel_error(profile_app, monkeypatch):
    """A messaging channel bound to this profile that failed to start (start error
    recorded) rolls the overall up to 'warn' — auxiliary, so amber not red."""
    import assistant.profiles as profiles_mod
    import assistant.secrets as secrets

    client, pid = profile_app

    monkeypatch.setattr(secrets, "status", lambda: _fake_key_status(present=True))
    # Bind discord to this profile and record a start error on the live manager.
    monkeypatch.setattr(profiles_mod, "channel_bindings", lambda: {"discord": pid})
    client.app.state.profiles.channel_errors["discord"] = "invalid bot token"

    body = client.get(api(pid, "/health")).json()
    assert body["overall"] == "warn"
    channels = next(c for c in body["checks"] if c["id"] == "channels")
    assert channels["state"] == "warn"
    assert any(it["platform"] == "discord" and it["error"] for it in channels["items"])


# ---- Named LLM configurations (global /api/llm-configs) ------------------------


def test_llm_configs_crud_use_delete_and_key_secrecy(profile_app, monkeypatch):
    """Create/update/use/delete named configs; the raw per-config key is never echoed
    (only a set/hint), and a config's secret is cleaned up on delete."""
    from assistant import secrets

    client, pid = profile_app
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    # empty install
    r = client.get("/api/llm-configs").json()
    assert r == {"configs": [], "active": None, "env_override": None}

    # create a local-server config with a secret key + activate
    r = client.post(
        "/api/llm-configs",
        json={
            "name": "Local",
            "type": "openai",
            "model": "gemma-4",
            "base_url": "http://192.168.0.55:8080/v1",
            "api_key": "sk-secret-1234",
            "activate": True,
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    cid = body["config"]["id"]
    assert body["ok"] is True and body["active"] == cid
    # raw key never echoed — only the set/hint summary
    assert body["config"]["key"] == {"set": True, "hint": "…1234"}
    assert "sk-secret-1234" not in r.text

    g = client.get("/api/llm-configs").json()
    assert g["active"] == cid
    assert g["configs"][0]["base_url"] == "http://192.168.0.55:8080/v1"
    assert g["configs"][0]["key"] == {"set": True, "hint": "…1234"}
    assert "sk-secret-1234" not in client.get("/api/llm-configs").text
    # the honest key labels: its own key wins; the shared env slot is reported too
    entry = g["configs"][0]
    assert entry["key_source"] == "config"
    assert entry["shared_key"]["env"] == "OPENAI_API_KEY"
    assert entry["shared_key"]["set"] is False  # env cleared above

    # update leaving api_key None → key unchanged, model changed
    r = client.post(
        f"/api/llm-configs/{cid}",
        json={
            "name": "Local",
            "type": "openai",
            "model": "gemma-5",
            "base_url": "http://192.168.0.55:8080/v1",
        },
    )
    assert r.status_code == 200
    g = client.get("/api/llm-configs").json()
    assert g["configs"][0]["model"] == "gemma-5"
    assert g["configs"][0]["key"]["set"] is True  # untouched

    # delete-active → 409
    assert client.delete(f"/api/llm-configs/{cid}").status_code == 409

    # add a second, switch to it, then the first is deletable
    r2 = client.post("/api/llm-configs", json={"name": "G", "type": "gemini", "model": "gemini-x"})
    cid2 = r2.json()["config"]["id"]
    assert client.post(f"/api/llm-configs/{cid2}/use").status_code == 200
    assert client.get("/api/llm-configs").json()["active"] == cid2

    assert client.delete(f"/api/llm-configs/{cid}").status_code == 200
    assert secrets.config_key(cid) == ""  # secret cleaned up

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


def test_llm_config_env_override_surfaced(profile_app, monkeypatch):
    """When AG2ASSISTANT_MODEL / _LLM_PROVIDER is set (they pin the model in
    load_config), GET reports it so the UI can show the 'pinned by env' banner."""
    client, pid = profile_app
    monkeypatch.setenv("AG2ASSISTANT_LLM_PROVIDER", "openai")
    monkeypatch.setenv("AG2ASSISTANT_MODEL", "gpt-x")
    assert client.get("/api/llm-configs").json()["env_override"] == {
        "provider": "openai",
        "model": "gpt-x",
    }


def test_llm_config_test_endpoint_pong_and_failures(profile_app, monkeypatch):
    """The /test endpoint runs a real PONG round-trip (agent faked here): a reply →
    {ok, reply, latency_ms}; any exception or a timeout → 502 {ok:false, error}."""
    import ag2

    from assistant.gateway import app as app_mod

    client, pid = profile_app
    entry = client.post(
        "/api/llm-configs", json={"name": "G", "type": "gemini", "model": "gemini-x"}
    ).json()["config"]

    class _OkAgent:
        def __init__(self, *a, **k):
            pass

        async def ask(self, *a, **k):
            return FakeReply("PONG")

    monkeypatch.setattr(ag2, "Agent", _OkAgent)
    r = client.post(f"/api/llm-configs/{entry['id']}/test")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True and body["reply"] == "PONG"
    assert isinstance(body["latency_ms"], int)

    class _BoomAgent:
        def __init__(self, *a, **k):
            pass

        async def ask(self, *a, **k):
            raise RuntimeError("nope-boom")

    monkeypatch.setattr(ag2, "Agent", _BoomAgent)
    r = client.post(f"/api/llm-configs/{entry['id']}/test")
    assert r.status_code == 502
    assert "nope-boom" in r.json()["error"]

    # a wedged call trips the (monkeypatched-tiny) timeout → 502
    class _HangAgent:
        def __init__(self, *a, **k):
            pass

        async def ask(self, *a, **k):
            await asyncio.sleep(0.5)
            return FakeReply("late")

    monkeypatch.setattr(ag2, "Agent", _HangAgent)
    monkeypatch.setattr(app_mod, "_LLM_TEST_TIMEOUT_S", 0.01)
    r = client.post(f"/api/llm-configs/{entry['id']}/test")
    assert r.status_code == 502

    # unknown id → 404
    assert client.post("/api/llm-configs/c_ghost/test").status_code == 404


def test_llm_config_draft_test_endpoint(profile_app, monkeypatch):
    """POST /api/llm-configs/test pings an UNSAVED editor draft: nothing persisted,
    a typed api_key is used for the call, a blank one falls back to the stored key
    of the config named by ``id``, and validation errors come back as 400 (the
    literal "test" segment must not be captured by the /{cid} update route)."""
    import ag2

    from assistant import llm_configs

    client, pid = profile_app

    captured = {}

    class _OkAgent:
        def __init__(self, name, config=None, **k):
            captured["config"] = config

        async def ask(self, *a, **k):
            return FakeReply("PONG")

    monkeypatch.setattr(ag2, "Agent", _OkAgent)

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
    assert llm_configs.list_configs() == []  # nothing persisted
    assert getattr(captured["config"], "api_key", None) == "sk-draft-key-1"  # draft key used

    # editing an existing config with a stored key: blank draft key falls back to it
    entry = client.post(
        "/api/llm-configs",
        json={
            "name": "E",
            "type": "openai",
            "model": "m",
            "base_url": "http://h/v1",
            "api_key": "sk-stored-key-2",
        },
    ).json()["config"]
    r = client.post(
        "/api/llm-configs/test",
        json={
            "id": entry["id"],
            "name": "E",
            "type": "openai",
            "model": "m",
            "base_url": "http://h/v1",
        },
    )
    assert r.status_code == 200
    assert getattr(captured["config"], "api_key", None) == "sk-stored-key-2"

    # a bad draft (unknown type) → 400 with the validator's message, not a 404 from
    # the update route misparsing "test" as a config id
    r = client.post("/api/llm-configs/test", json={"name": "X", "type": "nope", "model": "m"})
    assert r.status_code == 400
    assert "type must be one of" in r.json()["error"]


def test_llm_config_subscription_entry_view_signed_in(profile_app, monkeypatch):
    """An openai_subscription config's row/chip need the live ChatGPT sign-in state and
    a 'subscription' key_source so the UI can label it honestly without a 2nd fetch.
    Endpoint fields are stripped for this type (codex_auth owns the endpoint)."""
    from assistant import codex_auth

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

    monkeypatch.setattr(codex_auth, "status", lambda: {"signed_in": True, "account_id": "acc"})
    assert client.get("/api/llm-configs").json()["configs"][0]["signed_in"] is True

    monkeypatch.setattr(codex_auth, "status", lambda: {"signed_in": False})
    assert client.get("/api/llm-configs").json()["configs"][0]["signed_in"] is False


def test_llm_config_subscription_draft_test_routes_to_backend(profile_app, monkeypatch):
    """Testing a subscription draft flows through model_config's subscription branch:
    the probe carries auth_mode=subscription, so the built client points at the ChatGPT
    backend with the codex token and server-side storage disabled."""
    import ag2

    from assistant import codex_auth

    client, pid = profile_app
    monkeypatch.setattr(
        codex_auth,
        "creds_best_effort",
        lambda: codex_auth.Creds(access_token="TOK", account_id="acc"),
    )

    captured = {}

    class _OkAgent:
        def __init__(self, name, config=None, **k):
            captured["config"] = config

        async def ask(self, *a, **k):
            return FakeReply("PONG")

    monkeypatch.setattr(ag2, "Agent", _OkAgent)
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
