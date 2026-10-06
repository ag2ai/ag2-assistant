"""Profile-scoped refresh and explicit approval of retained source code."""

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from assistant.card_instances import InstanceError
from assistant.gateway.profile_manager import ProfileRuntime
from assistant.gateway.schemas.card_source import CardSourceResponse


class SourceTarget(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: str = Field(default="", max_length=4096)
    chat_id: str = Field(default="", max_length=200)
    surface_id: str = Field(default="", max_length=200)
    source_id: str = Field(min_length=1, max_length=200)


class SourceApproval(SourceTarget):
    code_version: str = Field(min_length=64, max_length=64)
    approved: bool
    secrets: dict[str, str] = Field(default_factory=dict)


def build_profile_router(get_runtime, *, tools=None, executor=None) -> APIRouter:
    r = APIRouter()

    def service(runtime):
        sources = runtime.require_gateway().card_sources
        if tools is not None:
            sources.tools = {
                name: runner
                for name, runner in tools.items()
                if name in {"get_weather", "get_quotes"}
            }
        if executor is not None:
            sources.executor = executor
        return sources

    @r.post("/card-sources/refresh", response_model=CardSourceResponse)
    async def refresh(req: SourceTarget, runtime: ProfileRuntime = Depends(get_runtime)):
        try:
            return await service(runtime).refresh(**req.model_dump())
        except (InstanceError, OSError, ValueError) as exc:
            return JSONResponse(
                {"error": str(exc)},
                status_code=exc.status if isinstance(exc, InstanceError) else 400,
            )

    @r.post("/card-sources/approval", response_model=CardSourceResponse)
    async def approve(req: SourceApproval, runtime: ProfileRuntime = Depends(get_runtime)):
        try:
            return await service(runtime).approve(**req.model_dump())
        except (InstanceError, OSError, ValueError) as exc:
            return JSONResponse(
                {"error": str(exc)},
                status_code=exc.status if isinstance(exc, InstanceError) else 400,
            )

    return r
