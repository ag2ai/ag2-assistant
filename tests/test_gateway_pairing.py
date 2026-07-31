"""Paired accounts through the API — the Settings surface for who a Connection answers.

Routes are install-level, like the Connection they govern (ADR 0019/0021):
GET/POST /api/connections/{cid}/pairing, DELETE …/pairing/{key} and POST …/pairing/code
to mint the one-time code. The platform-keyed routes act on the platform's Connection.
"""

from fastapi.testclient import TestClient

import assistant.channels as channels_mod
from assistant import connections, pairing
from assistant.gateway.app import create_app
from assistant.gateway.profile_manager import ProfileManager
from tests.conftest import use_fake_agent


class FakeChannel:
    """Stub adapter: never touches a network."""

    def __init__(self, platform: str, connection: str = "", **tokens: str) -> None:
        self.platform = platform

    async def start(self, router) -> None:
        pass

    async def stop(self) -> None:
        pass


def _client(monkeypatch) -> TestClient:
    """An install with every platform's token seeded, so each has one Connection to
    pair against."""
    use_fake_agent(monkeypatch)
    for env in ("TELEGRAM_BOT_TOKEN", "DISCORD_BOT_TOKEN", "SLACK_BOT_TOKEN", "SLACK_APP_TOKEN"):
        monkeypatch.setenv(env, "tok")
    monkeypatch.setattr(
        channels_mod, "get_channel", lambda platform, **kw: FakeChannel(platform, **kw)
    )
    return TestClient(create_app(ProfileManager(memory=False, persist=False)))


def _pairing(client, platform: str = "telegram") -> dict:
    return client.get(f"/api/channels/{platform}/pairing").json()


def _cid(platform: str = "telegram") -> str:
    return connections.connections_for(platform)[0].id


# --- listing ---


def test_a_fresh_channel_has_nobody_paired(monkeypatch):
    with _client(monkeypatch) as client:
        assert _pairing(client) == {"accounts": [], "code": None}


def test_an_unknown_platform_is_rejected(monkeypatch):
    with _client(monkeypatch) as client:
        assert client.get("/api/channels/carrier-pigeon/pairing").status_code == 400


def test_a_platform_with_no_connection_has_nobody_to_pair_to(monkeypatch):
    """Pairing is a grant to a Connection, so an unconfigured platform reads as an
    empty roster and refuses a write rather than granting into thin air."""
    use_fake_agent(monkeypatch)
    for env in ("TELEGRAM_BOT_TOKEN", "DISCORD_BOT_TOKEN", "SLACK_BOT_TOKEN", "SLACK_APP_TOKEN"):
        monkeypatch.delenv(env, raising=False)
    with TestClient(create_app(ProfileManager(memory=False, persist=False))) as client:
        assert _pairing(client) == {"accounts": [], "code": None}
        r = client.post("/api/channels/telegram/pairing", json={"value": "42"})
        assert r.status_code == 400
        assert "telegram" in r.json()["error"]


def test_an_unknown_connection_is_a_404(monkeypatch):
    with _client(monkeypatch) as client:
        assert client.get("/api/connections/cn-nope/pairing").status_code == 404
        r = client.post("/api/connections/cn-nope/pairing", json={"value": "42"})
        assert r.status_code == 404


# --- adding an account ---


def test_a_numeric_id_is_listed_as_pinned(monkeypatch):
    with _client(monkeypatch) as client:
        client.post("/api/channels/telegram/pairing", json={"value": "42"})
        assert _pairing(client)["accounts"] == [
            {"key": "42", "account_id": "42", "handle": None, "pending": False}
        ]


def test_a_handle_is_listed_distinctly_as_pending(monkeypatch):
    with _client(monkeypatch) as client:
        client.post("/api/channels/telegram/pairing", json={"value": "@nikita"})
        assert _pairing(client)["accounts"] == [
            {"key": "@nikita", "account_id": None, "handle": "nikita", "pending": True}
        ]


def test_a_handle_is_refused_where_messages_carry_none(monkeypatch):
    """Slack sends only a user id, so the Connection's platform still decides this."""
    with _client(monkeypatch) as client:
        r = client.post(f"/api/connections/{_cid('slack')}/pairing", json={"value": "@nikita"})
        assert r.status_code == 400


def test_adding_an_account_takes_effect_at_once(monkeypatch):
    with _client(monkeypatch) as client:
        client.post("/api/channels/telegram/pairing", json={"value": "42"})
        assert pairing.is_paired(_cid(), "42") is True


