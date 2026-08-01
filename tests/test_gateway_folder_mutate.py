"""HTTP contract for MUTATING a Folder file — a file inside a granted Folder *outside*
the Root — through ``PUT``/``DELETE /files/raw`` and ``POST /files/move`` with ABSOLUTE
paths (ticket 04). Every absolute path funnels through the one authorization resolver
(``FolderStore.resolve_within``) requiring ``read_write``, so this gateway seam covers:
edit-in-place (ADR 0011 ETag/conflict), rename/delete/move confined to the Folder's own
subtree, the no-cross-Root-move rejection, ``read``-only denial (403), ``chat_id``
scoping, and the untouched relative Files-space path (regression). Mirrors
``test_gateway_folder_files.py`` for the granted-Folder setup."""

from fastapi.testclient import TestClient

from tests.support.apps import api, make_profile_app


def _client(paths):
    app, pid = make_profile_app(paths, persist=True)
    return TestClient(app), pid


def _register_folder(client, path):
    return client.post("/api/folders", json={"path": str(path)}).json()["folder"]


def _grant(client, fid, pid, mode, *, chat_id="", task_id=""):
    body = {"profile": pid, "mode": mode}
    if chat_id:
        body["chat_id"] = chat_id
    if task_id:
        body["task_id"] = task_id
    return client.post(f"/api/folders/{fid}/grants", json=body)


def _get(client, pid, path, **params):
    return client.get(api(pid, "/files/raw"), params={"path": str(path), **params})


def _put(client, pid, path, body, *, if_match=None, **params):
    headers = {"If-Match": f'"{if_match}"'} if if_match else {}
    return client.put(
        api(pid, "/files/raw"),
        params={"path": str(path), **params},
        content=body,
        headers=headers,
    )


def _delete(client, pid, path, **params):
    return client.delete(api(pid, "/files/raw"), params={"path": str(path), **params})


def _move(client, pid, src, dst, **body):
    return client.post(api(pid, "/files/move"), json={"from": str(src), "to": str(dst), **body})


def _mkdir(client, pid, path, **body):
    return client.post(api(pid, "/files/mkdir"), json={"path": str(path), **body})


def _upload(client, pid, target_dir, name=b"", data=b"hi", **form):
    return client.post(
        api(pid, "/files/upload"),
        data={"dir": str(target_dir), **form},
        files=[("files", (name or "up.txt", data, "text/plain"))],
    )


# ---- task-scoped mutation (the open Task page carries ``chat_id=task:{id}``) ----


def test_read_write_task_folder_is_mutable_via_task_token(paths, tmp_path):
    media = tmp_path / "media"
    media.mkdir()
    client, pid = _client(paths)
    with client:
        f = _register_folder(client, media)
        _grant(client, f["id"], pid, "read_write", task_id="task-1")  # task-only read+write
        # the task page can create a directory inside it...
        assert _mkdir(client, pid, media / "clips", chat_id="task:task-1").status_code == 200
        assert (media / "clips").is_dir()
        # ...but the plain profile scope (no token) can't reach the folder at all
        assert _mkdir(client, pid, media / "nope").status_code in (400, 404)


def test_read_only_task_folder_rejects_mutation(paths, tmp_path):
    media = tmp_path / "media"
    media.mkdir()
    client, pid = _client(paths)
    with client:
        f = _register_folder(client, media)
        _grant(client, f["id"], pid, "read", task_id="task-1")  # read-only for the task
        r = _mkdir(client, pid, media / "clips", chat_id="task:task-1")
        assert r.status_code == 403  # visible + browsable, but not writable
        assert not (media / "clips").exists()


# ---- edit in place (ADR 0011 optimistic concurrency) ----


