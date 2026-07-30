"""HTTP contract for the ``@``-picker's ``/files/search`` route (ticket 01): a
chat-aware search over the profile's Files space that returns a bounded, ranked
list of matches with absolute paths. Corpus here is Files-space only; granted
Folders join in ticket 03 without a contract change. Mirrors
``test_gateway_files.py`` — drive through ``profile_app`` and assert the response."""

import os

from fastapi.testclient import TestClient

from assistant.workspace import SEARCH_LIMIT
from tests.support.apps import api, make_profile_app


def _upload(client, pid, name, *, dir=""):
    return client.post(
        api(pid, "/files/upload"),
        files=[("files", (name, b"x", "text/plain"))],
        data={"dir": dir},
    )


def _search(client, pid, q):
    return client.get(api(pid, "/files/search"), params={"q": q})


def test_search_matches_files_space_file_by_name(profile_app):
    client, pid = profile_app
    _upload(client, pid, "report.txt")
    _upload(client, pid, "notes.md")
    r = _search(client, pid, "report")
    assert r.status_code == 200
    names = [f["name"] for f in r.json()["results"]]
    assert "report.txt" in names and "notes.md" not in names


def test_query_tolerates_leading_and_trailing_slash(profile_app):
    # A path-style query like "@/media" must still find the "media" entry — a
    # leading/trailing slash is not a meaningful search character.
    client, pid = profile_app
    client.post(api(pid, "/files/mkdir"), json={"path": "media"})
    _upload(client, pid, "clip.txt", dir="media")
    for q in ("/media", "media/", "/media/"):
        results = _search(client, pid, q).json()["results"]
        assert any(r["name"] == "media" for r in results), q


def test_no_match_returns_empty_list_not_error(profile_app):
    client, pid = profile_app
    _upload(client, pid, "report.txt")
    r = _search(client, pid, "nothingmatchesthis")
    assert r.status_code == 200
    assert r.json()["results"] == []


def test_empty_query_returns_empty_list(profile_app):
    client, pid = profile_app
    _upload(client, pid, "report.txt")
    r = _search(client, pid, "")
    assert r.status_code == 200
    assert r.json()["results"] == []


def test_results_carry_absolute_paths_usable_by_read_file(profile_app):
    client, pid = profile_app
    _upload(client, pid, "report.txt", dir="docs")
    hit = _search(client, pid, "report").json()["results"][0]
    assert hit["kind"] == "file"
    assert hit["name"] == "report.txt"
    assert os.path.isabs(hit["path"]) and os.path.isfile(hit["path"])


def test_results_are_bounded_to_top_n(profile_app):
    client, pid = profile_app
    for i in range(SEARCH_LIMIT + 5):
        _upload(client, pid, f"match-{i:03d}.txt")
    results = _search(client, pid, "match").json()["results"]
    assert len(results) == SEARCH_LIMIT


def test_filename_matches_rank_above_path_only_matches(profile_app):
    client, pid = profile_app
    # a root file whose NAME matches...
    _upload(client, pid, "match.txt")
    # ...and a newer file whose only match is its containing directory path.
    _upload(client, pid, "zzz.txt", dir="match")
    results = _search(client, pid, "match").json()["results"]
    names = [f["name"] for f in results]
    assert names.index("match.txt") < names.index("zzz.txt")


# ---- Ticket 03: granted-Folder corpus (mirrors test_gateway_folders.py setup) ----


def _client():
    app, pid = make_profile_app(persist=True)
    return TestClient(app), pid


def _register_folder(client, path):
    return client.post("/api/folders", json={"path": str(path)}).json()["folder"]


def _grant(client, fid, pid, mode, *, chat_id=""):
    body = {"profile": pid, "mode": mode}
    if chat_id:
        body["chat_id"] = chat_id
    return client.post(f"/api/folders/{fid}/grants", json=body)


