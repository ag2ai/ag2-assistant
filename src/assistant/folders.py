"""Folder registry + Grants (CONTEXT.md "Folders", ADR 0006).

A FOLDER is an install-wide, named registry entry for one directory outside the
Root — a name and a path, unique by resolved path. A GRANT links one profile, one
task, or one chat to a Folder with a mode: ``read`` or ``read_write`` (write
implies read; write-only is unrepresentable), plus ``none`` — an override-only
mode that blocks a profile-granted Folder for that one task or chat. No Folder
(or no covering Grant) still means no access.

Resolution (ADR 0006, amended): per Folder the override chain is chat > task >
profile — a task-scoped Grant overrides the profile-scope Grant for that task,
and a chat-scoped Grant overrides both for that chat. Each level may widen
(``read`` → ``read_write``), narrow (``read_write`` → ``read``), or block
entirely (``none``) the one it inherits, affecting only its own scope and never
the wider one. With no override the next level down stands. Across nested
Folders that all cover a path, the most permissive surviving Grant wins.

One install-wide JSON document (``root_dir/folders.json``) with the same
concurrency machinery as the permission store: mtime self-refresh (a long-lived
gateway instance sees CLI/API writes), a cross-process file lock around every
read-modify-write, and atomic replace on save. A Folder whose path no longer
exists on disk is a badged, repointable state (``exists: false``), never an
error. Deleting a Folder is always allowed and drops every Grant to it.
"""

import contextlib
import json
import os
import tempfile
from pathlib import Path
from secrets import token_hex

from assistant.permissions import _lock_exclusive, _norm, _unlock

READ = "read"
READ_WRITE = "read_write"
NONE = "none"  # override-only (chat/task scope): block an inherited Folder
MODES = (READ, READ_WRITE)


class DuplicatePath(ValueError):
    """A create/update tried to register a path another Folder already holds
    (Folders are unique by resolved path). Carries the existing Folder's view so
    the API layer can 409 with a pointer to it."""

    def __init__(self, existing: dict):
        super().__init__(f"this path is already registered as {existing['name']!r}")
        self.existing = existing


