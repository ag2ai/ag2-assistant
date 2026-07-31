"""Install-level Channels: the API, boot, and the per-Channel default profile.

A channel (telegram/discord/slack) starts ONCE for the whole install as soon as its
token is configured — it is never owned by a profile (ADR 0019). What the registry
holds is a per-Connection *default profile*: where that Connection's conversations
land when nothing else has been chosen. Endpoints: GET /api/channels (state),
POST /api/channels/default (set/clear the default) and POST /api/channels/token.

GET /api/connections lists the Connections — one configured instance of a platform
each, migrated from an install's existing bot tokens on first read — and
POST /api/connections/{id}/default sets where one Connection's conversations land.
"""

import json
import os
import time

from fastapi.testclient import TestClient

import assistant.channels as channels_mod
from assistant import connections, pairing, peers, profiles, secrets
from assistant.config import data_dir
from assistant.gateway.app import create_app
from assistant.gateway.profile_manager import ProfileManager
from tests.conftest import api, use_fake_agent


class FakeChannel:
    """Stub Channel: records the Connection and token(s) it was constructed with and
    start/stop, without touching a network."""

    def __init__(self, platform: str, connection: str = "", **tokens: str) -> None:
        self.platform = platform
        self.connection = connection
        self.tokens = tokens
        self.started = False
        self.stopped = False
        self.router = None
        self.pushed: list[tuple[str, str]] = []

    async def notify(self, chat_id: str, text: str) -> None:
        self.pushed.append((chat_id, text))

    async def start(self, router) -> None:
        self.started = True
        self.router = router

    async def stop(self) -> None:
        self.stopped = True


def _stub_channels(monkeypatch):
    """Make get_channel return FakeChannels (patched where start_channel imports it)."""
    monkeypatch.setattr(
        channels_mod, "get_channel", lambda platform, **kw: FakeChannel(platform, **kw)
    )


def _app(monkeypatch, **kw):

    use_fake_agent(monkeypatch)
    return create_app(ProfileManager(memory=False, persist=False), **kw)


def _new_client(monkeypatch, **kw):

    return TestClient(_app(monkeypatch, **kw))


def _no_channel_env(monkeypatch):
    for env in ("TELEGRAM_BOT_TOKEN", "DISCORD_BOT_TOKEN", "SLACK_BOT_TOKEN", "SLACK_APP_TOKEN"):
        monkeypatch.delenv(env, raising=False)


def _only_connection(platform: str = "telegram") -> str:
    """The id of the platform's single Connection — what the manager keys by now."""
    return connections.connections_for(platform)[0].id


def _live(manager, platform: str = "telegram"):
    """The live adapter of the platform's single Connection."""
    return manager.channels[_only_connection(platform)]


def _default_gateway(manager, platform: str = "telegram"):
    """Where a conversation on the platform's Connection lands when the Peer has chosen
    nothing — i.e. what that Connection's default profile resolves to right now."""
    pid = manager.default_profile(_only_connection(platform))
    return manager.gateway_for_profile(pid) if pid else None


# --- GET /api/channels: shape, zero-profile install all-null ---


def test_get_channels_zero_profile_all_null(monkeypatch):
    _no_channel_env(monkeypatch)
    with _new_client(monkeypatch) as client:
        chans = client.get("/api/channels").json()
        assert set(chans) == {"telegram", "discord", "slack"}
        for platform, entry in chans.items():
            assert entry == {
                "default_profile": None,
                "token_present": False,
                "active": False,
                "error": f"no token configured for {platform}",
                "paired_accounts": 0,
            }


def test_get_channels_reflects_token_present(monkeypatch):
    _no_channel_env(monkeypatch)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "tok")
    _stub_channels(monkeypatch)
    with _new_client(monkeypatch) as client:
        chans = client.get("/api/channels").json()
        assert chans["telegram"]["token_present"] is True
        assert chans["discord"]["token_present"] is False


# --- boot: a token is all a Channel needs; no profile owns it ---


def test_a_configured_channel_starts_at_install_level(monkeypatch):
    """No binding, no profile — a token is enough for the adapter to come up, and it
    lives on the manager rather than inside any runtime."""

    monkeypatch.setenv("DISCORD_BOT_TOKEN", "tok")
    _stub_channels(monkeypatch)

    meta = profiles.create_profile("Work", "#109e91")
    profiles.profile_dir(meta.id).mkdir(parents=True, exist_ok=True)

    with _new_client(monkeypatch) as client:
        manager = client.app.state.profiles
        assert _live(manager, "discord").started is True
        assert _live(manager, "discord").router is manager.router
        assert client.get("/api/channels").json()["discord"]["active"] is True
        # the runtime knows nothing about it
        assert not hasattr(manager.get(meta.id), "channels")


def test_a_channel_starts_with_no_profiles_at_all(monkeypatch):
    """A fresh install with a token configured: the Channel is live and simply has
    nowhere to route yet."""

    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "tok")
    _stub_channels(monkeypatch)
    with _new_client(monkeypatch) as client:
        manager = client.app.state.profiles
        assert _live(manager).started is True
        assert _default_gateway(manager) is None


def test_channel_start_failure_does_not_crash_boot(monkeypatch):
    """A channel whose start() raises must still leave the server booted; the failure
    is recorded on manager.channel_errors and surfaced, not fatal."""

    class BoomChannel:
        platform = "discord"

        async def start(self, router):
            raise RuntimeError("connect failed")

        async def stop(self):
            pass

    monkeypatch.setenv("DISCORD_BOT_TOKEN", "bad")
    monkeypatch.setattr(channels_mod, "get_channel", lambda platform, **kw: BoomChannel())

    meta = profiles.create_profile("Work", "#109e91")
    profiles.profile_dir(meta.id).mkdir(parents=True, exist_ok=True)

    with _new_client(monkeypatch) as client:
        manager = client.app.state.profiles
        assert manager.get(meta.id) is not None  # booted despite the failure
        assert manager.channels == {}
        assert "could not start 'discord'" in manager.channel_errors[_only_connection("discord")]
        got = client.get("/api/channels").json()["discord"]
        assert got["active"] is False
        assert "could not start 'discord'" in got["error"]


def test_start_failure_error_never_echoes_token(monkeypatch):
    """Platform libraries embed the raw token in some error messages (Telegram:
    "The token <value> was rejected"). The recorded/returned error must be scrubbed."""

    class EchoBoomChannel:
        platform = "telegram"

        async def start(self, router):
            raise RuntimeError(f"The token `{os.environ['TELEGRAM_BOT_TOKEN']}` was rejected")

        async def stop(self):
            pass

    secret = "8123456:very-secret-token-value"
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", secret)
    monkeypatch.setattr(channels_mod, "get_channel", lambda platform, **kw: EchoBoomChannel())

    with _new_client(monkeypatch) as client:
        got = client.get("/api/channels")
        assert secret not in got.text
        assert "•••" in got.json()["telegram"]["error"]


def test_no_token_channel_stays_down_with_a_reason(monkeypatch):
    _no_channel_env(monkeypatch)
    with _new_client(monkeypatch) as client:
        entry = client.get("/api/channels").json()["telegram"]
        assert entry["active"] is False
        assert entry["token_present"] is False
        assert "no token configured for telegram" in entry["error"]


# --- POST /api/channels/default: where conversations land ---


def test_setting_the_default_profile_routes_messages_there(monkeypatch):

    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "tok")
    _stub_channels(monkeypatch)
    with _new_client(monkeypatch) as client:
        client.post("/api/profiles", json={"name": "Work", "accent": "#109e91"})
        manager = client.app.state.profiles

        r = client.post("/api/channels/default", json={"platform": "telegram", "profile": "work"})
        assert r.status_code == 200
        assert r.json() == {
            "telegram": {
                "default_profile": "work",
                "token_present": True,
                "active": True,
                "error": None,
                "paired_accounts": 0,
            }
        }
        assert profiles.connection_defaults()[_only_connection()] == "work"
        assert _default_gateway(manager) is manager.get("work").gateway
        assert client.get("/api/channels").json()["telegram"]["default_profile"] == "work"


