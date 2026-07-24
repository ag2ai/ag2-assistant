"""FolderStore — the install-wide Folder registry + Grants (ADR 0006)."""

import pytest

from assistant.folders import NONE, READ, READ_WRITE, DuplicatePath, FolderStore


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
    assert grants == [{"profile": "work", "chat_id": "", "task_id": "", "mode": READ_WRITE}]
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


def test_revoking_last_grant_garbage_collects_folder(tmp_path):
    store = _store(tmp_path)
    d = tmp_path / "acme"
    d.mkdir()
    f = store.create_folder(str(d))
    store.set_grant(f["id"], READ, profile="work")
    store.set_grant(f["id"], READ_WRITE, profile="work", chat_id="c1")
    # First revoke leaves the profile grant → Folder survives.
    store.revoke_grant(f["id"], profile="work", chat_id="c1")
    assert store.get_folder(f["id"]) is not None
    # Revoking the last grant GCs the Folder from the registry.
    store.revoke_grant(f["id"], profile="work")
    assert store.get_folder(f["id"]) is None


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


def test_chat_grant_overrides_profile_mode_for_that_chat(tmp_path):
    store = _store(tmp_path)
    d = tmp_path / "acme"
    d.mkdir()
    f = store.create_folder(str(d))
    store.set_grant(f["id"], READ_WRITE, profile="work")  # profile: read+write
    store.set_grant(f["id"], READ, profile="work", chat_id="c1")  # this chat: narrowed to read
    assert store.mode_for(d, "work", chat_id="c1") == READ  # chat override wins (narrows)
    assert store.mode_for(d, "work", chat_id="c2") == READ_WRITE  # other chats untouched
    assert store.mode_for(d, "work") == READ_WRITE  # profile grant unchanged


def test_chat_none_blocks_profile_folder_for_that_chat_only(tmp_path):
    store = _store(tmp_path)
    d = tmp_path / "acme"
    d.mkdir()
    f = store.create_folder(str(d))
    store.set_grant(f["id"], READ, profile="work")  # profile-wide read
    store.set_grant(f["id"], NONE, profile="work", chat_id="c1")  # blocked in c1
    assert store.mode_for(d, "work", chat_id="c1") is None  # this chat: no access
    assert store.mode_for(d, "work", chat_id="c2") == READ  # other chats keep it
    assert store.mode_for(d, "work") == READ  # profile grant intact


def test_chat_grant_can_widen_profile_mode(tmp_path):
    store = _store(tmp_path)
    d = tmp_path / "acme"
    d.mkdir()
    f = store.create_folder(str(d))
    store.set_grant(f["id"], READ, profile="work")
    store.set_grant(f["id"], READ_WRITE, profile="work", chat_id="c1")
    assert store.mode_for(d, "work", chat_id="c1") == READ_WRITE
    assert store.mode_for(d, "work", chat_id="c2") == READ


def test_none_block_on_parent_spares_independent_child_grant(tmp_path):
    store = _store(tmp_path)
    repos = tmp_path / "repos"
    acme = repos / "acme"
    acme.mkdir(parents=True)
    f_repos = store.create_folder(str(repos))
    f_acme = store.create_folder(str(acme))
    store.set_grant(f_repos["id"], READ, profile="work")
    store.set_grant(f_acme["id"], READ, profile="work")
    store.set_grant(
        f_repos["id"], NONE, profile="work", chat_id="c1"
    )  # block only the parent folder
    assert (
        store.mode_for(repos / "other", "work", chat_id="c1") is None
    )  # covered only by blocked parent
    assert store.mode_for(acme, "work", chat_id="c1") == READ  # child grant survives


def test_none_mode_rejected_at_profile_scope(tmp_path):
    store = _store(tmp_path)
    d = tmp_path / "acme"
    d.mkdir()
    f = store.create_folder(str(d))
    with pytest.raises(ValueError):
        store.set_grant(f["id"], NONE, profile="work")  # no chat_id: meaningless block


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


def test_task_grant_applies_to_task_scope_only(tmp_path):
    store = _store(tmp_path)
    d = tmp_path / "data"
    d.mkdir()
    f = store.create_folder(str(d))
    store.set_grant(f["id"], READ_WRITE, profile="work", task_id="task-1")
    assert store.mode_for(d, "work", task_id="task-1") == READ_WRITE
    assert store.mode_for(d, "work") is None  # профиль без гранта
    assert store.mode_for(d, "work", task_id="task-2") is None  # другая таска
    assert store.mode_for(d, "work", chat_id="web-1") is None  # чат вне таски


