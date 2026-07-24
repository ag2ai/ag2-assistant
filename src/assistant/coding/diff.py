"""Compute what a coding run changed, as full unified-diff hunks.

Diffs come from the working tree (a snapshot before the run vs. the tree after),
not from ACP events — this is uniform across agents and also captures writes an
agent makes directly on disk (outside the ACP mediated-fs path). In a git repo
the candidate set respects ``.gitignore`` (via ``git ls-files``); otherwise a
bounded walk skips heavy directories.
"""

import difflib
import os
import shutil
import subprocess
from dataclasses import dataclass

MAX_FILE_BYTES = 1_000_000  # per-file text cap; larger files are treated as binary
MAX_FILES = 5000  # candidate-set cap (defense against huge trees)

_SKIP_DIRS = {
    ".git",
    "node_modules",
    ".venv",
    "venv",
    "__pycache__",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    "dist",
    "build",
    ".next",
    ".turbo",
}

# Sentinel: the file exists but is binary/oversized (no text hunks).
_BINARY = object()


@dataclass(frozen=True)
class FileDiff:
    """One file's change in a coding run."""

    path: str  # directory-relative
    status: str  # "added" | "modified" | "deleted"
    hunks: str  # unified diff text ("" for binary/oversized)
    added: int  # added line count
    removed: int  # removed line count


# A snapshot maps a directory-relative path to its text content, or `_BINARY`.
Snapshot = dict[str, "str | object"]


def _git_available() -> bool:
    return shutil.which("git") is not None


def _is_git_repo(directory: str) -> bool:
    if not _git_available():
        return False
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--is-inside-work-tree"],
            cwd=directory,
            capture_output=True,
            text=True,
        )
    except OSError:
        return False
    return out.returncode == 0 and out.stdout.strip() == "true"


def _candidate_files(directory: str) -> list[str]:
    """Directory-relative paths to consider, capped at ``MAX_FILES``."""
    if _is_git_repo(directory):
        try:
            out = subprocess.run(
                ["git", "ls-files", "--cached", "--others", "--exclude-standard"],
                cwd=directory,
                capture_output=True,
                text=True,
                check=True,
            )
            rels = [line for line in out.stdout.splitlines() if line]
            return rels[:MAX_FILES]
        except (OSError, subprocess.CalledProcessError):
            pass  # fall through to the plain walk
    rels: list[str] = []
    for root, dirs, files in os.walk(directory):
        dirs[:] = [d for d in dirs if d not in _SKIP_DIRS]
        for name in files:
            rel = os.path.relpath(os.path.join(root, name), directory)
            rels.append(rel)
            if len(rels) >= MAX_FILES:
                return rels
    return rels


def _read(directory: str, rel: str) -> "str | object | None":
    """Return text content, ``_BINARY``, or ``None`` if the file is absent."""
    full = os.path.join(directory, rel)
    try:
        if os.path.getsize(full) > MAX_FILE_BYTES:
            return _BINARY
        with open(full, "rb") as f:
            raw = f.read()
    except OSError:
        return None
    if b"\x00" in raw:
        return _BINARY
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return _BINARY


def capture(directory: str) -> Snapshot:
    """Snapshot the working tree so a later ``compute_diff`` can show changes."""
    snap: Snapshot = {}
    for rel in _candidate_files(directory):
        val = _read(directory, rel)
        if val is not None:
            snap[rel] = val
    return snap


def _unified(rel: str, before: str, after: str) -> tuple[str, int, int]:
    """Unified diff of two texts plus (added, removed) line counts."""
    b_lines = before.splitlines(keepends=True)
    a_lines = after.splitlines(keepends=True)
    diff_lines = list(
        difflib.unified_diff(b_lines, a_lines, fromfile=f"a/{rel}", tofile=f"b/{rel}")
    )
    added = sum(1 for ln in diff_lines if ln.startswith("+") and not ln.startswith("+++"))
    removed = sum(1 for ln in diff_lines if ln.startswith("-") and not ln.startswith("---"))
    text = "".join(ln if ln.endswith("\n") else ln + "\n" for ln in diff_lines)
    return text, added, removed


def compute_diff(before: Snapshot, directory: str) -> list[FileDiff]:
    """Diff the current tree against ``before``; one ``FileDiff`` per changed file."""
    after_paths = set(_candidate_files(directory))
    diffs: list[FileDiff] = []
    for rel in sorted(set(before) | after_paths):
        old = before.get(rel, None)
        new = _read(directory, rel)  # current content (None if now absent)

        if old is None and new is None:
            continue
        if old is None:
            # added
            if new is _BINARY:
                diffs.append(FileDiff(rel, "added", "", 0, 0))
            else:
                hunks, added, removed = _unified(rel, "", new)  # type: ignore[arg-type]
                diffs.append(FileDiff(rel, "added", hunks, added, removed))
            continue
        if new is None:
            # deleted
            if old is _BINARY:
                diffs.append(FileDiff(rel, "deleted", "", 0, 0))
            else:
                hunks, added, removed = _unified(rel, old, "")  # type: ignore[arg-type]
                diffs.append(FileDiff(rel, "deleted", hunks, added, removed))
            continue
        # both present
        if old is _BINARY or new is _BINARY:
            if old is not new:  # e.g. text→binary or size crossing the cap
                diffs.append(FileDiff(rel, "modified", "", 0, 0))
            continue
        if old == new:
            continue
        hunks, added, removed = _unified(rel, old, new)  # type: ignore[arg-type]
        diffs.append(FileDiff(rel, "modified", hunks, added, removed))
    return diffs