def test_changing_the_default_needs_no_restart(monkeypatch):
    """The router resolves the profile per message, so a new default takes effect on
    the next message with the same live adapter."""

    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "tok")
    _stub_channels(monkeypatch)
    with _new_client(monkeypatch) as client:
        client.post("/api/profiles", json={"name": "Work", "accent": "#109e91"})
        client.post("/api/profiles", json={"name": "Personal", "accent": "#f95339"})
        manager = client.app.state.profiles

        client.post("/api/channels/default", json={"platform": "telegram", "profile": "work"})
        adapter = _live(manager)

        r = client.post(
            "/api/channels/default", json={"platform": "telegram", "profile": "personal"}
        )
        assert r.json()["telegram"]["default_profile"] == "personal"
        assert _live(manager) is adapter  # same live adapter
        assert adapter.stopped is False
        assert _default_gateway(manager) is manager.get("personal").gateway


def test_clearing_the_default_leaves_the_channel_running(monkeypatch):

    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "tok")
    _stub_channels(monkeypatch)
    with _new_client(monkeypatch) as client:
        client.post("/api/profiles", json={"name": "Work", "accent": "#109e91"})
        client.post("/api/channels/default", json={"platform": "telegram", "profile": "work"})
        manager = client.app.state.profiles

        r = client.post("/api/channels/default", json={"platform": "telegram", "profile": None})
        assert r.json() == {
            "telegram": {
                "default_profile": None,
                "token_present": True,
                "active": True,
                "error": None,
                "paired_accounts": 0,
            }
        }
        assert _live(manager).stopped is False
        assert _default_gateway(manager) is None
        assert profiles.connection_defaults() == {}


def test_default_unknown_platform_400(monkeypatch):
    with _new_client(monkeypatch) as client:
        client.post("/api/profiles", json={"name": "Work", "accent": "#109e91"})
        r = client.post("/api/channels/default", json={"platform": "irc", "profile": "work"})
        assert r.status_code == 400
        assert "irc" in r.json()["error"]


def test_default_unknown_profile_400(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "tok")
    _stub_channels(monkeypatch)
    with _new_client(monkeypatch) as client:
        client.post("/api/profiles", json={"name": "Work", "accent": "#109e91"})
        r = client.post("/api/channels/default", json={"platform": "telegram", "profile": "ghost"})
        assert r.status_code == 400
        assert "ghost" in r.json()["error"]


def test_default_archived_profile_400(monkeypatch):

    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "tok")
    _stub_channels(monkeypatch)
    with _new_client(monkeypatch) as client:
        client.post("/api/profiles", json={"name": "Work", "accent": "#109e91"})
        client.post("/api/profiles", json={"name": "Personal", "accent": "#f95339"})
        # archive personal (non-default needs no replacement)
        assert client.request("DELETE", "/api/profiles/personal").status_code == 200
        assert profiles.get_profile("personal").archived is True

        r = client.post(
            "/api/channels/default", json={"platform": "telegram", "profile": "personal"}
        )
        assert r.status_code == 400
        assert "personal" in r.json()["error"]


# --- a default profile that goes away ---


def test_archiving_the_default_profile_leaves_the_channel_live_and_unrouted(monkeypatch):
    """Archiving is not a reason to disconnect an install-level Channel: it stays up,
    its default is cleared, and messages have nowhere to land until a new one is set."""

    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "tok")
    _stub_channels(monkeypatch)

    with _new_client(monkeypatch) as client:
        client.post("/api/profiles", json={"name": "Work", "accent": "#109e91"})
        client.post("/api/profiles", json={"name": "Personal", "accent": "#f95339"})
        client.post("/api/channels/default", json={"platform": "telegram", "profile": "work"})
        manager = client.app.state.profiles
        adapter = _live(manager)

        r = client.request("DELETE", "/api/profiles/work", json={"new_default": "personal"})
        assert r.status_code == 200
        assert profiles.connection_defaults() == {}
        assert adapter.stopped is False
        assert client.get("/api/channels").json()["telegram"] == {
            "default_profile": None,
            "token_present": True,
            "active": True,
            "error": None,
            "paired_accounts": 0,
        }
        assert _default_gateway(manager) is None

        # personal can take over
        r = client.post(
            "/api/channels/default", json={"platform": "telegram", "profile": "personal"}
        )
        assert r.json()["telegram"]["default_profile"] == "personal"
        assert _default_gateway(manager) is manager.get("personal").gateway


def test_deleting_a_profile_clears_it_as_a_default(monkeypatch):
    """Belt-and-braces on the registry itself: archiving clears the default, but a
    delete must never leave a Channel defaulting to an id that no longer exists."""

    profiles.create_profile("Work", "#109e91")
    connection = connections.create_connection("telegram", tokens={"TELEGRAM_BOT_TOKEN": "tok"})
    profiles.set_connection_default(connection.id, "work")
    profiles.delete_profile("work")

    assert profiles.get_profile("work") is None
    assert profiles.connection_defaults() == {}


# --- POST /api/channels/token: secrets-backed tokens, live apply ---


def test_post_token_saves_flips_present_and_starts(monkeypatch):
    """Saving a token starts the Channel live — no profile involved — and the value is
    never echoed."""

    _no_channel_env(monkeypatch)
    _stub_channels(monkeypatch)
    with _new_client(monkeypatch) as client:
        client.post("/api/profiles", json={"name": "Work", "accent": "#109e91"})
        assert client.get("/api/channels").json()["telegram"]["active"] is False

        r = client.post(
            "/api/channels/token",
            json={"platform": "telegram", "tokens": {"TELEGRAM_BOT_TOKEN": "live-secret-tok"}},
        )
        assert r.status_code == 200
        entry = r.json()["telegram"]
        assert entry["token_present"] is True
        assert entry["active"] is True
        assert entry["error"] is None
        assert "live-secret-tok" not in r.text
        assert _live(client.app.state.profiles).started is True
        assert secrets.channel_token_status()["TELEGRAM_BOT_TOKEN"] is True


def test_post_token_clear_stops_channel(monkeypatch):
    """Clearing the token for a live Channel stops it and returns to waiting."""
    _no_channel_env(monkeypatch)
    _stub_channels(monkeypatch)
    with _new_client(monkeypatch) as client:
        client.post("/api/profiles", json={"name": "Work", "accent": "#109e91"})
        client.post(
            "/api/channels/token",
            json={"platform": "telegram", "tokens": {"TELEGRAM_BOT_TOKEN": "tok"}},
        )
        client.post("/api/channels/default", json={"platform": "telegram", "profile": "work"})
        manager = client.app.state.profiles
        chan = _live(manager)

        r = client.post(
            "/api/channels/token",
            json={"platform": "telegram", "tokens": {"TELEGRAM_BOT_TOKEN": ""}},
        )
        entry = r.json()["telegram"]
        assert entry["token_present"] is False
        assert entry["active"] is False
        assert entry["default_profile"] == "work"  # the default survives the token going
        assert manager.channels == {}
        assert chan.stopped is True


def test_post_token_slack_requires_both(monkeypatch):
    """Slack needs BOTH tokens: with only one present it is not token_present and does
    not start."""
    _no_channel_env(monkeypatch)
    _stub_channels(monkeypatch)
    with _new_client(monkeypatch) as client:
        manager = client.app.state.profiles

        # only the bot token
        r = client.post(
            "/api/channels/token",
            json={"platform": "slack", "tokens": {"SLACK_BOT_TOKEN": "b-tok"}},
        )
        entry = r.json()["slack"]
        assert entry["token_present"] is False
        assert entry["active"] is False
        assert manager.channels == {}

        # add the app token → now both present → starts
        r = client.post(
            "/api/channels/token",
            json={"platform": "slack", "tokens": {"SLACK_APP_TOKEN": "a-tok"}},
        )
        entry = r.json()["slack"]
        assert entry["token_present"] is True
        assert entry["active"] is True
        assert _only_connection("slack") in manager.channels


def test_post_token_unknown_platform_400(monkeypatch):
    with _new_client(monkeypatch) as client:
        r = client.post(
            "/api/channels/token",
            json={"platform": "irc", "tokens": {"IRC_TOKEN": "x"}},
        )
        assert r.status_code == 400
        assert "irc" in r.json()["error"]