def test_search_matches_file_in_granted_folder(tmp_path):
    repo = tmp_path / "acme"
    repo.mkdir()
    (repo / "widget.py").write_text("x")
    client, pid = _client()
    with client:
        f = _register_folder(client, repo)
        _grant(client, f["id"], pid, "read")
        hit = client.get(api(pid, "/files/search"), params={"q": "widget"}).json()["results"]
        assert len(hit) == 1
        assert hit[0]["name"] == "widget.py" and hit[0]["kind"] == "file"
        assert hit[0]["path"] == str(repo / "widget.py")


def test_file_in_ungranted_folder_is_absent(tmp_path):
    repo = tmp_path / "acme"
    repo.mkdir()
    (repo / "secret.py").write_text("x")
    client, pid = _client()
    with client:
        _register_folder(client, repo)  # registered but NOT granted
        results = client.get(api(pid, "/files/search"), params={"q": "secret"}).json()["results"]
        assert results == []


def test_file_in_chat_blocked_folder_is_absent(tmp_path):
    repo = tmp_path / "acme"
    repo.mkdir()
    (repo / "blocked.py").write_text("x")
    client, pid = _client()
    with client:
        f = _register_folder(client, repo)
        _grant(client, f["id"], pid, "read")  # readable at profile scope...
        _grant(client, f["id"], pid, "none", chat_id="c1")  # ...but blocked in chat c1
        # profile-scope search still sees it
        base = client.get(api(pid, "/files/search"), params={"q": "blocked"}).json()["results"]
        assert len(base) == 1
        # in the blocked chat it is absent (access-honoring guarantee)
        in_chat = client.get(
            api(pid, "/files/search"), params={"q": "blocked", "chat_id": "c1"}
        ).json()["results"]
        assert in_chat == []


def test_folder_walk_skips_noise_dirs(tmp_path):
    repo = tmp_path / "acme"
    (repo / ".git").mkdir(parents=True)
    (repo / ".git" / "config-target.txt").write_text("x")
    (repo / "node_modules").mkdir()
    (repo / "node_modules" / "target-dep.txt").write_text("x")
    (repo / "target-real.txt").write_text("x")
    client, pid = _client()
    with client:
        f = _register_folder(client, repo)
        _grant(client, f["id"], pid, "read")
        results = client.get(api(pid, "/files/search"), params={"q": "target"}).json()["results"]
        names = [r["name"] for r in results]
        assert names == ["target-real.txt"]


def test_files_space_directory_matches_with_kind_directory(profile_app):
    client, pid = profile_app
    client.post(api(pid, "/files/mkdir"), json={"path": "reports"})
    results = _search(client, pid, "reports").json()["results"]
    dirs = [r for r in results if r["kind"] == "directory"]
    assert any(d["name"] == "reports" and os.path.isabs(d["path"]) for d in dirs)


def test_granted_folder_directory_matches_with_kind_directory(tmp_path):
    repo = tmp_path / "acme"
    (repo / "widgets").mkdir(parents=True)
    (repo / "widgets" / "keep.py").write_text("x")
    client, pid = _client()
    with client:
        f = _register_folder(client, repo)
        _grant(client, f["id"], pid, "read")
        results = client.get(api(pid, "/files/search"), params={"q": "widgets"}).json()["results"]
        hit = next(r for r in results if r["name"] == "widgets")
        assert hit["kind"] == "directory" and hit["path"] == str(repo / "widgets")


def test_granted_folder_root_itself_is_referenceable(tmp_path):
    # A path-style query for the Folder's own name (``@/media``) must surface the
    # granted Folder ROOT as a directory, not just its contents — ``os.walk`` yields
    # a root's children but never the root, so it needs to be emitted explicitly.
    repo = tmp_path / "media"
    repo.mkdir()
    (repo / "clip.txt").write_text("x")
    client, pid = _client()
    with client:
        f = _register_folder(client, repo)
        _grant(client, f["id"], pid, "read")
        results = client.get(api(pid, "/files/search"), params={"q": "/media"}).json()["results"]
        root = next((r for r in results if r["path"] == str(repo)), None)
        assert root is not None, "the granted Folder root is missing from results"
        assert root["name"] == "media" and root["kind"] == "directory"


