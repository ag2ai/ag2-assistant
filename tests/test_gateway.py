"""Tests for the AGClaw gateway and its REST/WebSocket facade.

Unit tests stub the agent so they run without an LLM. Integration tests exercise
the real agent end-to-end.
"""

import asyncio

import pytest


class _FakeReply:
    """Minimal stand-in for AgentReply."""

    def __init__(self, body: str):
        self.body = body


class _FakeAgent:
    """Counts turns per stream id so echo[N] proves per-session continuity."""

    def __init__(self):
        self._counts: dict = {}

    async def ask(self, *msg, stream=None, **kwargs) -> _FakeReply:
        sid = getattr(stream, "id", "default")
        self._counts[sid] = self._counts.get(sid, 0) + 1
        return _FakeReply(f"echo[{self._counts[sid]}]: {msg[0]}")


@pytest.fixture
def fake_gateway(monkeypatch):
    """A Gateway whose agent is a deterministic fake (no LLM, no persistence)."""
    from assistant.gateway.core import Gateway

    gw = Gateway(memory=False, persist=False)
    gw._agent = _FakeAgent()
    return gw


async def test_send_message_returns_reply(fake_gateway):
    reply = await fake_gateway.send_message("hello", session_id="s1")
    assert reply == "echo[1]: hello"


async def test_on_tool_reports_each_tool_name(fake_gateway):
    """The on_tool callback receives every tool the agent calls (single + batch),
    and the stream subscription is cleaned up when the turn ends."""
    from autogen.beta.events import ToolCallEvent, ToolCallsEvent

    captured: dict = {}

    class _Stream:  # mimics MemoryStream's subscribe/unsubscribe + event delivery
        def subscribe(self, fn):
            captured["fn"] = fn
            return "sub-1"

        def unsubscribe(self, sid):
            captured["unsub"] = sid

    names: list = []

    async def on_tool(name):
        names.append(name)

    async def ask_coro():
        # Each call arrives twice (batch + provider event) with the SAME id, the
        # way AG2 really emits them — must still report once per id.
        await captured["fn"](ToolCallsEvent(calls=[ToolCallEvent(id="a", name="web_search")]))
        await captured["fn"](ToolCallEvent(id="a", name="web_search"))
        await captured["fn"](ToolCallsEvent(calls=[ToolCallEvent(id="b", name="get_task")]))
        await captured["fn"](ToolCallEvent(id="b", name="get_task"))
        return _FakeReply("done")

    reply = await fake_gateway._ask_watching_tools(_Stream(), ask_coro(), on_tool)

    assert reply.body == "done"
    assert names == ["web_search", "get_task"]  # deduped by id, not ×2
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

    monkeypatch.setattr(core_mod, "create_agent", lambda *a, **k: _FakeAgent())

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


def test_sessions_rest_endpoints(monkeypatch, tmp_path):
    from fastapi.testclient import TestClient

    import assistant.gateway.app as app_mod
    import assistant.gateway.core as core_mod
    from assistant.config import Config

    monkeypatch.setattr(core_mod, "create_agent", lambda *a, **k: _FakeAgent())
    app = app_mod.create_app(config=Config(data_dir=tmp_path), memory=False)
    with TestClient(app) as client:
        client.post("/api/message", json={"text": "first msg", "session_id": "u1"})
        sessions = client.get("/api/sessions").json()["sessions"]
        assert any(s["session_id"] == "u1" for s in sessions)
        msgs = client.get("/api/sessions/u1").json()["messages"]
        assert msgs[0]["text"] == "first msg"