def test_read_write_folder_file_edits_in_place_with_etag(paths, tmp_path):
    repo = tmp_path / "acme"
    repo.mkdir()
    doc = repo / "notes.md"
    doc.write_text("v1")
    client, pid = _client(paths)
    with client:
        f = _register_folder(client, repo)
        _grant(client, f["id"], pid, "read_write")
        etag = _get(client, pid, doc).headers.get("etag", "").strip('"')
        r = _put(client, pid, doc, "v2", if_match=etag)
        assert r.status_code == 200
        assert doc.read_text() == "v2"
        # a stale If-Match is a 409 conflict; the file is left untouched
        conflict = _put(client, pid, doc, "v3", if_match=etag)
        assert conflict.status_code == 409
        assert doc.read_text() == "v2"


def test_folder_listing_advertises_level_mode(paths, tmp_path):
    # The Directory listing carries THIS level's resolved mode so the tree derives its
    # rows' write affordances from the Grant that covers it — a read_write Folder nested
    # under a read root reads as writable when descended into (ticket 04).
    root = tmp_path / "root"
    (root / "inner").mkdir(parents=True)
    (root / "inner" / "f.txt").write_text("x")
    client, pid = _client(paths)
    with client:
        fr = _register_folder(client, root)
        fi = _register_folder(client, root / "inner")
        _grant(client, fr["id"], pid, "read")  # outer root: read-only
        _grant(client, fi["id"], pid, "read_write")  # nested: read_write
        listing = client.get(api(pid, "/files"), params={"path": str(root / "inner")}).json()
        assert listing["mode"] == "read_write"
        outer = client.get(api(pid, "/files"), params={"path": str(root)}).json()
        assert outer["mode"] == "read"


def test_get_advertises_folder_file_mode(paths, tmp_path):
    repo = tmp_path / "acme"
    repo.mkdir()
    (repo / "a.md").write_text("x")
    client, pid = _client(paths)
    with client:
        f = _register_folder(client, repo)
        _grant(client, f["id"], pid, "read")
        assert _get(client, pid, repo / "a.md").headers.get("X-File-Mode") == "read"
        _grant(client, f["id"], pid, "read_write")
        assert _get(client, pid, repo / "a.md").headers.get("X-File-Mode") == "read_write"


def test_read_only_folder_file_denies_write(paths, tmp_path):
    repo = tmp_path / "acme"
    repo.mkdir()
    doc = repo / "readme.md"
    doc.write_text("keep")
    client, pid = _client(paths)
    with client:
        f = _register_folder(client, repo)
        _grant(client, f["id"], pid, "read")  # read-only Grant
        r = _put(client, pid, doc, "hacked")
        assert r.status_code == 403
        assert doc.read_text() == "keep"


def test_non_granted_folder_write_is_404(paths, tmp_path):
    repo = tmp_path / "acme"
    repo.mkdir()
    doc = repo / "x.md"
    doc.write_text("y")
    client, pid = _client(paths)
    with client:
        _register_folder(client, repo)  # registered but NOT granted
        assert _put(client, pid, doc, "z").status_code == 404
        assert doc.read_text() == "y"


# ---- delete ----


def test_read_write_folder_file_and_dir_delete(paths, tmp_path):
    repo = tmp_path / "acme"
    (repo / "sub").mkdir(parents=True)
    (repo / "sub" / "a.txt").write_text("a")
    (repo / "top.txt").write_text("t")
    client, pid = _client(paths)
    with client:
        f = _register_folder(client, repo)
        _grant(client, f["id"], pid, "read_write")
        assert _delete(client, pid, repo / "top.txt").status_code == 200
        assert not (repo / "top.txt").exists()
        # a Directory deletes recursively; the emptied parent is pruned up to the root
        assert _delete(client, pid, repo / "sub").status_code == 200
        assert not (repo / "sub").exists()
        assert repo.is_dir()  # the Folder root itself is never removed


