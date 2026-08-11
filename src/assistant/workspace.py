"""The agent's working file space — app-layer helpers over ``config.workspace_dir``.

The agent reads/writes files in the workspace via AG2's ``FilesystemToolkit``
(see ``tools.build_agent_tools``). This module adds the pieces around that: a
per-task subfolder, persisting a produced deliverable as a real file, and a
**sandboxed** listing/resolve for the GUI Files browser (nothing escapes the
workspace root).
"""

import hashlib
import os
import re
import shutil
import tempfile
from datetime import datetime
from pathlib import Path

_SLUG_RE = re.compile(r"[^a-z0-9]+")

# Max byte size of an in-place text write; a larger body is rejected (ADR 0011).
_MAX_WRITE_BYTES = 5 * 1024 * 1024

# Top-N bound on ``@``-picker search results — a huge corpus can't flood the
# response (search, not enumeration); the user narrows by typing more.
SEARCH_LIMIT = 20

# Directories whose subtrees are dev noise — hidden from the folder picker and
# pruned during Folder walks/listings. The canonical list; ``filesearch`` reuses it.
# Names only: a workspace listing hides what is on this list and nothing else, so a
# folder the user made is never invisible for starting with a dot.
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


def slugify(text: str, default: str = "task", maxlen: int = 48) -> str:
    """A filesystem-safe slug from arbitrary text."""
    s = _SLUG_RE.sub("-", (text or "").lower()).strip("-")
    return s[:maxlen].strip("-") or default


def _root(workspace_dir) -> Path:
    return Path(workspace_dir).expanduser().resolve()


def write_deliverable_file(workspace_dir, task, deliverable: dict, content: str) -> str:
    """Persist a produced deliverable's content as a markdown file in the SHARED
    ``<workspace>/deliverables/`` folder (so task outputs land in the same workspace
    as chat, not a per-task subfolder); return its path relative to the workspace
    root (for the asset + API). Recurring runs are timestamped, and a name clash is
    de-duped, so successive/concurrent runs don't overwrite each other."""
    root = _root(workspace_dir)
    folder = root / "deliverables"
    folder.mkdir(parents=True, exist_ok=True)
    name = slugify(deliverable.get("description") or "deliverable")
    if getattr(task, "run_of", None):
        name = f"{datetime.now().strftime('%Y%m%d-%H%M')}-{name}"
    path = folder / f"{name}.md"
    n = 2
    while path.exists():  # don't clobber an existing deliverable with the same slug
        path = folder / f"{name}-{n}.md"
        n += 1
    path.write_text(content or "")
    return str(path.relative_to(root))


_IMAGE_EXT = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/webp": ".webp",
    "image/gif": ".gif",
}


def write_image(workspace_dir, prompt: str, data: bytes, media_type: str = "image/png") -> str:
    """Save a generated image into ``<workspace>/images/`` named from the prompt;
    return its workspace-relative path (so it shows in the Files browser / preview)."""
    root = _root(workspace_dir)
    folder = root / "images"
    folder.mkdir(parents=True, exist_ok=True)
    ext = _IMAGE_EXT.get(media_type, ".png")
    base = slugify(prompt, default="image")
    path = folder / f"{base}{ext}"
    n = 2
    while path.exists():  # don't clobber an earlier image with the same prompt slug
        path = folder / f"{base}-{n}{ext}"
        n += 1
    path.write_bytes(data)
    return str(path.relative_to(root))


def write_upload(workspace_dir, filename: str, data: bytes) -> str:
    """Save a user-uploaded file into ``<workspace>/uploads/`` (keeping a clean
    extension); return its workspace-relative path so the agent can edit it with
    generate_image(source_image=…) or read it by path — no more guessing via search."""
    root = _root(workspace_dir)
    folder = root / "uploads"
    folder.mkdir(parents=True, exist_ok=True)
    stem, dot, ext = (filename or "file").rpartition(".")
    base = slugify(stem or filename, default="upload")
    suffix = f".{slugify(ext)}" if dot and ext else ""
    path = folder / f"{base}{suffix}"
    n = 2
    while path.exists():
        path = folder / f"{base}-{n}{suffix}"
        n += 1
    path.write_bytes(data)
    return str(path.relative_to(root))


