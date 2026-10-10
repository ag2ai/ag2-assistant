"""Explicit shell Save for a current Card draft in this Profile's Chat."""

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict

from assistant.card_drafts import DraftError
from assistant.cards import CardError
from assistant.gateway.profile_manager import ProfileRuntime
from assistant.gateway.schemas.card import CardSaveResponse


class CardSaveRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    draft_id: str
    expected_version: int
    surface_id: str
    name: str
    filename: str
    request_id: str
    replace: bool = False
    conflict_token: str = ""


def build_profile_router(deps, get_runtime) -> APIRouter:
    r = APIRouter()

    @r.post("/chats/{chat_id}/cards/save", response_model=CardSaveResponse)
    async def save_card(
        chat_id: str, request: CardSaveRequest, runtime: ProfileRuntime = Depends(get_runtime)
    ):
        try:
            return await runtime.require_gateway().save_card_definition(
                chat_id, **request.model_dump()
            )
        except DraftError as exc:
            return JSONResponse({"error": str(exc)}, status_code=409)
        except CardError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        except OSError as exc:
            return JSONResponse({"error": str(exc)}, status_code=500)

    return r
