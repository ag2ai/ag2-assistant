"""HTTP contract for the Files tree's Thread-scoped Folder section (ticket 02).

Two gateway seams, both funnelling through the one authorization resolver
(``FolderStore.mode_for``), so this covers the resolver's real behavior:

  * ``GET /folders/roots`` — the Folder roots browsable for the open Thread
    (``chat_id``-scoped, ADR 0013), each with its resolved mode + missing-path badge.
  * ``GET /files?path=<abs>`` — ONE Directory level inside a granted Folder, noise
    pruned, authorized (``read``) and scoped by ``chat_id``; a relative path keeps
    today's Files-space listing (regression).

Mirrors ``test_gateway_folder_files.py`` for the granted-Folder setup."""

from fastapi.testclient import TestClient

from tests.support.apps import api, make_profile_app


def _client():
    app, pid = make_profile_app(persist=True)
    return TestClient(app), pid


def _register_folder(client, path, name=""):
    body = {"path": str(path)}
    if name:
        body["name"] = name
    return client.post("/api/folders", json=body).json()["folder"]


def _grant(client, fid, pid, mode, *, chat_id="", task_id=""):
    body = {"profile": pid, "mode": mode}
    if chat_id:
        body["chat_id"] = chat_id
    if task_id:
        body["task_id"] = task_id
    return client.post(f"/api/folders/{fid}/grants", json=body)


def _roots(client, pid, **params):
    return client.get(api(pid, "/folders/roots"), params=params)


def _list(client, pid, path, **params):
    return client.get(api(pid, "/files"), params={"path": str(path), **params})


# ---- Folder roots surface ---------------------------------------------------


def test_profile_granted_folder_shows_as_root(tmp_path):
    repo = tmp_path / "acme"
    repo.mkdir()
    client, pid = _client()
    with client:
        f = _register_folder(client, repo, name="Acme")
        _grant(client, f["id"], pid, "read")
        roots = _roots(client, pid).json()["roots"]
        assert len(roots) == 1
        (root,) = roots
        assert root["name"] == "Acme"
        assert root["path"] == str(repo.resolve())
        assert root["mode"] == "read"
        assert root["exists"] is True


def test_non_granted_folder_is_absent_from_roots(tmp_path):
    repo = tmp_path / "acme"
    repo.mkdir()
    client, pid = _client()
    with client:
        _register_folder(client, repo)  # registered but NOT granted
        assert _roots(client, pid).json()["roots"] == []


def test_read_write_grant_surfaces_its_mode(tmp_path):
    repo = tmp_path / "acme"
    repo.mkdir()
    client, pid = _client()
    with client:
        f = _register_folder(client, repo)
        _grant(client, f["id"], pid, "read_write")
        (root,) = _roots(client, pid).json()["roots"]
        assert root["mode"] == "read_write"


def test_chat_only_grant_scopes_roots_by_chat_id(tmp_path):
    repo = tmp_path / "acme"
    repo.mkdir()
    client, pid = _client()
    with client:
        f = _register_folder(client, repo)
        _grant(client, f["id"], pid, "read", chat_id="c1")  # chat-ONLY grant
        # shows for the chat that grants it...
        assert len(_roots(client, pid, chat_id="c1").json()["roots"]) == 1
        # ...absent for a different chat...
        assert _roots(client, pid, chat_id="c2").json()["roots"] == []
        # ...and absent with no chat_id (only profile-level grants surface)
        assert _roots(client, pid).json()["roots"] == []


def test_chat_widened_grant_reports_widened_mode(tmp_path):
    repo = tmp_path / "acme"
    repo.mkdir()
    client, pid = _client()
    with client:
        f = _register_folder(client, repo)
        _grant(client, f["id"], pid, "read")  # profile: read
        _grant(client, f["id"], pid, "read_write", chat_id="c1")  # chat c1 widens
        assert _roots(client, pid).json()["roots"][0]["mode"] == "read"
        assert _roots(client, pid, chat_id="c1").json()["roots"][0]["mode"] == "read_write"