def test_resolution_chain_chat_over_task_over_profile(tmp_path):
    store = _store(tmp_path)
    d = tmp_path / "data"
    d.mkdir()
    f = store.create_folder(str(d))
    store.set_grant(f["id"], READ, profile="work")  # профиль: read
    store.set_grant(f["id"], READ_WRITE, profile="work", task_id="task-1")  # таска расширяет
    assert store.mode_for(d, "work", chat_id="task-run:r1", task_id="task-1") == READ_WRITE
    store.set_grant(f["id"], NONE, profile="work", chat_id="task-run:r1")  # чат блокирует
    assert store.mode_for(d, "work", chat_id="task-run:r1", task_id="task-1") is None
    # другой ран той же таски не затронут чатовым override'ом
    assert store.mode_for(d, "work", chat_id="task-run:r2", task_id="task-1") == READ_WRITE


def test_task_none_blocks_profile_folder_for_that_task(tmp_path):
    store = _store(tmp_path)
    d = tmp_path / "data"
    d.mkdir()
    f = store.create_folder(str(d))
    store.set_grant(f["id"], READ, profile="work")
    store.set_grant(f["id"], NONE, profile="work", task_id="task-1")
    assert store.mode_for(d, "work", task_id="task-1") is None
    assert store.mode_for(d, "work") == READ  # профиль не пострадал
    assert store.mode_for(d, "work", task_id="task-2") == READ


def test_grant_rejects_both_chat_and_task_scope(tmp_path):
    store = _store(tmp_path)
    d = tmp_path / "data"
    d.mkdir()
    f = store.create_folder(str(d))
    with pytest.raises(ValueError):
        store.set_grant(f["id"], READ, profile="work", chat_id="c1", task_id="t1")


def test_none_valid_for_task_scope_but_not_profile(tmp_path):
    store = _store(tmp_path)
    d = tmp_path / "data"
    d.mkdir()
    f = store.create_folder(str(d))
    store.set_grant(f["id"], NONE, profile="work", task_id="task-1")  # ок
    with pytest.raises(ValueError):
        store.set_grant(f["id"], NONE, profile="work")  # профиль-скоуп — нет


def test_grant_views_carry_task_id(tmp_path):
    store = _store(tmp_path)
    d = tmp_path / "data"
    d.mkdir()
    f = store.create_folder(str(d))
    store.set_grant(f["id"], READ, profile="work", task_id="task-1")
    store.set_grant(f["id"], READ, profile="work")
    grants = store.get_folder(f["id"])["grants"]
    assert {(g["chat_id"], g["task_id"]) for g in grants} == {("", "task-1"), ("", "")}


def test_revoke_is_scope_exact(tmp_path):
    store = _store(tmp_path)
    d = tmp_path / "data"
    d.mkdir()
    f = store.create_folder(str(d))
    store.set_grant(f["id"], READ, profile="work")
    store.set_grant(f["id"], READ, profile="work", task_id="task-1")
    assert store.revoke_grant(f["id"], profile="work", task_id="task-1")
    assert store.mode_for(d, "work") == READ  # профильный жив
    assert not store.revoke_grant(f["id"], profile="work", task_id="task-1")  # уже нет


def test_granted_roots_include_task_scoped_folder(tmp_path):
    store = _store(tmp_path)
    media, src = tmp_path / "media", tmp_path / "src"
    media.mkdir()
    src.mkdir()
    f_media = store.create_folder(str(media))
    f_src = store.create_folder(str(src))
    store.set_grant(f_media["id"], READ, profile="work", task_id="task-1")  # task-only
    store.set_grant(f_src["id"], READ_WRITE, profile="work")  # profile-wide
    # without task context: only the profile folder is browsable
    assert {r["name"] for r in store.granted_roots("work")} == {"src"}
    # with the task context: the task folder joins the profile folders
    roots = store.granted_roots("work", task_id="task-1")
    assert {r["name"] for r in roots} == {"media", "src"}
    assert {r["name"]: r["mode"] for r in roots} == {"media": READ, "src": READ_WRITE}


