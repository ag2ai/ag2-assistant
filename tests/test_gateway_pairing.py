"""Paired accounts through the API — the Settings surface for who a Connection answers.

Routes are install-level, like the Connection they govern (ADR 0022/0021):
GET/POST /api/connections/{cid}/pairing, DELETE …/pairing/{key} and POST …/pairing/code
to mint the one-time code. A grant is to one Connection and to no other.
"""

from fastapi.testclient import TestClient

from assistant import pairing as pairing_mod
from assistant.connections import ConnectionStore
from assistant.gateway.app import create_app
from assistant.pairing import PairingStore
from tests.support.apps import make_manager, no_loopback_code_reader

# Every platform's token, so each has one migrated Connection to pair against.
SEEDED_ENV = {
    "TELEGRAM_BOT_TOKEN": "tok",
    "DISCORD_BOT_TOKEN": "tok",
    "SLACK_BOT_TOKEN": "tok",
    "SLACK_APP_TOKEN": "tok",
}


def _client(paths) -> TestClient:
    """An install with every platform's token seeded, so each has one Connection to
    pair against."""
    manager = make_manager(paths, env=SEEDED_ENV)
    return TestClient(create_app(manager, code_reader=no_loopback_code_reader))


def _pairing(client, paths, platform: str = "telegram") -> dict:
    return client.get(f"/api/connections/{_cid(paths, platform)}/pairing").json()


def _pair(client, paths, value: str, platform: str = "telegram"):
    return client.post(f"/api/connections/{_cid(paths, platform)}/pairing", json={"value": value})


def _cid(paths, platform: str = "telegram") -> str:
    return ConnectionStore(paths).connections_for(platform)[0].id


# --- listing ---


def test_a_fresh_channel_has_nobody_paired(paths):
    with _client(paths) as client:
        assert _pairing(client, paths) == {"accounts": [], "code": None}


def test_an_unknown_connection_is_a_404(paths):
    with _client(paths) as client:
        assert client.get("/api/connections/cn-nope/pairing").status_code == 404
        r = client.post("/api/connections/cn-nope/pairing", json={"value": "42"})
        assert r.status_code == 404


# --- adding an account ---


def test_a_numeric_id_is_listed_as_pinned(paths):
    with _client(paths) as client:
        _pair(client, paths, "42")
        assert _pairing(client, paths)["accounts"] == [
            {"key": "42", "account_id": "42", "handle": None, "pending": False}
        ]


def test_a_handle_is_listed_distinctly_as_pending(paths):
    with _client(paths) as client:
        _pair(client, paths, "@nikita")
        assert _pairing(client, paths)["accounts"] == [
            {"key": "@nikita", "account_id": None, "handle": "nikita", "pending": True}
        ]


def test_a_handle_is_refused_where_messages_carry_none(paths):
    """Slack sends only a user id, so the Connection's platform still decides this."""
    with _client(paths) as client:
        r = client.post(
            f"/api/connections/{_cid(paths, 'slack')}/pairing", json={"value": "@nikita"}
        )
        assert r.status_code == 400


def test_adding_an_account_takes_effect_at_once(paths):
    with _client(paths) as client:
        _pair(client, paths, "42")
        assert PairingStore(paths).is_paired(_cid(paths), "42") is True


def test_an_empty_entry_is_rejected(paths):
    with _client(paths) as client:
        assert _pair(client, paths, "  ").status_code == 400


def test_pairing_is_per_channel(paths):
    with _client(paths) as client:
        _pair(client, paths, "42")
        assert _pairing(client, paths, "discord")["accounts"] == []


# --- two Connections of one platform have two rosters ---


def test_an_account_paired_to_one_connection_is_unknown_to_another(paths):
    with _client(paths) as client:
        other = ConnectionStore(paths).create_connection(
            "telegram", "Personal", {"TELEGRAM_BOT_TOKEN": "t2"}
        )
        client.post(f"/api/connections/{_cid(paths)}/pairing", json={"value": "42"})
        assert client.get(f"/api/connections/{other.id}/pairing").json()["accounts"] == []
        assert PairingStore(paths).is_paired(other.id, "42") is False


def test_revoking_from_one_connection_leaves_the_other_paired(paths):
    with _client(paths) as client:
        other = ConnectionStore(paths).create_connection(
            "telegram", "Personal", {"TELEGRAM_BOT_TOKEN": "t2"}
        )
        for cid in (_cid(paths), other.id):
            client.post(f"/api/connections/{cid}/pairing", json={"value": "42"})
        client.delete(f"/api/connections/{_cid(paths)}/pairing/42")
        assert PairingStore(paths).is_paired(other.id, "42") is True


def test_a_code_is_minted_per_connection_and_replaces_only_its_own(paths):
    with _client(paths) as client:
        other = ConnectionStore(paths).create_connection(
            "telegram", "Personal", {"TELEGRAM_BOT_TOKEN": "t2"}
        )
        theirs = client.post(f"/api/connections/{other.id}/pairing/code").json()["code"]
        client.post(f"/api/connections/{_cid(paths)}/pairing/code")
        assert client.get(f"/api/connections/{other.id}/pairing").json()["code"]["code"] == theirs
        assert PairingStore(paths).redeem(_cid(paths), theirs, "42") == pairing_mod.UNKNOWN


# --- revoking ---


def test_revoking_removes_the_account_immediately(paths):
    with _client(paths) as client:
        _pair(client, paths, "42")
        assert client.delete(f"/api/connections/{_cid(paths)}/pairing/42").status_code == 200
        assert _pairing(client, paths)["accounts"] == []
        assert PairingStore(paths).is_paired(_cid(paths), "42") is False


def test_revoking_a_pending_invitation_works_by_its_handle(paths):
    with _client(paths) as client:
        _pair(client, paths, "@nikita")
        client.delete(f"/api/connections/{_cid(paths)}/pairing/@nikita")
        assert _pairing(client, paths)["accounts"] == []


def test_revoking_something_absent_is_a_404(paths):
    with _client(paths) as client:
        assert client.delete(f"/api/connections/{_cid(paths)}/pairing/42").status_code == 404


# --- the one-time code ---


def test_issuing_a_code_returns_it_and_shows_it_until_it_is_used(paths):
    with _client(paths) as client:
        code = client.post(f"/api/connections/{_cid(paths)}/pairing/code").json()["code"]
        assert _pairing(client, paths)["code"]["code"] == code

        PairingStore(paths).redeem(_cid(paths), code, "42")
        assert _pairing(client, paths)["code"] is None
        assert _pairing(client, paths)["accounts"][0]["account_id"] == "42"


def test_the_code_carries_when_it_stops_working(paths):
    with _client(paths) as client:
        issued = client.post(f"/api/connections/{_cid(paths)}/pairing/code").json()
        assert issued["expires_at"] > 0


# --- the Connection's own entry says why it is silent ---


def _entry(client, cid: str) -> dict:
    listed = client.get("/api/connections").json()["connections"]
    return next(c for c in listed if c["id"] == cid)


def test_a_connection_entry_counts_its_own_paired_accounts(paths):
    """A live Connection with nobody paired answers nobody — the count is what lets
    Settings say so, and it counts this Connection's roster alone."""
    with _client(paths) as client:
        other = ConnectionStore(paths).create_connection(
            "telegram", "Personal", {"TELEGRAM_BOT_TOKEN": "t2"}
        )
        assert _entry(client, _cid(paths))["paired_accounts"] == 0

        _pair(client, paths, "42")

        assert _entry(client, _cid(paths))["paired_accounts"] == 1
        assert _entry(client, other.id)["paired_accounts"] == 0
