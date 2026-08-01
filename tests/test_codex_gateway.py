"""Gateway HTTP routes for ChatGPT-subscription auth (/api/codex/*) and the
subscription named-LLM configuration (/api/llm-configs, type openai_subscription)."""

import base64
import json

import pytest

from assistant import codex_auth
from assistant.codex_auth import CodexAuth
from assistant.config import resolve_config
from assistant.llm_configs import LlmConfigStore
from tests.support import http


@pytest.fixture
def auth(paths) -> CodexAuth:
    """The token store the routes read, over the same layout the app runs on."""
    return CodexAuth(paths)


def _fake_jwt_acc(acc: str) -> str:
    h = base64.urlsafe_b64encode(b'{"alg":"none"}').rstrip(b"=").decode()
    b = (
        base64.urlsafe_b64encode(json.dumps({"chatgpt_account_id": acc}).encode())
        .rstrip(b"=")
        .decode()
    )
    return f"{h}.{b}.sig"


def test_codex_status_signed_out_then_in(profile_app, auth):
    client, _pid = profile_app
    assert client.get("/api/codex/status").json()["signed_in"] is False

    auth._store_tokens(
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


def test_codex_submit_exchanges_pasted_code(profile_app_factory, auth):
    handler, sent = http.recording_responder(
        {"access_token": "AX", "refresh_token": "RX", "expires_in": 3600}
    )
    client, _pid = profile_app_factory(codex_client=http.client(handler))

    # Begin a flow to register a pending PKCE verifier for the returned state.
    state = client.post("/api/codex/login_url").json()["state"]

    r = client.post("/api/codex/submit", json={"state": state, "code": "the-code"})
    assert r.status_code == 200 and r.json()["ok"] is True
    assert auth.status()["signed_in"] is True
    # the pasted code really went out as an authorization_code grant
    form = http.form_of(sent[0]["body"])
    assert form["grant_type"] == "authorization_code" and form["code"] == "the-code"


def test_codex_submit_unknown_state_400(profile_app):
    client, _pid = profile_app
    r = client.post("/api/codex/submit", json={"state": "nope", "code": "x"})
    assert r.status_code == 400 and r.json()["ok"] is False


def test_codex_logout(profile_app, auth):
    client, _pid = profile_app
    auth._store_tokens({"access_token": "A", "refresh_token": "R", "expires_in": 3600})
    assert client.post("/api/codex/logout").json()["ok"] is True
    assert auth.status()["signed_in"] is False


def test_subscription_config_not_usable_until_signed_in(profile_app, auth):
    """A subscription config exists but reads as not-signed-in until a ChatGPT session
    is present — the entry-view signed_in flag drives the UI's warn chip."""
    client, _pid = profile_app
    entry = client.post(
        "/api/llm-configs",
        json={"name": "ChatGPT", "type": "openai_subscription", "model": "gpt-5.5"},
    ).json()["config"]
    assert entry["key_source"] == "subscription"
    assert entry["signed_in"] is False  # no session yet

    auth._store_tokens({"access_token": "A", "refresh_token": "R", "expires_in": 3600})
    view = client.get("/api/llm-configs").json()["configs"][0]
    assert view["signed_in"] is True


def test_subscription_config_activation_sets_auth_mode(profile_app, auth, paths):
    """Activating an openai_subscription config makes it active and derives
    auth_mode=subscription in a fresh resolve; GET /settings surfaces sign-in."""
    client, pid = profile_app
    auth._store_tokens({"access_token": "A", "refresh_token": "R", "expires_in": 3600})
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
    assert LlmConfigStore(paths).active_id() == cid
    # A fresh resolve derives the subscription auth mode from the active entry's type.
    cfg = resolve_config({}, paths)
    assert cfg.llm.provider == "openai"
    assert cfg.llm.auth_mode == "subscription"
    # GET /settings still surfaces the ChatGPT sign-in state.
    assert client.get(f"/api/p/{pid}/settings").json()["codex"]["signed_in"] is True
