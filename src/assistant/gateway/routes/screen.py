"""Profile-scoped Screen discovery and expansion from Files."""

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from assistant.card_instances import InstanceError
from assistant.gateway.profile_manager import ProfileRuntime
from assistant.gateway.schemas.screen import ScreenListResponse, ScreenResponse, ScreenRow
from assistant.screens import list_screens, load_screen, rename_screen


class ScreenRenameRequest(BaseModel):
    path: str
    title: str


def build_profile_router(get_runtime) -> APIRouter:
    r = APIRouter()

    @r.get("/screens", response_model=ScreenListResponse)
    async def listing(runtime: ProfileRuntime = Depends(get_runtime)):
        return list_screens(runtime.require_gateway().config.workspace_dir)

    @r.patch("/screens/title", response_model=ScreenRow)
    async def rename(body: ScreenRenameRequest, runtime: ProfileRuntime = Depends(get_runtime)):
        try:
            return rename_screen(
                runtime.require_gateway().config.workspace_dir, body.path, body.title
            )
        except (InstanceError, OSError, ValueError) as exc:
            return JSONResponse(
                {"error": str(exc)},
                status_code=exc.status if isinstance(exc, InstanceError) else 400,
            )

    @r.get("/screens/view", response_model=ScreenResponse)
    async def view(path: str, runtime: ProfileRuntime = Depends(get_runtime)):
        try:
            return load_screen(
                runtime.require_gateway().config.workspace_dir,
                path,
                runtime.require_gateway().card_sources,
            )
        except (InstanceError, OSError, ValueError) as exc:
            return JSONResponse(
                {"error": str(exc)},
                status_code=exc.status if isinstance(exc, InstanceError) else 400,
            )

    return r
