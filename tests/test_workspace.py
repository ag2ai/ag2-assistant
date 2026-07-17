"""The agent's working file space: shared deliverables folder, image/upload
folders, sandboxing. Tasks save into the SAME shared workspace as chat (no
per-task subfolders)."""

from types import SimpleNamespace

from assistant.workspace import (
    delete,
    list_all_dirs,
    list_dirs,
    list_files,
    make_dir,
    move,
    resolve,
    save_upload,
    slugify,
    write_deliverable_file,
    write_image,
    write_upload,
)


def test_slugify():
    assert slugify("AI Headlines!") == "ai-headlines"
    assert slugify("  --Weird / Name-- ") == "weird-name"
    assert slugify("") == "task"


def test_deliverable_written_to_shared_folder(tmp_path):
    t = SimpleNamespace(title="AI Headlines", id="task-1", run_of=None)
    rel = write_deliverable_file(tmp_path, t, {"description": "the briefing"}, "# Hello\n")
    # shared <workspace>/deliverables/ — NOT a per-task <title-slug>/ subfolder
    assert rel == "deliverables/the-briefing.md"
    assert (tmp_path / rel).read_text() == "# Hello\n"


def test_deliverable_no_clobber(tmp_path):
    t = SimpleNamespace(title="AI Headlines", id="task-1", run_of=None)
    first = write_deliverable_file(tmp_path, t, {"description": "briefing"}, "a")
    second = write_deliverable_file(tmp_path, t, {"description": "briefing"}, "b")
    assert first == "deliverables/briefing.md"
    assert second == "deliverables/briefing-2.md"  # doesn't overwrite the first


def test_recurring_run_file_is_timestamped(tmp_path):
    run = SimpleNamespace(title="AI Headlines", id="task-1:run", run_of="task-1")
    rel = write_deliverable_file(tmp_path, run, {"description": "briefing"}, "x")
    assert rel.startswith("deliverables/") and rel.endswith("-briefing.md")
    assert rel != "deliverables/briefing.md"  # carries a date prefix


def test_resolve_blocks_traversal(tmp_path):
    (tmp_path / "a.md").write_text("ok")
    assert resolve(tmp_path, "a.md") is not None
    assert resolve(tmp_path, "../../etc/passwd") is None  # escapes the workspace
    assert resolve(tmp_path, "missing.md") is None


def test_list_files(tmp_path):
    (tmp_path / "ai-headlines").mkdir()
    (tmp_path / "ai-headlines" / "x.md").write_text("hi")
    files = list_files(tmp_path)
    assert len(files) == 1
    assert files[0]["path"] == "ai-headlines/x.md" and files[0]["dir"] == "ai-headlines"


def test_delete_removes_file_and_prunes_now_empty_folder(tmp_path):
    (tmp_path / "ai-headlines").mkdir()
    (tmp_path / "ai-headlines" / "x.md").write_text("hi")
    assert delete(tmp_path, "ai-headlines/x.md") is True
    assert not (tmp_path / "ai-headlines" / "x.md").exists()
    assert not (tmp_path / "ai-headlines").exists()  # the folder the delete emptied is pruned
    assert tmp_path.exists()  # and the workspace root is never touched


def test_delete_prunes_empty_ancestors_up_to_root(tmp_path):
    (tmp_path / "a" / "b" / "c").mkdir(parents=True)
    (tmp_path / "a" / "b" / "c" / "x.md").write_text("hi")
    assert delete(tmp_path, "a/b/c/x.md") is True
    assert not (tmp_path / "a").exists()  # whole now-empty chain a/b/c collapses
    assert tmp_path.exists()  # but never past the root


def test_delete_prune_stops_at_first_nonempty_ancestor(tmp_path):
    (tmp_path / "a" / "b").mkdir(parents=True)
    (tmp_path / "a" / "keep.md").write_text("keep")
    (tmp_path / "a" / "b" / "x.md").write_text("hi")
    assert delete(tmp_path, "a/b/x.md") is True
    assert not (tmp_path / "a" / "b").exists()  # emptied leaf is pruned
    assert (tmp_path / "a").exists()  # but a/ still holds keep.md — kept


def test_delete_leaves_a_sibling_empty_folder_untouched(tmp_path):
    # A folder that was already empty before the delete (New directory) sits off the
    # deleted file's ancestor chain, so pruning never reaches it (ADR 0007).
    (tmp_path / "a" / "b").mkdir(parents=True)
    (tmp_path / "a" / "b" / "x.md").write_text("hi")
    (tmp_path / "a" / "note").mkdir()  # intentionally-empty sibling of b/
    assert delete(tmp_path, "a/b/x.md") is True
    assert not (tmp_path / "a" / "b").exists()  # emptied leaf pruned
    assert (tmp_path / "a" / "note").exists()  # sibling empty folder preserved
    assert (tmp_path / "a").exists()  # a/ still holds note/


