"""Tests for the Google integration wiring (auth state + tool gating).

No real OAuth or API calls — those need the user's credentials and a browser.
These cover credential-state helpers, tool construction, gating of writes, and
conditional inclusion in the agent's tool list.
"""

import pytest

from agclaw.integrations import google_auth
from agclaw.tools.google import build_google_tools


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


def test_agent_tools_include_google_only_when_signed_in(monkeypatch):
    import agclaw.integrations.google_auth as ga
    import agclaw.tools as tools_mod

    monkeypatch.setattr(ga, "has_token", lambda: False)
    base = [t.name for t in tools_mod.build_agent_tools(provider="gemini")]
    assert not any(n.startswith("gmail_") for n in base)

    monkeypatch.setattr(ga, "has_token", lambda: True)
    signed_in = [t.name for t in tools_mod.build_agent_tools(provider="gemini")]
    assert "gmail_send" in signed_in
    assert "calendar_list_events" in signed_in
    assert "drive_search" in signed_in
