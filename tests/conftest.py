"""Shared test fixtures."""

import pytest


@pytest.fixture(autouse=True)
def _isolate_agclaw_home(monkeypatch, tmp_path):
    """Isolate tests from the developer's real ~/.agclaw state.

    A real Google token/credentials would make build_agent_tools append the 8
    Google tools and break tool-set assertions. We point the Google paths at an
    empty tmp dir so `is_configured()`/`has_token()` are naturally False; tests
    that need a token write to their own (separately-monkeypatched) paths, which
    run after this fixture and therefore override it.
    """
    try:
        import agclaw.integrations.google_auth as ga

        monkeypatch.setattr(ga, "token_path", lambda: tmp_path / "no_token.json")
        monkeypatch.setattr(ga, "credentials_path", lambda: tmp_path / "no_creds.json")
        monkeypatch.setattr(ga, "account_path", lambda: tmp_path / "no_account.txt")
    except Exception:
        pass
