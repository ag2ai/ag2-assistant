"""WP4 acceptance: the profile-scoped API + profile-management routes.

The autouse conftest fixture points HOME at a tmp dir, so the registry, profile
dirs, and stores resolve under disposable space. Agents are faked (no LLM).
"""

import json

import pytest
from fastapi.testclient import TestClient

from tests.conftest import api, use_fake_agent


def _app(monkeypatch, **kw):
    """A create_app around a fresh (zero-profile) ProfileManager."""
    from assistant.gateway.app import create_app
    from assistant.gateway.profile_manager import ProfileManager

    use_fake_agent(monkeypatch)
    return create_app(ProfileManager(memory=False, persist=False), **kw)


# --- zero-state contract (§3.5) ---


def test_profiles_zero_state_contract(monkeypatch):
    """GET /api/profiles on a fresh install: {[], null, false}; /api/p/* 404s."""
    with TestClient(_app(monkeypatch)) as client:
        body = client.get("/api/profiles").json()
        assert body == {"profiles": [], "active_default": None, "onboarded": False}
        # health still serves (SPA shell + global routes live with zero profiles)
        assert client.get("/api/health").json()["status"] == "ok"
        # nothing to serve under the profile prefix
        assert client.get(api("anything", "/sessions")).status_code == 404


# --- create + serve immediately (§3.5) ---


def test_create_profile_serves_immediately(monkeypatch):
    with TestClient(_app(monkeypatch)) as client:
        r = client.post("/api/profiles", json={"name": "Work", "palette": "teal"})
        assert r.status_code == 200
        pid = r.json()["profile"]["id"]
        assert pid == "work"

        # first profile becomes active_default and shows up in the list
        listed = client.get("/api/profiles").json()
        assert listed["active_default"] == "work"
        assert [p["id"] for p in listed["profiles"]] == ["work"]

        # its runtime is live: a prefixed route works right away
        assert client.get(api(pid, "/sessions")).status_code == 200
        assert client.get(api(pid, "/settings")).json()["mcp_servers"] == []


def test_create_profile_invalid_palette_400(monkeypatch):
    with TestClient(_app(monkeypatch)) as client:
        r = client.post("/api/profiles", json={"name": "X", "palette": "not-a-palette"})
        assert r.status_code == 400
        assert "palette" in r.json()["error"]


# --- unknown / archived status codes on a prefixed route ---


def test_unknown_pid_404_archived_410(monkeypatch):
    with TestClient(_app(monkeypatch)) as client:
        client.post("/api/profiles", json={"name": "Work", "palette": "teal"})
        client.post("/api/profiles", json={"name": "Personal", "palette": "coral"})

        assert client.get(api("ghost", "/tasks")).status_code == 404  # never existed
        assert client.get(api("work", "/tasks")).status_code == 200  # live

        # archive work (naming a replacement default), then it 410s
        r = client.request("DELETE", "/api/profiles/work", json={"new_default": "personal"})
        assert r.status_code == 200
        assert client.get(api("work", "/tasks")).status_code == 410


# --- onboarded flag ---


def test_onboarded_endpoint_flips_registry_flag(monkeypatch):
    from assistant import profiles

    with TestClient(_app(monkeypatch)) as client:
        assert client.get("/api/profiles").json()["onboarded"] is False
        assert client.post("/api/onboarded", json={"value": True}).json()["ok"] is True
        assert profiles.is_onboarded() is True
        assert client.get("/api/profiles").json()["onboarded"] is True

        # creating a profile after onboarding doesn't reset the flag
        client.post("/api/profiles", json={"name": "Work", "palette": "teal"})
        assert client.get("/api/profiles").json()["onboarded"] is True


# --- secrets/key reloads all runtimes ---


def test_secrets_key_reloads_all_runtimes(monkeypatch):
    """POST /api/secrets/key calls manager.reload on every runtime (observed via a spy)."""
    import assistant.secrets as secrets_mod
    from assistant.gateway.app import create_app
    from assistant.gateway.profile_manager import ProfileManager

    use_fake_agent(monkeypatch)
    monkeypatch.setattr(secrets_mod, "set_key", lambda provider, value: True)

    manager = ProfileManager(memory=False, persist=False)
    app = create_app(manager)
    with TestClient(app) as client:
        client.post("/api/profiles", json={"name": "Work", "palette": "teal"})
        client.post("/api/profiles", json={"name": "Personal", "palette": "coral"})

        reloaded: list[str] = []
        orig = manager.reload

        async def spy(pid):
            reloaded.append(pid)
            return await orig(pid)

        monkeypatch.setattr(manager, "reload", spy)

        assert client.post("/api/secrets/key", json={"provider": "openai", "value": "sk"}).json()[
            "ok"
        ]
        assert set(reloaded) == {"work", "personal"}  # every runtime reloaded