def test_folder_walk_skips_os_junk_files(tmp_path):
    # A folder-name query matches every descendant on its path, so OS junk like
    # .DS_Store would flood the picker — it must be pruned from results.
    repo = tmp_path / "media"
    (repo / "habr").mkdir(parents=True)
    (repo / ".DS_Store").write_text("x")
    (repo / "habr" / ".DS_Store").write_text("x")
    (repo / "habr" / "post.md").write_text("x")
    client, pid = _client()
    with client:
        f = _register_folder(client, repo)
        _grant(client, f["id"], pid, "read")
        results = client.get(api(pid, "/files/search"), params={"q": "media"}).json()["results"]
        assert not any(r["name"] == ".DS_Store" for r in results)
        assert any(r["name"] == "post.md" for r in results)  # real files still surface


def test_folder_walk_surfaces_non_noise_dot_dirs(tmp_path):
    # Only the known noise dot-dirs (SKIP_DIRS) are pruned; a legitimate dotted
    # directory like .github stays reachable — the corpus is what mode_for allows.
    repo = tmp_path / "acme"
    (repo / ".github").mkdir(parents=True)
    (repo / ".github" / "deploy-target.yml").write_text("x")
    client, pid = _client()
    with client:
        f = _register_folder(client, repo)
        _grant(client, f["id"], pid, "read")
        results = client.get(api(pid, "/files/search"), params={"q": "deploy"}).json()["results"]
        assert any(r["name"] == "deploy-target.yml" for r in results)


def test_search_matches_file_in_task_run_scoped_folder(tmp_path):
    """``chat_id=task-run:{run_id}`` must resolve the run's task_id (via
    ``runtime.tasks.get_run``) so a Folder granted only at task-scope is visible
    inside that run's thread, but not from an unrelated chat (or no chat_id at
    all) — the coverage gap called out for the ``/files/search`` route."""
    repo = tmp_path / "acme"
    repo.mkdir()
    (repo / "taskfile.py").write_text("x")
    client, pid = _client()
    with client:
        f = _register_folder(client, repo)

        created = client.post(api(pid, "/tasks"), json={"name": "T", "prompt": "go"}).json()["task"]
        task_id = created["id"]
        run = client.post(api(pid, f"/tasks/{task_id}/run")).json()["run"]
        run_id = run["id"]
        assert run["task_id"] == task_id

        # Task-scope grant ONLY — no profile-scope, no chat-scope grant exists.
        gw = client.app.state.profiles.get(pid).gateway
        gw.folders.set_grant(f["id"], "read", profile=pid, task_id=task_id)

        in_run = client.get(
            api(pid, "/files/search"), params={"q": "taskfile", "chat_id": f"task-run:{run_id}"}
        ).json()["results"]
        assert len(in_run) == 1
        assert in_run[0]["name"] == "taskfile.py"
        assert in_run[0]["path"] == str(repo / "taskfile.py")

        # An unrelated chat gets none of the task's Folder grant...
        other_chat = client.get(
            api(pid, "/files/search"), params={"q": "taskfile", "chat_id": "web-1"}
        ).json()["results"]
        assert other_chat == []

        # ...and neither does a bare profile-scope search (no chat_id at all).
        no_chat = client.get(api(pid, "/files/search"), params={"q": "taskfile"}).json()["results"]
        assert no_chat == []


def test_combined_corpus_ranks_filename_first(tmp_path):
    repo = tmp_path / "shared"  # only its PATH matches "shared"
    repo.mkdir()
    (repo / "irrelevant.py").write_text("x")
    client, pid = _client()
    with client:
        # a Files-space file whose NAME matches "shared"
        client.post(
            api(pid, "/files/upload"),
            files=[("files", ("shared.md", b"x", "text/plain"))],
        )
        f = _register_folder(client, repo)
        _grant(client, f["id"], pid, "read")
        results = client.get(api(pid, "/files/search"), params={"q": "shared"}).json()["results"]
        names = [r["name"] for r in results]
        # the filename match ranks above the path-only match from the Folder
        assert names.index("shared.md") < names.index("irrelevant.py")