def test_rest_message_endpoint(monkeypatch):
    """The REST facade returns a reply for a posted message (fake agent)."""
    from fastapi.testclient import TestClient

    import assistant.gateway.app as app_mod
    import assistant.gateway.core as core_mod

    # Patch the agent factory where the gateway core looks it up.
    monkeypatch.setattr(core_mod, "create_agent", lambda *a, **k: _FakeAgent())

    app = app_mod.create_app(memory=False, persist=False)
    with TestClient(app) as client:
        health = client.get("/api/health").json()
        assert health["status"] == "ok"

        resp = client.post(
            "/api/message", json={"text": "hi there", "session_id": "u1"}
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["reply"] == "echo[1]: hi there"
        assert body["session_id"] == "u1"


def test_create_app_shares_injected_gateway(fake_gateway):
    """When a gateway is injected (combined `agclaw run`), the app reuses it
    rather than creating its own, and doesn't tear it down on shutdown."""
    from fastapi.testclient import TestClient

    import assistant.gateway.app as app_mod

    app = app_mod.create_app(gateway=fake_gateway)
    with TestClient(app) as client:
        resp = client.post("/api/message", json={"text": "hi", "session_id": "u1"})
        assert resp.json()["reply"] == "echo[1]: hi"
    # the same shared gateway holds the session, and survives app shutdown
    assert fake_gateway.status()["sessions"] == 1
    assert fake_gateway.status()["status"] == "ok"


def test_stream_roundtrip(monkeypatch):
    """The /api/stream WebSocket replays history (ready) then runs a turn (turn_end)."""
    from fastapi.testclient import TestClient

    import assistant.gateway.app as app_mod
    import assistant.gateway.core as core_mod

    monkeypatch.setattr(core_mod, "create_agent", lambda *a, **k: _FakeAgent())

    app = app_mod.create_app(memory=False, persist=False)
    with TestClient(app) as client:
        with client.websocket_connect("/api/stream?session=w1") as ws:
            assert ws.receive_json()["type"] == "ready"
            ws.send_json({"text": "ping"})
            assert ws.receive_json()["type"] == "turn_end"


# --- gateway-hosted HITL ---


async def test_root_redirects_to_app(fake_gateway):
    """/ and unknown paths redirect to the Svelte app at /app."""
    from fastapi.testclient import TestClient

    import assistant.gateway.app as app_mod

    app = app_mod.create_app(gateway=fake_gateway)
    with TestClient(app) as client:
        root = client.get("/", follow_redirects=False)
        assert root.status_code == 307 and root.headers["location"] == "/app/"
        other = client.get("/anything", follow_redirects=False)
        assert other.status_code == 307 and other.headers["location"] == "/app/"
        assert client.get("/api/bogus", follow_redirects=False).status_code == 404


def test_favicon_served(fake_gateway):
    from fastapi.testclient import TestClient

    import assistant.gateway.app as app_mod

    app = app_mod.create_app(gateway=fake_gateway)
    with TestClient(app) as client:
        for path in ("/faviconlight.svg", "/favicondark.svg", "/favicon.ico"):
            r = client.get(path)
            assert r.status_code == 200, path
            assert "svg" in r.headers["content-type"]
            assert "<svg" in r.text


async def test_hitl_routes_served_by_gateway(fake_gateway):
    """The gateway serves the styled /hitl page, lists pending, resolves answers."""
    from httpx import ASGITransport, AsyncClient

    import assistant.gateway.app as app_mod
    from assistant.hitl.base import Question

    app = app_mod.create_app(gateway=fake_gateway)
    # register a pending question in this test's loop (the routes don't need the
    # gateway, and app.state.hitl is wired at create_app time)
    req_id, fut = app.state.hitl.register(Question(text="Proceed?", options=["Yes", "No"]))

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as client:
        listed = (await client.get("/api/hitl/pending")).json()["pending"]
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
    """A turn that exceeds REPLY_TIMEOUT surfaces an error frame on /api/stream."""
    from fastapi.testclient import TestClient

    import assistant.gateway.app as app_mod
    import assistant.gateway.core as core_mod

    class _HangAgent:
        async def ask(self, *a, stream=None, **k):
            await asyncio.Event().wait()  # never returns → triggers wait_for timeout

    monkeypatch.setattr(core_mod, "create_agent", lambda *a, **k: _HangAgent())
    monkeypatch.setattr(core_mod, "REPLY_TIMEOUT", 0.2)

    app = app_mod.create_app(memory=False, persist=False)
    with TestClient(app) as client:
        with client.websocket_connect("/api/stream?session=s1") as ws:
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
        recall = await gw.send_message(
            "What is my codeword? One word.", session_id="s1"
        )
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
    await gw1.send_message(
        "My lucky number is 7. Acknowledge.", session_id="resume-1"
    )
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

    monkeypatch.delenv("AGCLAW_ALLOWED_ORIGINS", raising=False)
    assert _origin_ok(None, "127.0.0.1:8800")                  # non-browser caller
    assert _origin_ok("http://127.0.0.1:8800", "127.0.0.1:8800")   # same-origin
    assert _origin_ok("http://127.0.0.1:8800/", "127.0.0.1:8800")  # trailing slash
    assert not _origin_ok("http://evil.example", "127.0.0.1:8800")  # other site
    assert not _origin_ok("http://127.0.0.1:9999", "127.0.0.1:8800")  # other port


def test_origin_allowlist_env(monkeypatch):
    """AGCLAW_ALLOWED_ORIGINS adds extra accepted origins for proxied demos."""
    from assistant.gateway.app import _origin_ok

    monkeypatch.setenv("AGCLAW_ALLOWED_ORIGINS", "https://demo.example, http://foo")
    assert _origin_ok("https://demo.example", "127.0.0.1:8800")
    assert not _origin_ok("https://other.example", "127.0.0.1:8800")


def test_cross_origin_requests_rejected(monkeypatch):
    """Cross-origin REST and WebSocket attempts are refused; same-origin works."""
    from fastapi.testclient import TestClient
    from starlette.websockets import WebSocketDisconnect

    import assistant.gateway.app as app_mod
    import assistant.gateway.core as core_mod

    monkeypatch.delenv("AGCLAW_ALLOWED_ORIGINS", raising=False)
    monkeypatch.setattr(core_mod, "create_agent", lambda *a, **k: _FakeAgent())

    app = app_mod.create_app(memory=False, persist=False)
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
                "/api/stream?session=x", headers={"origin": "http://evil.example"}
            ) as ws:
                ws.receive_json()

        # same-origin WebSocket still connects and replays history
        with client.websocket_connect(
            "/api/stream?session=y", headers={"origin": "http://testserver"}
        ) as ws:
            assert ws.receive_json()["type"] == "ready"