class FolderStore:
    """Persistent record of Folders and the Grants profiles, tasks and chats hold
    on them."""

    def __init__(self, path: Path | None) -> None:
        # ``path`` is REQUIRED (no global default). Pass ``None`` only for an
        # explicit ephemeral, non-persisting store (un-wired fallback / tests).
        self._path = Path(path) if path is not None else None
        self._folders: list[dict] = []
        self._grants: list[dict] = []
        self._stat: tuple[int, int] | None = None
        self._load()

    # --- persistence (same shape as PermissionStore: refresh / lock / atomic) ---

    def _load(self) -> None:
        self._folders, self._grants = [], []
        self._stat = None
        if self._path is None:
            return
        try:
            st = self._path.stat()
        except OSError:
            return
        self._stat = (st.st_mtime_ns, st.st_size)
        try:
            data = json.loads(self._path.read_text())
        except Exception:
            return
        if not isinstance(data, dict):
            return
        raw_folders = data.get("folders")
        raw_grants = data.get("grants")
        self._folders = (
            [f for f in raw_folders if isinstance(f, dict)] if isinstance(raw_folders, list) else []
        )
        self._grants = (
            [g for g in raw_grants if isinstance(g, dict)] if isinstance(raw_grants, list) else []
        )

    def _refresh(self) -> None:
        if self._path is None:
            return
        try:
            st = self._path.stat()
            current: tuple[int, int] | None = (st.st_mtime_ns, st.st_size)
        except OSError:
            current = None
        if current != self._stat:
            self._load()

    @contextlib.contextmanager
    def _mutate(self):
        if self._path is None:
            yield
            return
        self._path.parent.mkdir(parents=True, exist_ok=True)
        lock_path = self._path.parent / (self._path.name + ".lock")
        with open(lock_path, "w") as lock:
            _lock_exclusive(lock)
            try:
                self._refresh()
                yield
            finally:
                _unlock(lock)

    def _save(self) -> None:
        if self._path is None:
            return
        self._path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps({"folders": self._folders, "grants": self._grants}, indent=2)
        fd, tmp = tempfile.mkstemp(dir=str(self._path.parent), prefix=".folders.", suffix=".tmp")
        try:
            with os.fdopen(fd, "w") as f:
                f.write(payload)
            os.replace(tmp, self._path)
        except Exception:
            with contextlib.suppress(OSError):
                os.unlink(tmp)
            raise
        try:
            st = self._path.stat()
            self._stat = (st.st_mtime_ns, st.st_size)
        except OSError:
            self._stat = None

    # --- views ---

    def _view(self, f: dict) -> dict:
        return {
            "id": f.get("id", ""),
            "name": f.get("name", ""),
            "path": f.get("path", ""),
            "exists": Path(f.get("path", "")).is_dir(),
            "grants": [
                {
                    "profile": g.get("profile", ""),
                    "chat_id": g.get("chat_id", ""),
                    "task_id": g.get("task_id", ""),
                    "mode": g.get("mode", READ),
                }
                for g in self._grants
                if g.get("folder_id") == f.get("id")
            ],
        }

    def _find(self, fid: str) -> dict | None:
        fid = (fid or "").strip()
        return next((f for f in self._folders if f.get("id") == fid), None)

    def _find_by_path(self, resolved: str) -> dict | None:
        return next((f for f in self._folders if f.get("path") == resolved), None)

    def list_folders(self) -> list[dict]:
        self._refresh()
        return [self._view(f) for f in self._folders]

    def get_folder(self, fid: str) -> dict | None:
        self._refresh()
        f = self._find(fid)
        return self._view(f) if f else None

    # --- folder CRUD ---

    def create_folder(self, path, name: str = "") -> dict:
        """Register a directory as a Folder. ValueError on an empty path;
        DuplicatePath when the resolved path is already registered. The name
        defaults to the directory's basename (renameable later)."""
        raw = str(path or "").strip()
        if not raw:
            raise ValueError("folder path is required")
        resolved = str(_norm(raw))
        with self._mutate():
            dup = self._find_by_path(resolved)
            if dup:
                raise DuplicatePath(self._view(dup))
            entry = {
                "id": "f_" + token_hex(4),
                "name": (name or "").strip() or (Path(resolved).name or resolved),
                "path": resolved,
            }
            self._folders.append(entry)
            self._save()
            return self._view(entry)

    def update_folder(self, fid: str, *, name: str | None = None, path: str | None = None) -> dict:
        """Rename and/or repoint a Folder (None leaves a field unchanged).
        KeyError for an unknown id; DuplicatePath when the new path collides."""
        with self._mutate():
            entry = self._find(fid)
            if entry is None:
                raise KeyError(fid)
            if path is not None and str(path).strip():
                resolved = str(_norm(str(path).strip()))
                dup = self._find_by_path(resolved)
                if dup and dup.get("id") != entry["id"]:
                    raise DuplicatePath(self._view(dup))
                entry["path"] = resolved
            if name is not None and str(name).strip():
                entry["name"] = str(name).strip()
            self._save()
            return self._view(entry)

    def delete_folder(self, fid: str) -> bool:
        """Remove a Folder (False if unknown). Always allowed — every Grant to it
        is dropped instantly, revoking access everywhere at once (ADR 0006)."""
        with self._mutate():
            entry = self._find(fid)
            if entry is None:
                return False
            self._folders = [f for f in self._folders if f.get("id") != entry["id"]]
            self._grants = [g for g in self._grants if g.get("folder_id") != entry["id"]]
            self._save()
            return True

    # --- grants ---

    def set_grant(
        self, fid: str, mode: str, *, profile: str, chat_id: str = "", task_id: str = ""
    ) -> dict:
        """Upsert the Grant (folder, profile, task, chat) → mode. Empty chat_id and
        task_id = profile-scope; a task_id (no chat_id) = task-scope; a chat_id =
        chat-scope. Never both. ``none`` is an override-only mode (blocks an
        inherited Folder for that one chat/task). KeyError unknown folder;
        ValueError bad mode/profile/scope."""
        profile = (profile or "").strip()
        chat_id = (chat_id or "").strip()
        task_id = (task_id or "").strip()
        if chat_id and task_id:
            raise ValueError("a grant is chat-scoped or task-scoped, not both")
        if mode == NONE:
            if not (chat_id or task_id):
                raise ValueError("mode 'none' is only valid for a chat- or task-scoped grant")
        elif mode not in MODES:
            raise ValueError(f"mode must be one of {', '.join(MODES)}")
        if not profile:
            raise ValueError("profile is required")
        with self._mutate():
            entry = self._find(fid)
            if entry is None:
                raise KeyError(fid)
            self._grants = [
                g
                for g in self._grants
                if not (
                    g.get("folder_id") == entry["id"]
                    and g.get("profile") == profile
                    and g.get("chat_id", "") == chat_id
                    and g.get("task_id", "") == task_id
                )
            ]
            self._grants.append(
                {
                    "folder_id": entry["id"],
                    "profile": profile,
                    "chat_id": chat_id,
                    "task_id": task_id,
                    "mode": mode,
                }
            )
            self._save()
            return self._view(entry)

    def revoke_grant(self, fid: str, *, profile: str, chat_id: str = "", task_id: str = "") -> bool:
        fid = (fid or "").strip()
        profile = (profile or "").strip()
        chat_id = (chat_id or "").strip()
        task_id = (task_id or "").strip()
        with self._mutate():
            before = len(self._grants)
            self._grants = [
                g
                for g in self._grants
                if not (
                    g.get("folder_id") == fid
                    and g.get("profile") == profile
                    and g.get("chat_id", "") == chat_id
                    and g.get("task_id", "") == task_id
                )
            ]
            if len(self._grants) == before:
                return False
            self._save()
            return True

    def grant_path(
        self, path, mode: str, profile: str, chat_id: str = "", task_id: str = ""
    ) -> dict:
        """Find-or-create the Folder for ``path`` and upsert a Grant on it — the
        HITL mint path (approving a runtime prompt auto-creates the Folder,
        auto-named from the directory's basename, renameable later)."""
        resolved = str(_norm(str(path)))
        with self._mutate():
            entry = self._find_by_path(resolved)
            if entry is None:
                entry = {
                    "id": "f_" + token_hex(4),
                    "name": Path(resolved).name or resolved,
                    "path": resolved,
                }
                self._folders.append(entry)
                self._save()
        return self.set_grant(entry["id"], mode, profile=profile, chat_id=chat_id, task_id=task_id)

    def drop_task(self, task_id: str) -> None:
        """Remove every task-scope Grant of ``task_id`` — task deletion revokes the
        task's folder access everywhere at once (mirrors delete_folder)."""
        task_id = (task_id or "").strip()
        if not task_id:
            return
        with self._mutate():
            before = len(self._grants)
            self._grants = [g for g in self._grants if g.get("task_id", "") != task_id]
            if len(self._grants) != before:
                self._save()

    # --- the enforcement query ---

    def _resolved_folders(self, profile: str, chat_id: str = ""):
        """Yield ``(resolved path, folder entry, effective mode)`` for every granted
        Folder resolving to a non-``None`` mode for this profile∪chat."""
        self._refresh()
        for f in self._folders:
            try:
                p = Path(f.get("path", "")).expanduser().resolve()
            except (OSError, ValueError, RuntimeError):
                continue
            mode = self.mode_for(p, profile, chat_id)
            if mode is not None:
                yield p, f, mode

    def readable_roots(self, profile: str, chat_id: str = "") -> list[Path]:
        """On-disk, top-level granted-Folder paths readable for this profile∪chat:
        every non-``None`` ``mode_for``, minus roots nested under another readable root."""
        readable = [p for p, _, _ in self._resolved_folders(profile, chat_id) if p.is_dir()]
        return [p for p in readable if not any(p != o and o in p.parents for o in readable)]

    def granted_roots(self, profile: str, chat_id: str = "") -> list[dict]:
        """Folder roots browsable for this profile∪chat as ``{id, name, path, mode,
        exists}`` — non-``None`` ``mode_for``, deduped under existing roots, keeping missing paths."""
        granted = [
            (
                p,
                {
                    "id": f.get("id", ""),
                    "name": f.get("name", ""),
                    "path": str(p),
                    "mode": mode,
                    "exists": p.is_dir(),
                },
            )
            for p, f, mode in self._resolved_folders(profile, chat_id)
        ]
        existing = [p for p, v in granted if v["exists"]]
        return [v for p, v in granted if not any(o in p.parents for o in existing)]

    def mode_for_path(
        self, abs_path: str | os.PathLike[str], profile: str, chat_id: str = ""
    ) -> str | None:
        """Effective mode for an absolute path (``read_write`` | ``read`` | None):
        ``None`` unless it resolves inside a readable Folder root (traversal guard)."""
        return self.resolve_within(abs_path, profile, chat_id)[1]

    def resolve_within(
        self, abs_path: str | os.PathLike[str], profile: str, chat_id: str = ""
    ) -> tuple[Path | None, str | None]:
        """For an absolute path, ``(containing readable Folder root, effective mode)``
        or ``(None, None)`` if it resolves under no granted root — the shared resolver."""
        try:
            p = Path(abs_path).expanduser().resolve()
        except (OSError, ValueError, RuntimeError):
            return None, None
        root = next(
            (r for r in self.readable_roots(profile, chat_id) if p == r or r in p.parents), None
        )
        if root is None:
            return None, None
        return root, self.mode_for(p, profile, chat_id)

    def mode_for(self, folder, profile: str, chat_id: str = "", task_id: str = "") -> str | None:
        """The effective mode for ``folder`` in ``profile`` (optionally within one
        task and/or one chat): ``read_write`` | ``read`` | None. Per Folder the
        override chain is chat > task > profile — each level may widen, narrow, or
        (``none``) block the inherited one, affecting only its own scope. A Grant
        covers its folder and every subpath; across covering Folders the most
        permissive surviving Grant wins (ADR 0006, amended)."""
        self._refresh()
        f = _norm(folder)
        profile = (profile or "").strip()
        chat_id = (chat_id or "").strip()
        task_id = (task_id or "").strip()
        rank = {READ: 1, READ_WRITE: 2}
        best: str | None = None
        for entry in self._folders:
            gp = Path(entry.get("path", ""))
            if f != gp and gp not in f.parents:
                continue
            fid = entry.get("id")
            chat_mode = task_mode = prof_mode = None
            for g in self._grants:
                if g.get("profile") != profile or g.get("folder_id") != fid:
                    continue
                g_chat = g.get("chat_id", "")
                g_task = g.get("task_id", "")
                if chat_id and g_chat == chat_id:
                    chat_mode = g.get("mode")
                elif task_id and g_task == task_id and not g_chat:
                    task_mode = g.get("mode")
                elif not g_chat and not g_task:
                    prof_mode = g.get("mode")
            eff = (
                chat_mode
                if chat_mode is not None
                else (task_mode if task_mode is not None else prof_mode)
            )
            if eff is None or eff == NONE:
                continue
            if eff == READ_WRITE:
                return READ_WRITE
            if rank.get(eff, 0) > rank.get(best, 0):
                best = eff
        return best
