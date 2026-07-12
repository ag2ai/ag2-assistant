"""ChatGPT-subscription auth (assistant.codex_auth) + its model_config wiring.

No real network: the token endpoint is monkeypatched. The autouse HOME-isolation
fixture (conftest) points data_dir() at a tmp root, so each test writes its own
codex_auth.json.
"""

import base64
import json
import stat
import time

import httpx
import pytest

from assistant import agent, codex_auth
from assistant.config import load_config


def _fake_jwt(payload: dict) -> str:
    """A JWT with the given payload and a throwaway header/sig (unsigned; we only
    decode the payload)."""
    header = base64.urlsafe_b64encode(b'{"alg":"none"}').rstrip(b"=").decode()
    body = base64.urlsafe_b64encode(json.dumps(payload).encode()).rstrip(b"=").decode()
    return f"{header}.{body}.sig"


# --- PKCE + authorize URL --------------------------------------------------- #


def test_generate_pkce_distinct_and_urlsafe():
    verifier, challenge = codex_auth.generate_pkce()
    assert verifier and challenge and verifier != challenge
    # base64url, no padding
    assert "=" not in verifier and "=" not in challenge


def test_authorize_url_has_required_params():
    url = codex_auth.build_authorize_url("CHAL", "STATE123")
    for part in (
        "code_challenge=CHAL",
        "code_challenge_method=S256",
        "state=STATE123",
        f"client_id={codex_auth.CLIENT_ID}",
        "originator=codex_cli_rs",
        "id_token_add_organizations=true",
        "response_type=code",
    ):
        assert part in url, part


# --- pasted-value normalization (Docker/headless fallback) ------------------ #


def test_extract_auth_code_bare_code():
    assert codex_auth.extract_auth_code("  abc123  ") == "abc123"


def test_extract_auth_code_from_full_redirect_url():
    url = "http://localhost:1455/auth/callback?code=THE_CODE&state=xyz"
    assert codex_auth.extract_auth_code(url) == "THE_CODE"


def test_extract_auth_code_from_bare_query_string():
    assert codex_auth.extract_auth_code("code=THE_CODE&state=xyz") == "THE_CODE"


def test_extract_auth_code_empty():
    assert codex_auth.extract_auth_code("") == ""
    assert codex_auth.extract_auth_code(None) == ""


# --- account id extraction from JWT claims (three-level fallback) ----------- #


def test_account_id_top_level_claim():
    assert codex_auth.account_id_from(_fake_jwt({"chatgpt_account_id": "acc-top"}), "") == "acc-top"


def test_account_id_nested_auth_claim():
    tok = _fake_jwt({"https://api.openai.com/auth": {"chatgpt_account_id": "acc-nested"}})
    assert codex_auth.account_id_from(tok, "") == "acc-nested"


def test_account_id_organizations_fallback():
    tok = _fake_jwt({"https://api.openai.com/auth": {"organizations": [{"id": "org-1"}]}})
    assert codex_auth.account_id_from(tok, "") == "org-1"


def test_account_id_falls_back_to_access_token():
    access = _fake_jwt({"chatgpt_account_id": "from-access"})
    assert codex_auth.account_id_from("", access) == "from-access"


def test_account_id_none_when_absent():
    assert codex_auth.account_id_from("", "") is None
    assert codex_auth.account_id_from("not-a-jwt", "") is None


# --- token store: roundtrip, 0600, expiry -------------------------------- #


def test_store_tokens_writes_0600_with_absolute_expiry():
    creds = codex_auth._store_tokens(
        {"access_token": "A1", "refresh_token": "R1", "id_token": "", "expires_in": 3600}
    )
    assert creds.access_token == "A1"
    p = codex_auth._path()
    assert stat.S_IMODE(p.stat().st_mode) == 0o600
    data = json.loads(p.read_text())
    assert data["access_token"] == "A1"
    assert data["refresh_token"] == "R1"
    assert data["expires_at"] > time.time() + 3500


def test_store_tokens_keeps_previous_refresh_when_absent():
    codex_auth._store_tokens({"access_token": "A1", "refresh_token": "R1", "expires_in": 3600})
    # A response with no refresh_token must not wipe the stored one (OpenAI rotates).
    codex_auth._store_tokens({"access_token": "A2", "expires_in": 3600})
    assert codex_auth._read()["refresh_token"] == "R1"


