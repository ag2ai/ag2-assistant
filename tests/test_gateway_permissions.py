"""HTTP surface for the install-wide command permission store (global routes on `app`).

The routes are global (not profile-scoped) and back onto config.root_dir/permissions.json.
The autouse `_isolate_ag2assistant_home` fixture redirects HOME → tmp, so the store
lands in disposable space. A single faked-agent profile boots so the app lifespan is
happy; every assertion hits `/api/permissions*`. (Folders/Grants live under /api/folders,
covered by test_gateway_folders.py.)
"""

from fastapi.testclient import TestClient

from tests.support.apps import make_profile_app


def _client(paths):
    app, _pid = make_profile_app(paths, persist=True)
    return TestClient(app)


def test_permissions_get_empty(paths):
    with _client(paths) as client:
        r = client.get("/api/permissions")
        assert r.status_code == 200, r.text
        assert r.json() == {"commands": []}


def test_grant_command_with_prefix_yields_rule_string(paths):
    with _client(paths) as client:
        r = client.post(
            "/api/permissions/commands", json={"tool": "run_shell_command", "prefix": "git"}
        )
        assert r.status_code == 200, r.text
        assert "run_shell_command(git *)" in r.json()["commands"]

        # whole-tool grant (null prefix) → bare rule — action tools only
        r2 = client.post("/api/permissions/commands", json={"tool": "gmail_send", "prefix": None})
        assert "gmail_send" in r2.json()["commands"]


def test_grant_command_bad_prefix_is_400(paths):
    with _client(paths) as client:
        r = client.post(
            "/api/permissions/commands",
            json={"tool": "run_shell_command", "prefix": "git status"},  # not a single token
        )
        assert r.status_code == 400, r.text


def test_grant_command_bad_tool_is_400(paths):
    # A tool name that can't form a valid rule (spaces/parens) must be a 400, not a 500.
    with _client(paths) as client:
        r = client.post("/api/permissions/commands", json={"tool": "run shell", "prefix": None})
        assert r.status_code == 400, r.text


def test_grant_command_bare_exec_tool_is_400(paths):
    # A blanket grant (no prefix) on an arbitrary-execution tool — shell OR host
    # code — would authorise everything forever; the API must refuse all four names.
    with _client(paths) as client:
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


def test_delete_command_miss_is_404(paths):
    with _client(paths) as client:
        r = client.request(
            "DELETE", "/api/permissions/commands", json={"rule": "run_shell_command(git *)"}
        )
        assert r.status_code == 404, r.text
