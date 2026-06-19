"""Shared test fixtures."""

import pytest


@pytest.fixture(autouse=True)
def _isolate_ag2assistant_home(monkeypatch, tmp_path):
    """Isolate tests from the developer's real ~/.ag2assistant state.

    A real Google token/credentials would make build_agent_tools append the 8
    Google tools and break tool-set assertions. We point the Google paths at an
    empty tmp dir so `is_configured()`/`has_token()` are naturally False; tests
    that need a token write to their own (separately-monkeypatched) paths, which
    run after this fixture and therefore override it.

    We also redirect HOME to a tmp dir so anything resolving `~/.ag2assistant`
    (PermissionStore, the gateway's task/inquiry stores) writes to disposable
    space instead of the developer's real state.
    """
    monkeypatch.setenv("HOME", str(tmp_path))
    try:
        import assistant.integrations.google_auth as ga

        monkeypatch.setattr(ga, "token_path", lambda: tmp_path / "no_token.json")
        monkeypatch.setattr(ga, "credentials_path", lambda: tmp_path / "no_creds.json")
        monkeypatch.setattr(ga, "account_path", lambda: tmp_path / "no_account.txt")
    except Exception:
        pass