def test_read_only_folder_file_denies_delete(paths, tmp_path):
    repo = tmp_path / "acme"
    repo.mkdir()
    doc = repo / "keep.txt"
    doc.write_text("k")
    client, pid = _client(paths)
    with client:
        f = _register_folder(client, repo)
        _grant(client, f["id"], pid, "read")
        assert _delete(client, pid, doc).status_code == 403
        assert doc.exists()


# ---- move / rename within the subtree ----


def test_read_write_folder_file_renames_and_moves_within_subtree(paths, tmp_path):
    repo = tmp_path / "acme"
    (repo / "sub").mkdir(parents=True)
    doc = repo / "old.md"
    doc.write_text("body")
    client, pid = _client(paths)
    with client:
        f = _register_folder(client, repo)
        _grant(client, f["id"], pid, "read_write")
        # rename in place
        assert _move(client, pid, doc, repo / "new.md").status_code == 200
        assert (repo / "new.md").read_text() == "body" and not doc.exists()
        # move into a nested Directory
        assert _move(client, pid, repo / "new.md", repo / "sub" / "new.md").status_code == 200
        assert (repo / "sub" / "new.md").read_text() == "body"


def test_move_out_of_readable_root_is_rejected(paths, tmp_path):
    # Two separate granted Folders: a move from one to the other is cross-Root → rejected.
    repo = tmp_path / "acme"
    other = tmp_path / "beta"
    repo.mkdir()
    other.mkdir()
    doc = repo / "f.md"
    doc.write_text("data")
    client, pid = _client(paths)
    with client:
        fa = _register_folder(client, repo)
        fb = _register_folder(client, other)
        _grant(client, fa["id"], pid, "read_write")
        _grant(client, fb["id"], pid, "read_write")
        # target under a DIFFERENT readable root → no cross-Root move
        assert _move(client, pid, doc, other / "f.md").status_code == 400
        assert doc.exists() and not (other / "f.md").exists()
        # target with a ..-escape outside the source root → also rejected
        assert _move(client, pid, doc, repo / ".." / "beta" / "f.md").status_code == 400
        # target back into the Files space (a relative path) → rejected
        assert _move(client, pid, doc, "stolen.md").status_code == 400


def test_read_only_folder_denies_move(paths, tmp_path):
    repo = tmp_path / "acme"
    repo.mkdir()
    doc = repo / "f.md"
    doc.write_text("d")
    client, pid = _client(paths)
    with client:
        f = _register_folder(client, repo)
        _grant(client, f["id"], pid, "read")
        assert _move(client, pid, doc, repo / "g.md").status_code == 403
        assert doc.exists() and not (repo / "g.md").exists()


# ---- chat_id scoping (a chat-only read_write Grant) ----


def test_write_is_chat_scoped(paths, tmp_path):
    repo = tmp_path / "acme"
    repo.mkdir()
    doc = repo / "c.md"
    doc.write_text("orig")
    client, pid = _client(paths)
    with client:
        f = _register_folder(client, repo)
        _grant(client, f["id"], pid, "read_write", chat_id="c1")  # chat-ONLY read_write
        # allowed in the granting chat
        assert _put(client, pid, doc, "edited", chat_id="c1").status_code == 200
        assert doc.read_text() == "edited"
        # denied in another chat, and with no chat_id (no profile-level grant)
        assert _put(client, pid, doc, "x", chat_id="c2").status_code == 404
        assert _put(client, pid, doc, "x").status_code == 404
        assert doc.read_text() == "edited"


def test_chat_narrows_profile_write_to_read(paths, tmp_path):
    # Profile-level read_write, narrowed to read for one chat → that chat can't write.
    repo = tmp_path / "acme"
    repo.mkdir()
    doc = repo / "n.md"
    doc.write_text("base")
    client, pid = _client(paths)
    with client:
        f = _register_folder(client, repo)
        _grant(client, f["id"], pid, "read_write")  # profile scope: writable
        _grant(client, f["id"], pid, "read", chat_id="c1")  # narrowed in c1
        assert _put(client, pid, doc, "ok").status_code == 200  # no chat → profile grant
        assert _put(client, pid, doc, "no", chat_id="c1").status_code == 403  # narrowed
        assert doc.read_text() == "ok"


