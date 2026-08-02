"""Channel bot tokens in the global secrets store (§ channel-tokens).

Channel tokens get the same treatment as provider keys: stored in the install's
``secrets.json`` (0600) and never echoed. The store no longer writes the process
environment — it reports the variables it stands for via ``env_overlay()``, and
``channel_token_status`` answers about whatever env it is handed.
"""

import json
import stat

from assistant.secrets import SecretStore

TELEGRAM = "TELEGRAM_BOT_TOKEN"
DISCORD = "DISCORD_BOT_TOKEN"
SLACK_BOT = "SLACK_BOT_TOKEN"
SLACK_APP = "SLACK_APP_TOKEN"


# --- set_channel_token: save/remove roundtrip incl. overlay + file + 0600 ---


def test_set_channel_token_saves_overlay_and_file(paths):
    store = SecretStore(paths)
    assert store.set_channel_token(TELEGRAM, "tok-123") is True

    # the value is reported as an env variable the store stands for...
    assert store.env_overlay()[TELEGRAM] == "tok-123"
    # ...and persisted under the channels sub-map
    data = json.loads(paths.secrets_json.read_text())
    assert data["channels"][TELEGRAM] == "tok-123"
    # status reflects presence, never the value
    assert store.channel_token_status(store.env_overlay())[TELEGRAM] is True


def test_saving_a_token_does_not_touch_the_process_environment(paths):
    """The store is a value source, not a mutation: nothing lands in os.environ."""
    import os

    store = SecretStore(paths)
    store.set_channel_token(TELEGRAM, "tok-123")
    assert os.environ.get(TELEGRAM) != "tok-123"


def test_set_channel_token_file_is_0600(paths):
    SecretStore(paths).set_channel_token(DISCORD, "d-tok")
    assert stat.S_IMODE(paths.secrets_json.stat().st_mode) == 0o600


def test_set_channel_token_empty_removes(paths):
    store = SecretStore(paths)
    store.set_channel_token(TELEGRAM, "tok")
    assert store.env_overlay()[TELEGRAM] == "tok"

    assert store.set_channel_token(TELEGRAM, "") is True
    assert TELEGRAM not in store.env_overlay()
    data = json.loads(paths.secrets_json.read_text())
    # channels sub-map dropped entirely once empty
    assert "channels" not in data
    assert store.channel_token_status({})[TELEGRAM] is False


def test_set_channel_token_unknown_env_rejected(paths):
    store = SecretStore(paths)
    assert store.set_channel_token("OPENAI_API_KEY", "nope") is False
    assert store.set_channel_token("MADE_UP_TOKEN", "nope") is False


def test_set_channel_token_never_logs_value(paths, caplog):
    with caplog.at_level("DEBUG"):
        SecretStore(paths).set_channel_token(SLACK_BOT, "super-secret-value")
    assert "super-secret-value" not in caplog.text


# --- env_overlay: the saved token is what a call would see, and it wins ---


def test_env_overlay_carries_the_saved_token(paths):
    store = SecretStore(paths)
    store.set_channel_token(DISCORD, "saved-tok")
    assert store.env_overlay()[DISCORD] == "saved-tok"


def test_saved_token_overrides_a_preexisting_env_value(paths):
    """Precedence matches provider keys: a SAVED token beats an ambient env value."""
    store = SecretStore(paths)
    store.set_channel_token(DISCORD, "saved-tok")
    assert store.merged_env({DISCORD: "env-tok"})[DISCORD] == "saved-tok"


def test_channel_token_status_counts_env_only_token(paths):
    """A token present only in the given env (no saved secret) still counts."""
    store = SecretStore(paths)
    st = store.channel_token_status(store.merged_env({SLACK_APP: "env-only"}))
    assert st[SLACK_APP] is True
    assert st[SLACK_BOT] is False
