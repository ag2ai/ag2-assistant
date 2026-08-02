"""Phase 3B: install-level channel bindings API + hot start/stop/rebind.

A channel (telegram/discord/slack) is an install-level resource assigned to
exactly one profile or disabled. The binding lives in the global registry, so
two-profiles-enable conflicts are structurally impossible. Endpoints:
GET /api/channels (state) and POST /api/channels (bind/rebind/disable).

Tokens are real entries in the install's secret store and channels come from an
injected factory, so nothing here patches the environment or the channels module.
"""

from fastapi.testclient import TestClient

from assistant.gateway.app import create_app
from assistant.profiles import ProfileRegistry
from assistant.secrets import SecretStore
from tests.support.apps import make_manager


def _client(paths, *, channel_factory=None, made=None):
    """A started app over ``paths`` whose channels come from an injected factory."""
    manager = make_manager(paths, channel_factory=channel_factory, made=made)
    return TestClient(create_app(manager))


def _bindings(paths) -> dict:
    return ProfileRegistry(paths).channel_bindings()


# --- GET /api/channels: shape, zero-profile install all-null ---


def test_get_channels_zero_profile_all_null(paths):
    with _client(paths) as client:
        chans = client.get("/api/channels").json()
        assert set(chans) == {"telegram", "discord", "slack"}
        for entry in chans.values():
            assert entry == {
                "profile": None,
                "token_present": False,
                "active": False,
                "error": None,
            }


def test_get_channels_reflects_token_present(paths):
    SecretStore(paths).set_channel_token("TELEGRAM_BOT_TOKEN", "tok")
    with _client(paths) as client:
        chans = client.get("/api/channels").json()
        assert chans["telegram"]["token_present"] is True
        assert chans["discord"]["token_present"] is False


# --- POST /api/channels: bind + start ---


def test_post_binds_and_starts(paths):
    SecretStore(paths).set_channel_token("TELEGRAM_BOT_TOKEN", "tok")
    with _client(paths) as client:
        client.post("/api/profiles", json={"name": "Work", "accent": "#109e91"})

        r = client.post("/api/channels", json={"platform": "telegram", "profile": "work"})
        assert r.status_code == 200
        assert r.json() == {
            "telegram": {
                "profile": "work",
                "token_present": True,
                "active": True,
                "error": None,
            }
        }
        # persisted in the registry + live on the runtime
        assert _bindings(paths)["telegram"] == "work"
        runtime = client.app.state.profiles.get("work")
        assert "telegram" in runtime.channels
        assert runtime.channels["telegram"].started is True
        # reflected in GET
        assert client.get("/api/channels").json()["telegram"]["profile"] == "work"


def test_bound_channel_is_built_with_the_stored_token(paths):
    """The token reaches the channel as an explicit constructor kwarg — the adapter
    never has to read the process environment."""
    SecretStore(paths).set_channel_token("TELEGRAM_BOT_TOKEN", "tok-from-store")
    made = []
    with _client(paths, made=made) as client:
        client.post("/api/profiles", json={"name": "Work", "accent": "#109e91"})
        client.post("/api/channels", json={"platform": "telegram", "profile": "work"})
    assert [c.platform for c in made] == ["telegram"]
    assert made[0].tokens == {"token": "tok-from-store"}


def test_post_unknown_platform_400(paths):
    with _client(paths) as client:
        client.post("/api/profiles", json={"name": "Work", "accent": "#109e91"})
        r = client.post("/api/channels", json={"platform": "irc", "profile": "work"})
        assert r.status_code == 400
        assert "irc" in r.json()["error"]


def test_post_unknown_profile_400(paths):
    SecretStore(paths).set_channel_token("TELEGRAM_BOT_TOKEN", "tok")
    with _client(paths) as client:
        client.post("/api/profiles", json={"name": "Work", "accent": "#109e91"})
        r = client.post("/api/channels", json={"platform": "telegram", "profile": "ghost"})
        assert r.status_code == 400
        assert "ghost" in r.json()["error"]


def test_post_archived_profile_400(paths):
    SecretStore(paths).set_channel_token("TELEGRAM_BOT_TOKEN", "tok")
    with _client(paths) as client:
        client.post("/api/profiles", json={"name": "Work", "accent": "#109e91"})
        client.post("/api/profiles", json={"name": "Personal", "accent": "#f95339"})
        # archive personal (non-default needs no replacement)
        assert client.request("DELETE", "/api/profiles/personal").status_code == 200
        assert ProfileRegistry(paths).get_profile("personal").archived is True

        r = client.post("/api/channels", json={"platform": "telegram", "profile": "personal"})
        assert r.status_code == 400
        assert "personal" in r.json()["error"]


