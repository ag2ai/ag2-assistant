"""Phase-4 routes answer bodies their response models accept.

The 19 routes here — profiles and connections with their exposure, pairing and
group tables — run on the state that stresses their model. Empty first: a fresh
install has no profile archived, no group Peer and no pairing code, and a model
that made any of those fields required would turn a green route into a 500. Then
populated, because the model is the contract, so a key it forgot to declare
disappears from the wire silently rather than failing loudly.

Nothing here touches a network: the manager's channel factory is a fake, so a
Connection "starts" in-process and a token is never presented to a platform.
"""

import pytest
from fastapi.testclient import TestClient

from assistant.connections import ConnectionStore
from assistant.gateway.app import create_app
from assistant.peers import PeerStore
from assistant.profiles import ProfileRegistry
from tests.support.apps import make_manager, make_paths, no_loopback_code_reader

PROFILE_KEYS = {"id", "name", "accent", "workspace", "created"}
CONNECTION_KEYS = {
    "id",
    "platform",
    "name",
    "tokens",
    "default_profile",
    "active",
    "error",
    "paired_accounts",
}

# One platform's token, so the install migrates to exactly one Connection on the
# first read and every {cid} route below has something to address.
SEEDED_ENV = {"TELEGRAM_BOT_TOKEN": "tok"}


def _app(paths, *, env=None):
    return create_app(make_manager(paths, env=env), code_reader=no_loopback_code_reader)


@pytest.fixture
def bare(tmp_path):
    """A fresh install: no profile, no Connection, nothing archived."""
    with TestClient(_app(make_paths(tmp_path))) as client:
        yield client


@pytest.fixture
def seeded(paths):
    """One profile and one migrated Telegram Connection."""
    ProfileRegistry(paths).create_profile("Work", "#109e91")
    with TestClient(_app(paths, env=SEEDED_ENV)) as client:
        yield client


def _cid(client) -> str:
    """This install's one Connection id, read the way the SPA reads it."""
    return client.get("/api/connections").json()["connections"][0]["id"]


# ---- profiles ----


def test_the_profile_list_is_well_shaped_on_a_fresh_install(bare):
    """Both lists empty, no active default, not onboarded — and the two versions
    still present, because a booting client reads them from here and nowhere else."""
    body = bare.get("/api/profiles").json()
    assert body["profiles"] == []
    assert body["archived"] == []
    assert body["active_default"] is None
    assert body["onboarded"] is False
    assert body["version"] and body["ag2_version"]


def test_every_profile_row_declares_the_fields_the_switcher_renders(seeded):
    rows = seeded.get("/api/profiles").json()["profiles"]
    assert [set(row) for row in rows] == [PROFILE_KEYS]
    assert rows[0]["workspace"].endswith("workspace")


def test_an_archived_profile_moves_lists_rather_than_gaining_a_flag(seeded):
    """`archived` is a second list, not a field on the row: the Settings section is
    a different surface, and the row shape must stay identical across both.

    A second profile first — §4.9 refuses to archive the last unarchived one."""
    pid = seeded.post("/api/profiles", json={"name": "Spare", "accent": "#ff0000"}).json()[
        "profile"
    ]["id"]
    assert seeded.delete(f"/api/profiles/{pid}").json() == {"ok": True}
    body = seeded.get("/api/profiles").json()
    assert [row["id"] for row in body["profiles"]] != [pid]
    assert [set(row) for row in body["archived"]] == [PROFILE_KEYS]


def test_creating_renaming_and_restoring_all_answer_the_same_envelope(seeded):
    created = seeded.post("/api/profiles", json={"name": "Home", "accent": "#ff0000"})
    assert created.status_code == 200, created.text
    pid = created.json()["profile"]["id"]
    assert set(created.json()["profile"]) == PROFILE_KEYS

    renamed = seeded.post(f"/api/profiles/{pid}", json={"name": "House"})
    assert set(renamed.json()["profile"]) == PROFILE_KEYS
    assert renamed.json()["profile"]["name"] == "House"

    seeded.delete(f"/api/profiles/{pid}")
    restored = seeded.post(f"/api/profiles/{pid}/restore")
    assert restored.status_code == 200, restored.text
    assert set(restored.json()["profile"]) == PROFILE_KEYS


