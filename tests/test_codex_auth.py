"""ChatGPT-subscription auth (assistant.codex_auth) + its model_config wiring.

No real network: the token endpoint is answered by a real httpx client over
``MockTransport``. Each test gets its own token store through the ``paths`` fixture.
"""

import base64
import json
import stat
import time

import pytest

from assistant import agent, codex_auth
from assistant.codex_auth import CodexAuth
from assistant.config import resolve_config
from tests.support import http


@pytest.fixture
def auth(paths) -> CodexAuth:
    """The subscription auth over an isolated layout; no network client needed."""
    return CodexAuth(paths)


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


def test_extract_auth_code_from_full_redirect_url(auth):
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


def test_account_id_nested_auth_claim(auth):
    tok = _fake_jwt({"https://api.openai.com/auth": {"chatgpt_account_id": "acc-nested"}})
    assert codex_auth.account_id_from(tok, "") == "acc-nested"


def test_account_id_organizations_fallback(auth):
    tok = _fake_jwt({"https://api.openai.com/auth": {"organizations": [{"id": "org-1"}]}})
    assert codex_auth.account_id_from(tok, "") == "org-1"


def test_account_id_falls_back_to_access_token():
    access = _fake_jwt({"chatgpt_account_id": "from-access"})
    assert codex_auth.account_id_from("", access) == "from-access"


def test_account_id_none_when_absent():
    assert codex_auth.account_id_from("", "") is None
    assert codex_auth.account_id_from("not-a-jwt", "") is None


# --- token store: roundtrip, 0600, expiry -------------------------------- #


def test_store_tokens_writes_0600_with_absolute_expiry(paths, auth):
    creds = auth._store_tokens(
        {"access_token": "A1", "refresh_token": "R1", "id_token": "", "expires_in": 3600}
    )
    assert creds.access_token == "A1"
    p = paths.codex_tokens
    assert stat.S_IMODE(p.stat().st_mode) == 0o600
    data = json.loads(p.read_text())
    assert data["access_token"] == "A1"
    assert data["refresh_token"] == "R1"
    assert data["expires_at"] > time.time() + 3500


def test_store_tokens_keeps_previous_refresh_when_absent(auth):
    auth._store_tokens({"access_token": "A1", "refresh_token": "R1", "expires_in": 3600})
    # A response with no refresh_token must not wipe the stored one (OpenAI rotates).
    auth._store_tokens({"access_token": "A2", "expires_in": 3600})
    assert auth._read()["refresh_token"] == "R1"


# --- ensure_fresh ----------------------------------------------------------- #


def test_ensure_fresh_raises_when_not_signed_in(auth):
    with pytest.raises(codex_auth.CodexAuthError):
        auth.ensure_fresh()


def test_ensure_fresh_returns_cached_when_valid(auth, paths):
    def _boom(request):
        raise AssertionError("must not hit the network when the token is still valid")

    auth = CodexAuth(paths, client=http.client(_boom))
    auth._store_tokens({"access_token": "A1", "refresh_token": "R1", "expires_in": 3600})
    assert auth.ensure_fresh().access_token == "A1"


def test_ensure_fresh_refreshes_near_expiry(auth, paths):
    handler, sent = http.recording_responder(
        {"access_token": "A2", "refresh_token": "R2", "expires_in": 3600}
    )
    auth = CodexAuth(paths, client=http.client(handler))
    auth._store_tokens({"access_token": "A1", "refresh_token": "R1", "expires_in": 3600})
    data = auth._read()
    data["expires_at"] = time.time() + 5  # inside the refresh margin
    auth._write(data)

    creds = auth.ensure_fresh()
    assert creds.access_token == "A2"
    assert auth._read()["refresh_token"] == "R2"
    # the refresh really went to the token endpoint, as a refresh_token grant
    assert sent[0]["url"] == codex_auth.TOKEN_URL
    assert http.form_of(sent[0]["body"])["grant_type"] == "refresh_token"


def test_ensure_fresh_refresh_failure_raises(auth, paths):
    auth = CodexAuth(paths, client=http.client(http.failing_responder(400, "invalid_grant")))
    auth._store_tokens({"access_token": "A1", "refresh_token": "R1", "expires_in": 3600})
    data = auth._read()
    data["expires_at"] = time.time() - 10  # already expired → forces a refresh
    auth._write(data)

    with pytest.raises(codex_auth.CodexAuthError):
        auth.ensure_fresh()


# --- status / logout / headers --------------------------------------------- #


def test_status_and_logout(auth):
    assert auth.status()["signed_in"] is False
    auth._store_tokens(
        {
            "access_token": "A",
            "refresh_token": "R",
            "id_token": _fake_jwt({"chatgpt_account_id": "acc"}),
            "expires_in": 3600,
        }
    )
    st = auth.status()
    assert st["signed_in"] is True and st["account_id"] == "acc"
    assert "access_token" not in st and "refresh_token" not in st  # never leak raw tokens
    assert auth.logout() is True
    assert auth.status()["signed_in"] is False
    assert auth.logout() is False  # idempotent


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


def test_model_config_subscription_routes_to_backend(auth, paths):
    auth._store_tokens(
        {
            "access_token": "ACCESS",
            "refresh_token": "R",
            "id_token": _fake_jwt({"chatgpt_account_id": "acc"}),
            "expires_in": 3600,
        }
    )
    cfg = resolve_config(
        {"AG2ASSISTANT_LLM_PROVIDER": "openai", "AG2ASSISTANT_OPENAI_AUTH_MODE": "subscription"},
        paths,
    )
    mc = agent.model_config(cfg)
    assert type(mc).__name__ == "OpenAIResponsesConfig"
    assert mc.base_url == codex_auth.BACKEND_BASE
    assert mc.api_key == "ACCESS"  # SDK sends this as Authorization: Bearer
    assert mc.default_headers["chatgpt-account-id"] == "acc"
    # ChatGPT backend requires store=false (rejects server-side response storage)
    # and streaming (rejects non-streaming requests) — both forced, both its rules.
    assert mc.store is False
    assert mc.streaming is True


