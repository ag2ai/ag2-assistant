"""Paired accounts through the API — the Settings surface for who a Connection answers.

Routes are install-level, like the Connection they govern (ADR 0019/0021):
GET/POST /api/connections/{cid}/pairing, DELETE …/pairing/{key} and POST …/pairing/code
to mint the one-time code. A grant is to one Connection and to no other.
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
    return client.get(f"/api/connections/{_cid(platform)}/pairing").json()


def _pair(client, value: str, platform: str = "telegram"):
    return client.post(f"/api/connections/{_cid(platform)}/pairing", json={"value": value})


def _cid(platform: str = "telegram") -> str:
    return connections.connections_for(platform)[0].id


# --- listing ---


def test_a_fresh_channel_has_nobody_paired(monkeypatch):
    with _client(monkeypatch) as client:
        assert _pairing(client) == {"accounts": [], "code": None}


def test_an_unknown_connection_is_a_404(monkeypatch):
    with _client(monkeypatch) as client:
        assert client.get("/api/connections/cn-nope/pairing").status_code == 404
        r = client.post("/api/connections/cn-nope/pairing", json={"value": "42"})
        assert r.status_code == 404


# --- adding an account ---


def test_a_numeric_id_is_listed_as_pinned(monkeypatch):
    with _client(monkeypatch) as client:
        _pair(client, "42")
        assert _pairing(client)["accounts"] == [
            {"key": "42", "account_id": "42", "handle": None, "pending": False}
        ]


def test_a_handle_is_listed_distinctly_as_pending(monkeypatch):
    with _client(monkeypatch) as client:
        _pair(client, "@nikita")
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
        _pair(client, "42")
        assert pairing.is_paired(_cid(), "42") is True


def test_an_empty_entry_is_rejected(monkeypatch):
    with _client(monkeypatch) as client:
        assert _pair(client, "  ").status_code == 400


def test_pairing_is_per_channel(monkeypatch):
    with _client(monkeypatch) as client:
        _pair(client, "42")
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
        _pair(client, "42")
        assert client.delete(f"/api/connections/{_cid()}/pairing/42").status_code == 200
        assert _pairing(client)["accounts"] == []
        assert pairing.is_paired(_cid(), "42") is False


def test_revoking_a_pending_invitation_works_by_its_handle(monkeypatch):
    with _client(monkeypatch) as client:
        _pair(client, "@nikita")
        client.delete(f"/api/connections/{_cid()}/pairing/@nikita")
        assert _pairing(client)["accounts"] == []


def test_revoking_something_absent_is_a_404(monkeypatch):
    with _client(monkeypatch) as client:
        assert client.delete(f"/api/connections/{_cid()}/pairing/42").status_code == 404


# --- the one-time code ---


def test_issuing_a_code_returns_it_and_shows_it_until_it_is_used(monkeypatch):
    with _client(monkeypatch) as client:
        code = client.post(f"/api/connections/{_cid()}/pairing/code").json()["code"]
        assert _pairing(client)["code"]["code"] == code

        pairing.redeem(_cid(), code, "42")
        assert _pairing(client)["code"] is None
        assert _pairing(client)["accounts"][0]["account_id"] == "42"


def test_the_code_carries_when_it_stops_working(monkeypatch):
    with _client(monkeypatch) as client:
        issued = client.post(f"/api/connections/{_cid()}/pairing/code").json()
        assert issued["expires_at"] > 0


# --- the Connection's own entry says why it is silent ---


def _entry(client, cid: str) -> dict:
    listed = client.get("/api/connections").json()["connections"]
    return next(c for c in listed if c["id"] == cid)


def test_a_connection_entry_counts_its_own_paired_accounts(monkeypatch):
    """A live Connection with nobody paired answers nobody — the count is what lets
    Settings say so, and it counts this Connection's roster alone."""
    with _client(monkeypatch) as client:
        other = connections.create_connection("telegram", "Personal", {"TELEGRAM_BOT_TOKEN": "t2"})
        assert _entry(client, _cid())["paired_accounts"] == 0

        _pair(client, "42")

        assert _entry(client, _cid())["paired_accounts"] == 1
        assert _entry(client, other.id)["paired_accounts"] == 0