# --- rebind: moves the live channel from A's runtime to B's ---


def test_rebind_moves_live_channel(paths):
    SecretStore(paths).set_channel_token("TELEGRAM_BOT_TOKEN", "tok")
    with _client(paths) as client:
        client.post("/api/profiles", json={"name": "Work", "accent": "#109e91"})
        client.post("/api/profiles", json={"name": "Personal", "accent": "#f95339"})

        # bind to work
        client.post("/api/channels", json={"platform": "telegram", "profile": "work"})
        work_rt = client.app.state.profiles.get("work")
        personal_rt = client.app.state.profiles.get("personal")
        assert "telegram" in work_rt.channels
        work_chan = work_rt.channels["telegram"]

        # rebind to personal → work loses it (stopped), personal gains a fresh one
        r = client.post("/api/channels", json={"platform": "telegram", "profile": "personal"})
        assert r.json()["telegram"]["profile"] == "personal"
        assert r.json()["telegram"]["active"] is True
        assert "telegram" not in work_rt.channels
        assert work_chan.stopped is True
        assert "telegram" in personal_rt.channels
        assert personal_rt.channels["telegram"].started is True
        assert _bindings(paths)["telegram"] == "personal"


# --- disable: profile null stops the channel ---


def test_disable_stops_channel(paths):
    SecretStore(paths).set_channel_token("TELEGRAM_BOT_TOKEN", "tok")
    with _client(paths) as client:
        client.post("/api/profiles", json={"name": "Work", "accent": "#109e91"})
        client.post("/api/channels", json={"platform": "telegram", "profile": "work"})
        work_rt = client.app.state.profiles.get("work")
        chan = work_rt.channels["telegram"]

        r = client.post("/api/channels", json={"platform": "telegram", "profile": None})
        assert r.json() == {
            "telegram": {
                "profile": None,
                "token_present": True,
                "active": False,
                "error": None,
            }
        }
        assert "telegram" not in work_rt.channels
        assert chan.stopped is True
        assert _bindings(paths)["telegram"] is None


# --- bad-token start failure: binding persisted, inactive, error surfaced ---


def test_bad_token_start_failure_binding_persisted(paths):
    """A real channel's start() raises on a bad token/network. The binding must persist
    (registry reflects intent), active:false, and the error surfaces in GET /api/channels
    — never a 500, never a boot crash."""

    class BoomChannel:
        platform = "telegram"

        async def start(self, gateway):
            raise RuntimeError("Unauthorized: invalid token")

        async def stop(self):
            pass

    SecretStore(paths).set_channel_token("TELEGRAM_BOT_TOKEN", "bad")
    with _client(paths, channel_factory=lambda platform, **kw: BoomChannel()) as client:
        client.post("/api/profiles", json={"name": "Work", "accent": "#109e91"})
        r = client.post("/api/channels", json={"platform": "telegram", "profile": "work"})
        assert r.status_code == 200
        entry = r.json()["telegram"]
        assert entry["profile"] == "work"  # binding persisted
        assert entry["active"] is False
        assert "could not start 'telegram'" in entry["error"]
        # binding really is in the registry
        assert _bindings(paths)["telegram"] == "work"
        # surfaced in GET /api/channels too
        got = client.get("/api/channels").json()["telegram"]
        assert got["profile"] == "work"
        assert got["active"] is False
        assert "could not start 'telegram'" in got["error"]


def test_start_failure_error_never_echoes_token(paths):
    """Platform libraries embed the raw token in some error messages (Telegram:
    "The token <value> was rejected"). The recorded/returned error must be scrubbed."""

    class EchoBoomChannel:
        platform = "telegram"

        def __init__(self, token):
            self._token = token

        async def start(self, gateway):
            raise RuntimeError(f"The token `{self._token}` was rejected")

        async def stop(self):
            pass

    secret = "8123456:very-secret-token-value"
    SecretStore(paths).set_channel_token("TELEGRAM_BOT_TOKEN", secret)
    with _client(paths, channel_factory=lambda platform, **kw: EchoBoomChannel(**kw)) as client:
        client.post("/api/profiles", json={"name": "Work", "accent": "#109e91"})
        r = client.post("/api/channels", json={"platform": "telegram", "profile": "work"})
        assert r.status_code == 200
        assert secret not in r.text
        assert "•••" in r.json()["telegram"]["error"]
        assert secret not in client.get("/api/channels").text


