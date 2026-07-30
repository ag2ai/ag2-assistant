"""WP4 acceptance: the profile-scoped API + profile-management routes.

Every app runs on the isolated ``paths`` layout with faked collaborators, so the
registry, profile dirs, and stores live under disposable space and no runtime
touches an LLM.
"""

import json

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from assistant import AG2_VERSION, __version__
from assistant.gateway.app import create_app
from assistant.profiles import ProfileRegistry
from assistant.secrets import SecretStore
from assistant.usage import _today as t
from tests.support.apps import api, make_manager
from tests.support.fakes import fake_agent_factory


def _app(paths, **kw):
    """A create_app around a fresh (zero-profile) ProfileManager."""

    return create_app(make_manager(paths), **kw)


# --- zero-state contract (§3.5) ---


def test_profiles_zero_state_contract(paths):
    """GET /api/profiles on a fresh install: {[], null, false}; /api/p/* 404s."""
    with TestClient(_app(paths)) as client:
        body = client.get("/api/profiles").json()
        assert body == {
            "profiles": [],
            "archived": [],
            "active_default": None,
            "onboarded": False,
            "version": __version__,
            "ag2_version": AG2_VERSION,
        }
        # health still serves (SPA shell + global routes live with zero profiles)
        assert client.get("/api/health").json()["status"] == "ok"
        # nothing to serve under the profile prefix
        assert client.get(api("anything", "/chats")).status_code == 404


# --- create + serve immediately (§3.5) ---


def test_create_profile_serves_immediately(paths):
    with TestClient(_app(paths)) as client:
        r = client.post("/api/profiles", json={"name": "Work", "accent": "#109e91"})
        assert r.status_code == 200
        pid = r.json()["profile"]["id"]
        assert pid == "work"

        # first profile becomes active_default and shows up in the list
        listed = client.get("/api/profiles").json()
        assert listed["active_default"] == "work"
        assert [p["id"] for p in listed["profiles"]] == ["work"]

        # its runtime is live: a prefixed route works right away
        assert client.get(api(pid, "/chats")).status_code == 200
        assert client.get(api(pid, "/settings")).json()["mcp_servers"] == []


def test_create_profile_invalid_accent_400(paths):
    with TestClient(_app(paths)) as client:
        r = client.post("/api/profiles", json={"name": "X", "accent": "not-a-hex"})
        assert r.status_code == 400
        assert "accent" in r.json()["error"]


# --- unknown / archived status codes on a prefixed route ---


def test_unknown_pid_404_archived_410(paths):
    with TestClient(_app(paths)) as client:
        client.post("/api/profiles", json={"name": "Work", "accent": "#109e91"})
        client.post("/api/profiles", json={"name": "Personal", "accent": "#f95339"})

        assert client.get(api("ghost", "/tasks")).status_code == 404  # never existed
        assert client.get(api("work", "/tasks")).status_code == 200  # live

        # archive work (naming a replacement default), then it 410s
        r = client.request("DELETE", "/api/profiles/work", json={"new_default": "personal"})
        assert r.status_code == 200
        assert client.get(api("work", "/tasks")).status_code == 410


# --- onboarded flag ---


def test_onboarded_endpoint_flips_registry_flag(paths):

    with TestClient(_app(paths)) as client:
        assert client.get("/api/profiles").json()["onboarded"] is False
        assert client.post("/api/onboarded", json={"value": True}).json()["ok"] is True
        assert ProfileRegistry(paths).is_onboarded() is True
        assert client.get("/api/profiles").json()["onboarded"] is True

        # creating a profile after onboarding doesn't reset the flag
        client.post("/api/profiles", json={"name": "Work", "accent": "#109e91"})
        assert client.get("/api/profiles").json()["onboarded"] is True


# --- secrets/key reloads all runtimes ---


def test_secrets_key_reloads_all_runtimes(paths):
    """POST /api/secrets/key reloads every runtime — observed through the rebuilt
    agents (a reload re-asks the agent factory for that profile's config)."""

    built: list = []
    manager = make_manager(paths, agent_factory=fake_agent_factory(built=built))
    with TestClient(create_app(manager)) as client:
        client.post("/api/profiles", json={"name": "Work", "accent": "#109e91"})
        client.post("/api/profiles", json={"name": "Personal", "accent": "#f95339"})

        built.clear()
        assert client.post("/api/secrets/key", json={"provider": "openai", "value": "sk"}).json()[
            "ok"
        ]
        assert {cfg.data_dir.name for cfg in built} == {"work", "personal"}
        # the key really landed in the store, and every runtime now resolves it
        assert SecretStore(paths).status({})["openai"]["set"] is True
        assert {cfg.secret_env.get("OPENAI_API_KEY") for cfg in built} == {"sk"}


# --- workspace is derived under the profile dir (not a user choice) ---