def _inside(root: Path, rel: str) -> Path | None:
    """Resolve `rel` under `root` with the path-traversal guard, not requiring the
    path to exist or be a file. Returns the absolute path if it stays inside the
    root, else None."""
    try:
        p = (root / (rel or "")).resolve()
    except Exception:
        return None
    return p if (p == root or root in p.parents) else None


def resolve(workspace_dir, rel: str) -> Path | None:
    """Resolve a workspace-relative path to an absolute file path, or None if it
    escapes the workspace root (path-traversal guard) or isn't a file."""
    p = _inside(_root(workspace_dir), rel)
    return p if p is not None and p.is_file() else None


def mention_forms(workspace_dir, path: str) -> list[str]:
    """The OR-set of full-path strings a transcript scan matches for a previewed
    file's "Mentioned in N threads" backlink (ADR 0014).

    A Files-space (relative) path yields BOTH its workspace-**relative** form (as a
    produce/attachment event or bare prose writes it) and its **absolute** form (as
    an ``@`` ``Referenced files:`` block writes it). A Folder (absolute) path yields
    its absolute form, plus its workspace-relative form iff it lies under the
    workspace root. Never the bare basename. A blank path yields no forms (the scan
    is skipped). Existence is not required — the match is path-historical."""
    p = (path or "").strip()
    if not p:
        return []
    root = _root(workspace_dir)
    forms: list[str] = []
    if os.path.isabs(p):
        forms.append(p)
        try:
            forms.append(str(Path(p).resolve().relative_to(root)))
        except (ValueError, OSError):
            pass  # not under the workspace — absolute form only
    else:
        forms.append(p)
        forms.append(str(root / p))
    seen: set[str] = set()
    unique: list[str] = []
    for f in forms:
        if f and f not in seen:
            seen.add(f)
            unique.append(f)
    return unique


def _hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def etag_for_path(p: Path) -> str | None:
    """The opaque content-version token (ADR 0011) for a resolved file — a hash of
    its current bytes, equal to the token a matching `write_text` returns — or None
    if it can't be read."""
    try:
        return _hash(p.read_bytes())
    except OSError:
        return None


def write_text(
    workspace_dir,
    rel: str,
    content: str,
    *,
    base_token: str | None = None,
    force: bool = False,
    max_bytes: int = _MAX_WRITE_BYTES,
) -> tuple[str, str | None]:
    """Overwrite an already-existing file's contents with UTF-8 `content`,
    optimistically concurrent (ADR 0011); never creates a file. Returns
    ``(status, new_token)``: ``("ok", <hash>)`` wrote (``<hash>`` is the new
    content token, equal to a subsequent read's ETag); ``("not_found", None)``
    path missing or not a file; ``("conflict", None)`` `base_token` != current
    content hash, file left untouched; ``("invalid", None)`` traversal / the root
    / OS error; ``("too_large", None)`` body over `max_bytes`. `force=True` skips
    the token compare and replaces the bytes unconditionally."""
    root = _root(workspace_dir)
    p = _inside(root, rel)
    if p is None or p == root:
        return ("invalid", None)
    if not p.is_file():
        return ("not_found", None)
    data = content.encode("utf-8")
    if len(data) > max_bytes:
        return ("too_large", None)
    if not force:
        try:
            current = _hash(p.read_bytes())
        except OSError:
            return ("invalid", None)
        if base_token != current:
            return ("conflict", None)
    # Atomic replace: write a temp file beside the target, then rename over it, so a
    # concurrent reader or a mid-write crash never sees a torn/half-written file.
    tmp = None
    try:
        fd, tmp = tempfile.mkstemp(dir=p.parent, prefix=".tmp-", suffix=p.suffix)
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        os.replace(tmp, p)
    except OSError:
        if tmp is not None:
            try:
                os.unlink(tmp)
            except OSError:
                pass
        return ("invalid", None)
    return ("ok", _hash(data))