def test_an_empty_entry_is_rejected(monkeypatch):
    with _client(monkeypatch) as client:
        assert (
            client.post("/api/channels/telegram/pairing", json={"value": "  "}).status_code == 400
        )


def test_pairing_is_per_channel(monkeypatch):
    with _client(monkeypatch) as client:
        client.post("/api/channels/telegram/pairing", json={"value": "42"})
        assert _pairing(client, "discord")["accounts"] == []


# --- two Connections of one platform have two rosters ---


def test_an_account_paired_to_one_connection_is_unknown_to_another(monkeypatch):
    with _client(monkeypatch) as client:
        other = connections.create_connection("telegram", "Personal", {"TELEGRAM_BOT_TOKEN": "t2"})
        client.post(f"/api/connections/{_cid()}/pairing", json={"value": "42"})
        assert client.get(f"/api/connections/{other.id}/pairing").json()["accounts"] == []
        assert pairing.is_paired(other.id, "42") is False


def test_revoking_from_one_connection_leaves_the_other_paired(monkeypatch):
    with _client(monkeypatch) as client:
        other = connections.create_connection("telegram", "Personal", {"TELEGRAM_BOT_TOKEN": "t2"})
        for cid in (_cid(), other.id):
            client.post(f"/api/connections/{cid}/pairing", json={"value": "42"})
        client.delete(f"/api/connections/{_cid()}/pairing/42")
        assert pairing.is_paired(other.id, "42") is True


def test_a_code_is_minted_per_connection_and_replaces_only_its_own(monkeypatch):
    with _client(monkeypatch) as client:
        other = connections.create_connection("telegram", "Personal", {"TELEGRAM_BOT_TOKEN": "t2"})
        theirs = client.post(f"/api/connections/{other.id}/pairing/code").json()["code"]
        client.post(f"/api/connections/{_cid()}/pairing/code")
        assert client.get(f"/api/connections/{other.id}/pairing").json()["code"]["code"] == theirs
        assert pairing.redeem(_cid(), theirs, "42") == pairing.UNKNOWN


# --- revoking ---


def test_revoking_removes_the_account_immediately(monkeypatch):
    with _client(monkeypatch) as client:
        client.post("/api/channels/telegram/pairing", json={"value": "42"})
        assert client.delete("/api/channels/telegram/pairing/42").status_code == 200
        assert _pairing(client)["accounts"] == []
        assert pairing.is_paired(_cid(), "42") is False


def test_revoking_a_pending_invitation_works_by_its_handle(monkeypatch):
    with _client(monkeypatch) as client:
        client.post("/api/channels/telegram/pairing", json={"value": "@nikita"})
        client.delete("/api/channels/telegram/pairing/@nikita")
        assert _pairing(client)["accounts"] == []


def test_revoking_something_absent_is_a_404(monkeypatch):
    with _client(monkeypatch) as client:
        assert client.delete("/api/channels/telegram/pairing/42").status_code == 404


# --- the one-time code ---


def test_issuing_a_code_returns_it_and_shows_it_until_it_is_used(monkeypatch):
    with _client(monkeypatch) as client:
        code = client.post("/api/channels/telegram/pairing/code").json()["code"]
        assert _pairing(client)["code"]["code"] == code

        pairing.redeem(_cid(), code, "42")
        assert _pairing(client)["code"] is None
        assert _pairing(client)["accounts"][0]["account_id"] == "42"


def test_the_code_carries_when_it_stops_working(monkeypatch):
    with _client(monkeypatch) as client:
        issued = client.post("/api/channels/telegram/pairing/code").json()
        assert issued["expires_at"] > 0


# --- the Channel's own entry says why it is silent ---


def test_a_channel_entry_counts_its_paired_accounts(monkeypatch):
    with _client(monkeypatch) as client:
        assert client.get("/api/channels").json()["telegram"]["paired_accounts"] == 0
        client.post("/api/channels/telegram/pairing", json={"value": "42"})
        assert client.get("/api/channels").json()["telegram"]["paired_accounts"] == 1


def test_a_platform_entry_counts_every_connections_accounts(monkeypatch):
    """The platform view survives as a sum over its Connections."""
    with _client(monkeypatch) as client:
        other = connections.create_connection("telegram", "Personal", {"TELEGRAM_BOT_TOKEN": "t2"})
        client.post(f"/api/connections/{_cid()}/pairing", json={"value": "42"})
        client.post(f"/api/connections/{other.id}/pairing", json={"value": "99"})
        assert client.get("/api/channels").json()["telegram"]["paired_accounts"] == 2
