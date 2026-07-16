"""Folder registry + Grants (CONTEXT.md "Folders", ADR 0006).

A FOLDER is an install-wide, named registry entry for one directory outside the
Root — a name and a path, unique by resolved path. A GRANT links one profile or
one chat to a Folder with a mode: ``read`` or ``read_write`` (write implies
read; write-only is unrepresentable). Pure allowlist — no Folder (or no Grant to
it) means no access; there is no block concept. Effective access is the union of
the profile's Grants and the current chat's Grants; the most permissive covering
Grant wins, so Grants only ever widen.

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
MODES = (READ, READ_WRITE)


class DuplicatePath(ValueError):
    """A create/update tried to register a path another Folder already holds
    (Folders are unique by resolved path). Carries the existing Folder's view so
    the API layer can 409 with a pointer to it."""

    def __init__(self, existing: dict):
        super().__init__(f"this path is already registered as {existing['name']!r}")
        self.existing = existing


class FolderStore:
    """Persistent record of Folders and the Grants profiles/chats hold on them."""

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
        self._folders = [f for f in raw_folders if isinstance(f, dict)] if isinstance(raw_folders, list) else []
        self._grants = [g for g in raw_grants if isinstance(g, dict)] if isinstance(raw_grants, list) else []

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
                {"profile": g.get("profile", ""), "chat_id": g.get("chat_id", ""), "mode": g.get("mode", READ)}
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

    def set_grant(self, fid: str, mode: str, *, profile: str, chat_id: str = "") -> dict:
        """Upsert the Grant (folder, profile, chat) → mode. An empty chat_id is a
        profile-scope Grant. KeyError unknown folder; ValueError bad mode/profile."""
        if mode not in MODES:
            raise ValueError(f"mode must be one of {', '.join(MODES)}")
        profile = (profile or "").strip()
        if not profile:
            raise ValueError("profile is required")
        chat_id = (chat_id or "").strip()
        with self._mutate():
            entry = self._find(fid)
            if entry is None:
                raise KeyError(fid)
            self._grants = [
                g for g in self._grants
                if not (g.get("folder_id") == entry["id"] and g.get("profile") == profile and g.get("chat_id", "") == chat_id)
            ]
            self._grants.append({"folder_id": entry["id"], "profile": profile, "chat_id": chat_id, "mode": mode})
            self._save()
            return self._view(entry)

    def revoke_grant(self, fid: str, *, profile: str, chat_id: str = "") -> bool:
        fid = (fid or "").strip()
        profile = (profile or "").strip()
        chat_id = (chat_id or "").strip()
        with self._mutate():
            before = len(self._grants)
            self._grants = [
                g for g in self._grants
                if not (g.get("folder_id") == fid and g.get("profile") == profile and g.get("chat_id", "") == chat_id)
            ]
            if len(self._grants) == before:
                return False
            self._save()
            return True

    def grant_path(self, path, mode: str, profile: str, chat_id: str = "") -> dict:
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
        return self.set_grant(entry["id"], mode, profile=profile, chat_id=chat_id)

    # --- the enforcement query ---

    def mode_for(self, folder, profile: str, chat_id: str = "") -> str | None:
        """The effective mode for ``folder`` in ``profile`` (and optionally one
        chat): ``read_write`` | ``read`` | None. Union semantics — profile-scope
        Grants plus the given chat's Grants all apply; a Grant covers its folder
        and every subpath; the most permissive covering Grant wins (ADR 0006)."""
        self._refresh()
        f = _norm(folder)
        profile = (profile or "").strip()
        chat_id = (chat_id or "").strip()
        by_id = {x.get("id"): x for x in self._folders}
        best: str | None = None
        for g in self._grants:
            if g.get("profile") != profile:
                continue
            g_chat = g.get("chat_id", "")
            if g_chat and g_chat != chat_id:
                continue
            entry = by_id.get(g.get("folder_id"))
            if entry is None:
                continue
            gp = Path(entry.get("path", ""))
            if f != gp and gp not in f.parents:
                continue
            if g.get("mode") == READ_WRITE:
                return READ_WRITE
            best = READ
        return best
