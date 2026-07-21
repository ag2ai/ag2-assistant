"""HTTP contract for previewing/downloading a Folder file — a file inside a granted
Folder *outside* the Root — through ``GET /files/raw`` with an ABSOLUTE path (ticket
01). Every absolute path funnels through the one authorization resolver
(``FolderStore.mode_for_path``), so this gateway seam covers the resolver's real
behavior: serve on a covering ``read``/``read_write`` Grant, deny (404) on a
non-granted / chat-blocked Folder or a path escaping every readable root, honor
``chat_id``, and leave the relative Files-space path untouched (regression). Mirrors
``test_gateway_files_search.py`` for the granted-Folder setup."""

from fastapi.testclient import TestClient

from tests.conftest import api, make_profile_app, use_fake_agent


def _client(monkeypatch):
    use_fake_agent(monkeypatch)
    app, pid = make_profile_app(persist=True)
    return TestClient(app), pid


def _register_folder(client, path):
    return client.post("/api/folders", json={"path": str(path)}).json()["folder"]


def _grant(client, fid, pid, mode, *, chat_id=""):
    body = {"profile": pid, "mode": mode}
    if chat_id:
        body["chat_id"] = chat_id
    return client.post(f"/api/folders/{fid}/grants", json=body)


def _raw(client, pid, path, **params):
    return client.get(api(pid, "/files/raw"), params={"path": str(path), **params})


def test_read_granted_folder_file_serves_inline_and_download(monkeypatch, tmp_path):
    repo = tmp_path / "acme"
    repo.mkdir()
    (repo / "widget.py").write_text("print('hi')")
    client, pid = _client(monkeypatch)
    with client:
        f = _register_folder(client, repo)
        _grant(client, f["id"], pid, "read")
        # inline preview
        r = _raw(client, pid, repo / "widget.py")
        assert r.status_code == 200 and r.content == b"print('hi')"
        assert "inline" in r.headers.get("content-disposition", "")
        assert r.headers.get("etag")  # ADR 0011 content-version token
        # download → attachment
        r = _raw(client, pid, repo / "widget.py", download="true")
        assert r.status_code == 200 and r.content == b"print('hi')"
        assert "attachment" in r.headers.get("content-disposition", "")


def test_read_write_granted_folder_file_serves(monkeypatch, tmp_path):
    repo = tmp_path / "acme"
    repo.mkdir()
    (repo / "sub").mkdir()
    (repo / "sub" / "deep.txt").write_text("deep")
    client, pid = _client(monkeypatch)
    with client:
        f = _register_folder(client, repo)
        _grant(client, f["id"], pid, "read_write")
        # a nested file (read suffices for GET even under a read_write Grant)
        r = _raw(client, pid, repo / "sub" / "deep.txt")
        assert r.status_code == 200 and r.content == b"deep"


def test_non_granted_folder_file_is_denied(monkeypatch, tmp_path):
    repo = tmp_path / "acme"
    repo.mkdir()
    (repo / "secret.py").write_text("x")
    client, pid = _client(monkeypatch)
    with client:
        _register_folder(client, repo)  # registered but NOT granted
        assert _raw(client, pid, repo / "secret.py").status_code == 404


def test_path_escaping_every_readable_root_is_denied(monkeypatch, tmp_path):
    repo = tmp_path / "acme"
    repo.mkdir()
    (repo / "ok.txt").write_text("ok")
    outside = tmp_path / "outside.txt"  # a real file OUTSIDE the granted root
    outside.write_text("nope")
    client, pid = _client(monkeypatch)
    with client:
        f = _register_folder(client, repo)
        _grant(client, f["id"], pid, "read")
        # an absolute path under no readable root → denied
        assert _raw(client, pid, outside).status_code == 404
        # a `..`-escape that resolves outside the granted root → denied (traversal guard)
        assert _raw(client, pid, repo / ".." / "outside.txt").status_code == 404


def test_chat_grant_scopes_by_chat_id(monkeypatch, tmp_path):
    repo = tmp_path / "acme"
    repo.mkdir()
    (repo / "note.txt").write_text("n")
    client, pid = _client(monkeypatch)
    with client:
        f = _register_folder(client, repo)
        _grant(client, f["id"], pid, "read", chat_id="c1")  # chat-ONLY grant
        # served for the chat that grants it...
        assert _raw(client, pid, repo / "note.txt", chat_id="c1").status_code == 200
        # ...denied for a different chat...
        assert _raw(client, pid, repo / "note.txt", chat_id="c2").status_code == 404
        # ...and denied with no chat_id (only profile-level grants authorize)
        assert _raw(client, pid, repo / "note.txt").status_code == 404


def test_chat_blocked_folder_file_is_denied_in_that_chat(monkeypatch, tmp_path):
    repo = tmp_path / "acme"
    repo.mkdir()
    (repo / "blocked.txt").write_text("b")
    client, pid = _client(monkeypatch)
    with client:
        f = _register_folder(client, repo)
        _grant(client, f["id"], pid, "read")  # readable at profile scope...
        _grant(client, f["id"], pid, "none", chat_id="c1")  # ...but blocked in c1
        assert _raw(client, pid, repo / "blocked.txt").status_code == 200
        assert _raw(client, pid, repo / "blocked.txt", chat_id="c1").status_code == 404


def test_relative_path_still_hits_files_space(monkeypatch, tmp_path):
    # Regression: a relative path is unchanged — the Files-space sandbox serves it,
    # never touching the Folder resolver.
    client, pid = _client(monkeypatch)
    with client:
        client.post(
            api(pid, "/files/upload"),
            files=[("files", ("report.txt", b"hello", "text/plain"))],
        )
        r = _raw(client, pid, "report.txt")
        assert r.status_code == 200 and r.content == b"hello"
        # a relative traversal escape is still the sandbox 404, not a Folder lookup
        assert _raw(client, pid, "../../etc/passwd").status_code == 404
