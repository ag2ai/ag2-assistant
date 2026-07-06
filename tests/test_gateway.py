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
