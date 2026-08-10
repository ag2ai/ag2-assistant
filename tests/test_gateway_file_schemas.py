"""Phase-5 routes answer bodies their response models accept.

The 16 routes here — the install-wide Folder registry and the profile's file
surfaces — run in the two states that stress a model. Empty first: a fresh install
has no Folder, no Grant, an empty Files space and no Thread mentioning anything,
and a model that made any of those fields required would turn a green route into a
500. Then populated, because the model is the contract, so a key it forgot to
declare vanishes from the wire silently rather than failing loudly.

Two shapes get particular attention, since a union is where a model most easily
loses data: ``GET /files`` answers one branch or the other depending on whether the
requested path is absolute, and ``/files/mentions`` answers rows discriminated on
``kind`` — a run row carries every field a chat row does plus three of its own.
"""

import json

import pytest
from fastapi.testclient import TestClient

from tests.support.apps import api, make_profile_app

FOLDER_KEYS = {"id", "name", "path", "exists", "grants"}
GRANT_KEYS = {"profile", "chat_id", "task_id", "mode"}
ROOT_KEYS = {"id", "name", "path", "mode", "exists"}
FILE_ROW_KEYS = {"path", "name", "dir", "size", "modified"}
SEARCH_HIT_KEYS = {"path", "name", "dir", "kind"}
CHAT_MENTION_KEYS = {"stream_id", "kind", "title", "updated"}
RUN_MENTION_KEYS = CHAT_MENTION_KEYS | {"task_id", "task_name", "run_started_at"}


@pytest.fixture
def app_client(paths):
    """A started single-profile app: the Files space is empty and no Folder is
    registered until a test says otherwise."""
    app, pid = make_profile_app(paths, persist=True)
    with TestClient(app) as client:
        yield client, pid


def _register(client, path, name=""):
    body = {"path": str(path)}
    if name:
        body["name"] = name
    return client.post("/api/folders", json=body)


def _grant(client, fid, pid, mode="read", **scope):
    return client.post(f"/api/folders/{fid}/grants", json={"profile": pid, "mode": mode, **scope})


def _granted_folder(client, pid, root, *, name="Acme", mode="read"):
    """Register ``root`` as a Folder and grant this profile ``mode`` on it."""
    fid = _register(client, root, name=name).json()["folder"]["id"]
    _grant(client, fid, pid, mode)
    return fid


async def _seed_transcript(gw, sid, text, title=None):
    """Write a display-transcript doc straight onto the event store, so a mention
    scan sees it without an LLM turn (the idiom test_gateway_files_mentions uses)."""
    doc = {
        "chat_id": sid,
        "messages": [{"role": "user", "text": text}],
        "updated": "2026-01-01T00:00:00+00:00",
        "title": title,
    }
    await gw._event_store.write(gw._transcript_path(sid), json.dumps(doc))


# ---- folders: the install-wide registry ----


def test_the_folder_list_is_well_shaped_on_a_fresh_install(app_client):
    client, _pid = app_client
    assert client.get("/api/folders").json() == {"folders": []}


def test_a_created_folder_comes_back_with_the_snapshot_around_it(app_client, tmp_path):
    """Create answers ok + the changed Folder + every Folder, because the form
    re-renders that one row inside the list it belongs to."""
    client, _pid = app_client
    repo = tmp_path / "acme"
    repo.mkdir()
    body = _register(client, repo, name="Acme").json()
    assert body["ok"] is True
    assert set(body["folder"]) == FOLDER_KEYS
    assert body["folder"]["name"] == "Acme" and body["folder"]["exists"] is True
    assert body["folder"]["grants"] == []
    assert [f["id"] for f in body["folders"]] == [body["folder"]["id"]]


def test_a_grant_declares_every_field_the_settings_row_renders(app_client, tmp_path):
    client, pid = app_client
    repo = tmp_path / "acme"
    repo.mkdir()
    fid = _register(client, repo).json()["folder"]["id"]
    body = _grant(client, fid, pid, "read_write", chat_id="c1").json()
    assert body["ok"] is True and "folder" not in body
    (grant,) = body["folders"][0]["grants"]
    assert set(grant) == GRANT_KEYS
    assert grant == {"profile": pid, "chat_id": "c1", "task_id": "", "mode": "read_write"}


def test_a_none_grant_survives_the_model(app_client, tmp_path):
    """``none`` is override-only — a chat-scoped block over an inherited Folder —
    and it is a real member of the mode enum, not an error the model may drop."""
    client, pid = app_client
    repo = tmp_path / "acme"
    repo.mkdir()
    fid = _register(client, repo).json()["folder"]["id"]
    _grant(client, fid, pid, "read")
    body = _grant(client, fid, pid, "none", chat_id="c1").json()
    modes = {g["mode"] for g in body["folders"][0]["grants"]}
    assert modes == {"read", "none"}