def test_post_token_invalid_env_for_platform_400(monkeypatch):
    """An env name not valid for the platform → 400, nothing saved."""

    _no_channel_env(monkeypatch)
    with _new_client(monkeypatch) as client:
        r = client.post(
            "/api/channels/token",
            json={"platform": "telegram", "tokens": {"SLACK_BOT_TOKEN": "wrong"}},
        )
        assert r.status_code == 400
        assert "SLACK_BOT_TOKEN" in r.json()["error"]
        # nothing persisted
        assert secrets.channel_token_status()["SLACK_BOT_TOKEN"] is False


def test_post_token_with_no_default_profile_still_starts(monkeypatch):
    """A Channel does not wait for a profile to be chosen: it connects, and refuses
    messages until a default is set."""

    _no_channel_env(monkeypatch)
    _stub_channels(monkeypatch)
    with _new_client(monkeypatch) as client:
        r = client.post(
            "/api/channels/token",
            json={"platform": "discord", "tokens": {"DISCORD_BOT_TOKEN": "hidden-tok"}},
        )
        assert r.status_code == 200
        entry = r.json()["discord"]
        assert entry["default_profile"] is None
        assert entry["token_present"] is True
        assert entry["active"] is True
        assert "hidden-tok" not in r.text
        assert secrets.channel_token_status()["DISCORD_BOT_TOKEN"] is True
        assert _default_gateway(client.app.state.profiles, "discord") is None


# --- the mirror: a browser turn reaches the Peer attached to that Chat (ADR 0020) ---


def test_a_browser_turn_is_pushed_to_the_peer_attached_to_that_chat(monkeypatch):
    """End to end through the real wiring: the profile's gateway hands the completed
    turn to the install's router, which pushes it through the live adapter."""

    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "tok")
    _stub_channels(monkeypatch)
    with _new_client(monkeypatch) as client:
        client.post("/api/profiles", json={"name": "Work", "accent": "#109e91"})
        manager = client.app.state.profiles
        peers.attach(_only_connection(), "42", "web-1", platform="telegram")

        r = client.post(api("work", "/message"), json={"text": "hello", "chat_id": "web-1"})
        assert r.status_code == 200

        assert _live(manager).pushed == [("42", "You: hello\n\nMe: echo[1]: hello")]


def test_a_chat_no_peer_is_attached_to_is_pushed_nowhere(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "tok")
    _stub_channels(monkeypatch)
    with _new_client(monkeypatch) as client:
        client.post("/api/profiles", json={"name": "Work", "accent": "#109e91"})
        manager = client.app.state.profiles

        r = client.post(api("work", "/message"), json={"text": "hello", "chat_id": "web-1"})
        assert r.status_code == 200

        assert _live(manager).pushed == []


# --- group Peers: a group's profile is pinned, and re-pointed only from here ---


def _two_profiles(client) -> None:
    client.post("/api/profiles", json={"name": "Work", "accent": "#109e91"})
    client.post("/api/profiles", json={"name": "Home", "accent": "#c2410c"})


def test_groups_list_what_each_is_pinned_to(monkeypatch):
    with _new_client(monkeypatch) as client:
        _two_profiles(client)
        peers.select_profile("cn-tg", "-100", "work", platform="telegram", surface="group")
        peers.select_profile("cn-tg", "42", "home", platform="telegram")  # a DM, not a group

        view = client.get("/api/channels/telegram/groups").json()
        assert view["groups"] == [{"chat_id": "-100", "profile": "work"}]
        assert [p["id"] for p in view["profiles"]] == ["work", "home"]


def test_a_profile_withheld_from_groups_is_absent_from_the_group_picker(monkeypatch):
    work, _ = _two_telegram_bots(monkeypatch)
    with _new_client(monkeypatch) as client:
        _two_profiles(client)
        client.post(
            f"/api/connections/{work.id}/exposure",
            json={"profile": "home", "surface": f"{work.id}:group", "exposed": False},
        )

        view = client.get("/api/channels/telegram/groups").json()
        assert [p["id"] for p in view["profiles"]] == ["work"]


def test_re_pointing_a_group_moves_it_and_leaves_its_chat_behind(monkeypatch):
    with _new_client(monkeypatch) as client:
        _two_profiles(client)
        peers.select_profile("cn-tg", "-100", "work", platform="telegram", surface="group")
        peers.attach("cn-tg", "-100", "tg-1", platform="telegram", surface="group")

        r = client.post("/api/channels/telegram/groups/-100/profile", json={"profile": "home"})
        assert r.status_code == 200
        assert r.json()["groups"] == [{"chat_id": "-100", "profile": "home"}]
        assert peers.get_peer("cn-tg", "-100").chat is None


def test_a_group_cannot_be_pointed_at_a_profile_withheld_from_groups(monkeypatch):
    """The fence has one gate; the WebUI is not a way around it."""
    work, _ = _two_telegram_bots(monkeypatch)
    with _new_client(monkeypatch) as client:
        _two_profiles(client)
        peers.select_profile(work.id, "-100", "work", platform="telegram", surface="group")
        client.post(
            f"/api/connections/{work.id}/exposure",
            json={"profile": "home", "surface": f"{work.id}:group", "exposed": False},
        )

        r = client.post("/api/channels/telegram/groups/-100/profile", json={"profile": "home"})
        assert r.status_code == 400
        assert peers.get_peer(work.id, "-100").profile == "work"


def test_re_pointing_something_that_is_not_a_group_peer_404s(monkeypatch):
    with _new_client(monkeypatch) as client:
        _two_profiles(client)
        peers.select_profile("cn-tg", "42", "work", platform="telegram")  # a DM

        assert (
            client.post(
                "/api/channels/telegram/groups/42/profile", json={"profile": "home"}
            ).status_code
            == 404
        )
        assert (
            client.post(
                "/api/channels/telegram/groups/-999/profile", json={"profile": "home"}
            ).status_code
            == 404
        )
        assert peers.get_peer("cn-tg", "42").profile == "work"


def test_groups_unknown_platform_400(monkeypatch):
    with _new_client(monkeypatch) as client:
        assert client.get("/api/channels/irc/groups").status_code == 400
        assert (
            client.post(
                "/api/channels/irc/groups/-100/profile", json={"profile": "work"}
            ).status_code
            == 400
        )


# --- Connections: the install's configured platform instances (GET /api/connections) ---


def test_connections_empty_on_an_install_with_no_tokens(monkeypatch):
    with _new_client(monkeypatch) as client:
        assert client.get("/api/connections").json() == {"connections": []}


def test_connections_migrated_from_seeded_tokens(monkeypatch):
    """An install that already had bot tokens comes up with one Connection per
    configured platform, named after the platform, without anyone touching Settings."""
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "tok")
    monkeypatch.setenv("SLACK_BOT_TOKEN", "bot")
    monkeypatch.setenv("SLACK_APP_TOKEN", "app")
    _stub_channels(monkeypatch)
    with _new_client(monkeypatch) as client:
        got = client.get("/api/connections").json()["connections"]
        assert [(c["platform"], c["name"]) for c in got] == [
            ("telegram", "Telegram"),
            ("slack", "Slack"),
        ]
        ids = [c["id"] for c in got]
        assert all(ids) and len(set(ids)) == 2


def test_a_platform_missing_one_of_its_tokens_is_not_migrated(monkeypatch):
    """Slack needs both tokens; a half-configured platform gets no Connection."""
    monkeypatch.setenv("SLACK_BOT_TOKEN", "bot")
    _stub_channels(monkeypatch)
    with _new_client(monkeypatch) as client:
        assert client.get("/api/connections").json() == {"connections": []}


def test_connection_migration_is_idempotent(monkeypatch):
    """A second load neither duplicates nor renames — including across a restart and
    after the user has renamed the migrated Connection."""
    monkeypatch.setenv("DISCORD_BOT_TOKEN", "tok")
    _stub_channels(monkeypatch)
    with _new_client(monkeypatch) as client:
        first = client.get("/api/connections").json()["connections"]
        assert client.get("/api/connections").json()["connections"] == first
    connections.rename_connection(first[0]["id"], "Side project")
    with _new_client(monkeypatch) as client:
        again = client.get("/api/connections").json()["connections"]
        assert [c["id"] for c in again] == [c["id"] for c in first]
        assert again[0]["name"] == "Side project"


def test_a_malformed_registry_file_lists_no_connections(monkeypatch):
    data_dir().mkdir(parents=True, exist_ok=True)
    (data_dir() / "connections.json").write_text("{not json")
    with _new_client(monkeypatch) as client:
        r = client.get("/api/connections")
        assert r.status_code == 200
        assert r.json() == {"connections": []}