def test_workspace_is_derived_under_profile_dir(paths):

    with TestClient(_app(paths)) as client:
        r = client.post("/api/profiles", json={"name": "Work", "accent": "#109e91"})
        expected = str(ProfileRegistry(paths).profile_dir("work") / "workspace")
        assert r.json()["profile"]["workspace"] == expected

        runtime = client.app.state.profiles.get("work")
        assert str(runtime.config.workspace_dir) == expected

        # A stray `workspace` in the body is ignored — it's not an editable field.
        r = client.post("/api/profiles/work", json={"workspace": "/tmp/nope"})
        assert r.status_code == 200
        assert r.json()["profile"]["workspace"] == expected


def test_name_accent_edit_no_reload(paths):
    """Renames / accent changes are display-only: no runtime reload is triggered
    (a reload would rebuild the profile's agent)."""
    built: list = []
    manager = make_manager(paths, agent_factory=fake_agent_factory(built=built))
    with TestClient(create_app(manager)) as client:
        client.post("/api/profiles", json={"name": "Work", "accent": "#109e91"})
        runtime = client.app.state.profiles.get("work")

        built.clear()
        r = client.post("/api/profiles/work", json={"name": "Job", "accent": "#2f6fe0"})
        assert r.status_code == 200
        assert r.json()["profile"]["name"] == "Job"
        assert r.json()["profile"]["accent"] == "#2f6fe0"
        assert built == []  # display-only → no rebuild, so no reload

    # runtime.meta unaffected here (we only assert reload was skipped)
    assert runtime.pid == "work"


def test_update_unknown_profile_404(paths):
    with TestClient(_app(paths)) as client:
        assert client.post("/api/profiles/ghost", json={"name": "x"}).status_code == 404


# --- archive over HTTP: guardrails, success, then 410 ---


def test_archive_http_guardrails_and_success(paths):
    with TestClient(_app(paths)) as client:
        client.post("/api/profiles", json={"name": "Work", "accent": "#109e91"})

        # cannot archive the last unarchived profile → 400
        r = client.request("DELETE", "/api/profiles/work")
        assert r.status_code == 400

        client.post("/api/profiles", json={"name": "Personal", "accent": "#f95339"})
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


# --- archived list + restore + purge (ADR 0003) ---


def test_archived_list_in_payload(paths):
    """GET /api/profiles carries an `archived` array beside `profiles`."""
    with TestClient(_app(paths)) as client:
        client.post("/api/profiles", json={"name": "Work", "accent": "#109e91"})
        client.post("/api/profiles", json={"name": "Personal", "accent": "#f95339"})
        # archive the non-default → no replacement needed
        assert client.request("DELETE", "/api/profiles/personal").status_code == 200

        body = client.get("/api/profiles").json()
        assert [p["id"] for p in body["profiles"]] == ["work"]  # live only
        assert [p["id"] for p in body["archived"]] == ["personal"]  # archived only


def test_restore_over_http(paths):
    with TestClient(_app(paths)) as client:
        client.post("/api/profiles", json={"name": "Work", "accent": "#109e91"})
        client.post("/api/profiles", json={"name": "Personal", "accent": "#f95339"})
        client.request("DELETE", "/api/profiles/personal")
        assert client.get(api("personal", "/tasks")).status_code == 410  # archived

        r = client.post("/api/profiles/personal/restore")
        assert r.status_code == 200
        assert r.json()["profile"]["id"] == "personal"

        body = client.get("/api/profiles").json()
        assert "personal" in [p["id"] for p in body["profiles"]]  # back in the live list
        assert [p["id"] for p in body["archived"]] == []  # no longer archived
        assert client.get(api("personal", "/tasks")).status_code == 200  # booted live


def test_restore_non_archived_409(paths):
    with TestClient(_app(paths)) as client:
        client.post("/api/profiles", json={"name": "Work", "accent": "#109e91"})
        client.post("/api/profiles", json={"name": "Personal", "accent": "#f95339"})
        # work is live, not archived → restore is a conflict
        assert client.post("/api/profiles/work/restore").status_code == 409


def test_restore_unknown_404(paths):
    with TestClient(_app(paths)) as client:
        assert client.post("/api/profiles/ghost/restore").status_code == 404


def test_purge_requires_archive_first_409(paths):
    """Archive-first: a live profile cannot be hard-deleted (409), and it is untouched."""

    with TestClient(_app(paths)) as client:
        client.post("/api/profiles", json={"name": "Work", "accent": "#109e91"})
        client.post("/api/profiles", json={"name": "Personal", "accent": "#f95339"})

        r = client.request("DELETE", "/api/profiles/personal", params={"purge": "true"})
        assert r.status_code == 409
        assert ProfileRegistry(paths).get_profile("personal") is not None  # still there
        assert ProfileRegistry(paths).profile_dir("personal").exists()  # dir intact


