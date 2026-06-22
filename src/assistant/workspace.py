"""The agent's working file space — app-layer helpers over ``config.workspace_dir``.

The agent reads/writes files in the workspace via AG2's ``FilesystemToolkit``
(see ``tools.build_agent_tools``). This module adds the pieces around that: a
per-task subfolder, persisting a produced deliverable as a real file, and a
**sandboxed** listing/resolve for the GUI Files browser (nothing escapes the
workspace root).
"""

import re
from datetime import datetime
from pathlib import Path

_SLUG_RE = re.compile(r"[^a-z0-9]+")


def slugify(text: str, default: str = "task", maxlen: int = 48) -> str:
    """A filesystem-safe slug from arbitrary text."""
    s = _SLUG_RE.sub("-", (text or "").lower()).strip("-")
    return s[:maxlen].strip("-") or default


def _root(workspace_dir) -> Path:
    return Path(workspace_dir).expanduser().resolve()


def task_dir(workspace_dir, task) -> Path:
    """The folder for a task's files: ``<workspace>/<title-slug>``. Recurring runs
    share their template's title, so a task's outputs group in one folder."""
    label = getattr(task, "title", "") or getattr(task, "id", "")
    return Path(workspace_dir).expanduser() / slugify(label)


def write_deliverable_file(workspace_dir, task, deliverable: dict, content: str) -> str:
    """Persist a produced deliverable's content as a markdown file in the task's
    folder; return its path relative to the workspace root (for the asset + API).
    Recurring runs are timestamped so successive runs don't overwrite each other."""
    folder = task_dir(workspace_dir, task)
    folder.mkdir(parents=True, exist_ok=True)
    name = slugify(deliverable.get("description") or "deliverable")
    if getattr(task, "run_of", None):
        name = f"{datetime.now().strftime('%Y%m%d-%H%M')}-{name}"
    path = folder / f"{name}.md"
    path.write_text(content or "")
    return str(path.relative_to(Path(workspace_dir).expanduser()))


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


def resolve(workspace_dir, rel: str) -> Path | None:
    """Resolve a workspace-relative path to an absolute file path, or None if it
    escapes the workspace root (path-traversal guard) or isn't a file."""
    root = _root(workspace_dir)
    try:
        p = (root / (rel or "")).resolve()
    except Exception:
        return None
    inside = p == root or root in p.parents
    return p if inside and p.is_file() else None


def delete(workspace_dir, rel: str) -> bool:
    """Delete one workspace file (same sandbox guard as `resolve`). Returns True on
    success, False if the path doesn't resolve to a file inside the workspace. Also
    prunes now-empty parent folders (e.g. an emptied per-task subfolder) up to — but
    never including — the workspace root."""
    p = resolve(workspace_dir, rel)
    if p is None:
        return False
    root = _root(workspace_dir)
    try:
        p.unlink()
    except OSError:
        return False
    parent = p.parent
    while parent != root and root in parent.parents:
        try:
            parent.rmdir()  # only removes if empty
        except OSError:
            break
        parent = parent.parent
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
