"""Paired accounts through the API — the Settings surface for who a Channel answers.

Routes are install-level, like the Channel they govern (ADR 0019/0021):
GET/POST /api/channels/{platform}/pairing, DELETE …/pairing/{key}, and
POST …/pairing/code to mint the one-time code.
"""

from fastapi.testclient import TestClient

from assistant import pairing
from assistant.gateway.app import create_app
from assistant.gateway.profile_manager import ProfileManager
from tests.conftest import use_fake_agent


def _client(monkeypatch) -> TestClient:
    use_fake_agent(monkeypatch)
    return TestClient(create_app(ProfileManager(memory=False, persist=False)))


def _pairing(client, platform: str = "telegram") -> dict:
    return client.get(f"/api/channels/{platform}/pairing").json()


# --- listing ---


def test_a_fresh_channel_has_nobody_paired(monkeypatch):
    with _client(monkeypatch) as client:
        assert _pairing(client) == {"accounts": [], "code": None}


def test_an_unknown_platform_is_rejected(monkeypatch):
    with _client(monkeypatch) as client:
        assert client.get("/api/channels/carrier-pigeon/pairing").status_code == 400


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


def test_adding_an_account_takes_effect_at_once(monkeypatch):
    with _client(monkeypatch) as client:
        client.post("/api/channels/telegram/pairing", json={"value": "42"})
        assert pairing.is_paired("telegram", "42") is True


def test_an_empty_entry_is_rejected(monkeypatch):
    with _client(monkeypatch) as client:
        assert (
            client.post("/api/channels/telegram/pairing", json={"value": "  "}).status_code == 400
        )


def test_pairing_is_per_channel(monkeypatch):
    with _client(monkeypatch) as client:
        client.post("/api/channels/telegram/pairing", json={"value": "42"})
        assert _pairing(client, "discord")["accounts"] == []


# --- revoking ---


def test_revoking_removes_the_account_immediately(monkeypatch):
    with _client(monkeypatch) as client:
        client.post("/api/channels/telegram/pairing", json={"value": "42"})
        assert client.delete("/api/channels/telegram/pairing/42").status_code == 200
        assert _pairing(client)["accounts"] == []
        assert pairing.is_paired("telegram", "42") is False


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

        pairing.redeem("telegram", code, "42")
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
