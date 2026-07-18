"""HTTP contract for the user-writable Files space (ADR 0007): upload, mkdir,
move, and recursive Directory delete on the profile-scoped ``/files`` routes. Thin
by design — the mutation rules live in ``test_workspace.py``; here we lock the
route wiring, status codes, and per-profile scoping."""

from tests.conftest import api


def test_upload_lands_in_target_dir_and_shows_in_listing(profile_app):
    client, pid = profile_app
    # into a nested target Directory (created on the fly)
    r = client.post(
        api(pid, "/files/upload"),
        files=[("files", ("report.txt", b"hello", "text/plain"))],
        data={"dir": "docs"},
    )
    assert r.status_code == 200 and r.json()["saved"] == ["docs/report.txt"]
    listing = client.get(api(pid, "/files")).json()
    assert any(f["path"] == "docs/report.txt" for f in listing["files"])
    assert "docs" in listing["dirs"]


def test_upload_auto_suffixes_on_clash(profile_app):
    client, pid = profile_app
    up = lambda: client.post(  # noqa: E731
        api(pid, "/files/upload"), files=[("files", ("a.txt", b"x", "text/plain"))]
    )
    assert up().json()["saved"] == ["a.txt"]
    assert up().json()["saved"] == ["a (2).txt"]  # never overwrites


def test_upload_rejects_traversal_target(profile_app):
    client, pid = profile_app
    r = client.post(
        api(pid, "/files/upload"),
        files=[("files", ("a.txt", b"x", "text/plain"))],
        data={"dir": "../../etc"},
    )
    assert r.status_code == 400


def test_mkdir_creates_and_rejects_clash(profile_app):
    client, pid = profile_app
    r = client.post(api(pid, "/files/mkdir"), json={"path": "reports"})
    assert r.status_code == 200 and r.json()["path"] == "reports"
    assert "reports" in client.get(api(pid, "/files")).json()["dirs"]
    # already exists → 409 (no clobber)
    assert client.post(api(pid, "/files/mkdir"), json={"path": "reports"}).status_code == 409
    # traversal → 400
    assert client.post(api(pid, "/files/mkdir"), json={"path": "../nope"}).status_code == 400


def test_move_renames_and_clash_is_conflict(profile_app):
    client, pid = profile_app
    client.post(api(pid, "/files/upload"), files=[("files", ("a.txt", b"a", "text/plain"))])
    client.post(api(pid, "/files/upload"), files=[("files", ("b.txt", b"b", "text/plain"))])
    # rename a.txt → into a Directory
    r = client.post(api(pid, "/files/move"), json={"from": "a.txt", "to": "sub/a.txt"})
    assert r.status_code == 200 and r.json()["ok"] is True
    assert any(f["path"] == "sub/a.txt" for f in client.get(api(pid, "/files")).json()["files"])
    # clash onto existing b.txt → 409, source untouched
    client.post(api(pid, "/files/move"), json={"from": "sub/a.txt", "to": "x.txt"})
    client.post(api(pid, "/files/upload"), files=[("files", ("y.txt", b"y", "text/plain"))])
    conflict = client.post(api(pid, "/files/move"), json={"from": "x.txt", "to": "y.txt"})
    assert conflict.status_code == 409


def test_raw_serves_non_ascii_filename(profile_app):
    """A user upload keeps its original (possibly non-ASCII) name; serving it must
    RFC 5987-encode the Content-Disposition, not crash on latin-1 header encoding."""
    client, pid = profile_app
    name = "Снимок экрана — 2026.png"
    client.post(api(pid, "/files/upload"), files=[("files", (name, b"\x89PNG", "image/png"))])
    saved = client.get(api(pid, "/files")).json()["files"][0]["path"]
    r = client.get(api(pid, "/files/raw"), params={"path": saved})
    assert r.status_code == 200 and r.content == b"\x89PNG"
    assert "utf-8''" in r.headers.get("content-disposition", "")  # encoded, inline


def test_delete_directory_recursively(profile_app):
    client, pid = profile_app
    client.post(
        api(pid, "/files/upload"),
        files=[("files", ("f.txt", b"f", "text/plain"))],
        data={"dir": "d/sub"},
    )
    # delete the top Directory removes the whole subtree
    r = client.delete(api(pid, "/files/raw"), params={"path": "d"})
    assert r.status_code == 200
    listing = client.get(api(pid, "/files")).json()
    assert not any(f["path"].startswith("d/") for f in listing["files"])
    assert "d" not in listing["dirs"]


# ---- In-place editable writes: ETag / If-Match optimistic concurrency (ADR 0011) ----


def _put(client, pid, path, body, *, if_match=None):
    headers = {"If-Match": if_match} if if_match is not None else {}
    return client.put(api(pid, "/files/raw"), params={"path": path}, content=body, headers=headers)