def test_chat_blocked_folder_is_absent_in_that_chat(tmp_path):
    repo = tmp_path / "acme"
    repo.mkdir()
    client, pid = _client()
    with client:
        f = _register_folder(client, repo)
        _grant(client, f["id"], pid, "read")  # readable at profile scope...
        _grant(client, f["id"], pid, "none", chat_id="c1")  # ...but blocked in c1
        assert len(_roots(client, pid).json()["roots"]) == 1
        assert _roots(client, pid, chat_id="c1").json()["roots"] == []


def test_missing_path_folder_shows_as_repointable_root(tmp_path):
    repo = tmp_path / "gone"
    repo.mkdir()
    client, pid = _client()
    with client:
        f = _register_folder(client, repo)
        _grant(client, f["id"], pid, "read")
        repo.rmdir()  # the granted directory vanishes off disk
        (root,) = _roots(client, pid).json()["roots"]
        assert root["exists"] is False  # badged, repointable — not dropped, not an error
        assert root["mode"] == "read"


def test_nested_folder_appears_once_under_outermost(tmp_path):
    outer = tmp_path / "repo"
    (outer / "pkg").mkdir(parents=True)
    client, pid = _client()
    with client:
        outer_folder = _register_folder(client, outer)
        inner_folder = _register_folder(client, outer / "pkg")
        _grant(client, outer_folder["id"], pid, "read")
        _grant(client, inner_folder["id"], pid, "read")
        roots = _roots(client, pid).json()["roots"]
        assert [r["path"] for r in roots] == [str(outer.resolve())]  # inner deduped away


# ---- Folder-contents listing ------------------------------------------------


def test_list_folder_level_returns_files_and_subdirs(tmp_path):
    repo = tmp_path / "acme"
    (repo / "src").mkdir(parents=True)
    (repo / "README.md").write_text("hi")
    (repo / "src" / "deep.py").write_text("x")  # NOT in this level
    client, pid = _client()
    with client:
        f = _register_folder(client, repo)
        _grant(client, f["id"], pid, "read")
        body = _list(client, pid, repo).json()
        assert [d["name"] for d in body["dirs"]] == ["src"]
        assert [x["name"] for x in body["files"]] == ["README.md"]
        assert body["files"][0]["path"] == str((repo / "README.md").resolve())
        # one level only — the nested file is absent until src is expanded
        sub = _list(client, pid, repo / "src").json()
        assert [x["name"] for x in sub["files"]] == ["deep.py"]


def test_list_folder_level_prunes_noise_dirs(tmp_path):
    repo = tmp_path / "acme"
    (repo / ".git").mkdir(parents=True)
    (repo / "node_modules").mkdir()
    (repo / "app").mkdir()
    (repo / "keep.txt").write_text("k")
    (repo / ".DS_Store").write_text("junk")
    client, pid = _client()
    with client:
        f = _register_folder(client, repo)
        _grant(client, f["id"], pid, "read")
        body = _list(client, pid, repo).json()
        assert [d["name"] for d in body["dirs"]] == ["app"]  # .git / node_modules pruned
        assert [x["name"] for x in body["files"]] == ["keep.txt"]  # .DS_Store pruned


def test_list_non_granted_folder_is_denied(tmp_path):
    repo = tmp_path / "acme"
    repo.mkdir()
    (repo / "a.txt").write_text("a")
    client, pid = _client()
    with client:
        _register_folder(client, repo)  # registered but NOT granted
        assert _list(client, pid, repo).status_code == 404


def test_list_escaping_readable_root_is_denied(tmp_path):
    repo = tmp_path / "acme"
    repo.mkdir()
    (repo / "a.txt").write_text("a")
    (tmp_path / "outside").mkdir()
    client, pid = _client()
    with client:
        f = _register_folder(client, repo)
        _grant(client, f["id"], pid, "read")
        assert _list(client, pid, tmp_path / "outside").status_code == 404
        assert _list(client, pid, repo / "..").status_code == 404  # traversal guard


def test_list_folder_honors_chat_id(tmp_path):
    repo = tmp_path / "acme"
    repo.mkdir()
    (repo / "a.txt").write_text("a")
    client, pid = _client()
    with client:
        f = _register_folder(client, repo)
        _grant(client, f["id"], pid, "read", chat_id="c1")  # chat-ONLY grant
        assert _list(client, pid, repo, chat_id="c1").status_code == 200
        assert _list(client, pid, repo, chat_id="c2").status_code == 404
        assert _list(client, pid, repo).status_code == 404  # no chat_id → profile-only