def test_default_naming_numbers_the_later_connections_of_a_platform(monkeypatch):
    connections.create_connection("telegram")
    connections.create_connection("telegram")
    connections.create_connection("discord")
    with _new_client(monkeypatch) as client:
        got = client.get("/api/connections").json()["connections"]
        assert [c["name"] for c in got] == ["Telegram", "Telegram 2", "Discord"]


def test_the_connection_listing_never_echoes_a_token(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "super-secret-bot-token")
    _stub_channels(monkeypatch)
    with _new_client(monkeypatch) as client:
        r = client.get("/api/connections")
        assert "super-secret-bot-token" not in r.text
        entry = r.json()["connections"][0]
        assert set(entry) == {
            "id",
            "platform",
            "name",
            "tokens",
            "default_profile",
            "active",
            "error",
            "paired_accounts",
        }
        assert entry["tokens"] == {"TELEGRAM_BOT_TOKEN": {"set": True, "hint": "…oken"}}


# --- a Connection's token(s): its own, handed to its adapter explicitly ---


def test_migration_carries_the_platform_token_onto_its_connection(monkeypatch):
    """An install whose token was only ever an env var comes up with that token held
    by the Connection, and its adapter constructed from there."""
    _no_channel_env(monkeypatch)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "seed-tok")
    _stub_channels(monkeypatch)
    with _new_client(monkeypatch) as client:
        manager = client.app.state.profiles
        assert _live(manager).tokens == {"token": "seed-tok"}
        entry = client.get("/api/connections").json()["connections"][0]
        assert entry["tokens"]["TELEGRAM_BOT_TOKEN"]["set"] is True


def test_slack_gets_both_of_its_tokens_by_constructor_name(monkeypatch):
    _no_channel_env(monkeypatch)
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-seed")
    monkeypatch.setenv("SLACK_APP_TOKEN", "xapp-seed")
    _stub_channels(monkeypatch)
    with _new_client(monkeypatch) as client:
        assert _live(client.app.state.profiles, "slack").tokens == {
            "bot_token": "xoxb-seed",
            "app_token": "xapp-seed",
        }


def test_a_connections_token_beats_a_stray_environment_value(monkeypatch):
    """The env is a migration seed only: an adapter is constructed from what its
    Connection holds, never from a value left in the process."""
    _no_channel_env(monkeypatch)
    connections.create_connection("telegram", tokens={"TELEGRAM_BOT_TOKEN": "connection-tok"})
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "stray-env-tok")
    _stub_channels(monkeypatch)
    with _new_client(monkeypatch) as client:
        assert _live(client.app.state.profiles).tokens == {"token": "connection-tok"}


def test_two_connections_of_a_platform_hold_two_different_tokens(monkeypatch):
    _no_channel_env(monkeypatch)
    work = connections.create_connection("telegram", tokens={"TELEGRAM_BOT_TOKEN": "1111-work"})
    play = connections.create_connection("telegram", tokens={"TELEGRAM_BOT_TOKEN": "2222-play"})
    _stub_channels(monkeypatch)
    with _new_client(monkeypatch) as client:
        r = client.get("/api/connections")
        held = {c["id"]: c["tokens"]["TELEGRAM_BOT_TOKEN"] for c in r.json()["connections"]}
        assert held[work.id] == {"set": True, "hint": "…work"}
        assert held[play.id] == {"set": True, "hint": "…play"}
        assert "1111-work" not in r.text and "2222-play" not in r.text


def test_a_connection_missing_one_of_its_tokens_reports_it_unset(monkeypatch):
    _no_channel_env(monkeypatch)
    _stub_channels(monkeypatch)
    with _new_client(monkeypatch) as client:
        client.post(
            "/api/channels/token",
            json={"platform": "slack", "tokens": {"SLACK_BOT_TOKEN": "xoxb-half"}},
        )
        entry = client.get("/api/connections").json()["connections"][0]
        assert entry["platform"] == "slack"
        assert entry["tokens"] == {
            "SLACK_BOT_TOKEN": {"set": True, "hint": "…half"},
            "SLACK_APP_TOKEN": {"set": False, "hint": ""},
        }


def test_saving_a_token_puts_it_on_the_connection_and_starts_that_adapter(monkeypatch):
    _no_channel_env(monkeypatch)
    _stub_channels(monkeypatch)
    with _new_client(monkeypatch) as client:
        r = client.post(
            "/api/channels/token",
            json={"platform": "discord", "tokens": {"DISCORD_BOT_TOKEN": "fresh-tok"}},
        )
        assert "fresh-tok" not in r.text
        assert _live(client.app.state.profiles, "discord").tokens == {"token": "fresh-tok"}
        listed = client.get("/api/connections").json()["connections"]
        assert [c["platform"] for c in listed] == ["discord"]
        assert listed[0]["tokens"]["DISCORD_BOT_TOKEN"]["hint"] == "…-tok"


def test_replacing_a_token_restarts_the_adapter_with_the_new_value(monkeypatch):
    _no_channel_env(monkeypatch)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "old-aaaa")
    _stub_channels(monkeypatch)
    with _new_client(monkeypatch) as client:
        manager = client.app.state.profiles
        first = _live(manager)
        client.post(
            "/api/channels/token",
            json={"platform": "telegram", "tokens": {"TELEGRAM_BOT_TOKEN": "new-bbbb"}},
        )
        assert first.stopped is True
        assert _live(manager).tokens == {"token": "new-bbbb"}
        listed = client.get("/api/connections").json()["connections"]
        assert len(listed) == 1  # replaced in place, not a second Connection
        assert listed[0]["tokens"]["TELEGRAM_BOT_TOKEN"]["hint"] == "…bbbb"


def test_a_start_failure_never_echoes_the_connections_token(monkeypatch):
    """The scrubbing follows the token to its new home: with nothing in the env, an
    adapter that quotes the token it was handed is still masked."""

    class EchoBoomChannel:
        platform = "telegram"

        def __init__(self, token: str, connection: str = "") -> None:
            self._token = token

        async def start(self, router):
            raise RuntimeError(f"The token `{self._token}` was rejected")

        async def stop(self):
            pass

    _no_channel_env(monkeypatch)
    secret = "8123456:connection-only-token"
    connections.create_connection("telegram", tokens={"TELEGRAM_BOT_TOKEN": secret})
    monkeypatch.setattr(channels_mod, "get_channel", lambda platform, **kw: EchoBoomChannel(**kw))

    with _new_client(monkeypatch) as client:
        got = client.get("/api/channels")
        assert secret not in got.text
        assert "•••" in got.json()["telegram"]["error"]


# --- lifecycle per Connection: two bots of one platform, live side by side ---


def test_two_connections_of_one_platform_are_both_live(monkeypatch):
    """The manager holds one adapter per Connection, each built from its own token and
    told which Connection it is."""
    _no_channel_env(monkeypatch)
    work = connections.create_connection("telegram", tokens={"TELEGRAM_BOT_TOKEN": "1111-work"})
    play = connections.create_connection("telegram", tokens={"TELEGRAM_BOT_TOKEN": "2222-play"})
    _stub_channels(monkeypatch)
    with _new_client(monkeypatch) as client:
        manager = client.app.state.profiles
        assert set(manager.channels) == {work.id, play.id}
        assert manager.channels[work.id].tokens == {"token": "1111-work"}
        assert manager.channels[play.id].tokens == {"token": "2222-play"}
        assert manager.channels[play.id].connection == play.id
        assert client.get("/api/channels").json()["telegram"]["active"] is True


def test_one_connection_failing_to_start_leaves_the_others_running(monkeypatch):
    """A bad token takes down its own Connection and records its own reason; the
    sibling bot of the same platform keeps answering."""

    class Fussy(FakeChannel):
        async def start(self, router):
            if self.tokens["token"] == "bad-token":
                raise RuntimeError("connect failed")
            await FakeChannel.start(self, router)

    _no_channel_env(monkeypatch)
    good = connections.create_connection("telegram", tokens={"TELEGRAM_BOT_TOKEN": "good-token"})
    bad = connections.create_connection("telegram", tokens={"TELEGRAM_BOT_TOKEN": "bad-token"})
    monkeypatch.setattr(channels_mod, "get_channel", lambda platform, **kw: Fussy(platform, **kw))

    with _new_client(monkeypatch) as client:
        manager = client.app.state.profiles
        assert list(manager.channels) == [good.id]
        assert "could not start 'telegram'" in manager.channel_errors[bad.id]
        assert good.id not in manager.channel_errors