# ---- mkdir & upload into a read_write Folder (ticket 05) ----


def test_read_write_folder_mkdir_and_upload(paths, tmp_path):
    repo = tmp_path / "acme"
    repo.mkdir()
    client, pid = _client(paths)
    with client:
        f = _register_folder(client, repo)
        _grant(client, f["id"], pid, "read_write")
        # create a new Directory inside the Folder
        assert _mkdir(client, pid, repo / "docs").status_code == 200
        assert (repo / "docs").is_dir()
        # upload a file into it
        r = _upload(client, pid, repo / "docs", name="note.txt", data=b"body")
        assert r.status_code == 200
        assert (repo / "docs" / "note.txt").read_text() == "body"


def test_read_only_folder_denies_mkdir_and_upload(paths, tmp_path):
    repo = tmp_path / "acme"
    repo.mkdir()
    client, pid = _client(paths)
    with client:
        f = _register_folder(client, repo)
        _grant(client, f["id"], pid, "read")
        assert _mkdir(client, pid, repo / "docs").status_code == 403
        assert not (repo / "docs").exists()
        assert _upload(client, pid, repo / "sub").status_code == 403


def test_non_granted_folder_denies_mkdir_and_upload(paths, tmp_path):
    repo = tmp_path / "acme"
    repo.mkdir()
    client, pid = _client(paths)
    with client:
        _register_folder(client, repo)  # registered but NOT granted
        assert _mkdir(client, pid, repo / "docs").status_code == 400
        assert _upload(client, pid, repo / "docs").status_code == 400
        assert not (repo / "docs").exists()


def test_folder_mkdir_and_upload_confined_to_subtree(paths, tmp_path):
    repo = tmp_path / "acme"
    repo.mkdir()
    client, pid = _client(paths)
    with client:
        f = _register_folder(client, repo)
        _grant(client, f["id"], pid, "read_write")
        # a ..-escape target resolves outside the readable root → rejected, nothing created
        assert _mkdir(client, pid, repo / ".." / "escape").status_code == 400
        assert not (tmp_path / "escape").exists()
        assert _upload(client, pid, repo / "..").status_code in (400, 403)
        assert not (tmp_path / "up.txt").exists()


def test_folder_mkdir_upload_is_chat_scoped(paths, tmp_path):
    repo = tmp_path / "acme"
    repo.mkdir()
    client, pid = _client(paths)
    with client:
        f = _register_folder(client, repo)
        _grant(client, f["id"], pid, "read_write", chat_id="c1")  # chat-ONLY read_write
        assert _mkdir(client, pid, repo / "d", chat_id="c1").status_code == 200
        # another chat / no chat has no covering grant → the target root isn't readable
        assert _mkdir(client, pid, repo / "e", chat_id="c2").status_code == 400
        assert _mkdir(client, pid, repo / "e").status_code == 400
        assert not (repo / "e").exists()


# ---- regression: relative (Files-space) mutations unchanged ----


def test_relative_mutations_still_hit_files_space(paths, tmp_path):
    client, pid = _client(paths)
    with client:
        client.post(
            api(pid, "/files/upload"),
            files=[("files", ("report.txt", b"hello", "text/plain"))],
        )
        # edit in place
        etag = _get(client, pid, "report.txt").headers.get("etag", "").strip('"')
        assert _put(client, pid, "report.txt", "bye", if_match=etag).status_code == 200
        # rename
        assert _move(client, pid, "report.txt", "renamed.txt").status_code == 200
        # delete
        assert _delete(client, pid, "renamed.txt").status_code == 200
        # a relative traversal escape stays the sandbox 400/404, not a Folder lookup
        assert _put(client, pid, "../../etc/passwd", "x").status_code == 400