def save_upload(workspace_dir, filename: str, data: bytes, target_dir: str = "") -> str | None:
    """Save a user-uploaded file into the Files space under `target_dir` (root when
    empty), keeping the original filename. A name clash is auto-suffixed
    ``name (2).ext`` (then `(3)`, …) so nothing is overwritten. Returns the
    workspace-relative path, or None if `target_dir` escapes the root."""
    root = _root(workspace_dir)
    dest = _inside(root, target_dir)
    if dest is None:
        return None
    dest.mkdir(parents=True, exist_ok=True)
    name = Path(filename or "file").name or "file"  # drop any directory parts
    stem, dot, ext = name.rpartition(".")
    base, suffix = (stem, f".{ext}") if dot else (name, "")
    path = dest / name
    n = 2
    while path.exists():
        path = dest / f"{base} ({n}){suffix}"
        n += 1
    path.write_bytes(data)
    return str(path.relative_to(root))


#: Longest single filename component on the filesystems we target (POSIX NAME_MAX).
_NAME_MAX = 255


def invalid_dir_name(name: str) -> str | None:
    """Why `name` is unusable as a NEW single folder name, or None if it's fine.

    `make_dir` deliberately accepts a nested relative path ("a/b/c") because the Files
    tab creates trees that way. A picker's "new folder" field is a different thing — one
    name, in the folder you're looking at — so the stricter rules live here rather than in
    `make_dir`, which must keep its existing behaviour. Returns a message written for the
    person typing, not a code."""
    if not name or not name.strip():
        return "Enter a folder name"
    if name != name.strip():
        return "Name can't start or end with a space"
    if "/" in name or "\\" in name:
        return "Name can't contain slashes"
    if name in (".", ".."):
        return "Not a valid folder name"
    if name.startswith("."):
        # It would be created and then immediately hidden — list_dirs skips dotfolders.
        return "Names starting with a dot are hidden and won't show here"
    if any(ord(c) < 32 or ord(c) == 127 for c in name):
        return "Name contains invalid characters"
    if len(name.encode("utf-8", "surrogatepass")) > _NAME_MAX:
        return "Name is too long"
    return None


def make_dir(workspace_dir, rel: str) -> tuple[str, str | None]:
    """Create an empty Directory at `rel` (intermediate Directories created as
    needed). Returns ``(status, path)``: ``("ok", relpath)`` on success, else
    ``("exists", None)`` if it already exists (no clobber) or ``("invalid", None)``
    on a traversal escape / the root itself."""
    root = _root(workspace_dir)
    p = _inside(root, rel)
    if p is None or p == root:
        return ("invalid", None)
    try:
        if p.exists():
            return ("exists", None)
        p.mkdir(parents=True, exist_ok=False)
    except OSError:
        # exists() itself raises on an over-long component (ENAMETOOLONG), so it has to
        # sit inside the guard too — otherwise the name never reaches mkdir and the
        # OSError escapes as a 500 instead of a 400.
        return ("invalid", None)
    return ("ok", str(p.relative_to(root)))


def move(workspace_dir, src: str, dst: str) -> str:
    """Move/rename a file or Directory. `dst` may be a new name or a new relative
    path (intermediate Directories created); a Directory move carries its subtree.
    Never overwrites an existing `dst`. Returns ``"ok" | "not_found" | "exists" |
    "invalid"`` (``"invalid"`` = a traversal escape either side, or a Directory moved
    into its own subtree)."""
    root = _root(workspace_dir)
    sp = _inside(root, src)
    dp = _inside(root, dst)
    if sp is None or dp is None or sp == root or dp == root:
        return "invalid"
    if not sp.exists():
        return "not_found"
    if sp == dp:
        return "ok"  # rename to the same path — no-op
    if dp.exists():
        return "exists"
    if sp.is_dir() and sp in dp.parents:
        return "invalid"  # a Directory can't move inside itself
    try:
        dp.parent.mkdir(parents=True, exist_ok=True)
        sp.rename(dp)
    except OSError:
        return "invalid"
    return "ok"