def test_a_purge_answers_the_bare_acknowledgement(seeded):
    created = seeded.post("/api/profiles", json={"name": "Gone", "accent": "#ff0000"})
    pid = created.json()["profile"]["id"]
    seeded.delete(f"/api/profiles/{pid}")
    assert seeded.delete(f"/api/profiles/{pid}?purge=true").json() == {"ok": True}


# ---- connections ----


def test_the_connection_list_is_empty_on_an_install_with_no_token(bare):
    assert bare.get("/api/connections").json() == {"connections": []}


def test_a_connection_row_declares_every_field_the_settings_card_renders(seeded):
    rows = seeded.get("/api/connections").json()["connections"]
    assert [set(row) for row in rows] == [CONNECTION_KEYS]
    row = rows[0]
    assert set(row["tokens"]["TELEGRAM_BOT_TOKEN"]) == {"set", "hint"}
    assert row["default_profile"] is None
    assert row["paired_accounts"] == 0


def test_a_connection_that_will_not_start_still_answers_200_with_its_reason(paths):
    """`error` is why the adapter is down, so it must survive the model — a card
    reporting active:false without it would look healthy for no stated reason."""

    def _refuses(*_args, **_kwargs):
        raise RuntimeError("bad token")

    manager = make_manager(paths, channel_factory=_refuses)
    with TestClient(create_app(manager, code_reader=no_loopback_code_reader)) as client:
        r = client.post(
            "/api/connections",
            json={"platform": "telegram", "name": "Bot", "tokens": {"TELEGRAM_BOT_TOKEN": "t"}},
        )
    assert r.status_code == 200, r.text
    assert set(r.json()) == CONNECTION_KEYS
    assert r.json()["active"] is False
    assert r.json()["error"]


def test_the_three_connection_writes_answer_with_the_one_row_they_changed(seeded):
    """Create, rename and re-point all answer a bare Connection, not the list — the
    card re-renders itself from the response."""
    cid = _cid(seeded)
    renamed = seeded.post(f"/api/connections/{cid}", json={"name": "Renamed"})
    assert set(renamed.json()) == CONNECTION_KEYS
    assert renamed.json()["name"] == "Renamed"

    pid = seeded.get("/api/profiles").json()["profiles"][0]["id"]
    defaulted = seeded.post(f"/api/connections/{cid}/default", json={"profile": pid})
    assert set(defaulted.json()) == CONNECTION_KEYS
    assert defaulted.json()["default_profile"] == pid

    swapped = seeded.post(
        f"/api/connections/{cid}/token", json={"tokens": {"TELEGRAM_BOT_TOKEN": "t2"}}
    )
    assert swapped.status_code == 200, swapped.text
    assert set(swapped.json()) == CONNECTION_KEYS


def test_deleting_a_connection_answers_the_bare_acknowledgement(seeded):
    assert seeded.delete(f"/api/connections/{_cid(seeded)}").json() == {"ok": True}


# ---- exposure ----


def test_exposure_reports_a_surface_per_kind_and_a_row_per_profile(seeded):
    """Telegram splits dm from group, so both surfaces are listed, and exposure is
    default-allow: the one profile reads true on each without anything recorded."""
    cid = _cid(seeded)
    body = seeded.get(f"/api/connections/{cid}/exposure").json()
    assert [set(s) for s in body["surfaces"]] == [{"kind", "id"}, {"kind", "id"}]
    assert {s["kind"] for s in body["surfaces"]} == {"dm", "group"}
    assert body["default_profile"] is None
    (reach,) = body["exposure"].values()
    assert set(reach.values()) == {True}


def test_withdrawing_a_profile_answers_the_same_table_with_the_change(seeded):
    cid = _cid(seeded)
    pid = seeded.get("/api/profiles").json()["profiles"][0]["id"]
    surface = seeded.get(f"/api/connections/{cid}/exposure").json()["surfaces"][0]["id"]
    r = seeded.post(
        f"/api/connections/{cid}/exposure",
        json={"profile": pid, "surface": surface, "exposed": False},
    )
    assert r.status_code == 200, r.text
    assert r.json()["exposure"][pid][surface] is False


