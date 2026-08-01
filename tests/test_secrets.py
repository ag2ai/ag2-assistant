"""Channel bot tokens in the install's secrets store (§ channel-tokens).

A channel token belongs to a **Connection**, not to the process: it is stored under
that Connection's own key in ``secrets.json`` (0600), handed to its adapter
explicitly and never echoed. The legacy platform-keyed ``channels`` field and the
environment survive as a one-shot migration *seed* — read to give a first Connection
its token, never written and never exported. Each test gets its own ``SecretStore``
over the isolated ``paths`` fixture.
"""

import json
import os
import stat

import pytest

from assistant.secrets import SecretStore

TELEGRAM = "TELEGRAM_BOT_TOKEN"
DISCORD = "DISCORD_BOT_TOKEN"
SLACK_BOT = "SLACK_BOT_TOKEN"
SLACK_APP = "SLACK_APP_TOKEN"


def _seed_legacy_tokens(paths, **tokens: str) -> None:
    """Write the platform-keyed ``channels`` field a pre-Connection install held."""
    paths.secrets_json.parent.mkdir(parents=True, exist_ok=True)
    data = json.loads(paths.secrets_json.read_text()) if paths.secrets_json.exists() else {}
    data["channels"] = tokens
    paths.secrets_json.write_text(json.dumps(data))


# --- the seed: read for a first Connection, from the store or the given env ---


def test_a_saved_legacy_token_is_readable_as_a_seed(paths):
    _seed_legacy_tokens(paths, TELEGRAM_BOT_TOKEN="tok-123")
    assert SecretStore(paths).channel_token(TELEGRAM, {}) == "tok-123"


def test_an_env_only_token_is_a_seed_too(paths):
    """A token set only in the environment (e.g. ``.env``) still seeds a Connection."""
    store = SecretStore(paths)
    assert store.channel_token(SLACK_APP, {SLACK_APP: "env-only"}) == "env-only"
    assert store.channel_token(SLACK_BOT, {SLACK_APP: "env-only"}) == ""


def test_a_saved_token_beats_an_env_value(paths):
    _seed_legacy_tokens(paths, DISCORD_BOT_TOKEN="saved-tok")
    assert SecretStore(paths).channel_token(DISCORD, {DISCORD: "env-tok"}) == "saved-tok"


def test_an_absent_token_reads_empty(paths):
    assert SecretStore(paths).channel_token(TELEGRAM, {}) == ""


# --- a Connection's own tokens: save, merge, clear ---


def test_setting_a_connections_token_persists_it_under_that_connection(paths):
    store = SecretStore(paths)
    store.set_connection_tokens("cn_1", {TELEGRAM: "cn-tok"})

    assert store.connection_tokens("cn_1") == {TELEGRAM: "cn-tok"}
    data = json.loads(paths.secrets_json.read_text())
    assert data["connection_tokens"]["cn_1"][TELEGRAM] == "cn-tok"


def test_two_connections_of_one_platform_hold_their_own_tokens(paths):
    """The whole point of a Connection: two Telegram bots, two tokens, no collision."""
    store = SecretStore(paths)
    store.set_connection_tokens("cn_1", {TELEGRAM: "first"})
    store.set_connection_tokens("cn_2", {TELEGRAM: "second"})

    assert store.connection_tokens("cn_1") == {TELEGRAM: "first"}
    assert store.connection_tokens("cn_2") == {TELEGRAM: "second"}


def test_setting_merges_rather_than_replaces(paths):
    store = SecretStore(paths)
    store.set_connection_tokens("cn_1", {SLACK_BOT: "bot-tok"})
    store.set_connection_tokens("cn_1", {SLACK_APP: "app-tok"})
    assert store.connection_tokens("cn_1") == {SLACK_BOT: "bot-tok", SLACK_APP: "app-tok"}


def test_an_empty_value_clears_that_token(paths):
    store = SecretStore(paths)
    store.set_connection_tokens("cn_1", {SLACK_BOT: "bot-tok", SLACK_APP: "app-tok"})
    store.set_connection_tokens("cn_1", {SLACK_APP: ""})
    assert store.connection_tokens("cn_1") == {SLACK_BOT: "bot-tok"}


def test_clearing_forgets_every_token_the_connection_held(paths):
    store = SecretStore(paths)
    store.set_connection_tokens("cn_1", {TELEGRAM: "cn-tok"})
    store.set_connection_tokens("cn_2", {TELEGRAM: "other"})

    store.clear_connection_tokens("cn_1")
    assert store.connection_tokens("cn_1") == {}
    assert store.connection_tokens("cn_2") == {TELEGRAM: "other"}


def test_clearing_an_unknown_connection_is_a_no_op(paths):
    SecretStore(paths).clear_connection_tokens("cn_nope")


def test_an_unknown_env_name_is_rejected(paths):
    store = SecretStore(paths)
    with pytest.raises(ValueError):
        store.set_connection_tokens("cn_1", {"OPENAI_API_KEY": "nope"})
    with pytest.raises(ValueError):
        store.set_connection_tokens("cn_1", {"MADE_UP_TOKEN": "nope"})


def test_the_secrets_file_is_0600(paths):
    SecretStore(paths).set_connection_tokens("cn_1", {DISCORD: "d-tok"})
    assert stat.S_IMODE(paths.secrets_json.stat().st_mode) == 0o600


def test_status_reports_presence_and_a_hint_never_the_value(paths):
    store = SecretStore(paths)
    store.set_connection_tokens("cn_1", {SLACK_BOT: "xoxb-super-secret-1234"})

    st = store.connection_token_status("cn_1", (SLACK_BOT, SLACK_APP))
    assert st[SLACK_BOT]["set"] is True
    assert "super-secret" not in st[SLACK_BOT]["hint"]
    assert st[SLACK_APP] == {"set": False, "hint": ""}


# --- nothing exports a channel token: one process cannot hold three of them ---


def test_the_overlay_never_carries_a_connections_token(paths):
    """While a token sits in the process, something reads it and a second Connection
    quietly runs on the first one's token. Nothing puts one there."""
    store = SecretStore(paths)
    store.set_connection_tokens("cn_1", {TELEGRAM: "cn-tok"})

    assert TELEGRAM not in store.env_overlay()
    assert TELEGRAM not in store.merged_env({})


def test_the_overlay_never_carries_a_legacy_seed_either(paths):
    _seed_legacy_tokens(paths, DISCORD_BOT_TOKEN="saved-tok", TELEGRAM_BOT_TOKEN="tg-tok")
    store = SecretStore(paths)

    assert store.env_overlay() == {}
    assert store.merged_env({}) == {}


def test_saving_a_token_does_not_touch_the_process_environment(paths):
    """The store is a value source, not a mutation: nothing lands in os.environ."""
    SecretStore(paths).set_connection_tokens("cn_1", {TELEGRAM: "tok-123"})
    assert os.environ.get(TELEGRAM) != "tok-123"


def test_a_connections_token_is_never_logged(paths, caplog):
    with caplog.at_level("DEBUG"):
        SecretStore(paths).set_connection_tokens("cn_1", {SLACK_BOT: "super-secret-value"})
    assert "super-secret-value" not in caplog.text