def test_purge_archived_profile(paths):

    with TestClient(_app(paths)) as client:
        client.post("/api/profiles", json={"name": "Work", "accent": "#109e91"})
        client.post("/api/profiles", json={"name": "Personal", "accent": "#f95339"})
        client.request("DELETE", "/api/profiles/personal")  # archive first
        assert ProfileRegistry(paths).profile_dir("personal").exists()

        r = client.request("DELETE", "/api/profiles/personal", params={"purge": "true"})
        assert r.status_code == 200
        assert ProfileRegistry(paths).get_profile("personal") is None  # gone from registry
        assert not ProfileRegistry(paths).profile_dir("personal").exists()  # folder erased
        # gone from both lists
        body = client.get("/api/profiles").json()
        assert "personal" not in [p["id"] for p in body["profiles"] + body["archived"]]


def test_purge_unknown_404(paths):
    with TestClient(_app(paths)) as client:
        client.post("/api/profiles", json={"name": "Work", "accent": "#109e91"})
        r = client.request("DELETE", "/api/profiles/ghost", params={"purge": "true"})
        assert r.status_code == 404


# --- WS close on archive (§4.9) ---


def test_stream_ws_closed_4001_on_archive(paths):
    """An open /stream socket is closed with code 4001 when its profile is archived."""

    with TestClient(_app(paths)) as client:
        client.post("/api/profiles", json={"name": "Work", "accent": "#109e91"})
        client.post("/api/profiles", json={"name": "Personal", "accent": "#f95339"})

        with client.websocket_connect(api("work", "/stream?chat=w1")) as ws:
            assert ws.receive_json()["type"] == "ready"
            # archive work from under the open socket
            client.request("DELETE", "/api/profiles/work", json={"new_default": "personal"})
            with pytest.raises(WebSocketDisconnect) as exc:
                # the next receive observes the server-side close
                while True:
                    ws.receive_json()
            assert exc.value.code == 4001


# --- /api/status aggregate ---


def test_status_aggregate_shape(paths):
    with TestClient(_app(paths)) as client:
        client.post("/api/profiles", json={"name": "Work", "accent": "#109e91"})
        client.post("/api/profiles", json={"name": "Personal", "accent": "#f95339"})
        rows = client.get("/api/status").json()
        assert {r["pid"] for r in rows} == {"work", "personal"}
        for r in rows:
            assert set(r) == {"pid", "busy", "running_tasks", "unseen_done"}
            assert r["busy"] is True
            assert r["running_tasks"] == 0
            assert r["unseen_done"] == 0


# --- /api/usage install-wide roll-up ---


def _seed_usage(client, paths, pid: str, day: str, entry: dict) -> None:
    """Write a profile's usage.json directly on disk (UsageLedger's file schema:
    {day: {prompt, completion, total, cost, priced, by_model}}) and reload the live
    ledger from it, so GET /api/usage sees the seeded totals."""

    path = paths.profile_dir(pid) / "usage.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({day: entry}))
    client.app.state.profiles.get(pid).gateway._usage._load()


def _today() -> str:

    return t()


def test_usage_rollup_sums_two_profiles(paths):
    """Two profiles with seeded usage.json → GET /api/usage sums the numeric fields,
    carries per-profile pid/name, and priced is true when all contributors are priced."""
    with TestClient(_app(paths)) as client:
        client.post("/api/profiles", json={"name": "Work", "accent": "#109e91"})
        client.post("/api/profiles", json={"name": "Personal", "accent": "#f95339"})
        day = _today()
        _seed_usage(
            client,
            paths,
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
            paths,
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


def test_usage_rollup_unpriced_makes_total_unpriced(paths):
    """An unpriced profile makes the summed cost an underestimate → total.priced False,
    while the cost still sums the priced contributions."""
    with TestClient(_app(paths)) as client:
        client.post("/api/profiles", json={"name": "Work", "accent": "#109e91"})
        client.post("/api/profiles", json={"name": "Personal", "accent": "#f95339"})
        day = _today()
        _seed_usage(
            client,
            paths,
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
            paths,
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


def test_usage_rollup_zero_profiles(paths):
    """Fresh install: empty list + zeroed, unpriced total."""
    with TestClient(_app(paths)) as client:
        body = client.get("/api/usage").json()
        assert body["profiles"] == []
        assert body["total"] == {
            "prompt": 0.0,
            "completion": 0.0,
            "total": 0.0,
            "cost": 0.0,
            "priced": False,
        }


def test_usage_rollup_single_profile(paths):
    """One profile: its numbers are present; total mirrors it and is priced iff it is."""
    with TestClient(_app(paths)) as client:
        client.post("/api/profiles", json={"name": "Work", "accent": "#109e91"})
        day = _today()
        _seed_usage(
            client,
            paths,
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


def test_boot_payload_reports_the_running_ag2_version(paths):
    """The "Powered by" dialog shows which AG2 the app is actually running on, so the
    boot payload must carry a real version read from installed metadata — not a
    hardcoded string that can drift from the dependency."""
    from importlib.metadata import version

    with TestClient(_app(paths)) as client:
        assert client.get("/api/profiles").json()["ag2_version"] == version("ag2")
