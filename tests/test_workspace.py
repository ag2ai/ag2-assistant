"""The agent's working file space: shared deliverables folder, image/upload
folders, sandboxing. Tasks save into the SAME shared workspace as chat (no
per-task subfolders)."""

from types import SimpleNamespace

from assistant.workspace import (
    delete,
    list_files,
    resolve,
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


def test_delete_removes_file_and_prunes_empty_folder(tmp_path):
    (tmp_path / "ai-headlines").mkdir()
    (tmp_path / "ai-headlines" / "x.md").write_text("hi")
    assert delete(tmp_path, "ai-headlines/x.md") is True
    assert not (tmp_path / "ai-headlines" / "x.md").exists()
    assert not (tmp_path / "ai-headlines").exists()  # emptied task folder pruned
    assert tmp_path.exists()  # but never the workspace root


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
