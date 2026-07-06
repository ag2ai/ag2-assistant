"""Channel bot tokens in the global secrets store (§ channel-tokens).

Channel tokens get the same treatment as provider keys: stored in
``~/.ag2assistant/secrets.json`` (0600), loaded into os.environ, never echoed.
The autouse HOME-isolation fixture (conftest) points data_dir() at a tmp root, so
each test writes its own secrets.json.
"""

import json
import os
import stat

import pytest

from assistant import secrets
from assistant.config import data_dir

_CHANNEL_ENVS = ("TELEGRAM_BOT_TOKEN", "DISCORD_BOT_TOKEN", "SLACK_BOT_TOKEN", "SLACK_APP_TOKEN")


@pytest.fixture(autouse=True)
def _restore_channel_env():
    """set_channel_token / load_into_env write os.environ directly (not via monkeypatch),
    so snapshot + restore the channel env vars around each test to avoid leakage."""
    saved = {k: os.environ.get(k) for k in _CHANNEL_ENVS}
    yield
    for k, v in saved.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v


def _secrets_file():
    return data_dir() / "secrets.json"


def _clear_channel_env(monkeypatch):
    for env in ("TELEGRAM_BOT_TOKEN", "DISCORD_BOT_TOKEN", "SLACK_BOT_TOKEN", "SLACK_APP_TOKEN"):
        monkeypatch.delenv(env, raising=False)


# --- set_channel_token: save/remove roundtrip incl. env + file + 0600 ---


def test_set_channel_token_saves_env_and_file(monkeypatch):
    _clear_channel_env(monkeypatch)
    assert secrets.set_channel_token("TELEGRAM_BOT_TOKEN", "tok-123") is True

    # os.environ set live

    assert os.environ["TELEGRAM_BOT_TOKEN"] == "tok-123"
    # persisted under the channels sub-map
    data = json.loads(_secrets_file().read_text())
    assert data["channels"]["TELEGRAM_BOT_TOKEN"] == "tok-123"
    # status reflects presence, never the value
    assert secrets.channel_token_status()["TELEGRAM_BOT_TOKEN"] is True


def test_set_channel_token_file_is_0600(monkeypatch):
    _clear_channel_env(monkeypatch)
    secrets.set_channel_token("DISCORD_BOT_TOKEN", "d-tok")
    mode = stat.S_IMODE(_secrets_file().stat().st_mode)
    assert mode == 0o600


def test_set_channel_token_empty_removes(monkeypatch):
    _clear_channel_env(monkeypatch)

    secrets.set_channel_token("TELEGRAM_BOT_TOKEN", "tok")
    assert os.environ.get("TELEGRAM_BOT_TOKEN") == "tok"

    assert secrets.set_channel_token("TELEGRAM_BOT_TOKEN", "") is True
    assert "TELEGRAM_BOT_TOKEN" not in os.environ
    data = json.loads(_secrets_file().read_text())
    # channels sub-map dropped entirely once empty
    assert "channels" not in data
    assert secrets.channel_token_status()["TELEGRAM_BOT_TOKEN"] is False


def test_set_channel_token_unknown_env_rejected(monkeypatch):
    _clear_channel_env(monkeypatch)
    assert secrets.set_channel_token("OPENAI_API_KEY", "nope") is False
    assert secrets.set_channel_token("MADE_UP_TOKEN", "nope") is False


def test_set_channel_token_never_logs_value(monkeypatch, caplog):
    _clear_channel_env(monkeypatch)
    with caplog.at_level("DEBUG"):
        secrets.set_channel_token("SLACK_BOT_TOKEN", "super-secret-value")
    assert "super-secret-value" not in caplog.text


# --- load_into_env: saved token applied; precedence (saved overrides env) ---


def test_load_into_env_applies_saved_token(monkeypatch):
    _clear_channel_env(monkeypatch)

    # write a secrets.json with a channel token directly, then drop it from env
    secrets.set_channel_token("DISCORD_BOT_TOKEN", "saved-tok")
    os.environ.pop("DISCORD_BOT_TOKEN", None)

    secrets.load_into_env()
    assert os.environ["DISCORD_BOT_TOKEN"] == "saved-tok"


def test_load_into_env_saved_overrides_preexisting_env(monkeypatch):
    """Precedence matches provider keys: a SAVED token overrides a pre-existing env
    value (load_into_env is documented as overriding)."""
    _clear_channel_env(monkeypatch)

    secrets.set_channel_token("DISCORD_BOT_TOKEN", "saved-tok")
    monkeypatch.setenv("DISCORD_BOT_TOKEN", "env-tok")

    secrets.load_into_env()
    assert os.environ["DISCORD_BOT_TOKEN"] == "saved-tok"


def test_channel_token_status_counts_env_only_token(monkeypatch):
    """A token set only in the real env (no saved secret) still counts as present."""
    _clear_channel_env(monkeypatch)
    monkeypatch.setenv("SLACK_APP_TOKEN", "env-only")
    st = secrets.channel_token_status()
    assert st["SLACK_APP_TOKEN"] is True
    assert st["SLACK_BOT_TOKEN"] is False