def test_missing_token_bind_persisted_inactive(paths):
    """Binding a platform whose token is absent persists the binding but stays
    inactive with a 'no token configured' reason."""

    with _client(paths) as client:
        client.post("/api/profiles", json={"name": "Work", "accent": "#109e91"})
        r = client.post("/api/channels", json={"platform": "telegram", "profile": "work"})
        assert r.status_code == 200
        entry = r.json()["telegram"]
        assert entry["profile"] == "work"
        assert entry["active"] is False
        assert entry["token_present"] is False
        assert "no token configured for telegram" in entry["error"]
        assert _bindings(paths)["telegram"] == "work"


# --- boot: a bound channel starts at boot ---


def test_bound_channel_starts_on_boot(paths):
    SecretStore(paths).set_channel_token("DISCORD_BOT_TOKEN", "tok")

    # Pre-create a profile + registry binding BEFORE the app boots.
    registry = ProfileRegistry(paths)
    meta = registry.create_profile("Work", "#109e91")
    registry.profile_dir(meta.id).mkdir(parents=True, exist_ok=True)
    registry.bind_channel("discord", meta.id)

    with _client(paths) as client:
        runtime = client.app.state.profiles.get(meta.id)
        assert "discord" in runtime.channels
        assert runtime.channels["discord"].started is True
        assert client.get("/api/channels").json()["discord"]["active"] is True


def test_bound_channel_start_failure_does_not_crash_boot(paths):
    """A bound channel whose start() raises must still boot the runtime; the failure is
    recorded on manager.channel_errors and surfaced, not fatal."""

    class BoomChannel:
        platform = "discord"

        async def start(self, gateway):
            raise RuntimeError("connect failed")

        async def stop(self):
            pass

    SecretStore(paths).set_channel_token("DISCORD_BOT_TOKEN", "bad")
    registry = ProfileRegistry(paths)
    meta = registry.create_profile("Work", "#109e91")
    registry.profile_dir(meta.id).mkdir(parents=True, exist_ok=True)
    registry.bind_channel("discord", meta.id)

    with _client(paths, channel_factory=lambda platform, **kw: BoomChannel()) as client:
        runtime = client.app.state.profiles.get(meta.id)  # booted despite failure
        assert "discord" not in runtime.channels
        assert "could not start 'discord'" in client.app.state.profiles.channel_errors["discord"]
        got = client.get("/api/channels").json()["discord"]
        assert got["profile"] == meta.id
        assert got["active"] is False
        assert "could not start 'discord'" in got["error"]


# --- archive: a bound-profile archive clears the binding + stops the channel ---


def test_archive_owner_clears_binding_and_stops(paths):
    SecretStore(paths).set_channel_token("TELEGRAM_BOT_TOKEN", "tok")

    with _client(paths) as client:
        client.post("/api/profiles", json={"name": "Work", "accent": "#109e91"})
        client.post("/api/profiles", json={"name": "Personal", "accent": "#f95339"})

        # work owns telegram
        client.post("/api/channels", json={"platform": "telegram", "profile": "work"})
        work_rt = client.app.state.profiles.get("work")
        chan = work_rt.channels["telegram"]
        assert _bindings(paths)["telegram"] == "work"

        # archive work (naming personal as the replacement default)
        r = client.request("DELETE", "/api/profiles/work", json={"new_default": "personal"})
        assert r.status_code == 200
        # binding cleared, channel stopped
        assert _bindings(paths)["telegram"] is None
        assert chan.stopped is True
        assert client.get("/api/channels").json()["telegram"] == {
            "profile": None,
            "token_present": True,
            "active": False,
            "error": None,
        }

        # personal can now bind
        r = client.post("/api/channels", json={"platform": "telegram", "profile": "personal"})
        assert r.json()["telegram"]["active"] is True


# --- POST /api/channels/token: secrets-backed tokens, live apply ---