def test_delete_keeps_nonempty_folder(tmp_path):
    (tmp_path / "ai-headlines").mkdir()
    (tmp_path / "ai-headlines" / "x.md").write_text("hi")
    (tmp_path / "ai-headlines" / "y.md").write_text("yo")
    assert delete(tmp_path, "ai-headlines/x.md") is True
    assert (tmp_path / "ai-headlines").exists()  # still has y.md


def test_delete_blocks_traversal(tmp_path):
    assert delete(tmp_path, "../../etc/passwd") is False
    assert delete(tmp_path, "missing.md") is False


def test_write_image_saves_to_images_folder(tmp_path):
    rel = write_image(tmp_path, "A Red Circle", b"\x89PNG-bytes", "image/png")
    assert rel == "images/a-red-circle.png"
    assert (tmp_path / rel).read_bytes() == b"\x89PNG-bytes"


def test_write_image_no_clobber_and_ext_from_media_type(tmp_path):
    write_image(tmp_path, "Sunset", b"a", "image/png")
    second = write_image(tmp_path, "Sunset", b"b", "image/png")
    assert second == "images/sunset-2.png"  # doesn't overwrite the first
    jpg = write_image(tmp_path, "Beach", b"c", "image/jpeg")
    assert jpg == "images/beach.jpg"


def test_write_upload_saves_to_uploads_with_clean_name(tmp_path):
    rel = write_upload(tmp_path, "My Photo.PNG", b"img-bytes")
    assert rel == "uploads/my-photo.png"  # slugified stem + clean extension
    assert (tmp_path / rel).read_bytes() == b"img-bytes"
    # resolvable (so generate_image source_image / read_file can use it)
    assert resolve(tmp_path, rel) is not None
    # no clobber
    assert write_upload(tmp_path, "My Photo.PNG", b"x") == "uploads/my-photo-2.png"


def test_list_dirs_returns_subdirs_only_sorted_and_hides_dotfolders(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "Docs").mkdir()
    (tmp_path / ".git").mkdir()  # dotfolder — hidden
    (tmp_path / "readme.md").write_text("hi")  # a file — not a dir, excluded

    r = list_dirs(str(tmp_path))
    assert r["path"] == str(tmp_path.resolve())
    assert r["parent"] == str(tmp_path.resolve().parent)
    names = [d["name"] for d in r["dirs"]]
    assert names == ["Docs", "src"]  # case-insensitive sort, no .git, no readme.md
    assert all(d["path"] == str(tmp_path.resolve() / d["name"]) for d in r["dirs"])


def test_list_dirs_missing_or_not_a_dir_returns_none(tmp_path):
    assert list_dirs(str(tmp_path / "nope")) is None  # missing
    f = tmp_path / "a.txt"
    f.write_text("x")
    assert list_dirs(str(f)) is None  # a file, not a directory


def test_list_dirs_empty_path_defaults_to_home(tmp_path, monkeypatch):
    # conftest points HOME at tmp_path; an empty path expands "~" to it.
    (tmp_path / "proj").mkdir()
    r = list_dirs("")
    assert r is not None and "proj" in [d["name"] for d in r["dirs"]]


# ---- User-writable Files space (ADR 0007) ----


def test_save_upload_to_root_preserves_original_name(tmp_path):
    rel = save_upload(tmp_path, "My Report.PDF", b"pdf-bytes")
    assert rel == "My Report.PDF"  # original name kept (unlike write_upload's slug)
    assert (tmp_path / rel).read_bytes() == b"pdf-bytes"


def test_save_upload_to_nested_target_directory(tmp_path):
    (tmp_path / "docs" / "sub").mkdir(parents=True)
    rel = save_upload(tmp_path, "notes.md", b"hi", target_dir="docs/sub")
    assert rel == "docs/sub/notes.md"
    assert (tmp_path / rel).read_text() == "hi"


def test_save_upload_creates_missing_target_directory(tmp_path):
    rel = save_upload(tmp_path, "a.txt", b"x", target_dir="brand/new")
    assert rel == "brand/new/a.txt" and (tmp_path / rel).exists()


def test_save_upload_auto_suffixes_on_clash(tmp_path):
    assert save_upload(tmp_path, "photo.png", b"a") == "photo.png"
    assert save_upload(tmp_path, "photo.png", b"b") == "photo (2).png"  # never overwrites
    assert save_upload(tmp_path, "photo.png", b"c") == "photo (3).png"
    assert (tmp_path / "photo.png").read_bytes() == b"a"  # original untouched


