"""Filesystem diff capture for coding runs (assistant.coding.diff).

Diffs are computed from the working tree (before vs after the run), which is
robust across all agents and captures direct writes the agent makes outside the
ACP mediated-fs path.
"""

import subprocess

import pytest

from assistant.coding import diff as diffmod


def _write(p, text):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)


def test_added_file(tmp_path):
    before = diffmod.capture(str(tmp_path))
    _write(tmp_path / "hello.py", "print('hi')\n")
    diffs = diffmod.compute_diff(before, str(tmp_path))
    by_path = {d.path: d for d in diffs}
    assert "hello.py" in by_path
    d = by_path["hello.py"]
    assert d.status == "added"
    assert d.added == 1
    assert "print('hi')" in d.hunks


def test_modified_file(tmp_path):
    _write(tmp_path / "app.py", "a = 1\nb = 2\n")
    before = diffmod.capture(str(tmp_path))
    _write(tmp_path / "app.py", "a = 1\nb = 3\n")
    diffs = diffmod.compute_diff(before, str(tmp_path))
    d = {x.path: x for x in diffs}["app.py"]
    assert d.status == "modified"
    assert d.added == 1 and d.removed == 1
    assert "-b = 2" in d.hunks and "+b = 3" in d.hunks


def test_deleted_file(tmp_path):
    _write(tmp_path / "gone.txt", "bye\n")
    before = diffmod.capture(str(tmp_path))
    (tmp_path / "gone.txt").unlink()
    diffs = diffmod.compute_diff(before, str(tmp_path))
    d = {x.path: x for x in diffs}["gone.txt"]
    assert d.status == "deleted"
    assert d.removed == 1


def test_unchanged_files_excluded(tmp_path):
    _write(tmp_path / "same.txt", "stable\n")
    before = diffmod.capture(str(tmp_path))
    diffs = diffmod.compute_diff(before, str(tmp_path))
    assert diffs == []


def test_binary_file_skipped(tmp_path):
    before = diffmod.capture(str(tmp_path))
    (tmp_path / "blob.bin").write_bytes(b"\x00\x01\x02\xff\xfe")
    diffs = diffmod.compute_diff(before, str(tmp_path))
    # A binary add is reported by path but carries no text hunks.
    paths = {d.path: d for d in diffs}
    assert "blob.bin" in paths
    assert paths["blob.bin"].hunks == ""


def test_oversized_file_reported_without_hunks(tmp_path):
    # Oversized (like binary) is reported as a changed path but carries no hunks,
    # so a giant generated file shows as "changed (no preview)" rather than dumping.
    before = diffmod.capture(str(tmp_path), max_file_bytes=16)
    _write(tmp_path / "big.txt", "x" * 100 + "\n")
    diffs = diffmod.compute_diff(before, str(tmp_path), max_file_bytes=16)
    d = {x.path: x for x in diffs}["big.txt"]
    assert d.status == "added"
    assert d.hunks == ""


@pytest.mark.skipif(not diffmod._git_available(), reason="git not installed")
def test_git_ignored_files_excluded(tmp_path):
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    _write(tmp_path / ".gitignore", "ignored/\n")
    before = diffmod.capture(str(tmp_path))
    _write(tmp_path / "ignored" / "secret.txt", "nope\n")
    _write(tmp_path / "tracked.py", "yes = 1\n")
    diffs = diffmod.compute_diff(before, str(tmp_path))
    paths = {d.path for d in diffs}
    assert "tracked.py" in paths
    assert not any(p.startswith("ignored/") for p in paths)
