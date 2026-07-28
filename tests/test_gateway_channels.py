"""Install-level Channels: the API, boot, and the per-Channel default profile.

A channel (telegram/discord/slack) starts ONCE for the whole install as soon as its
token is configured — it is never owned by a profile (ADR 0019). What the registry
holds is a per-Channel *default profile*: where that platform's conversations land
when nothing else has been chosen. Endpoints: GET /api/channels (state),
POST /api/channels/default (set/clear the default) and POST /api/channels/token.
"""

import os

from fastapi.testclient import TestClient

import assistant.channels as channels_mod
from assistant import profiles, secrets
from assistant.channels.base import InboundMessage
from assistant.gateway.app import create_app
from assistant.gateway.profile_manager import ProfileManager
from tests.conftest import use_fake_agent


class FakeChannel:
    """Stub Channel: records start/stop without touching a network."""

    def __init__(self, platform: str) -> None:
        self.platform = platform
        self.started = False
        self.stopped = False
        self.router = None

    async def start(self, router) -> None:
        self.started = True
        self.router = router

    async def stop(self) -> None:
        self.stopped = True


def _stub_channels(monkeypatch):
    """Make get_channel return FakeChannels (patched where start_channel imports it)."""
    monkeypatch.setattr(channels_mod, "get_channel", lambda platform, **kw: FakeChannel(platform))


def _app(monkeypatch, **kw):

    use_fake_agent(monkeypatch)
    return create_app(ProfileManager(memory=False, persist=False), **kw)


def _new_client(monkeypatch, **kw):

    return TestClient(_app(monkeypatch, **kw))


def _no_channel_env(monkeypatch):
    for env in ("TELEGRAM_BOT_TOKEN", "DISCORD_BOT_TOKEN", "SLACK_BOT_TOKEN", "SLACK_APP_TOKEN"):
        monkeypatch.delenv(env, raising=False)


def _inbound(platform: str = "telegram") -> InboundMessage:
    return InboundMessage(
        text="hi", sender_id="u1", chat_id="c1", platform=platform, is_direct=True
    )


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
        assert manager.channels["discord"].started is True
        assert manager.channels["discord"].router is manager.router
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
        assert manager.channels["telegram"].started is True
        assert manager.gateway_for(_inbound()) is None


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
        assert "discord" not in manager.channels
        assert "could not start 'discord'" in manager.channel_errors["discord"]
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
            }
        }
        assert profiles.channel_defaults()["telegram"] == "work"
        assert manager.gateway_for(_inbound()) is manager.get("work").gateway
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
        adapter = manager.channels["telegram"]

        r = client.post(
            "/api/channels/default", json={"platform": "telegram", "profile": "personal"}
        )
        assert r.json()["telegram"]["default_profile"] == "personal"
        assert manager.channels["telegram"] is adapter  # same live adapter
        assert adapter.stopped is False
        assert manager.gateway_for(_inbound()) is manager.get("personal").gateway


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
            }
        }
        assert manager.channels["telegram"].stopped is False
        assert manager.gateway_for(_inbound()) is None
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
        adapter = manager.channels["telegram"]

        r = client.request("DELETE", "/api/profiles/work", json={"new_default": "personal"})
        assert r.status_code == 200
        assert profiles.channel_defaults()["telegram"] is None
        assert adapter.stopped is False
        assert client.get("/api/channels").json()["telegram"] == {
            "default_profile": None,
            "token_present": True,
            "active": True,
            "error": None,
        }
        assert manager.gateway_for(_inbound()) is None

        # personal can take over
        r = client.post(
            "/api/channels/default", json={"platform": "telegram", "profile": "personal"}
        )
        assert r.json()["telegram"]["default_profile"] == "personal"
        assert manager.gateway_for(_inbound()) is manager.get("personal").gateway


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
        assert client.app.state.profiles.channels["telegram"].started is True
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
        chan = manager.channels["telegram"]

        r = client.post(
            "/api/channels/token",
            json={"platform": "telegram", "tokens": {"TELEGRAM_BOT_TOKEN": ""}},
        )
        entry = r.json()["telegram"]
        assert entry["token_present"] is False
        assert entry["active"] is False
        assert entry["default_profile"] == "work"  # the default survives the token going
        assert "telegram" not in manager.channels
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
        assert "slack" not in manager.channels

        # add the app token → now both present → starts
        r = client.post(
            "/api/channels/token",
            json={"platform": "slack", "tokens": {"SLACK_APP_TOKEN": "a-tok"}},
        )
        entry = r.json()["slack"]
        assert entry["token_present"] is True
        assert entry["active"] is True
        assert "slack" in manager.channels


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
        assert client.app.state.profiles.gateway_for(_inbound("discord")) is None
