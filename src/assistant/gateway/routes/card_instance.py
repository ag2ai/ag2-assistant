"""Profile-scoped instance Save and validated Files previews."""

import json

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from assistant.card_instances import InstanceError, read_instance
from assistant.gateway.profile_manager import ProfileRuntime
from assistant.gateway.routes.deps import GatewayDeps
from assistant.gateway.schemas.card_instance import CardInstanceResponse, CardInstanceSaveResponse
from assistant.workspace import _MAX_WRITE_BYTES


class CardInstanceSaveRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    surface_id: str = Field(min_length=1, max_length=200)
    message: dict
    path: str = Field(min_length=1, max_length=4096)
    request_id: str = Field(min_length=1, max_length=200)


def build_profile_router(d: GatewayDeps, get_runtime) -> APIRouter:
    r = APIRouter()

    @r.post("/chats/{chat_id}/card-instances/save", response_model=CardInstanceSaveResponse)
    async def save_instance(
        chat_id: str, request: Request, runtime: ProfileRuntime = Depends(get_runtime)
    ):
        chunks = []
        size = 0
        async for chunk in request.stream():
            size += len(chunk)
            if size > _MAX_WRITE_BYTES:
                return JSONResponse({"error": "File too large"}, status_code=413)
            chunks.append(chunk)
        try:
            payload = CardInstanceSaveRequest.model_validate_json(b"".join(chunks), strict=True)
            return await runtime.require_gateway().save_card_instance(
                chat_id, **payload.model_dump()
            )
        except (ValidationError, json.JSONDecodeError) as exc:
            return JSONResponse({"error": str(exc)}, status_code=422)
        except InstanceError as exc:
            return JSONResponse({"error": str(exc)}, status_code=exc.status)
        except OSError as exc:
            return JSONResponse({"error": f"File could not be saved: {exc}"}, status_code=500)

    @r.get("/card-instances", response_model=CardInstanceResponse)
    async def get_instance(path: str, runtime: ProfileRuntime = Depends(get_runtime)):
        try:
            return read_instance(runtime.require_config().workspace_dir, path)
        except InstanceError as exc:
            return JSONResponse({"error": str(exc)}, status_code=exc.status)
        except OSError as exc:
            return JSONResponse({"error": f"File could not be read: {exc}"}, status_code=400)

    return r
