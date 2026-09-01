"""ACP listeners in Settings: the routes over ProfileManager's boot/
lifecycle surface — list, create-and-start, stop/start/rotate, delete.

A real ``ag2.Agent`` is used, not the plain ``FakeAgent``: starting a listener
wraps the bound profile's agent in ``ACPAgent``, which the fake lacks the surface
for (mirrors tests/test_acp_lifecycle.py).
"""

import socket

import pytest
from ag2 import Agent as Ag2Agent
from ag2.testing import TestConfig
from fastapi.testclient import TestClient

from assistant.gateway.app import create_app
from assistant.profiles import ProfileRegistry
from tests.support.apps import make_manager, make_paths, no_loopback_code_reader
from tests.support.fakes import fake_agent_factory

LISTENER_KEYS = {"id", "name", "profile", "port", "running", "error", "has_token"}


def _free_port() -> int:
    """A loopback port nothing is listening on yet — released before the caller binds it."""
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _pong_factory():
    """A real, ACP-wrappable Agent that always replies "pong"."""
    return fake_agent_factory(
        agent=lambda config, **kwargs: Ag2Agent(name="acp-settings-test", config=TestConfig("pong"))
    )


def _app(paths):
    return create_app(
        make_manager(paths, agent_factory=_pong_factory()), code_reader=no_loopback_code_reader
    )


@pytest.fixture
def bare(tmp_path):
    """A fresh install: no profile, no listener."""
    with TestClient(_app(make_paths(tmp_path))) as client:
        yield client


@pytest.fixture
def profiled(paths):
    """One running profile, ready to bind a listener to."""
    pid = ProfileRegistry(paths).create_profile("Work", "#109e91").id
    with TestClient(_app(paths)) as client:
        yield client, pid


def test_the_listener_list_is_empty_on_a_fresh_install(bare):
    assert bare.get("/api/acp/listeners").json() == {"listeners": []}


def test_creating_starts_it_and_returns_the_token_exactly_once(profiled):
    client, pid = profiled
    port = _free_port()
    r = client.post(
        "/api/acp/listeners", json={"profile": pid, "port": port, "name": "Space · work"}
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert set(body) == {"listener", "token"}
    assert set(body["listener"]) == LISTENER_KEYS
    listener = body["listener"]
    assert listener["profile"] == pid
    assert listener["port"] == port
    assert listener["name"] == "Space · work"
    assert listener["running"] is True
    assert listener["error"] is None
    assert listener["has_token"] is True
    assert body["token"], "a token must be generated when the request omits one"

    # The list view never carries the raw value — only whether one is set.
    rows = client.get("/api/acp/listeners").json()["listeners"]
    assert len(rows) == 1
    assert set(rows[0]) == LISTENER_KEYS
    assert rows[0]["has_token"] is True
    assert "token" not in rows[0]


def test_a_supplied_token_is_kept_and_still_only_answered_once(profiled):
    client, pid = profiled
    port = _free_port()
    r = client.post(
        "/api/acp/listeners", json={"profile": pid, "port": port, "token": "s3cret-value"}
    )
    assert r.json()["token"] == "s3cret-value"
    assert "s3cret-value" not in client.get("/api/acp/listeners").text


def test_an_unknown_profile_is_refused_before_anything_is_written(bare):
    r = bare.post("/api/acp/listeners", json={"profile": "nope", "port": _free_port()})
    assert r.status_code == 400
    assert "unknown profile" in r.json()["error"]
    assert bare.get("/api/acp/listeners").json() == {"listeners": []}


def test_a_taken_port_creates_a_record_that_reads_as_unreachable(profiled):
    """Creation still answers 200 — the honesty pattern channels use for a bad
    token: the record exists, and `error` says why it is not live."""
    client, pid = profiled
    port = _free_port()
    first = client.post("/api/acp/listeners", json={"profile": pid, "port": port}).json()
    assert first["listener"]["running"] is True

    second = client.post(
        "/api/acp/listeners", json={"profile": pid, "port": port, "name": "Second"}
    )
    assert second.status_code == 200, second.text
    listener = second.json()["listener"]
    assert listener["running"] is False
    assert listener["error"] and str(port) in listener["error"]


def test_stop_and_start_flip_the_running_flag(profiled):
    client, pid = profiled
    port = _free_port()
    cid = client.post("/api/acp/listeners", json={"profile": pid, "port": port}).json()["listener"][
        "id"
    ]

    stopped = client.post(f"/api/acp/listeners/{cid}/stop")
    assert stopped.status_code == 200, stopped.text
    assert stopped.json()["running"] is False

    started = client.post(f"/api/acp/listeners/{cid}/start")
    assert started.status_code == 200, started.text
    assert started.json()["running"] is True
    assert started.json()["error"] is None


def test_rotating_the_token_answers_a_new_one_and_restarts(profiled):
    client, pid = profiled
    port = _free_port()
    created = client.post(
        "/api/acp/listeners", json={"profile": pid, "port": port, "token": "old-token"}
    ).json()
    cid = created["listener"]["id"]

    rotated = client.post(f"/api/acp/listeners/{cid}/rotate-token")
    assert rotated.status_code == 200, rotated.text
    body = rotated.json()
    assert set(body) == {"listener", "token"}
    assert body["token"] and body["token"] != "old-token"
    assert body["listener"]["has_token"] is True
    assert body["listener"]["running"] is True
    assert "old-token" not in rotated.text


def test_deleting_stops_it_and_forgets_the_record_and_token(profiled):
    client, pid = profiled
    port = _free_port()
    cid = client.post("/api/acp/listeners", json={"profile": pid, "port": port}).json()["listener"][
        "id"
    ]

    r = client.delete(f"/api/acp/listeners/{cid}")
    assert r.status_code == 200, r.text
    assert r.json() == {"ok": True}
    assert client.get("/api/acp/listeners").json() == {"listeners": []}


def test_unknown_listener_ids_404_on_every_action_route(bare):
    assert bare.delete("/api/acp/listeners/nope").status_code == 404
    assert bare.post("/api/acp/listeners/nope/stop").status_code == 404
    assert bare.post("/api/acp/listeners/nope/start").status_code == 404
    assert bare.post("/api/acp/listeners/nope/rotate-token").status_code == 404