def test_granted_roots_surface_stronger_nested_grant(tmp_path):
    # A nested Folder whose effective mode EXCEEDS its covering parent is its own root,
    # not deduped away: a chat/run read_write grant on src/assistant must stay visible
    # even though the task grants src (read) — else the stronger grant vanishes.
    store = _store(tmp_path)
    (tmp_path / "src" / "assistant").mkdir(parents=True)
    f_src = store.create_folder(str(tmp_path / "src"))
    f_a = store.create_folder(str(tmp_path / "src" / "assistant"))
    store.set_grant(f_src["id"], READ, profile="work", task_id="t1")
    store.set_grant(f_a["id"], READ_WRITE, profile="work", chat_id="task-run:r1")
    roots = store.granted_roots("work", chat_id="task-run:r1", task_id="t1")
    assert {r["name"]: r["mode"] for r in roots} == {"src": READ, "assistant": READ_WRITE}


def test_granted_roots_dedupe_nested_at_same_or_weaker_mode(tmp_path):
    # The dedupe still collapses a nested root the parent already covers at >= its mode:
    # read under read (equal) and read under read_write (weaker) both fold into the parent.
    store = _store(tmp_path)
    (tmp_path / "repo" / "pkg").mkdir(parents=True)
    f_o = store.create_folder(str(tmp_path / "repo"))
    f_i = store.create_folder(str(tmp_path / "repo" / "pkg"))
    store.set_grant(f_i["id"], READ, profile="work")
    store.set_grant(f_o["id"], READ, profile="work")  # equal mode → inner folds away
    assert [r["name"] for r in store.granted_roots("work")] == ["repo"]
    store.set_grant(f_o["id"], READ_WRITE, profile="work")  # parent stronger → still folds
    assert [r["name"] for r in store.granted_roots("work")] == ["repo"]


def test_granted_roots_task_none_hides_profile_folder(tmp_path):
    store = _store(tmp_path)
    d = tmp_path / "src"
    d.mkdir()
    f = store.create_folder(str(d))
    store.set_grant(f["id"], READ, profile="work")  # profile-wide read
    store.set_grant(f["id"], NONE, profile="work", task_id="task-1")  # this task: blocked
    assert {r["name"] for r in store.granted_roots("work")} == {"src"}
    assert store.granted_roots("work", task_id="task-1") == []  # dropped for the task


def test_resolve_within_honors_task_scope(tmp_path):
    store = _store(tmp_path)
    d = tmp_path / "media"
    (d / "clips").mkdir(parents=True)
    f = store.create_folder(str(d))
    store.set_grant(f["id"], READ_WRITE, profile="work", task_id="task-1")
    # no task context: the path resolves under no readable root
    assert store.resolve_within(d / "clips", "work") == (None, None)
    assert store.mode_for_path(d / "clips", "work") is None
    # with the task: the containing root + its effective mode come back
    root, mode = store.resolve_within(d / "clips", "work", task_id="task-1")
    assert root == d.resolve() and mode == READ_WRITE
    assert store.mode_for_path(d / "clips", "work", task_id="task-1") == READ_WRITE


def test_readable_roots_include_task_folder(tmp_path):
    store = _store(tmp_path)
    d = tmp_path / "media"
    d.mkdir()
    f = store.create_folder(str(d))
    store.set_grant(f["id"], READ, profile="work", task_id="task-1")
    assert store.readable_roots("work") == []
    assert store.readable_roots("work", task_id="task-1") == [d.resolve()]


def test_drop_task_removes_only_that_tasks_grants(tmp_path):
    store = _store(tmp_path)
    d1 = tmp_path / "a"
    d1.mkdir()
    d2 = tmp_path / "b"
    d2.mkdir()
    f1 = store.create_folder(str(d1))
    f2 = store.create_folder(str(d2))
    store.set_grant(f1["id"], READ, profile="work", task_id="task-1")
    store.set_grant(f2["id"], READ, profile="work", task_id="task-2")
    store.set_grant(f1["id"], READ, profile="work")
    store.drop_task("task-1")
    assert store.mode_for(d1, "work", task_id="task-1") == READ  # профильный ещё покрывает
    assert not any(g["task_id"] == "task-1" for g in store.get_folder(f1["id"])["grants"])
    assert store.mode_for(d2, "work", task_id="task-2") == READ  # чужая таска цела


def test_drop_task_garbage_collects_task_only_folder(tmp_path):
    store = _store(tmp_path)
    d = tmp_path / "a"
    d.mkdir()
    f = store.create_folder(str(d))
    store.set_grant(f["id"], READ, profile="work", task_id="task-1")  # task-only Folder
    store.drop_task("task-1")
    assert store.get_folder(f["id"]) is None  # no grants left → GC'd, not orphaned