def test_model_config_subscription_merges_advanced_options(auth, paths):
    """The Advanced (JSON) options of a subscription config still apply — but the
    fields the subscription owns (endpoint, token, headers, streaming, store) are
    forced AFTER the merge, so options can neither redirect the endpoint nor leak
    a key; our "api" surface switch is dropped as meaningless here."""
    auth._store_tokens(
        {
            "access_token": "ACCESS",
            "refresh_token": "R",
            "id_token": _fake_jwt({"chatgpt_account_id": "acc"}),
            "expires_in": 3600,
        }
    )
    cfg = resolve_config(
        {"AG2ASSISTANT_LLM_PROVIDER": "openai", "AG2ASSISTANT_OPENAI_AUTH_MODE": "subscription"},
        paths,
    )
    cfg.llm.provider_options["openai"] = {
        "max_output_tokens": 2048,  # a real option → passes through
        "api": "chat",  # our surface switch → dropped
        "base_url": "http://evil/v1",  # forced back to the ChatGPT backend
        "api_key": "sk-injected",  # forced back to the OAuth token
        "streaming": False,  # forced back on (backend requires it)
        "store": True,  # forced back off (backend rejects it)
    }
    mc = agent.model_config(cfg)
    assert mc.max_output_tokens == 2048
    assert mc.base_url == codex_auth.BACKEND_BASE
    assert mc.api_key == "ACCESS"
    assert mc.streaming is True
    assert mc.store is False


def test_model_config_api_key_mode_unchanged(paths):
    cfg = resolve_config(
        {
            "AG2ASSISTANT_LLM_PROVIDER": "openai",
            "AG2ASSISTANT_OPENAI_AUTH_MODE": "api_key",
            "OPENAI_API_KEY": "sk-test",
        },
        paths,
    )
    mc = agent.model_config(cfg)
    assert mc.api_key == "sk-test"
    assert mc.base_url is None


def test_model_config_subscription_does_not_raise_when_not_signed_in(paths):
    # Building the agent in subscription mode with no token must NOT raise (that would
    # 500 a reload); it returns a config with an empty key → the TURN fails cleanly.
    cfg = resolve_config(
        {"AG2ASSISTANT_LLM_PROVIDER": "openai", "AG2ASSISTANT_OPENAI_AUTH_MODE": "subscription"},
        paths,
    )
    mc = agent.model_config(cfg)  # must not raise
    assert mc.base_url == codex_auth.BACKEND_BASE
    assert mc.api_key == ""


def test_creds_best_effort_never_raises(auth, paths):
    auth = CodexAuth(paths, client=http.client(http.failing_responder(403, "unsupported_country")))
    # not signed in → empty creds, no exception
    assert auth.creds_best_effort().access_token == ""
    # signed in but expired + refresh fails → falls back to the stored (stale) token
    auth._store_tokens({"access_token": "STALE", "refresh_token": "R", "expires_in": 3600})
    d = auth._read()
    d["expires_at"] = time.time() - 10
    auth._write(d)
    assert auth.creds_best_effort().access_token == "STALE"


def _write_codex_cli(paths, tokens: dict):
    """A signed-in official Codex CLI, at the location Paths points us to."""
    paths.codex_auth.parent.mkdir(parents=True, exist_ok=True)
    paths.codex_auth.write_text(json.dumps({"auth_mode": "chatgpt", "tokens": tokens}))


def test_reuses_codex_cli_session_when_no_own_login(auth, paths):
    # No AG2-owned login, but the official Codex CLI is signed in → reuse its tokens.
    _write_codex_cli(
        paths,
        {
            "access_token": "CLI_ACCESS",
            "refresh_token": "r",
            "account_id": "acc-cli",
            "id_token": "",
        },
    )
    assert auth.is_signed_in() is True
    st = auth.status()
    assert st["signed_in"] is True and st["source"] == "codex-cli" and st["account_id"] == "acc-cli"
    creds = auth.ensure_fresh()  # must not raise; returns the CLI token
    assert creds.access_token == "CLI_ACCESS" and creds.account_id == "acc-cli"


def test_own_login_takes_precedence_over_codex_cli(auth, paths):
    _write_codex_cli(paths, {"access_token": "CLI", "refresh_token": "r", "account_id": "acc-cli"})
    auth._store_tokens({"access_token": "OWN", "refresh_token": "R", "expires_in": 3600})
    assert auth.status()["source"] == "ag2"
    assert auth.ensure_fresh().access_token == "OWN"


def test_codex_cli_account_id_derived_from_jwt_when_absent(auth, paths):
    # No explicit account_id in the CLI file → derive it from the id_token JWT.
    _write_codex_cli(
        paths,
        {
            "access_token": "A",
            "refresh_token": "r",
            "id_token": _fake_jwt({"chatgpt_account_id": "acc-jwt"}),
        },
    )
    assert auth._codex_cli_creds().account_id == "acc-jwt"


def test_config_auth_mode_env_override(paths):
    env = {"AG2ASSISTANT_OPENAI_AUTH_MODE": "SUBSCRIPTION"}  # normalized to lower
    assert resolve_config(env, paths).llm.auth_mode == "subscription"
