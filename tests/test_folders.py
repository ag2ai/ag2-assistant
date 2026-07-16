"""FolderStore — the install-wide Folder registry + Grants (ADR 0006)."""

import pytest

from assistant.folders import READ, READ_WRITE, DuplicatePath, FolderStore


def _store(tmp_path):
    return FolderStore(path=tmp_path / "folders.json")


def test_create_folder_auto_names_from_basename(tmp_path):
    store = _store(tmp_path)
    d = tmp_path / "acme"
    d.mkdir()
    view = store.create_folder(str(d))
    assert view["name"] == "acme"
    assert view["path"] == str(d.resolve())
    assert view["id"].startswith("f_")
    assert view["exists"] is True
    assert view["grants"] == []


def test_create_folder_rejects_duplicate_path(tmp_path):
    store = _store(tmp_path)
    d = tmp_path / "acme"
    d.mkdir()
    first = store.create_folder(str(d), name="acme")
    with pytest.raises(DuplicatePath) as exc:
        store.create_folder(str(d) + "/", name="other")  # same resolved path
    assert exc.value.existing["id"] == first["id"]


def test_create_folder_requires_path(tmp_path):
    with pytest.raises(ValueError):
        _store(tmp_path).create_folder("")


def test_update_folder_renames_and_repoints(tmp_path):
    store = _store(tmp_path)
    a, b = tmp_path / "a", tmp_path / "b"
    a.mkdir()
    b.mkdir()
    f = store.create_folder(str(a))
    v = store.update_folder(f["id"], name="renamed")
    assert v["name"] == "renamed" and v["path"] == str(a.resolve())
    v = store.update_folder(f["id"], path=str(b))
    assert v["path"] == str(b.resolve())
    with pytest.raises(KeyError):
        store.update_folder("f_nope", name="x")


def test_update_folder_rejects_duplicate_path(tmp_path):
    store = _store(tmp_path)
    a, b = tmp_path / "a", tmp_path / "b"
    a.mkdir()
    b.mkdir()
    store.create_folder(str(a))
    f2 = store.create_folder(str(b))
    with pytest.raises(DuplicatePath):
        store.update_folder(f2["id"], path=str(a))


def test_missing_path_is_badged_not_an_error(tmp_path):
    store = _store(tmp_path)
    d = tmp_path / "gone"
    d.mkdir()
    f = store.create_folder(str(d))
    d.rmdir()
    assert store.get_folder(f["id"])["exists"] is False


def test_grants_upsert_and_revoke(tmp_path):
    store = _store(tmp_path)
    d = tmp_path / "acme"
    d.mkdir()
    f = store.create_folder(str(d))
    store.set_grant(f["id"], READ, profile="work")
    store.set_grant(f["id"], READ_WRITE, profile="work")  # upsert, not duplicate
    grants = store.get_folder(f["id"])["grants"]
    assert grants == [{"profile": "work", "chat_id": "", "mode": READ_WRITE}]
    assert store.revoke_grant(f["id"], profile="work") is True
    assert store.revoke_grant(f["id"], profile="work") is False
    with pytest.raises(KeyError):
        store.set_grant("f_nope", READ, profile="work")
    with pytest.raises(ValueError):
        store.set_grant(f["id"], "execute", profile="work")
    with pytest.raises(ValueError):
        store.set_grant(f["id"], READ, profile="")


def test_delete_folder_revokes_all_grants(tmp_path):
    store = _store(tmp_path)
    d = tmp_path / "acme"
    d.mkdir()
    f = store.create_folder(str(d))
    store.set_grant(f["id"], READ, profile="work")
    store.set_grant(f["id"], READ_WRITE, profile="work", chat_id="c1")
    assert store.delete_folder(f["id"]) is True
    assert store.delete_folder(f["id"]) is False
    assert store.mode_for(d, "work") is None
    assert store.mode_for(d, "work", chat_id="c1") is None


def test_mode_for_covers_subpaths(tmp_path):
    store = _store(tmp_path)
    d = tmp_path / "repos"
    (d / "acme" / "src").mkdir(parents=True)
    f = store.create_folder(str(d))
    store.set_grant(f["id"], READ, profile="work")
    assert store.mode_for(d / "acme" / "src", "work") == READ
    assert store.mode_for(tmp_path, "work") is None  # parent of a grant: not covered
    assert store.mode_for(d, "other-profile") is None


def test_mode_for_union_most_permissive_wins(tmp_path):
    store = _store(tmp_path)
    repos = tmp_path / "repos"
    acme = repos / "acme"
    acme.mkdir(parents=True)
    f_repos = store.create_folder(str(repos))
    f_acme = store.create_folder(str(acme))
    # read on the parent, read_write on the child: child subtree is read_write
    store.set_grant(f_repos["id"], READ, profile="work")
    store.set_grant(f_acme["id"], READ_WRITE, profile="work")
    assert store.mode_for(acme, "work") == READ_WRITE
    assert store.mode_for(repos, "work") == READ
    # flipped: read_write on the parent covers the child (grants only widen)
    store.set_grant(f_repos["id"], READ_WRITE, profile="work")
    store.set_grant(f_acme["id"], READ, profile="work")
    assert store.mode_for(acme, "work") == READ_WRITE


def test_mode_for_chat_grants_union_with_profile(tmp_path):
    store = _store(tmp_path)
    a, b = tmp_path / "a", tmp_path / "b"
    a.mkdir()
    b.mkdir()
    fa = store.create_folder(str(a))
    fb = store.create_folder(str(b))
    store.set_grant(fa["id"], READ, profile="work")  # profile-wide
    store.set_grant(fb["id"], READ_WRITE, profile="work", chat_id="c1")  # one chat
    # in chat c1: both apply (profile ∪ chat)
    assert store.mode_for(a, "work", chat_id="c1") == READ
    assert store.mode_for(b, "work", chat_id="c1") == READ_WRITE
    # in another chat: only the profile grant applies
    assert store.mode_for(b, "work", chat_id="c2") is None
    # with no chat context: only profile grants apply
    assert store.mode_for(b, "work") is None


def test_grant_path_finds_or_creates_by_path(tmp_path):
    store = _store(tmp_path)
    d = tmp_path / "acme"
    d.mkdir()
    v1 = store.grant_path(str(d), READ, profile="work")
    assert store.mode_for(d, "work") == READ
    # a second grant_path on the same path reuses the Folder, upgrades the grant
    v2 = store.grant_path(str(d), READ_WRITE, profile="work")
    assert v2["id"] == v1["id"]
    assert len(store.list_folders()) == 1
    assert store.mode_for(d, "work") == READ_WRITE


def test_persistence_and_fresh_store_reload(tmp_path):
    d = tmp_path / "acme"
    d.mkdir()
    store = _store(tmp_path)
    f = store.create_folder(str(d))
    store.set_grant(f["id"], READ_WRITE, profile="work")
    fresh = _store(tmp_path)
    assert fresh.mode_for(d, "work") == READ_WRITE
    assert fresh.list_folders()[0]["name"] == "acme"


def test_load_tolerates_non_object_json(tmp_path):
    p = tmp_path / "folders.json"
    p.write_text("[1, 2, 3]")
    store = FolderStore(path=p)
    assert store.list_folders() == []


def test_ephemeral_store_persists_nothing(tmp_path):
    d = tmp_path / "acme"
    d.mkdir()
    store = FolderStore(path=None)
    f = store.create_folder(str(d))
    store.set_grant(f["id"], READ, profile="work")
    assert store.mode_for(d, "work") == READ
    assert not (tmp_path / "folders.json").exists()