# ---- pairing ----


def test_a_fresh_connection_has_an_empty_roster_and_no_code(seeded):
    assert seeded.get(f"/api/connections/{_cid(seeded)}/pairing").json() == {
        "accounts": [],
        "code": None,
    }


def test_a_paired_id_and_a_pending_handle_share_one_row_shape(seeded):
    """A handle is an invitation with nobody behind it, so `account_id` is null
    there — null, not absent, which is what the zod twin's `.nullable()` demands."""
    cid = _cid(seeded)
    seeded.post(f"/api/connections/{cid}/pairing", json={"value": "42"})
    r = seeded.post(f"/api/connections/{cid}/pairing", json={"value": "@someone"})
    assert r.status_code == 200, r.text
    rows = r.json()["accounts"]
    assert [set(row) for row in rows] == [{"key", "account_id", "handle", "pending"}] * 2
    pinned, pending = rows
    assert pinned["account_id"] == "42" and pinned["pending"] is False
    assert pending["account_id"] is None and pending["pending"] is True


def test_revoking_answers_the_roster_that_is_left(seeded):
    cid = _cid(seeded)
    key = seeded.post(f"/api/connections/{cid}/pairing", json={"value": "42"}).json()["accounts"][
        0
    ]["key"]
    r = seeded.delete(f"/api/connections/{cid}/pairing/{key}")
    assert r.status_code == 200, r.text
    assert r.json() == {"accounts": [], "code": None}


def test_minting_a_code_answers_the_code_alone(seeded):
    """This route alone answers the code rather than the roster around it, and the
    body is nullable — so the model has to be `PairingCodeOut | None`."""
    cid = _cid(seeded)
    r = seeded.post(f"/api/connections/{cid}/pairing/code")
    assert r.status_code == 200, r.text
    assert set(r.json()) == {"code", "expires_at"}
    assert seeded.get(f"/api/connections/{cid}/pairing").json()["code"] == r.json()


# ---- groups ----


def test_a_connection_with_no_group_peer_still_offers_the_profiles(seeded):
    """`profiles` is what a group MAY be pinned to, so it is populated before any
    group exists — the empty state that a required-field mistake would 500 on."""
    body = seeded.get(f"/api/connections/{_cid(seeded)}/groups").json()
    assert body["groups"] == []
    assert [set(p) for p in body["profiles"]] == [{"id", "name"}]


def test_repointing_a_group_answers_the_table_with_the_new_pin(paths, seeded):
    """An unpinned group reads `profile: null` until it is re-pointed (ADR 0022)."""
    cid = _cid(seeded)
    PeerStore(paths).select_profile(cid, "g1", None, platform="telegram", surface="group")
    listed = seeded.get(f"/api/connections/{cid}/groups").json()
    assert listed["groups"] == [{"chat_id": "g1", "profile": None}]

    pid = seeded.get("/api/profiles").json()["profiles"][0]["id"]
    r = seeded.post(f"/api/connections/{cid}/groups/g1/profile", json={"profile": pid})
    assert r.status_code == 200, r.text
    assert r.json()["groups"] == [{"chat_id": "g1", "profile": pid}]


def test_a_group_surface_hides_a_withdrawn_profile_from_the_picker(paths, seeded):
    """The picker's options are the profiles exposed to THIS Connection's group
    surface, which is exactly what the route is allowed to pin."""
    cid = _cid(seeded)
    pid = seeded.get("/api/profiles").json()["profiles"][0]["id"]
    surface = next(
        s["id"]
        for s in seeded.get(f"/api/connections/{cid}/exposure").json()["surfaces"]
        if s["kind"] == "group"
    )
    ConnectionStore(paths, {}).set_exposure(cid, pid, surface, False)
    assert seeded.get(f"/api/connections/{cid}/groups").json()["profiles"] == []