def test_save_upload_suffix_no_extension(tmp_path):
    assert save_upload(tmp_path, "LICENSE", b"a") == "LICENSE"
    assert save_upload(tmp_path, "LICENSE", b"b") == "LICENSE (2)"


def test_save_upload_rejects_traversal(tmp_path):
    assert save_upload(tmp_path, "x.txt", b"a", target_dir="../../etc") is None
    # a filename that tries to climb is reduced to its basename, staying inside
    rel = save_upload(tmp_path, "../../evil.sh", b"a")
    assert rel == "evil.sh" and (tmp_path / "evil.sh").exists()


def test_make_dir_creates_empty_directory(tmp_path):
    assert make_dir(tmp_path, "reports") == ("ok", "reports")
    assert (tmp_path / "reports").is_dir()


def test_make_dir_nested_and_shown_by_list_all_dirs(tmp_path):
    assert make_dir(tmp_path, "a/b/c") == ("ok", "a/b/c")
    dirs = list_all_dirs(tmp_path)
    assert "a" in dirs and "a/b" in dirs and "a/b/c" in dirs


def test_make_dir_rejects_existing(tmp_path):
    (tmp_path / "dup").mkdir()
    assert make_dir(tmp_path, "dup") == ("exists", None)  # no clobber


def test_make_dir_rejects_traversal_and_root(tmp_path):
    assert make_dir(tmp_path, "../escape") == ("invalid", None)
    assert make_dir(tmp_path, "") == ("invalid", None)  # the root itself


def test_delete_directory_recursively(tmp_path):
    (tmp_path / "d" / "sub").mkdir(parents=True)
    (tmp_path / "d" / "one.md").write_text("1")
    (tmp_path / "d" / "sub" / "two.md").write_text("2")
    assert delete(tmp_path, "d") is True
    assert not (tmp_path / "d").exists()
    assert tmp_path.exists()  # never the root


def test_delete_directory_rejects_traversal_and_root(tmp_path):
    assert delete(tmp_path, "../..") is False
    assert delete(tmp_path, "") is False  # can't delete the root


def test_move_renames_file_in_place(tmp_path):
    (tmp_path / "a.md").write_text("hi")
    assert move(tmp_path, "a.md", "b.md") == "ok"
    assert not (tmp_path / "a.md").exists()
    assert (tmp_path / "b.md").read_text() == "hi"


def test_move_file_into_another_directory(tmp_path):
    (tmp_path / "a.md").write_text("hi")
    assert move(tmp_path, "a.md", "docs/a.md") == "ok"  # intermediate dir created
    assert (tmp_path / "docs" / "a.md").read_text() == "hi"


def test_move_directory_rewrites_subtree(tmp_path):
    (tmp_path / "old" / "sub").mkdir(parents=True)
    (tmp_path / "old" / "sub" / "x.md").write_text("x")
    assert move(tmp_path, "old", "new") == "ok"
    assert (tmp_path / "new" / "sub" / "x.md").read_text() == "x"
    assert not (tmp_path / "old").exists()


def test_move_rejects_clash(tmp_path):
    (tmp_path / "a.md").write_text("a")
    (tmp_path / "b.md").write_text("b")
    assert move(tmp_path, "a.md", "b.md") == "exists"  # never overwrites
    assert (tmp_path / "a.md").read_text() == "a"  # source untouched
    assert (tmp_path / "b.md").read_text() == "b"  # destination untouched


def test_move_missing_source(tmp_path):
    assert move(tmp_path, "nope.md", "b.md") == "not_found"


def test_move_rejects_traversal_on_either_side(tmp_path):
    (tmp_path / "a.md").write_text("a")
    assert move(tmp_path, "../../etc/passwd", "b.md") == "invalid"
    assert move(tmp_path, "a.md", "../../evil.md") == "invalid"
    assert (tmp_path / "a.md").exists()


def test_move_directory_into_own_subtree_rejected(tmp_path):
    (tmp_path / "d" / "sub").mkdir(parents=True)
    assert move(tmp_path, "d", "d/sub/d") == "invalid"


def test_list_all_dirs_includes_empty_directories(tmp_path):
    (tmp_path / "empty").mkdir()
    (tmp_path / "withfile").mkdir()
    (tmp_path / "withfile" / "f.md").write_text("f")
    dirs = list_all_dirs(tmp_path)
    assert "empty" in dirs and "withfile" in dirs
