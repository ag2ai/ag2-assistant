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

from tests.conftest import api, make_profile_app, use_fake_agent


def _client(monkeypatch):
    use_fake_agent(monkeypatch)
    app, pid = make_profile_app(persist=True)
    return TestClient(app), pid


def _register_folder(client, path, name=""):
    body = {"path": str(path)}
    if name:
        body["name"] = name
    return client.post("/api/folders", json=body).json()["folder"]


def _grant(client, fid, pid, mode, *, chat_id=""):
    body = {"profile": pid, "mode": mode}
    if chat_id:
        body["chat_id"] = chat_id
    return client.post(f"/api/folders/{fid}/grants", json=body)


def _roots(client, pid, **params):
    return client.get(api(pid, "/folders/roots"), params=params)


def _list(client, pid, path, **params):
    return client.get(api(pid, "/files"), params={"path": str(path), **params})


# ---- Folder roots surface ---------------------------------------------------


def test_profile_granted_folder_shows_as_root(monkeypatch, tmp_path):
    repo = tmp_path / "acme"
    repo.mkdir()
    client, pid = _client(monkeypatch)
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


def test_non_granted_folder_is_absent_from_roots(monkeypatch, tmp_path):
    repo = tmp_path / "acme"
    repo.mkdir()
    client, pid = _client(monkeypatch)
    with client:
        _register_folder(client, repo)  # registered but NOT granted
        assert _roots(client, pid).json()["roots"] == []


def test_read_write_grant_surfaces_its_mode(monkeypatch, tmp_path):
    repo = tmp_path / "acme"
    repo.mkdir()
    client, pid = _client(monkeypatch)
    with client:
        f = _register_folder(client, repo)
        _grant(client, f["id"], pid, "read_write")
        (root,) = _roots(client, pid).json()["roots"]
        assert root["mode"] == "read_write"


def test_chat_only_grant_scopes_roots_by_chat_id(monkeypatch, tmp_path):
    repo = tmp_path / "acme"
    repo.mkdir()
    client, pid = _client(monkeypatch)
    with client:
        f = _register_folder(client, repo)
        _grant(client, f["id"], pid, "read", chat_id="c1")  # chat-ONLY grant
        # shows for the chat that grants it...
        assert len(_roots(client, pid, chat_id="c1").json()["roots"]) == 1
        # ...absent for a different chat...
        assert _roots(client, pid, chat_id="c2").json()["roots"] == []
        # ...and absent with no chat_id (only profile-level grants surface)
        assert _roots(client, pid).json()["roots"] == []


def test_chat_widened_grant_reports_widened_mode(monkeypatch, tmp_path):
    repo = tmp_path / "acme"
    repo.mkdir()
    client, pid = _client(monkeypatch)
    with client:
        f = _register_folder(client, repo)
        _grant(client, f["id"], pid, "read")  # profile: read
        _grant(client, f["id"], pid, "read_write", chat_id="c1")  # chat c1 widens
        assert _roots(client, pid).json()["roots"][0]["mode"] == "read"
        assert _roots(client, pid, chat_id="c1").json()["roots"][0]["mode"] == "read_write"


def test_chat_blocked_folder_is_absent_in_that_chat(monkeypatch, tmp_path):
    repo = tmp_path / "acme"
    repo.mkdir()
    client, pid = _client(monkeypatch)
    with client:
        f = _register_folder(client, repo)
        _grant(client, f["id"], pid, "read")  # readable at profile scope...
        _grant(client, f["id"], pid, "none", chat_id="c1")  # ...but blocked in c1
        assert len(_roots(client, pid).json()["roots"]) == 1
        assert _roots(client, pid, chat_id="c1").json()["roots"] == []