def delete(workspace_dir, rel: str) -> bool:
    """Delete a workspace file, or a Directory and its contents recursively,
    sandboxed to the workspace root (never the root itself). Returns False if the
    path escapes the root, is missing, or is the root.

    Empty parent Directories the delete *just emptied* are then pruned, walking up
    to the root and stopping at the first Directory that still holds something. A
    folder that was already empty before this delete (e.g. one made via New
    directory) is never touched — it's not on the deleted path's ancestor chain —
    so intentionally-empty Directories stay first-class (ADR 0007)."""
    root = _root(workspace_dir)
    p = _inside(root, rel)
    if p is None or p == root or not p.exists():
        return False
    try:
        shutil.rmtree(p) if p.is_dir() else p.unlink()
    except OSError:
        return False
    # Prune now-empty ancestors of what we deleted, up to (never including) root.
    parent = p.parent
    while parent != root and root in parent.parents:
        try:
            next(parent.iterdir())  # still holds a file or Directory — stop here
            break
        except StopIteration:
            pass
        except OSError:
            break
        try:
            parent.rmdir()
        except OSError:
            break
        parent = parent.parent
    return True


def list_files(workspace_dir) -> list[dict]:
    """Every file under the workspace, newest first — for the GUI Files browser.
    ``SKIP_DIRS`` subtrees are pruned; every other directory is the user's to see."""
    root = _root(workspace_dir)
    if not root.exists():
        return []
    out: list[dict] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        parent = Path(dirpath)
        rel_dir = "" if parent == root else str(parent.relative_to(root))
        for name in filenames:
            try:
                st = (parent / name).stat()
            except OSError:
                continue
            out.append(
                {
                    "path": name if not rel_dir else f"{rel_dir}/{name}",
                    "name": name,
                    "dir": rel_dir,
                    "size": st.st_size,
                    "modified": datetime.fromtimestamp(st.st_mtime).astimezone().isoformat(),
                }
            )
    out.sort(key=lambda f: f["modified"], reverse=True)
    return out


def list_all_dirs(workspace_dir) -> list[str]:
    """Every Directory under the workspace root (recursively), workspace-relative —
    so the Files tree can show empty Directories that the files-only `list_files`
    omits (New directory / move can create them). Sorted for a stable tree.
    ``SKIP_DIRS`` subtrees are pruned; every other directory is the user's to see."""
    root = _root(workspace_dir)
    if not root.exists():
        return []
    out: list[str] = []
    for dirpath, dirnames, _ in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        parent = Path(dirpath)
        for name in dirnames:
            out.append(name if parent == root else f"{parent.relative_to(root)}/{name}")
    out.sort()
    return out


def match_rank(query: str, name: str, rel_path: str) -> int | None:
    """Rank tier for a candidate against an already-lowercased `query`, or ``None``
    if it doesn't match: ``0`` when the filename matches (ranked first), ``1`` when
    only the path matches. Shared by the Files space and granted-Folder corpora (see
    :mod:`assistant.filesearch`) so both rank on one scale."""
    if query in name.lower():
        return 0
    if query in rel_path.lower():
        return 1
    return None


def list_dirs(path: str) -> dict | None:
    """Immediate subdirectories of `path` (non-recursive) — for the folder picker that
    lets the user choose a Folder to register anywhere on the host (not workspace-scoped).
    Dotfolders and dev-noise dirs (``__pycache__``, ``node_modules``, … — see
    ``SKIP_DIRS``) are hidden. Returns ``{path, parent, dirs:[{name, path}]}`` (absolute
    paths), or None if `path` isn't a readable directory."""
    try:
        p = Path(path or "~").expanduser().resolve()
    except Exception:
        return None
    if not p.is_dir():
        return None
    dirs: list[dict] = []
    try:
        for item in p.iterdir():
            if item.name.startswith(".") or item.name in SKIP_DIRS:
                continue  # the picker browses the whole host: hide dotfolders too
            try:
                if item.is_dir():
                    dirs.append({"name": item.name, "path": str(item)})
            except OSError:
                continue  # skip entries we can't stat (broken symlinks, etc.)
    except (PermissionError, OSError):
        return None
    dirs.sort(key=lambda d: d["name"].lower())
    return {"path": str(p), "parent": (str(p.parent) if p.parent != p else None), "dirs": dirs}
