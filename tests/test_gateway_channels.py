"""Install-level Channels: the API, boot, and the per-Channel default profile.

A channel (telegram/discord/slack) starts ONCE for the whole install as soon as its
token is configured — it is never owned by a profile (ADR 0019). What the registry
holds is a per-Channel *default profile*: where that platform's conversations land
when nothing else has been chosen. Endpoints: GET /api/channels (state),
POST /api/channels/default (set/clear the default) and POST /api/channels/token.

GET /api/connections lists the Connections — one configured instance of a platform
each, migrated from an install's existing bot tokens on first read.
"""

import os

from fastapi.testclient import TestClient

import assistant.channels as channels_mod
from assistant import connections, peers, profiles, secrets
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
    """Where a conversation on ``platform`` lands when the Peer has chosen nothing —
    i.e. what the Channel's default profile resolves to right now."""
    pid = manager.default_profile(platform)
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
        assert profiles.channel_defaults()["telegram"] == "work"
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
        assert profiles.channel_defaults()["telegram"] is None


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
        assert profiles.channel_defaults()["telegram"] is None
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
    profiles.set_channel_default("telegram", "work")
    profiles.delete_profile("work")

    assert profiles.get_profile("work") is None
    assert profiles.channel_defaults()["telegram"] is None


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
        client.post("/api/channels/default", json={"platform": "telegram", "profile": "work"})
        client.post(
            "/api/channels/token",
            json={"platform": "telegram", "tokens": {"TELEGRAM_BOT_TOKEN": "tok"}},
        )
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
        peers.attach("telegram", "42", "web-1", connection=_only_connection())

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
        peers.select_profile("telegram", "-100", "work", surface="group")
        peers.select_profile("telegram", "42", "home")  # a DM, not a group

        view = client.get("/api/channels/telegram/groups").json()
        assert view["groups"] == [{"chat_id": "-100", "profile": "work"}]
        assert [p["id"] for p in view["profiles"]] == ["work", "home"]


def test_a_profile_withheld_from_groups_is_absent_from_the_group_picker(monkeypatch):
    with _new_client(monkeypatch) as client:
        _two_profiles(client)
        client.post(
            "/api/profiles/home/exposure", json={"surface": "telegram:group", "exposed": False}
        )

        view = client.get("/api/channels/telegram/groups").json()
        assert [p["id"] for p in view["profiles"]] == ["work"]


def test_re_pointing_a_group_moves_it_and_leaves_its_chat_behind(monkeypatch):
    with _new_client(monkeypatch) as client:
        _two_profiles(client)
        peers.select_profile("telegram", "-100", "work", surface="group")
        peers.attach("telegram", "-100", "tg-1", surface="group")

        r = client.post("/api/channels/telegram/groups/-100/profile", json={"profile": "home"})
        assert r.status_code == 200
        assert r.json()["groups"] == [{"chat_id": "-100", "profile": "home"}]
        assert peers.get_peer("telegram", "-100").chat is None


def test_a_group_cannot_be_pointed_at_a_profile_withheld_from_groups(monkeypatch):
    """The fence has one gate; the WebUI is not a way around it."""
    with _new_client(monkeypatch) as client:
        _two_profiles(client)
        peers.select_profile("telegram", "-100", "work", surface="group")
        client.post(
            "/api/profiles/home/exposure", json={"surface": "telegram:group", "exposed": False}
        )

        r = client.post("/api/channels/telegram/groups/-100/profile", json={"profile": "home"})
        assert r.status_code == 400
        assert peers.get_peer("telegram", "-100").profile == "work"


def test_re_pointing_something_that_is_not_a_group_peer_404s(monkeypatch):
    with _new_client(monkeypatch) as client:
        _two_profiles(client)
        peers.select_profile("telegram", "42", "work")  # a DM

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
        assert peers.get_peer("telegram", "42").profile == "work"


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
        assert set(entry) == {"id", "platform", "name", "tokens"}
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
        peers.attach("telegram", "42", "web-1", connection=play.id)

        r = client.post(api("work", "/message"), json={"text": "hello", "chat_id": "web-1"})
        assert r.status_code == 200

        assert manager.channels[work.id].pushed == []
        assert manager.channels[play.id].pushed == [("42", "You: hello\n\nMe: echo[1]: hello")]