# --- workspace edit reloads the runtime (config picks up new workspace) ---


def test_workspace_edit_reloads_runtime(monkeypatch, tmp_path):
    with TestClient(_app(monkeypatch)) as client:
        ws1 = tmp_path / "ws-one"
        ws2 = tmp_path / "ws-two"
        client.post(
            "/api/profiles",
            json={"name": "Work", "palette": "teal", "workspace": str(ws1)},
        )
        runtime = client.app.state.profiles.get("work")
        assert str(runtime.config.workspace_dir) == str(ws1)

        r = client.post("/api/profiles/work", json={"workspace": str(ws2)})
        assert r.status_code == 200
        assert r.json()["profile"]["workspace"] == str(ws2)
        # reference-swap reload re-resolved the config against the new registry entry
        assert str(runtime.config.workspace_dir) == str(ws2)


def test_name_palette_edit_no_reload(monkeypatch):
    """Renames / palette changes are display-only: no runtime reload is triggered."""
    with TestClient(_app(monkeypatch)) as client:
        client.post("/api/profiles", json={"name": "Work", "palette": "teal"})
        runtime = client.app.state.profiles.get("work")

        reloaded: list[str] = []
        monkeypatch.setattr(
            client.app.state.profiles,
            "reload",
            lambda pid: reloaded.append(pid),
        )

        r = client.post("/api/profiles/work", json={"name": "Job", "palette": "ocean"})
        assert r.status_code == 200
        assert r.json()["profile"]["name"] == "Job"
        assert r.json()["profile"]["palette"] == "ocean"
        assert reloaded == []  # display-only → no reload

    # runtime.meta unaffected here (we only assert reload was skipped)
    assert runtime.pid == "work"


def test_update_unknown_profile_404(monkeypatch):
    with TestClient(_app(monkeypatch)) as client:
        assert client.post("/api/profiles/ghost", json={"name": "x"}).status_code == 404


# --- archive over HTTP: guardrails, success, then 410 ---


def test_archive_http_guardrails_and_success(monkeypatch):
    with TestClient(_app(monkeypatch)) as client:
        client.post("/api/profiles", json={"name": "Work", "palette": "teal"})

        # cannot archive the last unarchived profile → 400
        r = client.request("DELETE", "/api/profiles/work")
        assert r.status_code == 400

        client.post("/api/profiles", json={"name": "Personal", "palette": "coral"})
        # archiving the active default without a replacement → 400
        r = client.request("DELETE", "/api/profiles/work")
        assert r.status_code == 400

        # unknown profile → 404
        assert client.request("DELETE", "/api/profiles/ghost").status_code == 404

        # success with a replacement default
        r = client.request("DELETE", "/api/profiles/work", json={"new_default": "personal"})
        assert r.status_code == 200
        assert client.get("/api/profiles").json()["active_default"] == "personal"
        assert [p["id"] for p in client.get("/api/profiles").json()["profiles"]] == ["personal"]

        # routes on the archived profile now 410; archiving it again → 410
        assert client.get(api("work", "/tasks")).status_code == 410
        assert client.request("DELETE", "/api/profiles/work").status_code == 410


# --- WS close on archive (§4.9) ---


def test_stream_ws_closed_4001_on_archive(monkeypatch):
    """An open /stream socket is closed with code 4001 when its profile is archived."""
    from starlette.websockets import WebSocketDisconnect

    with TestClient(_app(monkeypatch)) as client:
        client.post("/api/profiles", json={"name": "Work", "palette": "teal"})
        client.post("/api/profiles", json={"name": "Personal", "palette": "coral"})

        with client.websocket_connect(api("work", "/stream?session=w1")) as ws:
            assert ws.receive_json()["type"] == "ready"
            # archive work from under the open socket
            client.request("DELETE", "/api/profiles/work", json={"new_default": "personal"})
            with pytest.raises(WebSocketDisconnect) as exc:
                # the next receive observes the server-side close
                while True:
                    ws.receive_json()
            assert exc.value.code == 4001


# --- /api/status aggregate ---


def test_status_aggregate_shape(monkeypatch):
    with TestClient(_app(monkeypatch)) as client:
        client.post("/api/profiles", json={"name": "Work", "palette": "teal"})
        client.post("/api/profiles", json={"name": "Personal", "palette": "coral"})
        rows = client.get("/api/status").json()
        assert {r["pid"] for r in rows} == {"work", "personal"}
        for r in rows:
            assert set(r) == {"pid", "busy", "running_tasks"}
            assert r["busy"] is True
            assert r["running_tasks"] == 0


