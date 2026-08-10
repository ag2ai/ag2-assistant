"""Folders and their Grants — the install-wide registry of directories outside
the Root (ADR 0006). Mirrors web/src/schemas/folder.ts.

Two shapes answer the mutations, and the difference is the point: a create or an
update echoes the changed Folder next to the whole snapshot (the form re-renders
that one row), while a delete or a Grant change echoes the snapshot alone.
"""

from typing import Literal

from pydantic import BaseModel


class GrantOut(BaseModel):
    """One stored Grant: (profile, task, chat) → mode. Empty ``chat_id`` and
    ``task_id`` together mean profile scope. ``none`` is override-only — a chat- or
    task-scoped block over an inherited Folder — so it appears here but never as an
    effective mode (assistant/folders.py:37)."""

    profile: str
    chat_id: str
    task_id: str
    mode: Literal["read", "read_write", "none"]


class FolderOut(BaseModel):
    """One registered Folder. ``exists`` is checked per read rather than stored: a
    Folder whose directory was moved or unmounted stays registered and repointable,
    badged instead of erased (FolderStore._view)."""

    id: str
    name: str
    path: str
    exists: bool
    grants: list[GrantOut]


class FolderListResponse(BaseModel):
    """GET /api/folders."""

    folders: list[FolderOut]


class FolderSavedResponse(BaseModel):
    """POST /api/folders and POST /api/folders/{fid} — the changed Folder plus the
    snapshot around it."""

    ok: Literal[True]
    folder: FolderOut
    folders: list[FolderOut]


class FolderMutatedResponse(BaseModel):
    """DELETE /api/folders/{fid} and both Grant routes: the snapshot only, because
    the row the call touched may no longer exist (a Folder left with no grants is
    garbage-collected inside revoke_grant)."""

    ok: Literal[True]
    folders: list[FolderOut]


class FolderConflictResponse(BaseModel):
    """409 on create/update: the resolved path is already registered, and the body
    points at the Folder holding it so the client can offer that one instead of
    reporting a dead end. No ``ok`` key here — this is an error body that carries
    one extra field, not a success envelope."""

    error: str
    existing: FolderOut


class FolderRootOut(BaseModel):
    """One Folder root browsable in the open Thread. ``mode`` is the EFFECTIVE mode
    resolved for this profile∪task∪chat, so ``none`` cannot appear; a missing path
    rides along with ``exists: false`` rather than being dropped (ADR 0013)."""

    id: str
    name: str
    path: str
    mode: Literal["read", "read_write"]
    exists: bool


class FolderRootsResponse(BaseModel):
    """GET /api/p/{pid}/folders/roots — the tree's Thread-scoped Folder section."""

    roots: list[FolderRootOut]