def test_stopping_one_connection_leaves_its_sibling_live(monkeypatch):
    _no_channel_env(monkeypatch)
    work = connections.create_connection("telegram", tokens={"TELEGRAM_BOT_TOKEN": "1111-work"})
    play = connections.create_connection("telegram", tokens={"TELEGRAM_BOT_TOKEN": "2222-play"})
    _stub_channels(monkeypatch)
    with _new_client(monkeypatch) as client:
        manager = client.app.state.profiles
        stopped = manager.channels[work.id]

        assert client.portal.call(manager.stop_channel, work.id) is True

        assert stopped.stopped is True
        assert list(manager.channels) == [play.id]
        assert client.get("/api/channels").json()["telegram"]["active"] is True


def test_restarting_a_connection_rebuilds_only_that_adapter(monkeypatch):
    _no_channel_env(monkeypatch)
    work = connections.create_connection("telegram", tokens={"TELEGRAM_BOT_TOKEN": "1111-work"})
    play = connections.create_connection("telegram", tokens={"TELEGRAM_BOT_TOKEN": "2222-play"})
    _stub_channels(monkeypatch)
    with _new_client(monkeypatch) as client:
        manager = client.app.state.profiles
        before = manager.channels[work.id]
        untouched = manager.channels[play.id]

        assert client.portal.call(manager.restart_channel, work.id) == (True, None)

        assert before.stopped is True
        assert manager.channels[work.id] is not before
        assert manager.channels[play.id] is untouched


def test_an_unknown_connection_cannot_be_started_or_restarted(monkeypatch):
    _no_channel_env(monkeypatch)
    _stub_channels(monkeypatch)
    with _new_client(monkeypatch) as client:
        manager = client.app.state.profiles
        for call in (manager.start_channel, manager.restart_channel):
            try:
                client.portal.call(call, "cn-ghost")
            except ValueError as exc:
                assert "cn-ghost" in str(exc)
            else:
                raise AssertionError("an unknown connection must be refused")


def test_a_connection_with_no_token_stays_down_with_its_own_reason(monkeypatch):
    """A Connection created before its token was filled in records why it is inactive
    without disturbing the one that is live."""
    _no_channel_env(monkeypatch)
    live = connections.create_connection("telegram", tokens={"TELEGRAM_BOT_TOKEN": "1111-work"})
    empty = connections.create_connection("telegram")
    _stub_channels(monkeypatch)
    with _new_client(monkeypatch) as client:
        manager = client.app.state.profiles
        assert list(manager.channels) == [live.id]
        assert manager.channel_errors[empty.id] == "no token configured for telegram"


def test_a_browser_turn_reaches_the_peer_on_its_own_connection(monkeypatch):
    """The push side is keyed the same way: a turn mirrored to a Peer of the second
    Telegram bot goes out through that bot's adapter alone."""
    _no_channel_env(monkeypatch)
    work = connections.create_connection("telegram", tokens={"TELEGRAM_BOT_TOKEN": "1111-work"})
    play = connections.create_connection("telegram", tokens={"TELEGRAM_BOT_TOKEN": "2222-play"})
    _stub_channels(monkeypatch)
    with _new_client(monkeypatch) as client:
        client.post("/api/profiles", json={"name": "Work", "accent": "#109e91"})
        manager = client.app.state.profiles
        peers.attach(play.id, "42", "web-1", platform="telegram")

        r = client.post(api("work", "/message"), json={"text": "hello", "chat_id": "web-1"})
        assert r.status_code == 200

        assert manager.channels[work.id].pushed == []
        assert manager.channels[play.id].pushed == [("42", "You: hello\n\nMe: echo[1]: hello")]


# --- the default Profile is the Connection's own ---


def _two_telegram_bots(monkeypatch):
    """Two Telegram Connections, the shape every per-Connection assertion needs."""
    _no_channel_env(monkeypatch)
    work = connections.create_connection(
        "telegram", "Work bot", tokens={"TELEGRAM_BOT_TOKEN": "1111-work"}
    )
    play = connections.create_connection(
        "telegram", "Play bot", tokens={"TELEGRAM_BOT_TOKEN": "2222-play"}
    )
    _stub_channels(monkeypatch)
    return work, play


def test_each_connection_carries_its_own_default_profile(monkeypatch):
    """Two bots of one platform land their conversations in two different Profiles."""
    work, play = _two_telegram_bots(monkeypatch)
    with _new_client(monkeypatch) as client:
        _two_profiles(client)
        manager = client.app.state.profiles

        assert (
            client.post(f"/api/connections/{work.id}/default", json={"profile": "work"}).json()[
                "default_profile"
            ]
            == "work"
        )
        client.post(f"/api/connections/{play.id}/default", json={"profile": "home"})

        listed = {
            c["id"]: c["default_profile"]
            for c in client.get("/api/connections").json()["connections"]
        }
        assert listed == {work.id: "work", play.id: "home"}
        assert manager.default_profile(work.id) == "work"
        assert manager.default_profile(play.id) == "home"


def test_clearing_one_connections_default_leaves_its_siblings_alone(monkeypatch):
    work, play = _two_telegram_bots(monkeypatch)
    with _new_client(monkeypatch) as client:
        _two_profiles(client)
        client.post(f"/api/connections/{work.id}/default", json={"profile": "work"})
        client.post(f"/api/connections/{play.id}/default", json={"profile": "work"})

        r = client.post(f"/api/connections/{work.id}/default", json={"profile": None})

        assert r.json()["default_profile"] is None
        assert profiles.connection_defaults() == {play.id: "work"}


def test_archiving_a_profile_clears_it_as_every_connections_default(monkeypatch):
    """A Profile that has gone stops being anyone's default — on both bots at once."""
    work, play = _two_telegram_bots(monkeypatch)
    with _new_client(monkeypatch) as client:
        _two_profiles(client)
        client.post(f"/api/connections/{work.id}/default", json={"profile": "home"})
        client.post(f"/api/connections/{play.id}/default", json={"profile": "home"})

        assert client.request("DELETE", "/api/profiles/home").status_code == 200

        assert profiles.connection_defaults() == {}
        listed = client.get("/api/connections").json()["connections"]
        assert [c["default_profile"] for c in listed] == [None, None]


def test_a_default_on_an_unknown_connection_404s(monkeypatch):
    with _new_client(monkeypatch) as client:
        _two_profiles(client)
        r = client.post("/api/connections/cn-ghost/default", json={"profile": "work"})
        assert r.status_code == 404
        assert "cn-ghost" in r.json()["error"]


def test_a_connections_default_refuses_an_unknown_or_archived_profile(monkeypatch):
    work, _ = _two_telegram_bots(monkeypatch)
    with _new_client(monkeypatch) as client:
        _two_profiles(client)
        assert client.request("DELETE", "/api/profiles/home").status_code == 200

        assert (
            client.post(
                f"/api/connections/{work.id}/default", json={"profile": "ghost"}
            ).status_code
            == 400
        )
        assert (
            client.post(f"/api/connections/{work.id}/default", json={"profile": "home"}).status_code
            == 400
        )
        assert profiles.connection_defaults() == {}


def test_a_platform_with_no_connection_has_nowhere_to_put_a_default(monkeypatch):
    """The default belongs to a Connection, so there must be one to hold it."""
    _no_channel_env(monkeypatch)
    with _new_client(monkeypatch) as client:
        _two_profiles(client)
        r = client.post("/api/channels/default", json={"platform": "telegram", "profile": "work"})
        assert r.status_code == 400
        assert "telegram" in r.json()["error"]


# --- Profile exposure is the Connection's own, and cannot contradict its default ---


def _surface(cid: str, kind: str = "dm") -> str:
    """One surface id of a Telegram Connection — its two are switched independently."""
    return f"{cid}:{kind}"


def _withdraw(client, cid: str, pid: str, kind: str = "dm", exposed: bool = False):
    return client.post(
        f"/api/connections/{cid}/exposure",
        json={"profile": pid, "surface": _surface(cid, kind), "exposed": exposed},
    )


