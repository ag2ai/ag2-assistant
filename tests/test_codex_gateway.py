"""Gateway HTTP routes for ChatGPT-subscription auth (/api/codex/*) and the
subscription named-LLM configuration (/api/llm-configs, type openai_subscription)."""

import pytest

from assistant import codex_auth


@pytest.fixture(autouse=True)
def _no_real_loopback(monkeypatch):
    """The /api/codex/login_url route starts a background loopback listener on port
    1455. In tests we never complete that real OAuth round-trip, so stub the capture
    to fail fast — no socket bind, no 300s-lingering thread. The manual /submit path
    (the headless fallback) is what these tests exercise instead."""

    def _fail(*_a, **_k):
        raise codex_auth.CodexAuthError("no loopback in tests")

    monkeypatch.setattr(codex_auth, "_capture_code", _fail)


def _fake_jwt_acc(acc: str) -> str:
    import base64
    import json

    h = base64.urlsafe_b64encode(b'{"alg":"none"}').rstrip(b"=").decode()
    b = (
        base64.urlsafe_b64encode(json.dumps({"chatgpt_account_id": acc}).encode())
        .rstrip(b"=")
        .decode()
    )
    return f"{h}.{b}.sig"


def test_codex_status_signed_out_then_in(profile_app):
    client, _pid = profile_app
    assert client.get("/api/codex/status").json()["signed_in"] is False

    codex_auth._store_tokens(
        {
            "access_token": "A",
            "refresh_token": "R",
            "id_token": _fake_jwt_acc("acc-9"),
            "expires_in": 3600,
        }
    )
    st = client.get("/api/codex/status").json()
    assert st["signed_in"] is True and st["account_id"] == "acc-9"


def test_codex_login_url_returns_consent_url_and_state(profile_app):
    client, _pid = profile_app
    body = client.post("/api/codex/login_url").json()
    assert body["ok"] is True
    assert body["auth_url"].startswith(codex_auth.AUTH_URL)
    assert "code_challenge_method=S256" in body["auth_url"]
    assert body["state"]  # opaque anti-CSRF token


def test_codex_submit_exchanges_pasted_code(profile_app, monkeypatch):
    client, _pid = profile_app

    # Begin a flow to register a pending PKCE verifier for the returned state.
    state = client.post("/api/codex/login_url").json()["state"]

    class FakeResp:
        status_code = 200
        text = ""

        def json(self):
            return {"access_token": "AX", "refresh_token": "RX", "expires_in": 3600}

    import httpx

    monkeypatch.setattr(httpx, "post", lambda *a, **k: FakeResp())
    r = client.post("/api/codex/submit", json={"state": state, "code": "the-code"})
    assert r.status_code == 200 and r.json()["ok"] is True
    assert codex_auth.status()["signed_in"] is True


def test_codex_submit_unknown_state_400(profile_app):
    client, _pid = profile_app
    r = client.post("/api/codex/submit", json={"state": "nope", "code": "x"})
    assert r.status_code == 400 and r.json()["ok"] is False


def test_codex_logout(profile_app):
    client, _pid = profile_app
    codex_auth._store_tokens({"access_token": "A", "refresh_token": "R", "expires_in": 3600})
    assert client.post("/api/codex/logout").json()["ok"] is True
    assert codex_auth.status()["signed_in"] is False


def test_subscription_config_not_usable_until_signed_in(profile_app):
    """A subscription config exists but reads as not-signed-in until a ChatGPT session
    is present — the entry-view signed_in flag drives the UI's warn chip."""
    client, pid = profile_app
    entry = client.post(
        "/api/llm-configs",
        json={"name": "ChatGPT", "type": "openai_subscription", "model": "gpt-5.5"},
    ).json()["config"]
    assert entry["key_source"] == "subscription"
    assert entry["signed_in"] is False  # no session yet

    codex_auth._store_tokens({"access_token": "A", "refresh_token": "R", "expires_in": 3600})
    view = client.get("/api/llm-configs").json()["configs"][0]
    assert view["signed_in"] is True


def test_subscription_config_activation_sets_auth_mode(profile_app, monkeypatch):
    """Activating an openai_subscription config makes it active and derives
    auth_mode=subscription in a fresh load_config; GET /settings surfaces sign-in."""
    from assistant import llm_configs
    from assistant.config import load_config

    monkeypatch.delenv("AG2ASSISTANT_OPENAI_AUTH_MODE", raising=False)
    client, pid = profile_app
    codex_auth._store_tokens({"access_token": "A", "refresh_token": "R", "expires_in": 3600})
    r = client.post(
        "/api/llm-configs",
        json={
            "name": "ChatGPT",
            "type": "openai_subscription",
            "model": "gpt-5.5",
            "activate": True,
        },
    )
    assert r.status_code == 200 and r.json()["ok"] is True
    cid = r.json()["config"]["id"]
    assert llm_configs.active_id() == cid
    # A fresh load derives the subscription auth mode from the active entry's type.
    cfg = load_config()
    assert cfg.llm.provider == "openai"
    assert cfg.llm.auth_mode == "subscription"
    # GET /settings still surfaces the ChatGPT sign-in state.
    assert client.get(f"/api/p/{pid}/settings").json()["codex"]["signed_in"] is True