# --- ensure_fresh ----------------------------------------------------------- #


def test_ensure_fresh_raises_when_not_signed_in():
    with pytest.raises(codex_auth.CodexAuthError):
        codex_auth.ensure_fresh()


def test_ensure_fresh_returns_cached_when_valid(monkeypatch):
    codex_auth._store_tokens({"access_token": "A1", "refresh_token": "R1", "expires_in": 3600})

    def _boom(*a, **k):
        raise AssertionError("must not hit the network when the token is still valid")

    monkeypatch.setattr(httpx, "post", _boom)
    assert codex_auth.ensure_fresh().access_token == "A1"


def test_ensure_fresh_refreshes_near_expiry(monkeypatch):
    codex_auth._store_tokens({"access_token": "A1", "refresh_token": "R1", "expires_in": 3600})
    data = codex_auth._read()
    data["expires_at"] = time.time() + 5  # inside the refresh margin
    codex_auth._write(data)

    class FakeResp:
        status_code = 200
        text = ""

        def json(self):
            return {"access_token": "A2", "refresh_token": "R2", "expires_in": 3600}

    monkeypatch.setattr(httpx, "post", lambda *a, **k: FakeResp())
    creds = codex_auth.ensure_fresh()
    assert creds.access_token == "A2"
    assert codex_auth._read()["refresh_token"] == "R2"


def test_ensure_fresh_refresh_failure_raises(monkeypatch):
    codex_auth._store_tokens({"access_token": "A1", "refresh_token": "R1", "expires_in": 3600})
    data = codex_auth._read()
    data["expires_at"] = time.time() - 10  # already expired → forces a refresh
    codex_auth._write(data)

    class FakeResp:
        status_code = 400
        text = "invalid_grant"

        def json(self):
            return {}

    monkeypatch.setattr(httpx, "post", lambda *a, **k: FakeResp())
    with pytest.raises(codex_auth.CodexAuthError):
        codex_auth.ensure_fresh()


# --- status / logout / headers --------------------------------------------- #


def test_status_and_logout():
    assert codex_auth.status()["signed_in"] is False
    codex_auth._store_tokens(
        {
            "access_token": "A",
            "refresh_token": "R",
            "id_token": _fake_jwt({"chatgpt_account_id": "acc"}),
            "expires_in": 3600,
        }
    )
    st = codex_auth.status()
    assert st["signed_in"] is True and st["account_id"] == "acc"
    assert "access_token" not in st and "refresh_token" not in st  # never leak raw tokens
    assert codex_auth.logout() is True
    assert codex_auth.status()["signed_in"] is False
    assert codex_auth.logout() is False  # idempotent


def test_default_headers():
    creds = codex_auth.Creds(access_token="tok", account_id="acc")
    headers = codex_auth.default_headers(creds, session_id="s1")
    assert headers["chatgpt-account-id"] == "acc"
    assert headers["OpenAI-Beta"] == codex_auth.OPENAI_BETA
    assert headers["originator"] == codex_auth.ORIGINATOR
    assert headers["session_id"] == "s1"


def test_default_headers_omits_missing():
    headers = codex_auth.default_headers(codex_auth.Creds(access_token="t", account_id=None))
    assert "chatgpt-account-id" not in headers
    assert "session_id" not in headers


# --- model_config wiring ---------------------------------------------------- #


def test_model_config_subscription_routes_to_backend(monkeypatch):
    codex_auth._store_tokens(
        {
            "access_token": "ACCESS",
            "refresh_token": "R",
            "id_token": _fake_jwt({"chatgpt_account_id": "acc"}),
            "expires_in": 3600,
        }
    )
    monkeypatch.setenv("AG2ASSISTANT_LLM_PROVIDER", "openai")
    monkeypatch.setenv("AG2ASSISTANT_OPENAI_AUTH_MODE", "subscription")
    cfg = load_config()
    mc = agent.model_config(cfg)
    assert type(mc).__name__ == "OpenAIResponsesConfig"
    assert mc.base_url == codex_auth.BACKEND_BASE
    assert mc.api_key == "ACCESS"  # SDK sends this as Authorization: Bearer
    assert mc.default_headers["chatgpt-account-id"] == "acc"
    # ChatGPT backend requires store=false (rejects server-side response storage).
    assert mc.store is False


