"""Tests for the AGClaw gateway and its REST/WebSocket facade.

Unit tests stub the agent so they run without an LLM. Integration tests exercise
the real agent end-to-end.
"""

import pytest


class _FakeReply:
    """Minimal stand-in for AgentReply that records the conversation chain."""

    def __init__(self, history: list[str]):
        self.history = history
        self.body = f"echo[{len(history)}]: {history[-1]}"

    async def ask(self, text: str, **kwargs) -> "_FakeReply":
        return _FakeReply(self.history + [text])


class _FakeAgent:
    async def ask(self, text: str, **kwargs) -> _FakeReply:
        return _FakeReply([text])


@pytest.fixture
def fake_gateway(monkeypatch):
    """A Gateway whose agent is a deterministic fake (no LLM)."""
    from agclaw.gateway.core import Gateway

    gw = Gateway(memory=False)
    gw._agent = _FakeAgent()
    return gw


async def test_send_message_returns_reply(fake_gateway):
    reply = await fake_gateway.send_message("hello", session_id="s1")
    assert reply == "echo[1]: hello"


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


def test_rest_message_endpoint(monkeypatch):
    """The REST facade returns a reply for a posted message (fake agent)."""
    from fastapi.testclient import TestClient

    import agclaw.gateway.app as app_mod
    import agclaw.gateway.core as core_mod

    # Patch the agent factory where the gateway core looks it up.
    monkeypatch.setattr(core_mod, "create_agent", lambda *a, **k: _FakeAgent())

    app = app_mod.create_app(memory=False)
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


def test_ws_message_roundtrip(monkeypatch):
    """The WebSocket facade streams thinking + reply frames."""
    from fastapi.testclient import TestClient

    import agclaw.gateway.app as app_mod
    import agclaw.gateway.core as core_mod

    monkeypatch.setattr(core_mod, "create_agent", lambda *a, **k: _FakeAgent())

    app = app_mod.create_app(memory=False)
    with TestClient(app) as client:
        with client.websocket_connect("/api/ws") as ws:
            ws.send_json({"text": "ping", "session_id": "w1"})
            first = ws.receive_json()
            assert first["type"] == "thinking"
            second = ws.receive_json()
            assert second["type"] == "reply"
            assert second["text"] == "echo[1]: ping"


@pytest.mark.integration
async def test_gateway_real_agent_multiturn_and_isolation():
    """End-to-end with the real agent: multi-turn recall + session isolation."""
    from agclaw.gateway.core import Gateway

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