def test_exposure_is_default_allow_on_every_surface_of_a_connection(monkeypatch):
    """A Profile nobody has withdrawn is reachable through every Connection, without
    anyone visiting Settings."""
    work, _ = _two_telegram_bots(monkeypatch)
    with _new_client(monkeypatch) as client:
        _two_profiles(client)

        view = client.get(f"/api/connections/{work.id}/exposure").json()

        assert view["surfaces"] == [
            {"kind": "dm", "id": _surface(work.id, "dm")},
            {"kind": "group", "id": _surface(work.id, "group")},
        ]
        assert view["exposure"] == {
            pid: {_surface(work.id, "dm"): True, _surface(work.id, "group"): True}
            for pid in ("work", "home")
        }
        assert view["default_profile"] is None


def test_a_single_surface_platform_exposes_one_surface_named_after_its_connection(monkeypatch):
    _no_channel_env(monkeypatch)
    discord = connections.create_connection("discord", tokens={"DISCORD_BOT_TOKEN": "tok"})
    _stub_channels(monkeypatch)
    with _new_client(monkeypatch) as client:
        _two_profiles(client)

        view = client.get(f"/api/connections/{discord.id}/exposure").json()

        assert view["surfaces"] == [{"kind": "all", "id": discord.id}]
        assert view["exposure"] == {"work": {discord.id: True}, "home": {discord.id: True}}


def test_withdrawing_a_profile_from_one_connection_leaves_the_other_reachable(monkeypatch):
    """The point of the whole change: with two Telegram bots, a Profile answers on the
    one it is exposed to and on no other."""
    work, play = _two_telegram_bots(monkeypatch)
    with _new_client(monkeypatch) as client:
        _two_profiles(client)
        manager = client.app.state.profiles

        for kind in ("dm", "group"):
            assert _withdraw(client, work.id, "home", kind).status_code == 200

        assert client.get(f"/api/connections/{play.id}/exposure").json()["exposure"]["home"] == {
            _surface(play.id, "dm"): True,
            _surface(play.id, "group"): True,
        }
        reachable = [p.id for p in manager.available_profiles(_surface(work.id, "dm"))]
        assert reachable == ["work"]
        assert [p.id for p in manager.available_profiles(_surface(play.id, "dm"))] == [
            "work",
            "home",
        ]


def test_a_connections_direct_messages_and_groups_are_withdrawn_independently(monkeypatch):
    work, _ = _two_telegram_bots(monkeypatch)
    with _new_client(monkeypatch) as client:
        _two_profiles(client)

        view = _withdraw(client, work.id, "home", "group").json()

        assert view["exposure"]["home"] == {
            _surface(work.id, "dm"): True,
            _surface(work.id, "group"): False,
        }


def test_exposing_again_drops_the_withdrawal(monkeypatch):
    work, _ = _two_telegram_bots(monkeypatch)
    with _new_client(monkeypatch) as client:
        _two_profiles(client)
        _withdraw(client, work.id, "home", "dm")

        view = _withdraw(client, work.id, "home", "dm", exposed=True).json()

        assert view["exposure"]["home"][_surface(work.id, "dm")] is True


def test_a_profile_withdrawn_from_every_surface_cannot_be_made_the_default(monkeypatch):
    """Enforced here and not only in the browser: the API is reachable directly."""
    work, _ = _two_telegram_bots(monkeypatch)
    with _new_client(monkeypatch) as client:
        _two_profiles(client)
        for kind in ("dm", "group"):
            _withdraw(client, work.id, "home", kind)

        r = client.post(f"/api/connections/{work.id}/default", json={"profile": "home"})

        assert r.status_code == 400
        assert "home" in r.json()["error"]
        assert profiles.connection_defaults() == {}


def test_a_profile_withdrawn_from_one_surface_is_still_eligible_as_the_default(monkeypatch):
    work, _ = _two_telegram_bots(monkeypatch)
    with _new_client(monkeypatch) as client:
        _two_profiles(client)
        _withdraw(client, work.id, "home", "group")

        r = client.post(f"/api/connections/{work.id}/default", json={"profile": "home"})

        assert r.status_code == 200
        assert r.json()["default_profile"] == "home"


def test_withdrawing_the_default_from_its_last_surface_clears_the_default(monkeypatch):
    """The table can never show a Connection pointing where it cannot reach."""
    work, play = _two_telegram_bots(monkeypatch)
    with _new_client(monkeypatch) as client:
        _two_profiles(client)
        client.post(f"/api/connections/{work.id}/default", json={"profile": "home"})
        client.post(f"/api/connections/{play.id}/default", json={"profile": "home"})
        _withdraw(client, work.id, "home", "dm")

        view = _withdraw(client, work.id, "home", "group").json()

        assert view["default_profile"] is None
        assert profiles.connection_defaults() == {play.id: "home"}


def test_withdrawing_a_profile_that_is_not_the_default_leaves_the_default_alone(monkeypatch):
    work, _ = _two_telegram_bots(monkeypatch)
    with _new_client(monkeypatch) as client:
        _two_profiles(client)
        client.post(f"/api/connections/{work.id}/default", json={"profile": "work"})

        for kind in ("dm", "group"):
            view = _withdraw(client, work.id, "home", kind).json()

        assert view["default_profile"] == "work"


def test_exposure_refuses_an_unknown_connection_profile_or_surface(monkeypatch):
    work, _ = _two_telegram_bots(monkeypatch)
    with _new_client(monkeypatch) as client:
        _two_profiles(client)

        assert client.get("/api/connections/cn-ghost/exposure").status_code == 404
        assert (
            client.post(
                "/api/connections/cn-ghost/exposure",
                json={"profile": "work", "surface": "cn-ghost:dm", "exposed": False},
            ).status_code
            == 404
        )
        assert _withdraw(client, work.id, "ghost").status_code == 400
        assert (
            client.post(
                f"/api/connections/{work.id}/exposure",
                json={"profile": "work", "surface": "telegram:dm", "exposed": False},
            ).status_code
            == 400
        )


# --- migration: an install that had a default Profile and Peers before Connections ---


def _pre_connection_install(default_pid: str) -> None:
    """Rewrite the registries the way an install from before Connections existed holds
    them: a platform-keyed default profile, and a Peer named by platform alone."""
    registry = json.loads((data_dir() / "profiles.json").read_text())
    registry.pop("connection_defaults", None)
    registry["channel_defaults"] = {"telegram": default_pid}
    (data_dir() / "profiles.json").write_text(json.dumps(registry))
    (data_dir() / "peers.json").write_text(
        json.dumps({"peers": [{"platform": "telegram", "chat_id": "42", "profile": default_pid}]})
    )
    (data_dir() / "pairing.json").write_text(
        json.dumps(
            {
                "accounts": [{"platform": "telegram", "account_id": "42", "handle": None}],
                "codes": [
                    {"platform": "telegram", "code": "AAAA-1111", "expires_at": time.time() + 600}
                ],
            }
        )
    )


def test_migration_carries_the_platform_default_onto_its_connection(monkeypatch):
    """An upgraded install keeps routing where it did, with nobody visiting Settings."""
    _no_channel_env(monkeypatch)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "seed-tok")
    _stub_channels(monkeypatch)
    profiles.create_profile("Work", "#109e91")
    _pre_connection_install("work")
    with _new_client(monkeypatch) as client:
        entry = client.get("/api/connections").json()["connections"][0]
        assert entry["default_profile"] == "work"
        assert client.get("/api/channels").json()["telegram"]["default_profile"] == "work"


def test_migration_carries_existing_peers_onto_the_migrated_connection(monkeypatch):
    """The conversation continues in place: its Peer is now the Connection's."""
    _no_channel_env(monkeypatch)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "seed-tok")
    _stub_channels(monkeypatch)
    profiles.create_profile("Work", "#109e91")
    _pre_connection_install("work")
    with _new_client(monkeypatch) as client:
        cid = client.get("/api/connections").json()["connections"][0]["id"]
        assert peers.get_peer(cid, "42").profile == "work"


