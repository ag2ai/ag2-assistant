"""Channel bot tokens in the global secrets store (§ channel-tokens).

A channel token belongs to a **Connection**, not to the process: it is stored under
that Connection's own key and handed to its adapter explicitly. The legacy
platform-keyed ``channels`` field and the process env survive as a one-shot
migration *seed* — read to give a first Connection its token, and never written or
exported. The autouse HOME-isolation fixture (conftest) points data_dir() at a tmp
root, so each test writes its own secrets.json.
"""

import json
import os

import pytest

from assistant import secrets
from assistant.config import data_dir

_CHANNEL_ENVS = ("TELEGRAM_BOT_TOKEN", "DISCORD_BOT_TOKEN", "SLACK_BOT_TOKEN", "SLACK_APP_TOKEN")


@pytest.fixture(autouse=True)
def _restore_channel_env():
    """load_into_env writes os.environ directly (not via monkeypatch), so snapshot +
    restore the channel env vars around each test to avoid leakage."""
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
    for env in _CHANNEL_ENVS:
        monkeypatch.delenv(env, raising=False)


def _seed_legacy_tokens(**tokens: str) -> None:
    """Write the platform-keyed ``channels`` field a pre-Connection install held."""
    _secrets_file().parent.mkdir(parents=True, exist_ok=True)
    data = json.loads(_secrets_file().read_text()) if _secrets_file().exists() else {}
    data["channels"] = tokens
    _secrets_file().write_text(json.dumps(data))


# --- the seed: read for a first Connection, from the store or the process env ---


def test_a_saved_legacy_token_is_readable_as_a_seed(monkeypatch):
    _clear_channel_env(monkeypatch)
    _seed_legacy_tokens(TELEGRAM_BOT_TOKEN="tok-123")

    assert secrets.channel_token("TELEGRAM_BOT_TOKEN") == "tok-123"
    assert secrets.channel_token_status()["TELEGRAM_BOT_TOKEN"] is True


def test_an_env_only_token_is_a_seed_too(monkeypatch):
    """A token set only in the real env (e.g. ``.env``) still seeds a Connection."""
    _clear_channel_env(monkeypatch)
    monkeypatch.setenv("SLACK_APP_TOKEN", "env-only")

    assert secrets.channel_token("SLACK_APP_TOKEN") == "env-only"
    st = secrets.channel_token_status()
    assert st["SLACK_APP_TOKEN"] is True
    assert st["SLACK_BOT_TOKEN"] is False


def test_a_saved_token_beats_a_process_env_value(monkeypatch):
    _clear_channel_env(monkeypatch)
    _seed_legacy_tokens(DISCORD_BOT_TOKEN="saved-tok")
    monkeypatch.setenv("DISCORD_BOT_TOKEN", "env-tok")

    assert secrets.channel_token("DISCORD_BOT_TOKEN") == "saved-tok"


def test_an_absent_token_reads_empty(monkeypatch):
    _clear_channel_env(monkeypatch)
    assert secrets.channel_token("TELEGRAM_BOT_TOKEN") == ""
    assert secrets.channel_token_status()["TELEGRAM_BOT_TOKEN"] is False


# --- nothing exports a channel token: one process cannot hold three of them ---


def test_load_into_env_never_exports_a_channel_token(monkeypatch):
    """While a token sits in the process, something reads it and a second Connection
    quietly runs on the first one's token. Nothing puts one there."""
    _clear_channel_env(monkeypatch)
    _seed_legacy_tokens(DISCORD_BOT_TOKEN="saved-tok", TELEGRAM_BOT_TOKEN="tg-tok")

    secrets.load_into_env()

    assert "DISCORD_BOT_TOKEN" not in os.environ
    assert "TELEGRAM_BOT_TOKEN" not in os.environ


def test_a_connections_token_is_never_exported(monkeypatch):
    _clear_channel_env(monkeypatch)
    secrets.set_connection_tokens("cn_1", {"TELEGRAM_BOT_TOKEN": "cn-tok"})

    secrets.load_into_env()

    assert "TELEGRAM_BOT_TOKEN" not in os.environ
    assert secrets.connection_tokens("cn_1") == {"TELEGRAM_BOT_TOKEN": "cn-tok"}


def test_a_connections_token_is_never_logged(monkeypatch, caplog):
    _clear_channel_env(monkeypatch)
    with caplog.at_level("DEBUG"):
        secrets.set_connection_tokens("cn_1", {"SLACK_BOT_TOKEN": "super-secret-value"})
    assert "super-secret-value" not in caplog.text