def test_post_token_saves_flips_present_and_starts(paths):
    """Saving a token for a BOUND platform: token_present flips true, the channel
    starts live on the bound runtime, and the value is never echoed."""

    with _client(paths) as client:
        client.post("/api/profiles", json={"name": "Work", "accent": "#109e91"})
        # bind first (no token yet → inactive, waiting)
        r = client.post("/api/channels", json={"platform": "telegram", "profile": "work"})
        assert r.json()["telegram"]["active"] is False
        assert r.json()["telegram"]["token_present"] is False

        # now supply the token via the secrets endpoint
        r = client.post(
            "/api/channels/token",
            json={"platform": "telegram", "tokens": {"TELEGRAM_BOT_TOKEN": "live-secret-tok"}},
        )
        assert r.status_code == 200
        entry = r.json()["telegram"]
        assert entry["profile"] == "work"
        assert entry["token_present"] is True
        assert entry["active"] is True
        assert entry["error"] is None
        # value never echoed anywhere in the response
        assert "live-secret-tok" not in r.text
        # started live on the bound runtime
        runtime = client.app.state.profiles.get("work")
        assert runtime.channels["telegram"].started is True
        # persisted to the secrets store
        assert SecretStore(paths).channel_token_status({})["TELEGRAM_BOT_TOKEN"] is True
        assert _bindings(paths)["telegram"] == "work"


def test_post_token_clear_stops_channel(paths):
    """Clearing the token for a live bound platform stops it and returns to waiting."""
    with _client(paths) as client:
        client.post("/api/profiles", json={"name": "Work", "accent": "#109e91"})
        client.post("/api/channels", json={"platform": "telegram", "profile": "work"})
        client.post(
            "/api/channels/token",
            json={"platform": "telegram", "tokens": {"TELEGRAM_BOT_TOKEN": "tok"}},
        )
        runtime = client.app.state.profiles.get("work")
        chan = runtime.channels["telegram"]

        # clear it
        r = client.post(
            "/api/channels/token",
            json={"platform": "telegram", "tokens": {"TELEGRAM_BOT_TOKEN": ""}},
        )
        entry = r.json()["telegram"]
        assert entry["token_present"] is False
        assert entry["active"] is False
        assert entry["profile"] == "work"  # binding intact — just waiting for a token
        assert "telegram" not in runtime.channels
        assert chan.stopped is True


def test_post_token_slack_requires_both(paths):
    """Slack needs BOTH tokens: with only one present it is not token_present and does
    not start."""
    with _client(paths) as client:
        client.post("/api/profiles", json={"name": "Work", "accent": "#109e91"})
        client.post("/api/channels", json={"platform": "slack", "profile": "work"})

        # only the bot token
        r = client.post(
            "/api/channels/token",
            json={"platform": "slack", "tokens": {"SLACK_BOT_TOKEN": "b-tok"}},
        )
        entry = r.json()["slack"]
        assert entry["token_present"] is False
        assert entry["active"] is False
        runtime = client.app.state.profiles.get("work")
        assert "slack" not in runtime.channels

        # add the app token → now both present → starts
        r = client.post(
            "/api/channels/token",
            json={"platform": "slack", "tokens": {"SLACK_APP_TOKEN": "a-tok"}},
        )
        entry = r.json()["slack"]
        assert entry["token_present"] is True
        assert entry["active"] is True
        assert "slack" in runtime.channels


def test_post_token_unknown_platform_400(paths):
    with _client(paths) as client:
        r = client.post(
            "/api/channels/token",
            json={"platform": "irc", "tokens": {"IRC_TOKEN": "x"}},
        )
        assert r.status_code == 400
        assert "irc" in r.json()["error"]


def test_post_token_invalid_env_for_platform_400(paths):
    """An env name not valid for the platform → 400, nothing saved."""

    with _client(paths) as client:
        r = client.post(
            "/api/channels/token",
            json={"platform": "telegram", "tokens": {"SLACK_BOT_TOKEN": "wrong"}},
        )
        assert r.status_code == 400
        assert "SLACK_BOT_TOKEN" in r.json()["error"]
        # nothing persisted
        assert SecretStore(paths).channel_token_status({})["SLACK_BOT_TOKEN"] is False


def test_post_token_unbound_platform_saves_without_start(paths):
    """Saving a token for an UNBOUND platform persists it + flips token_present, but
    starts nothing (no owning profile). Value never echoed."""

    made = []
    with _client(paths, made=made) as client:
        r = client.post(
            "/api/channels/token",
            json={"platform": "discord", "tokens": {"DISCORD_BOT_TOKEN": "hidden-tok"}},
        )
        assert r.status_code == 200
        entry = r.json()["discord"]
        assert entry["profile"] is None
        assert entry["token_present"] is True
        assert entry["active"] is False
        assert "hidden-tok" not in r.text
        assert SecretStore(paths).channel_token_status({})["DISCORD_BOT_TOKEN"] is True
    assert made == []  # nothing was ever constructed
