"""Global /api/folders routes — the install-wide Folder registry + Grants."""

from fastapi.testclient import TestClient

from tests.conftest import make_profile_app, use_fake_agent


def _client(monkeypatch):
    use_fake_agent(monkeypatch)
    app, pid = make_profile_app(persist=True)
    return TestClient(app), pid


def test_folder_crud_roundtrip(monkeypatch, tmp_path):
    d = tmp_path / "acme"
    d.mkdir()
    client, _pid = _client(monkeypatch)
    with client:
        r = client.post("/api/folders", json={"path": str(d)})
        assert r.status_code == 200
        f = r.json()["folder"]
        assert f["name"] == "acme" and f["exists"] is True
        # duplicate path → 409 with a pointer to the existing Folder
        r = client.post("/api/folders", json={"path": str(d)})
        assert r.status_code == 409 and r.json()["existing"]["id"] == f["id"]
        # rename
        r = client.post(f"/api/folders/{f['id']}", json={"name": "work-repo"})
        assert r.status_code == 200
        assert any(x["name"] == "work-repo" for x in r.json()["folders"])
        # bad path on create → 400
        r = client.post("/api/folders", json={"path": str(tmp_path / "nope")})
        assert r.status_code == 400
        # delete
        assert client.delete(f"/api/folders/{f['id']}").status_code == 200
        assert client.delete(f"/api/folders/{f['id']}").status_code == 404
        assert client.get("/api/folders").json()["folders"] == []


def test_grant_upsert_and_revoke(monkeypatch, tmp_path):
    d = tmp_path / "acme"
    d.mkdir()
    client, pid = _client(monkeypatch)
    with client:
        f = client.post("/api/folders", json={"path": str(d)}).json()["folder"]
        r = client.post(f"/api/folders/{f['id']}/grants", json={"profile": pid, "mode": "read"})
        assert r.status_code == 200
        r = client.post(
            f"/api/folders/{f['id']}/grants",
            json={"profile": pid, "chat_id": "c1", "mode": "read_write"},
        )
        grants = next(x for x in r.json()["folders"] if x["id"] == f["id"])["grants"]
        assert len(grants) == 2
        # bad mode → 400; unknown folder → 404
        assert (
            client.post(
                f"/api/folders/{f['id']}/grants", json={"profile": pid, "mode": "rw"}
            ).status_code
            == 400
        )
        assert (
            client.post(
                "/api/folders/f_nope/grants", json={"profile": pid, "mode": "read"}
            ).status_code
            == 404
        )
        # revoke chat grant only
        r = client.request(
            "DELETE", f"/api/folders/{f['id']}/grants", json={"profile": pid, "chat_id": "c1"}
        )
        assert r.status_code == 200
        grants = next(x for x in r.json()["folders"] if x["id"] == f["id"])["grants"]
        assert grants == [{"profile": pid, "chat_id": "", "task_id": "", "mode": "read"}]
        # revoking a grant that isn't there → 404
        r = client.request(
            "DELETE", f"/api/folders/{f['id']}/grants", json={"profile": pid, "chat_id": "c1"}
        )
        assert r.status_code == 404


def test_task_scope_grant_roundtrip(monkeypatch, tmp_path):
    d = tmp_path / "acme"
    d.mkdir()
    client, pid = _client(monkeypatch)
    with client:
        fid = client.post("/api/folders", json={"path": str(d)}).json()["folder"]["id"]
        r = client.post(
            f"/api/folders/{fid}/grants",
            json={"profile": "work", "task_id": "task-1", "mode": "read_write"},
        )
        assert r.status_code == 200
        grants = [g for f in r.json()["folders"] if f["id"] == fid for g in f["grants"]]
        assert {"profile": "work", "chat_id": "", "task_id": "task-1", "mode": "read_write"} in grants
        # both scopes at once → 400
        r = client.post(
            f"/api/folders/{fid}/grants",
            json={"profile": "work", "chat_id": "c1", "task_id": "t1", "mode": "read"},
        )
        assert r.status_code == 400
        # revoke is precise about scope
        r = client.request(
            "DELETE", f"/api/folders/{fid}/grants", json={"profile": "work", "task_id": "task-1"}
        )
        assert r.status_code == 200


def test_folder_permission_routes_are_gone(monkeypatch):
    client, _pid = _client(monkeypatch)
    with client:
        snapshot = client.get("/api/permissions").json()
        assert "folders" not in snapshot and "blocked" not in snapshot
        assert client.post("/api/permissions/folders", json={"path": "/tmp/x"}).status_code == 404
        assert client.post("/api/permissions/blocked", json={"path": "/tmp/x"}).status_code == 404


def test_project_folder_route_is_gone(monkeypatch):
    client, pid = _client(monkeypatch)
    with client:
        r = client.post(f"/api/p/{pid}/settings/project-folder", json={"path": "/tmp"})
        assert r.status_code == 404
        assert "project_folder" not in client.get(f"/api/p/{pid}/settings").json()