def test_get_raw_emits_etag(profile_app):
    client, pid = profile_app
    client.post(api(pid, "/files/upload"), files=[("files", ("n.md", b"hi", "text/plain"))])
    r = client.get(api(pid, "/files/raw"), params={"path": "n.md"})
    assert r.status_code == 200 and r.headers.get("etag")


def test_put_with_correct_if_match_writes_and_returns_new_etag(profile_app):
    client, pid = profile_app
    client.post(api(pid, "/files/upload"), files=[("files", ("n.md", b"old", "text/plain"))])
    etag = client.get(api(pid, "/files/raw"), params={"path": "n.md"}).headers["etag"]
    r = _put(client, pid, "n.md", b"new contents", if_match=etag)
    assert r.status_code == 200
    new_etag = r.headers["etag"]
    assert new_etag and new_etag != etag
    # and the write took effect
    assert client.get(api(pid, "/files/raw"), params={"path": "n.md"}).content == b"new contents"


def test_get_then_put_then_stale_put_conflicts(profile_app):
    client, pid = profile_app
    client.post(api(pid, "/files/upload"), files=[("files", ("n.md", b"v0", "text/plain"))])
    etag = client.get(api(pid, "/files/raw"), params={"path": "n.md"}).headers["etag"]
    # first save with the fresh etag succeeds
    r1 = _put(client, pid, "n.md", b"v1", if_match=etag)
    assert r1.status_code == 200
    # the original etag is now stale — a second save with it is a 409, file untouched
    r2 = _put(client, pid, "n.md", b"v2", if_match=etag)
    assert r2.status_code == 409
    assert client.get(api(pid, "/files/raw"), params={"path": "n.md"}).content == b"v1"


def test_put_missing_path_is_404_and_creates_nothing(profile_app):
    client, pid = profile_app
    r = _put(client, pid, "ghost.md", b"x", if_match="whatever")
    assert r.status_code == 404
    assert not any(f["path"] == "ghost.md" for f in client.get(api(pid, "/files")).json()["files"])


def test_put_traversal_is_400(profile_app):
    client, pid = profile_app
    r = _put(client, pid, "../../evil.md", b"x", if_match="whatever")
    assert r.status_code == 400


def test_forced_put_without_if_match_overwrites(profile_app):
    client, pid = profile_app
    client.post(api(pid, "/files/upload"), files=[("files", ("n.md", b"old", "text/plain"))])
    r = _put(client, pid, "n.md", b"forced")  # no If-Match ⇒ force past the compare
    assert r.status_code == 200
    assert client.get(api(pid, "/files/raw"), params={"path": "n.md"}).content == b"forced"


def test_put_oversize_body_is_413(profile_app):
    from assistant.workspace import _MAX_WRITE_BYTES

    client, pid = profile_app
    client.post(api(pid, "/files/upload"), files=[("files", ("n.md", b"old", "text/plain"))])
    r = _put(client, pid, "n.md", b"x" * (_MAX_WRITE_BYTES + 1))
    assert r.status_code == 413
    # the oversize write is rejected before it lands — the file is untouched
    assert client.get(api(pid, "/files/raw"), params={"path": "n.md"}).content == b"old"


def test_put_non_utf8_body_is_400(profile_app):
    client, pid = profile_app
    client.post(api(pid, "/files/upload"), files=[("files", ("n.md", b"old", "text/plain"))])
    r = _put(client, pid, "n.md", b"\xff\xfe not utf-8")
    assert r.status_code == 400
    assert client.get(api(pid, "/files/raw"), params={"path": "n.md"}).content == b"old"


def test_put_to_directory_is_404(profile_app):
    client, pid = profile_app
    client.post(api(pid, "/files/mkdir"), json={"path": "sub"})
    r = _put(client, pid, "sub", b"x", if_match="whatever")
    assert r.status_code == 404


def test_put_is_per_profile_scoped(profile_app):
    client, pid = profile_app
    # a second profile, live immediately (its runtime boots on create)
    other = client.post("/api/profiles", json={"name": "Other", "accent": "#f95339"}).json()[
        "profile"
    ]["id"]
    # same relative path exists under both profiles with different content
    client.post(api(pid, "/files/upload"), files=[("files", ("shared.md", b"mine", "text/plain"))])
    client.post(
        api(other, "/files/upload"), files=[("files", ("shared.md", b"theirs", "text/plain"))]
    )
    # a forced write under `pid` must not touch `other`'s file
    assert _put(client, pid, "shared.md", b"changed", if_match=None).status_code == 200
    assert client.get(api(other, "/files/raw"), params={"path": "shared.md"}).content == b"theirs"
