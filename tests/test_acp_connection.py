"""ACP listener Connections: a listener is fixed to exactly one Profile at
creation, recorded via ``ConnectionStore``'s ACP methods, and never resolved through
exposure/default-profile (ADR 0022 does not apply to this platform — see
ADR 0031).
"""

import pytest

from assistant.connections import ConnectionStore
from assistant.profiles import ProfileRegistry


def _profile(paths, name: str) -> str:
    return ProfileRegistry(paths).create_profile(name, "#336699").id


def test_create_acp_connection_round_trips_through_connection_store(paths):
    pid = _profile(paths, "Work")
    store = ConnectionStore(paths)

    created = store.create_acp_connection(pid, name="Laptop bridge", port=8802, token="s3cr3t")
    assert created.connection.platform == "acp"
    assert created.connection.name == "Laptop bridge"
    assert created.profile == pid
    assert created.port == 8802

    # A fresh ConnectionStore instance reads the same state back from disk.
    reloaded = ConnectionStore(paths).get_acp_connection(created.connection.id)
    assert reloaded is not None
    assert reloaded.connection.id == created.connection.id
    assert reloaded.connection.platform == "acp"
    assert reloaded.connection.name == "Laptop bridge"
    assert reloaded.profile == pid
    assert reloaded.port == 8802
    assert ConnectionStore(paths).acp_token_for(created.connection.id) == "s3cr3t"


def test_stdio_listener_has_no_port(paths):
    pid = _profile(paths, "Work")
    listener = ConnectionStore(paths).create_acp_connection(pid)
    assert listener.port is None


def test_default_name_numbers_unnamed_listeners(paths):
    pid = _profile(paths, "Work")
    store = ConnectionStore(paths)
    first = store.create_acp_connection(pid)
    second = store.create_acp_connection(pid)
    assert first.connection.name == "ACP"
    assert second.connection.name == "ACP 2"


def test_create_acp_connection_rejects_unknown_profile(paths):
    with pytest.raises(ValueError):
        ConnectionStore(paths).create_acp_connection("no-such-profile")


def test_create_acp_connection_rejects_archived_profile(paths):
    registry = ProfileRegistry(paths)
    pid = registry.create_profile("Old", "#336699").id
    registry.archive_profile(pid)
    with pytest.raises(ValueError):
        ConnectionStore(paths).create_acp_connection(pid)


def test_delete_acp_connection_forgets_it_and_its_token(paths):
    pid = _profile(paths, "Work")
    store = ConnectionStore(paths)
    listener = store.create_acp_connection(pid, token="s3cr3t")

    store.delete_acp_connection(listener.connection.id)

    assert store.get_acp_connection(listener.connection.id) is None
    assert store.acp_token_for(listener.connection.id) == ""


def test_delete_acp_connection_unknown_id_raises(paths):
    with pytest.raises(ValueError):
        ConnectionStore(paths).delete_acp_connection("acp_missing")


def test_acp_listeners_are_absent_from_the_messaging_connection_list(paths):
    """An ACP listener must never surface through ``list_connections``/
    ``get_connection`` — that is the list ``Gateway.start()`` boots as messaging
    channels, and ACP has no bot token for that loop to find."""
    pid = _profile(paths, "Work")
    store = ConnectionStore(paths)
    listener = store.create_acp_connection(pid)

    assert store.list_connections() == []
    assert store.get_connection(listener.connection.id) is None


def test_exposure_and_default_profile_do_not_apply_to_an_acp_listener(paths):
    """Exposure/default-profile (ADR 0022) are Connection-keyed mechanisms that read
    ``connections.json`` — an ACP listener id is unknown to them by construction,
    which keeps this platform structurally out of that machinery (ADR 0031)."""
    pid = _profile(paths, "Work")
    store = ConnectionStore(paths)
    listener = store.create_acp_connection(pid)
    cid = listener.connection.id

    with pytest.raises(ValueError):
        store.exposure(cid)
    with pytest.raises(ValueError):
        store.reachable(cid, pid)
    with pytest.raises(ValueError):
        store.set_default_profile(cid, pid)


# ---- isolation: two listeners on two profiles cannot reach each other's binding ----


def test_two_listeners_resolve_to_their_own_distinct_profile(paths):
    store = ConnectionStore(paths)
    pid_a = _profile(paths, "Alpha")
    pid_b = _profile(paths, "Bravo")

    listener_a = store.create_acp_connection(pid_a, name="Alpha listener")
    listener_b = store.create_acp_connection(pid_b, name="Bravo listener")

    assert listener_a.connection.id != listener_b.connection.id
    assert store.acp_profile_for(listener_a.connection.id) == pid_a
    assert store.acp_profile_for(listener_b.connection.id) == pid_b
    # Neither binding can be read as the other's profile.
    assert store.acp_profile_for(listener_a.connection.id) != pid_b
    assert store.acp_profile_for(listener_b.connection.id) != pid_a


def test_two_listeners_do_not_share_tokens(paths):
    store = ConnectionStore(paths)
    pid_a = _profile(paths, "Alpha")
    pid_b = _profile(paths, "Bravo")

    listener_a = store.create_acp_connection(pid_a, token="alpha-secret")
    listener_b = store.create_acp_connection(pid_b, token="bravo-secret")

    assert store.acp_token_for(listener_a.connection.id) == "alpha-secret"
    assert store.acp_token_for(listener_b.connection.id) == "bravo-secret"
    assert store.acp_token_for(listener_a.connection.id) != store.acp_token_for(
        listener_b.connection.id
    )


def test_deleting_one_listener_leaves_the_other_bound(paths):
    store = ConnectionStore(paths)
    pid_a = _profile(paths, "Alpha")
    pid_b = _profile(paths, "Bravo")
    listener_a = store.create_acp_connection(pid_a)
    listener_b = store.create_acp_connection(pid_b)

    store.delete_acp_connection(listener_a.connection.id)

    assert store.get_acp_connection(listener_a.connection.id) is None
    still_there = store.get_acp_connection(listener_b.connection.id)
    assert still_there is not None
    assert still_there.profile == pid_b