# ---- Task-scoped Folder section (the open Task page / run thread) -----------
# The Files rail carries the open Thread's scope in the same ``chat_id`` slot as a
# synthetic token: ``task:{task_id}`` for a Task page, ``task-run:{run_id}`` for a run
# thread (translated to its task via ``get_run``). Both resolve the task-scope Grants
# that a plain chat scope can't see (the "FileTree ignores the task's folders" bug).


def test_task_scoped_folder_shows_as_root_via_task_token(tmp_path):
    media = tmp_path / "media"
    media.mkdir()
    client, pid = _client()
    with client:
        f = _register_folder(client, media, name="Media")
        _grant(client, f["id"], pid, "read", task_id="task-1")  # task-ONLY grant
        # profile scope (no token) can't see it...
        assert _roots(client, pid).json()["roots"] == []
        # ...the open Task page (task:{id}) does, with its resolved mode
        (root,) = _roots(client, pid, chat_id="task:task-1").json()["roots"]
        assert root["name"] == "Media" and root["mode"] == "read"
        # ...and a different task doesn't
        assert _roots(client, pid, chat_id="task:task-2").json()["roots"] == []


def test_task_none_override_hides_profile_folder_on_task_page(tmp_path):
    repo = tmp_path / "acme"
    repo.mkdir()
    client, pid = _client()
    with client:
        f = _register_folder(client, repo)
        _grant(client, f["id"], pid, "read")  # profile-wide read
        _grant(client, f["id"], pid, "none", task_id="task-1")  # blocked for this task
        assert len(_roots(client, pid).json()["roots"]) == 1
        assert _roots(client, pid, chat_id="task:task-1").json()["roots"] == []


def test_list_task_folder_authorizes_via_task_token(tmp_path):
    media = tmp_path / "media"
    media.mkdir()
    (media / "clip.txt").write_text("x")
    client, pid = _client()
    with client:
        f = _register_folder(client, media)
        _grant(client, f["id"], pid, "read", task_id="task-1")
        assert _list(client, pid, media, chat_id="task:task-1").status_code == 200
        assert _list(client, pid, media).status_code == 404  # profile scope can't reach it
        assert _list(client, pid, media, chat_id="task:task-2").status_code == 404


def test_run_thread_token_resolves_chat_task_and_profile_together(tmp_path):
    # A run thread carries ``task-run:{run_id}``: the endpoint derives the run's task
    # (via get_run) AND keeps the token as the chat scope, so a run resolves all three
    # layers at once — its own chat-scoped grant, the task's grants, and profile grants.
    media, workdir, scripts = tmp_path / "media", tmp_path / "assistant", tmp_path / "scripts"
    for d in (media, workdir, scripts):
        d.mkdir()
    client, pid = _client()
    with client:
        task = client.post(api(pid, "/tasks"), json={"name": "T", "prompt": "p"}).json()["task"]
        run = client.post(api(pid, f"/tasks/{task['id']}/run")).json()["run"]
        token = f"task-run:{run['id']}"
        fm = _register_folder(client, media, name="media")
        fw = _register_folder(client, workdir, name="assistant")
        fs = _register_folder(client, scripts, name="scripts")
        _grant(client, fm["id"], pid, "read", task_id=task["id"])  # task scope
        _grant(client, fw["id"], pid, "read_write", chat_id=token)  # this run's chat scope
        _grant(client, fs["id"], pid, "read")  # profile scope
        roots = _roots(client, pid, chat_id=token).json()["roots"]
        assert {r["name"]: r["mode"] for r in roots} == {
            "media": "read",
            "assistant": "read_write",
            "scripts": "read",
        }


def test_relative_path_still_lists_files_space(tmp_path):
    # Regression: no/relative path is unchanged — today's whole-Files-space listing.
    client, pid = _client()
    with client:
        client.post(
            api(pid, "/files/upload"),
            files=[("files", ("report.txt", b"hello", "text/plain"))],
        )
        body = client.get(api(pid, "/files")).json()
        assert "root" in body and any(f["name"] == "report.txt" for f in body["files"])