def test_an_updated_folder_echoes_the_change_and_the_snapshot(app_client, tmp_path):
    client, _pid = app_client
    repo = tmp_path / "acme"
    repo.mkdir()
    fid = _register(client, repo).json()["folder"]["id"]
    body = client.post(f"/api/folders/{fid}", json={"name": "Renamed"}).json()
    assert body["ok"] is True and body["folder"]["name"] == "Renamed"
    assert [f["name"] for f in body["folders"]] == ["Renamed"]


def test_a_missing_directory_stays_a_badged_folder(app_client, tmp_path):
    """A Folder whose directory went away is registered and repointable, so
    ``exists`` has to reach the client rather than the row being dropped."""
    client, _pid = app_client
    gone = tmp_path / "gone"
    gone.mkdir()
    _register(client, gone)
    gone.rmdir()
    (row,) = client.get("/api/folders").json()["folders"]
    assert row["exists"] is False and set(row) == FOLDER_KEYS


def test_a_path_conflict_points_at_the_folder_holding_it(app_client, tmp_path):
    """409 carries the whole colliding Folder, not just a message: the client
    offers that one instead of reporting a dead end."""
    client, _pid = app_client
    repo = tmp_path / "acme"
    repo.mkdir()
    first = _register(client, repo, name="Acme").json()["folder"]
    clash = _register(client, repo, name="Again")
    assert clash.status_code == 409
    body = clash.json()
    assert body["error"]
    assert set(body["existing"]) == FOLDER_KEYS
    assert body["existing"]["id"] == first["id"]


def test_delete_and_revoke_answer_the_snapshot_alone(app_client, tmp_path):
    """Neither can echo the row it touched — a delete removed it, and a revoke may
    have garbage-collected the Folder along with its last Grant."""
    client, pid = app_client
    repo = tmp_path / "acme"
    repo.mkdir()
    fid = _register(client, repo).json()["folder"]["id"]
    _grant(client, fid, pid, "read")
    revoked = client.request("DELETE", f"/api/folders/{fid}/grants", json={"profile": pid}).json()
    assert revoked == {"ok": True, "folders": []}  # last grant gone → Folder collected

    fid = _register(client, repo).json()["folder"]["id"]
    assert client.delete(f"/api/folders/{fid}").json() == {"ok": True, "folders": []}


# ---- folders/roots: the Thread-scoped section of the tree ----


def test_folder_roots_are_empty_without_a_grant(app_client):
    client, pid = app_client
    assert client.get(api(pid, "/folders/roots")).json() == {"roots": []}


def test_each_folder_root_declares_what_the_tree_renders(app_client, tmp_path):
    client, pid = app_client
    repo = tmp_path / "acme"
    repo.mkdir()
    _granted_folder(client, pid, repo, name="Acme", mode="read_write")
    (root,) = client.get(api(pid, "/folders/roots")).json()["roots"]
    assert set(root) == ROOT_KEYS
    assert root["name"] == "Acme" and root["path"] == str(repo)
    # The EFFECTIVE mode, so `none` can never appear here.
    assert root["mode"] == "read_write" and root["exists"] is True


# ---- files: the two branches of one route ----


def test_an_empty_files_space_still_answers_the_full_shape(app_client):
    client, pid = app_client
    body = client.get(api(pid, "/files")).json()
    assert set(body) == {"root", "files", "dirs"}
    assert body["root"] and body["files"] == [] and body["dirs"] == []


def test_every_file_row_declares_the_fields_the_browser_renders(app_client):
    client, pid = app_client
    client.post(
        api(pid, "/files/upload"),
        files=[("files", ("report.txt", b"hello", "text/plain"))],
        data={"dir": "docs"},
    )
    body = client.get(api(pid, "/files")).json()
    assert [set(row) for row in body["files"]] == [FILE_ROW_KEYS]
    assert body["files"][0]["dir"] == "docs" and body["files"][0]["size"] == 5
    assert body["dirs"] == ["docs"]  # a flat list of relative paths, not objects


def test_an_absolute_path_takes_the_other_branch_of_the_union(app_client, tmp_path):
    """The same route, a different body: a Folder listing carries `path` and the
    level's own resolved `mode` instead of `root`, and its rows are objects."""
    client, pid = app_client
    repo = tmp_path / "acme"
    (repo / "src").mkdir(parents=True)
    (repo / "README.md").write_text("hi")
    _granted_folder(client, pid, repo)
    body = client.get(api(pid, "/files"), params={"path": str(repo)}).json()
    assert set(body) == {"path", "dirs", "files", "mode"}
    assert body["path"] == str(repo) and body["mode"] == "read"
    assert [set(d) for d in body["dirs"]] == [{"name", "path"}]
    assert [set(f) for f in body["files"]] == [{"name", "path", "size"}]
    assert body["files"][0]["path"] == str(repo / "README.md")  # absolute, not relative


# ---- files/search: the @-picker corpus ----


