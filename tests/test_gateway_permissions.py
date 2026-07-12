"""HTTP surface for the install-wide permission store (global routes on `app`).

The routes are global (not profile-scoped) and back onto config.root_dir/permissions.json.
The autouse `_isolate_ag2assistant_home` fixture redirects HOME → tmp, so the store
lands in disposable space. A single faked-agent profile boots so the app lifespan is
happy; every assertion hits `/api/permissions*`.
"""

from fastapi.testclient import TestClient

from tests.conftest import make_profile_app, use_fake_agent


def _client(monkeypatch):
    use_fake_agent(monkeypatch)
    app, _pid = make_profile_app(persist=True)
    return TestClient(app)


def test_permissions_get_empty(monkeypatch):
    with _client(monkeypatch) as client:
        r = client.get("/api/permissions")
        assert r.status_code == 200, r.text
        assert r.json() == {"folders": [], "blocked": [], "commands": []}


def test_grant_folder_appears_in_get_and_fresh_store(monkeypatch):
    from assistant.config import load_config
    from assistant.permissions import PermissionStore, _norm

    with _client(monkeypatch) as client:
        r = client.post("/api/permissions/folders", json={"path": "/tmp/work-repo"})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["ok"] is True
        # snapshot carries the normalised (resolved) path
        norm = str(_norm("/tmp/work-repo"))
        assert norm in body["folders"]  # full snapshot returned

        # visible via a plain GET
        assert norm in client.get("/api/permissions").json()["folders"]
        # and to a fresh store over the same file (is_allowed normalises the query)
        store = PermissionStore(load_config().root_dir / "permissions.json")
        assert store.is_allowed("/tmp/work-repo") is True


def test_delete_folder_miss_is_404(monkeypatch):
    with _client(monkeypatch) as client:
        r = client.request("DELETE", "/api/permissions/folders", json={"path": "/never/granted"})
        assert r.status_code == 404, r.text


def test_grant_and_delete_folder_round_trip(monkeypatch):
    with _client(monkeypatch) as client:
        client.post("/api/permissions/folders", json={"path": "/tmp/x"})
        r = client.request("DELETE", "/api/permissions/folders", json={"path": "/tmp/x"})
        assert r.status_code == 200, r.text
        assert "/tmp/x" not in r.json()["folders"]


def test_grant_command_with_prefix_yields_rule_string(monkeypatch):
    with _client(monkeypatch) as client:
        r = client.post(
            "/api/permissions/commands", json={"tool": "run_shell_command", "prefix": "git"}
        )
        assert r.status_code == 200, r.text
        assert "run_shell_command(git *)" in r.json()["commands"]

        # whole-tool grant (null prefix) → bare rule — action tools only
        r2 = client.post("/api/permissions/commands", json={"tool": "gmail_send", "prefix": None})
        assert "gmail_send" in r2.json()["commands"]


def test_grant_command_bad_prefix_is_400(monkeypatch):
    with _client(monkeypatch) as client:
        r = client.post(
            "/api/permissions/commands",
            json={"tool": "run_shell_command", "prefix": "git status"},  # not a single token
        )
        assert r.status_code == 400, r.text


def test_grant_command_bad_tool_is_400(monkeypatch):
    # A tool name that can't form a valid rule (spaces/parens) must be a 400, not a 500.
    with _client(monkeypatch) as client:
        r = client.post("/api/permissions/commands", json={"tool": "run shell", "prefix": None})
        assert r.status_code == 400, r.text


def test_grant_command_bare_exec_tool_is_400(monkeypatch):
    # A blanket grant (no prefix) on an arbitrary-execution tool — shell OR host
    # code — would authorise everything forever; the API must refuse all four names.
    with _client(monkeypatch) as client:
        for tool in ("run_shell_command", "run_shell_local", "run_code", "run_code_local"):
            r = client.post("/api/permissions/commands", json={"tool": tool, "prefix": None})
            assert r.status_code == 400, r.text
        # nothing minted
        assert client.get("/api/permissions").json()["commands"] == []
        # the per-prefix form is still fine
        r = client.post(
            "/api/permissions/commands", json={"tool": "run_shell_command", "prefix": "git"}
        )
        assert r.status_code == 200, r.text


def test_delete_command_miss_is_404(monkeypatch):
    with _client(monkeypatch) as client:
        r = client.request(
            "DELETE", "/api/permissions/commands", json={"rule": "run_shell_command(git *)"}
        )
        assert r.status_code == 404, r.text


def test_blocked_removes_conflicting_grant(monkeypatch):
    from assistant.permissions import _norm

    with _client(monkeypatch) as client:
        client.post("/api/permissions/folders", json={"path": "/tmp/secret"})
        r = client.post("/api/permissions/blocked", json={"path": "/tmp/secret"})
        assert r.status_code == 200, r.text
        body = r.json()
        norm = str(_norm("/tmp/secret"))
        assert norm in body["blocked"]
        assert norm not in body["folders"]  # conflicting grant removed


def test_empty_path_is_400(monkeypatch):
    with _client(monkeypatch) as client:
        r = client.post("/api/permissions/folders", json={"path": "  "})
        assert r.status_code == 400, r.text