# --- /api/usage install-wide roll-up ---


def _seed_usage(client, pid: str, day: str, entry: dict) -> None:
    """Write a profile's usage.json directly on disk (UsageLedger's file schema:
    {day: {prompt, completion, total, cost, priced, by_model}}) and reload the live
    ledger from it, so GET /api/usage sees the seeded totals."""
    from assistant import profiles

    path = profiles.profile_dir(pid) / "usage.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({day: entry}))
    client.app.state.profiles.get(pid).gateway._usage._load()


def _today() -> str:
    from assistant.usage import _today as t

    return t()


def test_usage_rollup_sums_two_profiles(monkeypatch):
    """Two profiles with seeded usage.json → GET /api/usage sums the numeric fields,
    carries per-profile pid/name, and priced is true when all contributors are priced."""
    with TestClient(_app(monkeypatch)) as client:
        client.post("/api/profiles", json={"name": "Work", "palette": "teal"})
        client.post("/api/profiles", json={"name": "Personal", "palette": "coral"})
        day = _today()
        _seed_usage(
            client,
            "work",
            day,
            {
                "prompt": 100.0,
                "completion": 50.0,
                "total": 150.0,
                "cost": 0.01,
                "priced": True,
                "by_model": {"gemini-3.5-flash": {}},
            },
        )
        _seed_usage(
            client,
            "personal",
            day,
            {
                "prompt": 200.0,
                "completion": 100.0,
                "total": 300.0,
                "cost": 0.02,
                "priced": True,
                "by_model": {"gpt-5": {}},
            },
        )

        body = client.get("/api/usage").json()
        assert {p["pid"] for p in body["profiles"]} == {"work", "personal"}
        assert {p["name"] for p in body["profiles"]} == {"Work", "Personal"}
        # each per-profile row carries its own usage_today() snapshot
        work = next(p for p in body["profiles"] if p["pid"] == "work")
        assert work["total"] == 150.0 and work["date"] == day

        total = body["total"]
        assert total["prompt"] == 300.0
        assert total["completion"] == 150.0
        assert total["total"] == 450.0
        assert abs(total["cost"] - 0.03) < 1e-9
        assert total["priced"] is True  # every contributor priced


def test_usage_rollup_unpriced_makes_total_unpriced(monkeypatch):
    """An unpriced profile makes the summed cost an underestimate → total.priced False,
    while the cost still sums the priced contributions."""
    with TestClient(_app(monkeypatch)) as client:
        client.post("/api/profiles", json={"name": "Work", "palette": "teal"})
        client.post("/api/profiles", json={"name": "Personal", "palette": "coral"})
        day = _today()
        _seed_usage(
            client,
            "work",
            day,
            {
                "prompt": 100.0,
                "completion": 50.0,
                "total": 150.0,
                "cost": 0.01,
                "priced": True,
                "by_model": {},
            },
        )
        _seed_usage(
            client,
            "personal",
            day,
            {
                "prompt": 10.0,
                "completion": 5.0,
                "total": 15.0,
                "cost": 0.0,
                "priced": False,
                "by_model": {},
            },
        )

        total = client.get("/api/usage").json()["total"]
        assert total["total"] == 165.0
        assert abs(total["cost"] - 0.01) < 1e-9  # priced contributions still summed
        assert total["priced"] is False  # one unpriced → underestimate flagged


def test_usage_rollup_zero_profiles(monkeypatch):
    """Fresh install: empty list + zeroed, unpriced total."""
    with TestClient(_app(monkeypatch)) as client:
        body = client.get("/api/usage").json()
        assert body["profiles"] == []
        assert body["total"] == {
            "prompt": 0.0,
            "completion": 0.0,
            "total": 0.0,
            "cost": 0.0,
            "priced": False,
        }


def test_usage_rollup_single_profile(monkeypatch):
    """One profile: its numbers are present; total mirrors it and is priced iff it is."""
    with TestClient(_app(monkeypatch)) as client:
        client.post("/api/profiles", json={"name": "Work", "palette": "teal"})
        day = _today()
        _seed_usage(
            client,
            "work",
            day,
            {
                "prompt": 100.0,
                "completion": 50.0,
                "total": 150.0,
                "cost": 0.01,
                "priced": True,
                "by_model": {},
            },
        )
        body = client.get("/api/usage").json()
        assert [p["pid"] for p in body["profiles"]] == ["work"]
        assert body["total"]["total"] == 150.0
        assert body["total"]["priced"] is True