def test_model_config_api_key_mode_unchanged(monkeypatch):
    monkeypatch.setenv("AG2ASSISTANT_LLM_PROVIDER", "openai")
    monkeypatch.setenv("AG2ASSISTANT_OPENAI_AUTH_MODE", "api_key")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    cfg = load_config()
    mc = agent.model_config(cfg)
    assert mc.api_key == "sk-test"
    assert mc.base_url is None


def test_model_config_subscription_does_not_raise_when_not_signed_in(monkeypatch):
    # Building the agent in subscription mode with no token must NOT raise (that would
    # 500 a reload); it returns a config with an empty key → the TURN fails cleanly.
    monkeypatch.setenv("AG2ASSISTANT_LLM_PROVIDER", "openai")
    monkeypatch.setenv("AG2ASSISTANT_OPENAI_AUTH_MODE", "subscription")
    cfg = load_config()
    mc = agent.model_config(cfg)  # must not raise
    assert mc.base_url == codex_auth.BACKEND_BASE
    assert mc.api_key == ""


def test_creds_best_effort_never_raises():
    # not signed in → empty creds, no exception
    assert codex_auth.creds_best_effort().access_token == ""
    # signed in but expired + refresh fails → falls back to the stored (stale) token
    codex_auth._store_tokens({"access_token": "STALE", "refresh_token": "R", "expires_in": 3600})
    d = codex_auth._read()
    d["expires_at"] = time.time() - 10
    codex_auth._write(d)
    import httpx

    class FakeResp:
        status_code = 403
        text = "unsupported_country"

        def json(self):
            return {}

    orig = httpx.post
    httpx.post = lambda *a, **k: FakeResp()
    try:
        assert codex_auth.creds_best_effort().access_token == "STALE"
    finally:
        httpx.post = orig


def _write_codex_cli(tmp_path, tokens: dict):
    d = tmp_path / ".codex"
    d.mkdir(parents=True, exist_ok=True)
    (d / "auth.json").write_text(json.dumps({"auth_mode": "chatgpt", "tokens": tokens}))


def test_reuses_codex_cli_session_when_no_own_login(tmp_path):
    # No AG2-owned login, but the official Codex CLI is signed in → reuse its tokens.
    _write_codex_cli(
        tmp_path,
        {
            "access_token": "CLI_ACCESS",
            "refresh_token": "r",
            "account_id": "acc-cli",
            "id_token": "",
        },
    )
    assert codex_auth.is_signed_in() is True
    st = codex_auth.status()
    assert st["signed_in"] is True and st["source"] == "codex-cli" and st["account_id"] == "acc-cli"
    creds = codex_auth.ensure_fresh()  # must not raise; returns the CLI token
    assert creds.access_token == "CLI_ACCESS" and creds.account_id == "acc-cli"


def test_own_login_takes_precedence_over_codex_cli(tmp_path):
    _write_codex_cli(
        tmp_path, {"access_token": "CLI", "refresh_token": "r", "account_id": "acc-cli"}
    )
    codex_auth._store_tokens({"access_token": "OWN", "refresh_token": "R", "expires_in": 3600})
    assert codex_auth.status()["source"] == "ag2"
    assert codex_auth.ensure_fresh().access_token == "OWN"


def test_codex_cli_account_id_derived_from_jwt_when_absent(tmp_path):
    # No explicit account_id in the CLI file → derive it from the id_token JWT.
    _write_codex_cli(
        tmp_path,
        {
            "access_token": "A",
            "refresh_token": "r",
            "id_token": _fake_jwt({"chatgpt_account_id": "acc-jwt"}),
        },
    )
    assert codex_auth._codex_cli_creds().account_id == "acc-jwt"


def test_config_auth_mode_env_override(monkeypatch):
    monkeypatch.setenv("AG2ASSISTANT_OPENAI_AUTH_MODE", "SUBSCRIPTION")  # normalized to lower
    assert load_config().llm.auth_mode == "subscription"