def test_migration_carries_the_paired_accounts_and_live_code_onto_the_connection(monkeypatch):
    """Nobody who could reach the assistant before the upgrade loses access to it."""
    _no_channel_env(monkeypatch)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "seed-tok")
    _stub_channels(monkeypatch)
    profiles.create_profile("Work", "#109e91")
    _pre_connection_install("work")
    with _new_client(monkeypatch) as client:
        roster = client.get("/api/channels/telegram/pairing").json()
        assert [a["account_id"] for a in roster["accounts"]] == ["42"]
        assert roster["code"]["code"] == "AAAA-1111"
        assert pairing.is_paired(_only_connection(), "42") is True


def test_migration_carries_platform_withdrawals_onto_the_connections_surfaces(monkeypatch):
    """A Profile withheld from Telegram groups before the upgrade stays withheld from
    the migrated bot's group surface, and keeps its direct messages."""
    _no_channel_env(monkeypatch)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "seed-tok")
    _stub_channels(monkeypatch)
    profiles.create_profile("Work", "#109e91")
    profiles.set_exposure("work", "telegram:group", False)
    _pre_connection_install("work")
    with _new_client(monkeypatch) as client:
        cid = client.get("/api/connections").json()["connections"][0]["id"]

        exposure = client.get(f"/api/connections/{cid}/exposure").json()["exposure"]

        assert exposure["work"] == {f"{cid}:dm": True, f"{cid}:group": False}


# --- the Connection lifecycle over HTTP: create, rename, replace token, delete ---


def _boom_channels(monkeypatch, bad: str):
    """get_channel builds a FakeChannel, except on ``bad`` — whose start() raises, the
    way a rejected token does."""

    class BoomChannel:
        def __init__(self, platform: str) -> None:
            self.platform = platform

        async def start(self, router):
            raise RuntimeError("Unauthorized")

        async def stop(self):
            pass

    def build(platform: str, connection: str = "", **tokens: str):
        if bad in tokens.values():
            return BoomChannel(platform)
        return FakeChannel(platform, connection, **tokens)

    monkeypatch.setattr(channels_mod, "get_channel", build)


def _create(client, platform: str = "telegram", name: str = "Work bot", **tokens: str):
    return client.post(
        "/api/connections", json={"platform": platform, "name": name, "tokens": tokens}
    )


def test_creating_a_connection_starts_it_immediately(monkeypatch):
    """A platform, a name and a token are all it takes: the adapter is live and the
    entry comes back from the same call."""
    _no_channel_env(monkeypatch)
    _stub_channels(monkeypatch)
    with _new_client(monkeypatch) as client:
        r = _create(client, TELEGRAM_BOT_TOKEN="1111-work")

        assert r.status_code == 200
        entry = r.json()
        assert entry["platform"] == "telegram"
        assert entry["name"] == "Work bot"
        assert entry["active"] is True
        assert entry["error"] is None
        manager = client.app.state.profiles
        assert manager.channels[entry["id"]].tokens == {"token": "1111-work"}
        assert [c["id"] for c in client.get("/api/connections").json()["connections"]] == [
            entry["id"]
        ]


def test_creating_a_second_connection_of_a_platform_leaves_the_first_running(monkeypatch):
    _no_channel_env(monkeypatch)
    _stub_channels(monkeypatch)
    with _new_client(monkeypatch) as client:
        manager = client.app.state.profiles
        first = _create(client, name="Work bot", TELEGRAM_BOT_TOKEN="1111-work").json()

        second = _create(client, name="Play bot", TELEGRAM_BOT_TOKEN="2222-play").json()

        assert second["id"] != first["id"]
        assert manager.channels[first["id"]].started is True
        assert manager.channels[first["id"]].stopped is False
        assert manager.channels[second["id"]].tokens == {"token": "2222-play"}


def test_creating_without_every_token_the_platform_needs_is_refused(monkeypatch):
    """Slack takes two tokens; one of them is not a Connection, it is a typo."""
    _no_channel_env(monkeypatch)
    _stub_channels(monkeypatch)
    with _new_client(monkeypatch) as client:
        r = _create(client, "slack", "Team", SLACK_BOT_TOKEN="xoxb")

        assert r.status_code == 400
        assert "SLACK_APP_TOKEN" in r.json()["error"]
        assert client.get("/api/connections").json()["connections"] == []


def test_creating_with_a_blank_token_or_an_unknown_platform_is_refused(monkeypatch):
    _no_channel_env(monkeypatch)
    _stub_channels(monkeypatch)
    with _new_client(monkeypatch) as client:
        assert _create(client, TELEGRAM_BOT_TOKEN="   ").status_code == 400
        assert _create(client, "matrix", "Bot", TELEGRAM_BOT_TOKEN="tok").status_code == 400
        assert client.get("/api/connections").json()["connections"] == []


def test_a_connection_that_fails_to_start_is_recorded_and_reports_why(monkeypatch):
    """A bad token is not a failed request: the Connection exists, inactive, with the
    reason attached — exactly as a failed boot behaves."""
    _no_channel_env(monkeypatch)
    _boom_channels(monkeypatch, bad="rejected")
    with _new_client(monkeypatch) as client:
        r = _create(client, TELEGRAM_BOT_TOKEN="rejected")

        assert r.status_code == 200
        entry = r.json()
        assert entry["active"] is False
        assert "could not start 'telegram'" in entry["error"]
        listed = client.get("/api/connections").json()["connections"]
        assert [c["id"] for c in listed] == [entry["id"]]


def test_a_connection_can_be_renamed(monkeypatch):
    work, _ = _two_telegram_bots(monkeypatch)
    with _new_client(monkeypatch) as client:
        r = client.post(f"/api/connections/{work.id}", json={"name": "Support bot"})

        assert r.status_code == 200
        assert r.json()["name"] == "Support bot"
        assert connections.get_connection(work.id).name == "Support bot"
        assert client.post(f"/api/connections/{work.id}", json={"name": " "}).status_code == 400
        assert client.post("/api/connections/cn-ghost", json={"name": "x"}).status_code == 404


def test_replacing_a_token_restarts_the_connection_and_keeps_its_identity(monkeypatch):
    """A rotated token is the same bot: its paired accounts, group pins, exposure and
    default Profile all stay attached."""
    work, _ = _two_telegram_bots(monkeypatch)
    with _new_client(monkeypatch) as client:
        _two_profiles(client)
        manager = client.app.state.profiles
        client.post(f"/api/connections/{work.id}/pairing", json={"value": "42"})
        peers.select_profile(work.id, "-100", "work", platform="telegram", surface="group")
        _withdraw(client, work.id, "home", "group")
        client.post(f"/api/connections/{work.id}/default", json={"profile": "work"})
        was = manager.channels[work.id]

        r = client.post(
            f"/api/connections/{work.id}/token", json={"tokens": {"TELEGRAM_BOT_TOKEN": "3333-new"}}
        )

        assert r.status_code == 200
        assert r.json()["active"] is True
        assert was.stopped is True
        assert manager.channels[work.id] is not was
        assert manager.channels[work.id].tokens == {"token": "3333-new"}
        assert connections.tokens_for(work.id) == {"TELEGRAM_BOT_TOKEN": "3333-new"}
        assert [a.account_id for a in pairing.list_accounts(work.id)] == ["42"]
        assert peers.get_peer(work.id, "-100").profile == "work"
        assert profiles.connection_defaults()[work.id] == "work"
        exposure = client.get(f"/api/connections/{work.id}/exposure").json()["exposure"]
        assert exposure["home"] == {_surface(work.id): True, _surface(work.id, "group"): False}


def test_a_failed_token_replacement_leaves_the_previous_token_live(monkeypatch):
    """A typo must not strand a working bot: the old token is restored and the adapter
    comes back up on it."""
    _no_channel_env(monkeypatch)
    work = connections.create_connection(
        "telegram", "Work bot", tokens={"TELEGRAM_BOT_TOKEN": "1111-work"}
    )
    _boom_channels(monkeypatch, bad="typo")
    with _new_client(monkeypatch) as client:
        manager = client.app.state.profiles

        r = client.post(
            f"/api/connections/{work.id}/token", json={"tokens": {"TELEGRAM_BOT_TOKEN": "typo"}}
        )

        assert r.status_code == 400
        assert "could not start 'telegram'" in r.json()["error"]
        assert connections.tokens_for(work.id) == {"TELEGRAM_BOT_TOKEN": "1111-work"}
        assert manager.channels[work.id].tokens == {"token": "1111-work"}
        listed = client.get("/api/connections").json()["connections"][0]
        assert listed["active"] is True
        assert listed["error"] is None


