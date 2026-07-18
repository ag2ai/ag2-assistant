"""Corpus search for the composer's ``@``-picker (ADR 0012).

Ranks a combined corpus — the profile's Files space plus every granted Folder the
current profile∪chat can read — filename-first and bounds it to ``SEARCH_LIMIT``.
The Folder walk honors the same ``FolderStore.mode_for`` resolution the agent's own
reads use, so the picker never surfaces a file the agent would be denied. Results
include files (``kind: "file"``) and Directories (``kind: "directory"``).
"""

import os
from pathlib import Path

from assistant.workspace import SEARCH_LIMIT, list_all_dirs, list_files, match_rank

# Directories whose subtrees are search noise — pruned during the Folder walk.
SKIP_DIRS = frozenset(
    {
        ".git",
        ".hg",
        ".svn",
        "node_modules",
        ".venv",
        "venv",
        "__pycache__",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        ".tox",
        "dist",
        "build",
        ".next",
        ".idea",
    }
)

# OS-junk files that are never a useful reference — pruned from both corpus halves so a
# folder-name query (which matches every descendant on its path) doesn't surface them.
SKIP_FILES = frozenset({".DS_Store", "Thumbs.db", "desktop.ini", ".localized"})

# Hard cap on entries examined per search across all granted Folders, so a huge
# repo can't stall the composer. Truncation is acceptable (search, not enumeration).
WALK_CAP = 20_000


def _candidate(tier: int, name: str, abs_path: str, dir_label: str, kind: str) -> tuple:
    """One ranked candidate: ``(tier, name_lower, abs_path, result)`` — the tuple the
    caller sorts (filename-first) and dedups by ``abs_path``."""
    return (
        tier,
        name.lower(),
        abs_path,
        {"path": abs_path, "name": name, "dir": dir_label, "kind": kind},
    )


def _readable_roots(store, profile: str, chat_id: str) -> list[Path]:
    """The top-level granted-Folder paths readable for this profile∪chat.

    A Folder is included iff ``mode_for`` resolves it to a non-``None`` mode. Roots
    nested under another readable root are dropped so nothing is walked twice."""
    readable: list[Path] = []
    for f in store.list_folders():
        try:
            p = Path(f.get("path", "")).expanduser().resolve()
        except (OSError, ValueError):
            continue
        if p.is_dir() and store.mode_for(p, profile, chat_id) is not None:
            readable.append(p)
    return [p for p in readable if not any(p != o and o in p.parents for o in readable)]


def _walk_folder(root: Path, query: str, out: list, scanned: list) -> None:
    """Append candidates for files and Directories under ``root`` matching ``query``,
    pruning ``SKIP_DIRS`` and stopping at ``WALK_CAP``. ``dir`` labels are rooted at
    the Folder's basename (``acme/src``) so same-named hits stay distinguishable."""
    label = root.name or str(root)
    # The Folder's own root is a referenceable Directory too. ``os.walk`` yields only a
    # root's *contents*, never the root itself, so a query matching the Folder name
    # (``@/media`` for a granted ``…/media`` Folder) would otherwise find everything
    # under it but never the Folder — emit the root as its own directory candidate.
    root_tier = match_rank(query, label, label)
    if root_tier is not None and scanned[0] < WALK_CAP:
        scanned[0] += 1
        out.append(_candidate(root_tier, label, str(root), "", "directory"))
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        filenames = [f for f in filenames if f not in SKIP_FILES]
        rel_dir = Path(dirpath).relative_to(root)
        dir_label = label if str(rel_dir) == "." else f"{label}/{rel_dir}"
        # Match against the display path (Folder label included) so typing the
        # Folder's name surfaces entries under it, mirroring the Files-space half.
        for name, kind in [(d, "directory") for d in dirnames] + [(f, "file") for f in filenames]:
            if scanned[0] >= WALK_CAP:
                return
            scanned[0] += 1
            tier = match_rank(query, name, f"{dir_label}/{name}")
            if tier is not None:
                out.append(_candidate(tier, name, str(Path(dirpath) / name), dir_label, kind))


def search_corpus(
    workspace_dir,
    query: str,
    *,
    folders=None,
    profile: str = "",
    chat_id: str = "",
    limit: int = SEARCH_LIMIT,
) -> list[dict]:
    """Search the reachable corpus for `query` (case-insensitive substring on an
    entry's name or relative path), ranked filename-first and bounded to `limit`.

    The corpus is the Files space at `workspace_dir` plus — when a `folders` store is
    given — every Folder readable for `profile`∪`chat_id`. Each result carries an
    ABSOLUTE `path`, a `name`, a `dir` display label, and a `kind`. A blank query
    matches nothing, not an error."""
    # Strip surrounding slashes so a path-style query (``@/media``) still matches the
    # ``media`` entry; interior slashes stay, for path-segment matches (``src/utils``).
    q = (query or "").strip().strip("/").lower()
    if not q:
        return []

    candidates: list = []
    root = Path(workspace_dir).expanduser().resolve()

    # Files space — reuse the recursive listing rather than re-walking from scratch.
    for f in list_files(workspace_dir):
        if f["name"] in SKIP_FILES:
            continue
        tier = match_rank(q, f["name"], f["path"])
        if tier is not None:
            candidates.append(_candidate(tier, f["name"], str(root / f["path"]), f["dir"], "file"))
    for rel in list_all_dirs(workspace_dir):
        name = Path(rel).name
        tier = match_rank(q, name, rel)
        if tier is not None:
            parent = str(Path(rel).parent)
            candidates.append(
                _candidate(
                    tier, name, str(root / rel), "" if parent == "." else parent, "directory"
                )
            )

    # Granted Folders — access-honoring walk (never surfaces a denied entry).
    if folders is not None:
        scanned = [0]
        for froot in _readable_roots(folders, profile, chat_id):
            _walk_folder(froot, q, candidates, scanned)

    # Rank filename-first (tier), then a deterministic tiebreak; dedup by absolute
    # path (nested corpora can surface the same entry twice); bound to `limit`.
    candidates.sort(key=lambda c: (c[0], c[1], c[2]))
    results: list[dict] = []
    seen: set[str] = set()
    for _tier, _name, abs_path, result in candidates:
        if abs_path in seen:
            continue
        seen.add(abs_path)
        results.append(result)
        if len(results) >= limit:
            break
    return results