def test_a_blank_search_is_an_empty_list(app_client):
    client, pid = app_client
    assert client.get(api(pid, "/files/search"), params={"q": ""}).json() == {"results": []}


def test_a_search_hit_carries_its_kind_and_an_absolute_path(app_client, tmp_path):
    client, pid = app_client
    repo = tmp_path / "acme"
    (repo / "notes").mkdir(parents=True)
    (repo / "notes" / "notes.md").write_text("x")
    _granted_folder(client, pid, repo)
    hits = client.get(api(pid, "/files/search"), params={"q": "notes"}).json()["results"]
    assert hits and all(set(h) == SEARCH_HIT_KEYS for h in hits)
    assert {h["kind"] for h in hits} <= {"file", "directory"}
    assert all(h["path"].startswith(str(repo)) for h in hits)


# ---- files/mentions: the discriminated rows ----


def test_mentions_are_empty_for_a_file_nothing_talks_about(app_client):
    client, pid = app_client
    assert client.get(api(pid, "/files/mentions"), params={"path": "a.md"}).json() == {
        "threads": []
    }


def test_a_chat_mention_declares_the_four_fields_of_its_branch(app_client):
    client, pid = app_client
    gw = client.app.state.profiles.get(pid).gateway
    client.portal.call(_seed_transcript, gw, "c1", "look at reports/a.md please")
    (row,) = client.get(api(pid, "/files/mentions"), params={"path": "reports/a.md"}).json()[
        "threads"
    ]
    assert set(row) == CHAT_MENTION_KEYS
    assert row["kind"] == "chat" and row["stream_id"] == "c1"


def test_a_run_mention_keeps_the_three_fields_only_its_branch_has(app_client):
    """A run row is a chat row plus task_id, task_name and run_started_at — the
    three keys a model that collapsed the two branches into one would drop, and the
    popover has nothing to say about which Task the run belongs to without them."""
    client, pid = app_client
    gw = client.app.state.profiles.get(pid).gateway
    client.portal.call(_seed_transcript, gw, "task-run:r1", "produced reports/a.md", "Nightly")
    (row,) = client.get(api(pid, "/files/mentions"), params={"path": "reports/a.md"}).json()[
        "threads"
    ]
    assert set(row) == RUN_MENTION_KEYS
    assert row["kind"] == "run" and row["stream_id"] == "task-run:r1"


# ---- the mutations ----


def test_upload_answers_the_names_it_actually_saved(app_client):
    """An upload auto-suffixes rather than overwrite, so the saved names are the
    only place the client learns what its files ended up called."""
    client, pid = app_client
    send = lambda: client.post(  # noqa: E731
        api(pid, "/files/upload"), files=[("files", ("a.txt", b"x", "text/plain"))]
    )
    assert send().json() == {"ok": True, "saved": ["a.txt"]}
    assert send().json() == {"ok": True, "saved": ["a (2).txt"]}


def test_mkdir_answers_the_path_it_created(app_client):
    client, pid = app_client
    assert client.post(api(pid, "/files/mkdir"), json={"path": "reports"}).json() == {
        "ok": True,
        "path": "reports",
    }


def test_move_answers_a_bare_acknowledgement(app_client):
    client, pid = app_client
    client.post(api(pid, "/files/upload"), files=[("files", ("a.txt", b"x", "text/plain"))])
    assert client.post(api(pid, "/files/move"), json={"from": "a.txt", "to": "b.txt"}).json() == {
        "ok": True
    }


def test_an_in_place_write_answers_its_new_content_token(app_client):
    """The etag rides the body as well as the header, and the client prefers the
    body — so it has to survive the model."""
    client, pid = app_client
    client.post(api(pid, "/files/upload"), files=[("files", ("a.txt", b"x", "text/plain"))])
    r = client.put(api(pid, "/files/raw"), params={"path": "a.txt"}, content=b"rewritten")
    body = r.json()
    assert set(body) == {"ok", "etag"}
    assert body["ok"] is True and body["etag"]
    assert r.headers["ETag"].strip('"') == body["etag"]


def test_delete_answers_a_bare_acknowledgement(app_client):
    client, pid = app_client
    client.post(api(pid, "/files/upload"), files=[("files", ("a.txt", b"x", "text/plain"))])
    r = client.request("DELETE", api(pid, "/files/raw"), params={"path": "a.txt"})
    assert r.json() == {"ok": True}


def test_serving_a_file_is_untouched_by_the_rollout(app_client):
    """``response_model=None`` is the hatch for a non-JSON route: the GET still
    answers raw bytes with its ETag and mode headers, not a validated body."""
    client, pid = app_client
    client.post(api(pid, "/files/upload"), files=[("files", ("a.txt", b"hello", "text/plain"))])
    r = client.get(api(pid, "/files/raw"), params={"path": "a.txt"})
    assert r.status_code == 200 and r.content == b"hello"
    assert r.headers["ETag"] and r.headers["X-File-Mode"] == "read_write"
