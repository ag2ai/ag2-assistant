"""Folders: directories outside the Root, registered install-wide and reached
through per-profile/task/chat Grants (ADR 0006).

The registry itself is install-level, so its CRUD is unprefixed; only the
"which roots may this Thread browse" question is profile-scoped, and it lives in
build_profile_router below rather than in file.py because its zod twin
(``FolderRoots``) is declared in folder.ts.

Pairs with gateway/schemas/folder.py (the response models) and
web/src/schemas/folder.ts (their zod twins) — same file name in all three trees.
"""

from pathlib import Path

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from assistant.folders import DuplicatePath, FolderStore
from assistant.gateway.profile_manager import ProfileRuntime
from assistant.gateway.routes.common import scope_task_id
from assistant.gateway.routes.deps import GatewayDeps
from assistant.gateway.schemas import (
    FolderConflictResponse,
    FolderListResponse,
    FolderMutatedResponse,
    FolderRootsResponse,
    FolderSavedResponse,
)

# The two writes that can collide on a path answer 409 with the Folder already
# holding it, so the client can point at that one instead of reporting a dead end.
CONFLICT_RESPONSE = {409: {"model": FolderConflictResponse, "description": "Conflict"}}


class FolderCreateRequest(BaseModel):
    path: str
    name: str = ""


class FolderUpdateRequest(BaseModel):
    name: str | None = None
    path: str | None = None


class FolderGrantRequest(BaseModel):
    profile: str
    chat_id: str = ""
    task_id: str = ""
    mode: str


class FolderGrantDeleteRequest(BaseModel):
    profile: str
    chat_id: str = ""
    task_id: str = ""


def build_router(d: GatewayDeps) -> APIRouter:
    """The install-wide Folder registry and its Grants, in the order they had in
    app.py."""
    r = APIRouter()

    def _folder_store():
        """A fresh FolderStore over the install-wide file. mtime self-refresh means
        live turns pick up any change on their next check — no manager.reload()."""
        return FolderStore(d.paths.root / "folders.json")

    def _folders_snapshot(store) -> dict:
        return {"folders": store.list_folders()}

    @r.get("/api/folders", response_model=FolderListResponse)
    async def get_folders():
        """Every Folder with its path-exists badge and its Grants."""
        return _folders_snapshot(_folder_store())

    @r.post("/api/folders", response_model=FolderSavedResponse, responses=CONFLICT_RESPONSE)
    async def create_folder(req: FolderCreateRequest):
        """Register a directory as a Folder. 400 for a non-directory; 409 with a
        pointer when the resolved path is already registered (path-unique)."""
        fp = Path(req.path or "").expanduser()
        if not req.path.strip() or not fp.is_dir():
            return JSONResponse({"error": "not a directory"}, status_code=400)
        store = _folder_store()
        try:
            view = store.create_folder(req.path, name=req.name)
        except DuplicatePath as exc:
            return JSONResponse({"error": str(exc), "existing": exc.existing}, status_code=409)
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        return {"ok": True, "folder": view, **_folders_snapshot(store)}

    @r.post("/api/folders/{fid}", response_model=FolderSavedResponse, responses=CONFLICT_RESPONSE)
    async def update_folder(fid: str, req: FolderUpdateRequest):
        """Rename and/or repoint a Folder. 404 unknown; 409 path collision."""
        store = _folder_store()
        try:
            view = store.update_folder(fid, name=req.name, path=req.path)
        except KeyError:
            return JSONResponse({"error": f"unknown folder: {fid}"}, status_code=404)
        except DuplicatePath as exc:
            return JSONResponse({"error": str(exc), "existing": exc.existing}, status_code=409)
        return {"ok": True, "folder": view, **_folders_snapshot(store)}

    @r.delete("/api/folders/{fid}", response_model=FolderMutatedResponse)
    async def delete_folder(fid: str):
        """Delete a Folder — always allowed; every Grant to it is revoked instantly."""
        store = _folder_store()
        if not store.delete_folder(fid):
            return JSONResponse({"error": f"unknown folder: {fid}"}, status_code=404)
        return {"ok": True, **_folders_snapshot(store)}

    @r.post("/api/folders/{fid}/grants", response_model=FolderMutatedResponse)
    async def set_folder_grant(fid: str, req: FolderGrantRequest):
        """Upsert one Grant: (profile, task, chat) → mode. Empty chat_id+task_id = profile-scope."""
        store = _folder_store()
        try:
            store.set_grant(
                fid, req.mode, profile=req.profile, chat_id=req.chat_id, task_id=req.task_id
            )
        except KeyError:
            return JSONResponse({"error": f"unknown folder: {fid}"}, status_code=404)
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        return {"ok": True, **_folders_snapshot(store)}

    @r.delete("/api/folders/{fid}/grants", response_model=FolderMutatedResponse)
    async def revoke_folder_grant(fid: str, req: FolderGrantDeleteRequest):
        """Revoke one Grant: (profile, task, chat) → mode. Empty chat_id+task_id = profile-scope.
        404 when no such Grant exists."""
        store = _folder_store()
        if not store.revoke_grant(
            fid, profile=req.profile, chat_id=req.chat_id, task_id=req.task_id
        ):
            return JSONResponse({"error": "no such grant"}, status_code=404)
        # A Folder left with no grants is garbage-collected inside revoke_grant, so it
        # is uniform across every revoke path (CLI, API, task deletion) — see FolderStore._gc.
        return {"ok": True, **_folders_snapshot(store)}

    return r


def build_profile_router(d: GatewayDeps, get_runtime) -> APIRouter:
    """The one profile-scoped Folder surface: which roots this Thread may browse."""
    r = APIRouter()

    @r.get("/folders/roots", response_model=FolderRootsResponse)
    async def list_folder_roots(chat_id: str = "", runtime: ProfileRuntime = Depends(get_runtime)):
        """The Folder roots browsable in the open Thread — the tree's Thread-scoped
        Folder section (ADR 0013). Each root: ``{id, name, path (absolute), mode,
        exists}``, resolved through the same ``mode_for`` truth the ``@``-picker and the
        agent's reads share, scoped by ``chat_id`` plus the task decoded from it (a Task
        page carries ``task:{id}``, a run thread ``task-run:{run_id}``), so the open
        Task/run's task-scope Folders surface, not just profile grants (absent scope →
        profile-level grants only). A missing-path Folder is included as a badged,
        repointable root (``exists: false``), never an error."""
        gw = runtime.gateway
        folders = gw.folders if gw is not None else None
        if folders is None:
            return {"roots": []}
        task_id = await scope_task_id(runtime, chat_id)
        return {"roots": folders.granted_roots(runtime.config.data_dir.name, chat_id, task_id)}

    return r
