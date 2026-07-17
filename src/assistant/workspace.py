"""The agent's working file space — app-layer helpers over ``config.workspace_dir``.

The agent reads/writes files in the workspace via AG2's ``FilesystemToolkit``
(see ``tools.build_agent_tools``). This module adds the pieces around that: a
per-task subfolder, persisting a produced deliverable as a real file, and a
**sandboxed** listing/resolve for the GUI Files browser (nothing escapes the
workspace root).
"""

import re
import shutil
from datetime import datetime
from pathlib import Path

_SLUG_RE = re.compile(r"[^a-z0-9]+")


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


def make_dir(workspace_dir, rel: str) -> tuple[str, str | None]:
    """Create an empty Directory at `rel` (intermediate Directories created as
    needed). Returns ``(status, path)``: ``("ok", relpath)`` on success, else
    ``("exists", None)`` if it already exists (no clobber) or ``("invalid", None)``
    on a traversal escape / the root itself."""
    root = _root(workspace_dir)
    p = _inside(root, rel)
    if p is None or p == root:
        return ("invalid", None)
    if p.exists():
        return ("exists", None)
    try:
        p.mkdir(parents=True, exist_ok=False)
    except OSError:
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
    path escapes the root, is missing, or is the root. Empty parent Directories are
    left in place — they're first-class in the Files space (ADR 0007)."""
    root = _root(workspace_dir)
    p = _inside(root, rel)
    if p is None or p == root or not p.exists():
        return False
    try:
        shutil.rmtree(p) if p.is_dir() else p.unlink()
    except OSError:
        return False
    return True


def list_files(workspace_dir) -> list[dict]:
    """Every file under the workspace, newest first — for the GUI Files browser."""
    root = _root(workspace_dir)
    if not root.exists():
        return []
    out: list[dict] = []
    for p in root.rglob("*"):
        if not p.is_file():
            continue
        try:
            st = p.stat()
        except OSError:
            continue
        out.append(
            {
                "path": str(p.relative_to(root)),
                "name": p.name,
                "dir": str(p.parent.relative_to(root)) if p.parent != root else "",
                "size": st.st_size,
                "modified": datetime.fromtimestamp(st.st_mtime).astimezone().isoformat(),
            }
        )
    out.sort(key=lambda f: f["modified"], reverse=True)
    return out


def list_all_dirs(workspace_dir) -> list[str]:
    """Every Directory under the workspace root (recursively), workspace-relative —
    so the Files tree can show empty Directories that the files-only `list_files`
    omits (New directory / move can create them). Sorted for a stable tree."""
    root = _root(workspace_dir)
    if not root.exists():
        return []
    out: list[str] = []
    for p in root.rglob("*"):
        try:
            if p.is_dir():
                out.append(str(p.relative_to(root)))
        except OSError:
            continue
    out.sort()
    return out


def list_dirs(path: str) -> dict | None:
    """Immediate subdirectories of `path` (non-recursive) — for the folder picker that
    lets the user choose a Folder to register anywhere on the host (not workspace-scoped).
    Dotfolders are hidden. Returns ``{path, parent, dirs:[{name, path}]}`` (absolute
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
            if item.name.startswith("."):
                continue  # hide dotfolders (.git, .venv, …) by default
            try:
                if item.is_dir():
                    dirs.append({"name": item.name, "path": str(item)})
            except OSError:
                continue  # skip entries we can't stat (broken symlinks, etc.)
    except (PermissionError, OSError):
        return None
    dirs.sort(key=lambda d: d["name"].lower())
    return {"path": str(p), "parent": (str(p.parent) if p.parent != p else None), "dirs": dirs}