def test_missing_path_folder_shows_as_repointable_root(monkeypatch, tmp_path):
    repo = tmp_path / "gone"
    repo.mkdir()
    client, pid = _client(monkeypatch)
    with client:
        f = _register_folder(client, repo)
        _grant(client, f["id"], pid, "read")
        repo.rmdir()  # the granted directory vanishes off disk
        (root,) = _roots(client, pid).json()["roots"]
        assert root["exists"] is False  # badged, repointable — not dropped, not an error
        assert root["mode"] == "read"


def test_nested_folder_appears_once_under_outermost(monkeypatch, tmp_path):
    outer = tmp_path / "repo"
    (outer / "pkg").mkdir(parents=True)
    client, pid = _client(monkeypatch)
    with client:
        fo = _register_folder(client, outer)
        fi = _register_folder(client, outer / "pkg")
        _grant(client, fo["id"], pid, "read")
        _grant(client, fi["id"], pid, "read")
        roots = _roots(client, pid).json()["roots"]
        assert [r["path"] for r in roots] == [str(outer.resolve())]  # inner deduped away


# ---- Folder-contents listing ------------------------------------------------


def test_list_folder_level_returns_files_and_subdirs(monkeypatch, tmp_path):
    repo = tmp_path / "acme"
    (repo / "src").mkdir(parents=True)
    (repo / "README.md").write_text("hi")
    (repo / "src" / "deep.py").write_text("x")  # NOT in this level
    client, pid = _client(monkeypatch)
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


def test_list_folder_level_prunes_noise_dirs(monkeypatch, tmp_path):
    repo = tmp_path / "acme"
    (repo / ".git").mkdir(parents=True)
    (repo / "node_modules").mkdir()
    (repo / "app").mkdir()
    (repo / "keep.txt").write_text("k")
    (repo / ".DS_Store").write_text("junk")
    client, pid = _client(monkeypatch)
    with client:
        f = _register_folder(client, repo)
        _grant(client, f["id"], pid, "read")
        body = _list(client, pid, repo).json()
        assert [d["name"] for d in body["dirs"]] == ["app"]  # .git / node_modules pruned
        assert [x["name"] for x in body["files"]] == ["keep.txt"]  # .DS_Store pruned


def test_list_non_granted_folder_is_denied(monkeypatch, tmp_path):
    repo = tmp_path / "acme"
    repo.mkdir()
    (repo / "a.txt").write_text("a")
    client, pid = _client(monkeypatch)
    with client:
        _register_folder(client, repo)  # registered but NOT granted
        assert _list(client, pid, repo).status_code == 404


def test_list_escaping_readable_root_is_denied(monkeypatch, tmp_path):
    repo = tmp_path / "acme"
    repo.mkdir()
    (repo / "a.txt").write_text("a")
    (tmp_path / "outside").mkdir()
    client, pid = _client(monkeypatch)
    with client:
        f = _register_folder(client, repo)
        _grant(client, f["id"], pid, "read")
        assert _list(client, pid, tmp_path / "outside").status_code == 404
        assert _list(client, pid, repo / "..").status_code == 404  # traversal guard


def test_list_folder_honors_chat_id(monkeypatch, tmp_path):
    repo = tmp_path / "acme"
    repo.mkdir()
    (repo / "a.txt").write_text("a")
    client, pid = _client(monkeypatch)
    with client:
        f = _register_folder(client, repo)
        _grant(client, f["id"], pid, "read", chat_id="c1")  # chat-ONLY grant
        assert _list(client, pid, repo, chat_id="c1").status_code == 200
        assert _list(client, pid, repo, chat_id="c2").status_code == 404
        assert _list(client, pid, repo).status_code == 404  # no chat_id → profile-only


def test_relative_path_still_lists_files_space(monkeypatch, tmp_path):
    # Regression: no/relative path is unchanged — today's whole-Files-space listing.
    client, pid = _client(monkeypatch)
    with client:
        client.post(
            api(pid, "/files/upload"),
            files=[("files", ("report.txt", b"hello", "text/plain"))],
        )
        body = client.get(api(pid, "/files")).json()
        assert "root" in body and any(f["name"] == "report.txt" for f in body["files"])