def test_replacing_a_token_refuses_an_incomplete_or_unknown_body(monkeypatch):
    work, _ = _two_telegram_bots(monkeypatch)
    with _new_client(monkeypatch) as client:
        assert (
            client.post(f"/api/connections/{work.id}/token", json={"tokens": {}}).status_code == 400
        )
        assert (
            client.post(
                f"/api/connections/{work.id}/token", json={"tokens": {"DISCORD_BOT_TOKEN": "x"}}
            ).status_code
            == 400
        )
        assert (
            client.post(
                "/api/connections/cn-ghost/token",
                json={"tokens": {"TELEGRAM_BOT_TOKEN": "x"}},
            ).status_code
            == 404
        )
        assert connections.tokens_for(work.id) == {"TELEGRAM_BOT_TOKEN": "1111-work"}


def test_deleting_a_connection_stops_it_and_takes_its_dependent_state_with_it(monkeypatch):
    work, _ = _two_telegram_bots(monkeypatch)
    with _new_client(monkeypatch) as client:
        _two_profiles(client)
        manager = client.app.state.profiles
        client.post(f"/api/connections/{work.id}/pairing", json={"value": "42"})
        client.post(f"/api/connections/{work.id}/pairing/code")
        peers.select_profile(work.id, "-100", "work", platform="telegram", surface="group")
        _withdraw(client, work.id, "home", "group")
        client.post(f"/api/connections/{work.id}/default", json={"profile": "work"})
        live = manager.channels[work.id]

        r = client.delete(f"/api/connections/{work.id}")

        assert r.status_code == 200
        assert live.stopped is True
        assert work.id not in manager.channels
        assert connections.get_connection(work.id) is None
        assert pairing.list_accounts(work.id) == []
        assert pairing.live_code(work.id) is None
        assert peers.get_peer(work.id, "-100") is None
        assert work.id not in profiles.connection_defaults()
        assert profiles.get_profile("home").withdrawn == []
        assert connections.tokens_for(work.id) == {}
        assert client.delete(f"/api/connections/{work.id}").status_code == 404


def test_deleting_one_connection_leaves_the_other_of_its_platform_untouched(monkeypatch):
    work, play = _two_telegram_bots(monkeypatch)
    with _new_client(monkeypatch) as client:
        _two_profiles(client)
        manager = client.app.state.profiles
        client.post(f"/api/connections/{play.id}/pairing", json={"value": "77"})
        peers.select_profile(play.id, "-200", "home", platform="telegram", surface="group")
        _withdraw(client, play.id, "home", "group")
        client.post(f"/api/connections/{play.id}/default", json={"profile": "home"})

        client.delete(f"/api/connections/{work.id}")

        assert manager.channels[play.id].stopped is False
        assert connections.tokens_for(play.id) == {"TELEGRAM_BOT_TOKEN": "2222-play"}
        assert [a.account_id for a in pairing.list_accounts(play.id)] == ["77"]
        assert peers.get_peer(play.id, "-200").profile == "home"
        assert profiles.connection_defaults() == {play.id: "home"}
        exposure = client.get(f"/api/connections/{play.id}/exposure").json()["exposure"]
        assert exposure["home"][_surface(play.id, "group")] is False


def test_the_list_entry_carries_what_a_settings_row_needs(monkeypatch):
    """One call renders the row: identity, token-set flag, liveness, reason and reach —
    and never a token value."""
    work, _ = _two_telegram_bots(monkeypatch)
    with _new_client(monkeypatch) as client:
        _two_profiles(client)
        client.post(f"/api/connections/{work.id}/pairing", json={"value": "42"})
        client.post(f"/api/connections/{work.id}/default", json={"profile": "work"})

        entry = client.get("/api/connections").json()["connections"][0]

        assert entry == {
            "id": work.id,
            "platform": "telegram",
            "name": "Work bot",
            "tokens": {"TELEGRAM_BOT_TOKEN": {"set": True, "hint": "…work"}},
            "default_profile": "work",
            "active": True,
            "error": None,
            "paired_accounts": 1,
        }
        assert "1111-work" not in json.dumps(entry)


# --- Group Peers per Connection (GET/POST /api/connections/{cid}/groups*) ---


def test_a_connection_lists_only_its_own_groups(monkeypatch):
    """A group belongs to the bot it is talking to, not to the platform."""
    work, play = _two_telegram_bots(monkeypatch)
    with _new_client(monkeypatch) as client:
        _two_profiles(client)
        peers.select_profile(work.id, "-100", "work", platform="telegram", surface="group")
        peers.select_profile(play.id, "-200", "home", platform="telegram", surface="group")
        peers.select_profile(work.id, "42", "work", platform="telegram")  # a DM, not a group

        assert client.get(f"/api/connections/{work.id}/groups").json()["groups"] == [
            {"chat_id": "-100", "profile": "work"}
        ]
        assert client.get(f"/api/connections/{play.id}/groups").json()["groups"] == [
            {"chat_id": "-200", "profile": "home"}
        ]


def test_a_profile_withdrawn_from_this_connections_groups_is_not_offered(monkeypatch):
    """What the picker offers is what this Connection can reach — so a group pinned
    outside that set reads as unreachable rather than looking fine."""
    work, play = _two_telegram_bots(monkeypatch)
    with _new_client(monkeypatch) as client:
        _two_profiles(client)
        client.post(
            f"/api/connections/{work.id}/exposure",
            json={"profile": "home", "surface": f"{work.id}:group", "exposed": False},
        )

        assert [
            p["id"] for p in client.get(f"/api/connections/{work.id}/groups").json()["profiles"]
        ] == ["work"]
        # The other bot is untouched.
        assert [
            p["id"] for p in client.get(f"/api/connections/{play.id}/groups").json()["profiles"]
        ] == [
            "work",
            "home",
        ]


def test_re_pointing_a_group_on_its_connection_moves_it(monkeypatch):
    work, play = _two_telegram_bots(monkeypatch)
    with _new_client(monkeypatch) as client:
        _two_profiles(client)
        peers.select_profile(work.id, "-100", "work", platform="telegram", surface="group")
        peers.attach(work.id, "-100", "tg-1", platform="telegram", surface="group")

        r = client.post(f"/api/connections/{work.id}/groups/-100/profile", json={"profile": "home"})
        assert r.status_code == 200
        assert r.json()["groups"] == [{"chat_id": "-100", "profile": "home"}]
        assert peers.get_peer(work.id, "-100").profile == "home"
        # Moving a group leaves its Chat behind, as any profile switch does.
        assert peers.get_peer(work.id, "-100").chat is None
        # Nothing was created on the other bot.
        assert client.get(f"/api/connections/{play.id}/groups").json()["groups"] == []


def test_a_group_cannot_be_pointed_at_a_profile_this_connection_cannot_reach(monkeypatch):
    work, _ = _two_telegram_bots(monkeypatch)
    with _new_client(monkeypatch) as client:
        _two_profiles(client)
        peers.select_profile(work.id, "-100", "work", platform="telegram", surface="group")
        client.post(
            f"/api/connections/{work.id}/exposure",
            json={"profile": "home", "surface": f"{work.id}:group", "exposed": False},
        )

        r = client.post(f"/api/connections/{work.id}/groups/-100/profile", json={"profile": "home"})
        assert r.status_code == 400
        assert peers.get_peer(work.id, "-100").profile == "work"


def test_re_pointing_a_group_of_another_connection_404s(monkeypatch):
    """A chat id is not unique across bots; the group has to be one of this one's."""
    work, play = _two_telegram_bots(monkeypatch)
    with _new_client(monkeypatch) as client:
        _two_profiles(client)
        peers.select_profile(play.id, "-100", "work", platform="telegram", surface="group")

        r = client.post(f"/api/connections/{work.id}/groups/-100/profile", json={"profile": "home"})
        assert r.status_code == 404
        assert peers.get_peer(play.id, "-100").profile == "work"


def test_connection_group_routes_404_on_an_unknown_connection(monkeypatch):
    _two_telegram_bots(monkeypatch)
    with _new_client(monkeypatch) as client:
        _two_profiles(client)
        assert client.get("/api/connections/nope/groups").status_code == 404
        assert (
            client.post(
                "/api/connections/nope/groups/-100/profile", json={"profile": "work"}
            ).status_code
            == 404
        )
