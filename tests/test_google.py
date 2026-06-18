"""Tests for the Google integration wiring (auth state + tool gating).

No real OAuth or API calls — those need the user's credentials and a browser.
These cover credential-state helpers, tool construction, gating of writes, and
conditional inclusion in the agent's tool list.
"""

import pytest

from assistant.integrations import google_auth
from assistant.tools.google import build_google_tools


@pytest.fixture
def google_paths(tmp_path, monkeypatch):
    creds = tmp_path / "google_credentials.json"
    token = tmp_path / "google_token.json"
    monkeypatch.setattr(google_auth, "credentials_path", lambda: creds)
    monkeypatch.setattr(google_auth, "token_path", lambda: token)
    return creds, token


def test_not_configured_or_signed_in_by_default(google_paths):
    assert google_auth.is_configured() is False
    assert google_auth.has_token() is False


def test_is_configured_when_client_present(google_paths):
    creds, _ = google_paths
    creds.write_text("{}")
    assert google_auth.is_configured() is True


def test_logout_removes_token(google_paths):
    _, token = google_paths
    token.write_text("{}")
    assert google_auth.has_token() is True
    assert google_auth.logout() is True
    assert google_auth.has_token() is False
    assert google_auth.logout() is False  # nothing left to remove


def test_extract_drive_id_from_url_or_id():
    from assistant.tools.google import _extract_drive_id

    assert _extract_drive_id(
        "https://docs.google.com/spreadsheets/d/1AbC_dEf-123/edit#gid=0"
    ) == "1AbC_dEf-123"
    assert _extract_drive_id("https://drive.google.com/open?id=XYZ789") == "XYZ789"
    assert _extract_drive_id("rawFileId") == "rawFileId"


def test_google_guidance_in_turn_prompt_when_signed_in(monkeypatch):
    import assistant.integrations.google_auth as ga
    from assistant.agent import turn_prompt
    from assistant.config import Config

    monkeypatch.setattr(ga, "has_token", lambda: True)
    joined = " ".join(turn_prompt(Config()))
    assert "drive_search" in joined and "never" in joined.lower()

    monkeypatch.setattr(ga, "has_token", lambda: False)
    assert "drive_search" not in " ".join(turn_prompt(Config()))


def test_build_google_tools_names():
    names = [t.name for t in build_google_tools()]
    assert names == [
        "gmail_search", "gmail_read", "gmail_send", "gmail_create_draft",
        "calendar_list_events", "calendar_create_event",
        "drive_search", "drive_read",
    ]


def test_write_tools_are_gated_reads_are_not():
    tools = {t.name: t for t in build_google_tools()}
    # sends / writes carry the approval middleware; reads/searches don't
    assert tools["gmail_send"]._middleware
    assert tools["calendar_create_event"]._middleware
    assert not tools["gmail_search"]._middleware
    assert not tools["gmail_read"]._middleware
    assert not tools["drive_read"]._middleware
    assert not tools["gmail_create_draft"]._middleware  # draft can't send → ungated


def test_save_credentials_validates(google_paths):
    creds, _ = google_paths
    # valid installed-client JSON is accepted
    google_auth.save_credentials_json('{"installed": {"client_id": "x"}}')
    assert creds.exists()
    # garbage is rejected
    import pytest as _pytest

    with _pytest.raises(Exception):
        google_auth.save_credentials_json("not json")
    with _pytest.raises(ValueError):
        google_auth.save_credentials_json('{"nope": 1}')


# --- gateway endpoints (mocked auth) ---


def _client(monkeypatch):
    from fastapi.testclient import TestClient

    import assistant.gateway.app as app_mod
    import assistant.gateway.core as core_mod

    class _FakeAgent:
        async def ask(self, *a, stream=None, **k):
            class R: body = "ok"
            return R()

    monkeypatch.setattr(core_mod, "create_agent", lambda *a, **k: _FakeAgent())
    return TestClient(app_mod.create_app(memory=False, persist=False))


def test_google_status_endpoint(monkeypatch):
    from assistant.integrations import google_auth as ga

    monkeypatch.setattr(ga, "is_configured", lambda: True)
    monkeypatch.setattr(ga, "has_token", lambda: True)
    monkeypatch.setattr(ga, "account_email", lambda: "me@example.com")
    with _client(monkeypatch) as client:
        st = client.get("/api/google/status").json()
        assert st == {"configured": True, "signed_in": True, "email": "me@example.com"}


def test_google_login_url_and_callback(monkeypatch):
    from assistant.integrations import google_auth as ga

    monkeypatch.setattr(ga, "is_configured", lambda: True)
    sentinel_flow = object()
    monkeypatch.setattr(
        ga, "make_login_flow",
        lambda redirect_uri: ("https://accounts.google.com/o/oauth2/auth?x=1", "st8", sentinel_flow),
    )
    completed = {}
    monkeypatch.setattr(
        ga, "complete_login",
        lambda flow, code: (completed.update(flow=flow, code=code), "me@example.com")[1],
    )
    with _client(monkeypatch) as client:
        r = client.post("/api/google/login_url").json()
        assert r["ok"] is True
        assert "accounts.google.com" in r["auth_url"]
        # the redirect catches the code and completes the stored flow
        page = client.get("/api/google/callback", params={"state": "st8", "code": "abc"})
        assert page.status_code == 200
        assert "Connected" in page.text
        assert completed["flow"] is sentinel_flow and completed["code"] == "abc"
        # an unknown state is rejected gracefully
        assert "no longer valid" in client.get(
            "/api/google/callback", params={"state": "bogus", "code": "x"}
        ).text


def test_google_credentials_upload_endpoint(monkeypatch, tmp_path):
    from assistant.integrations import google_auth as ga

    monkeypatch.setattr(ga, "credentials_path", lambda: tmp_path / "creds.json")
    with _client(monkeypatch) as client:
        ok = client.post(
            "/api/google/credentials", json={"content": '{"installed": {"client_id": "x"}}'}
        ).json()
        assert ok["ok"] is True
        assert (tmp_path / "creds.json").exists()
        bad = client.post("/api/google/credentials", json={"content": "nope"}).json()
        assert bad["ok"] is False


def test_agent_tools_include_google_only_when_signed_in(monkeypatch):
    import assistant.integrations.google_auth as ga
    import assistant.tools as tools_mod

    monkeypatch.setattr(ga, "has_token", lambda: False)
    base = [t.name for t in tools_mod.build_agent_tools(provider="gemini")]
    assert not any(n.startswith("gmail_") for n in base)

    monkeypatch.setattr(ga, "has_token", lambda: True)
    signed_in = [t.name for t in tools_mod.build_agent_tools(provider="gemini")]
    assert "gmail_send" in signed_in
    assert "calendar_list_events" in signed_in
    assert "drive_search" in signed_in
