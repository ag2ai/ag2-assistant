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
