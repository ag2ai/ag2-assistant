"""Phase-1 routes answer bodies their response models accept.

Each route runs twice where the state matters: once on a bare install and once
with a profile present. The bare pass is the one that matters — it exposes fields
that are only sometimes sent, which a model must declare with a default or turn
into a 500.
"""

from fastapi.testclient import TestClient

from assistant.gateway.app import create_app
from tests.support.apps import make_manager, make_paths


def test_health_on_a_zero_profile_install(tmp_path):
    """The fresh-install stub is {status, profiles} — a different shape from the
    running one, so every field but `status` has to be optional."""
    app = create_app(make_manager(make_paths(tmp_path)))
    with TestClient(app) as client:
        r = client.get("/api/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"
    assert r.json()["profiles"] == 0


def test_attaching_a_model_adds_no_null_keys_to_the_wire(tmp_path):
    """A defaulted field must not turn into an explicit null on the wire.

    Without response_model_exclude_unset the zero-profile stub would grow
    model/memory/platform/chats as nulls, and zod declares those `.optional()`,
    which rejects null — the front end would throw. The key SET is the assertion.
    """
    app = create_app(make_manager(make_paths(tmp_path)))
    with TestClient(app) as client:
        assert set(client.get("/api/health").json()) == {"status", "profiles"}
        agents = client.get("/api/coding/agents").json()
    assert set(agents) == {"mode", "bridge", "connected", "agents"}
    # `bridge` is explicitly None, not merely unset, so it must survive the filter.
    assert agents["bridge"] is None


def test_usage_rollup_on_a_zero_profile_install(tmp_path):
    app = create_app(make_manager(make_paths(tmp_path)))
    with TestClient(app) as client:
        r = client.get("/api/usage")
    assert r.status_code == 200
    body = r.json()
    assert body["profiles"] == []
    assert body["total"] == {
        "prompt": 0.0,
        "completion": 0.0,
        "total": 0.0,
        "cost": 0.0,
        "priced": False,
    }


def test_usage_rollup_with_a_profile(profile_app):
    client, pid = profile_app
    body = client.get("/api/usage").json()
    assert [row["pid"] for row in body["profiles"]] == [pid]
    assert "by_model" in body["profiles"][0]
    assert "date" in body["profiles"][0]
    # The install-wide sum carries neither a date nor a by_model breakdown.
    assert "date" not in body["total"]


def test_status_is_a_bare_array(profile_app):
    client, pid = profile_app
    body = client.get("/api/status").json()
    assert isinstance(body, list)
    assert body[0]["pid"] == pid
    assert set(body[0]) == {"pid", "busy", "running_tasks", "unseen_done"}


def test_profile_usage(profile_app):
    client, pid = profile_app
    body = client.get(f"/api/p/{pid}/usage").json()
    assert {"prompt", "completion", "total", "cost", "priced", "date", "by_model"} <= set(body)


def test_coding_agents_local_mode(tmp_path):
    """Without AG2ASSISTANT_ACP_BRIDGE the answer is mode=local, bridge=null and
    no `error` key at all — so `error` must be optional."""
    app = create_app(make_manager(make_paths(tmp_path)))
    with TestClient(app) as client:
        r = client.get("/api/coding/agents")
    assert r.status_code == 200
    body = r.json()
    assert body["mode"] == "local"
    assert body["bridge"] is None
    assert body["connected"] is True
    assert "error" not in body
    for agent in body["agents"]:
        assert {"name", "label", "available"} == set(agent)


def test_coding_models_declares_a_schema_despite_a_custom_response(tmp_path):
    """The handler returns a Response it built itself (cache headers), so the
    model rides an explicit response_model= while the annotation stays Response."""
    from assistant.gateway.openapi_schema import build_schema

    schema = build_schema()["paths"]["/api/coding/{agent}/models"]["get"]
    ref = schema["responses"]["200"]["content"]["application/json"]["schema"]["$ref"]
    assert ref.endswith("/CodingCatalogResponse")


def test_fs_list_success_branch(tmp_path):
    app = create_app(make_manager(make_paths(tmp_path)))
    (tmp_path / "visible").mkdir()
    with TestClient(app) as client:
        body = client.get("/api/fs/list", params={"path": str(tmp_path)}).json()
    assert body["ok"] is True
    assert body["path"] == str(tmp_path)
    assert "parent" in body
    assert {"visible"} <= {d["name"] for d in body["dirs"]}


def test_fs_list_error_branch_keeps_its_own_shape(tmp_path):
    """An unreadable directory answers 200 with ok:false — the union must not
    coerce this into the success branch."""
    app = create_app(make_manager(make_paths(tmp_path)))
    with TestClient(app) as client:
        body = client.get("/api/fs/list", params={"path": str(tmp_path / "nope")}).json()
    assert body == {"ok": False, "error": "not a readable directory"}


def test_universal_and_profile_memory_round_trip(profile_app):
    client, pid = profile_app
    assert client.get("/api/memory").json() == {"text": ""}
    assert client.post("/api/memory", json={"text": "lives in Berlin"}).json() == {"ok": True}
    assert client.get("/api/memory").json() == {"text": "lives in Berlin"}

    assert client.get(f"/api/p/{pid}/memory").json() == {"text": ""}
    assert client.post(f"/api/p/{pid}/memory", json={"text": "terse"}).json() == {"ok": True}
    assert client.get(f"/api/p/{pid}/memory").json() == {"text": "terse"}


def test_onboarded(profile_app):
    client, _pid = profile_app
    assert client.post("/api/onboarded", json={"value": True}).json() == {"ok": True}
