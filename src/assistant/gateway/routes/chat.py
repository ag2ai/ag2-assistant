"""Chats: list the resumable conversations, read one back, rename/star/retarget
it, delete it — and send a message into one.

Pairs with gateway/schemas/chat.py (the response models) and
web/src/schemas/chat.ts (their zod twins) — same file name in all three trees.
"""

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel

from assistant.gateway.profile_manager import ProfileRuntime
from assistant.gateway.routes.common import chat_asker
from assistant.gateway.routes.deps import GatewayDeps
from assistant.gateway.schemas import (
    ChatListResponse,
    MessageResponse,
    Ok,
    TranscriptResponse,
)


class ChatPatch(BaseModel):
    """Partial chat-metadata update: rename, star, and/or set the Chat override.
    Absent field = unchanged; ``model=""`` clears the override back to inheriting."""

    title: str | None = None
    starred: bool | None = None
    model: str | None = None


class MessageRequest(BaseModel):
    text: str
    chat_id: str = "default"
    platform: str | None = None
    # No model field: a choice made before the Chat existed rides the WebSocket frame,
    # and a Channel resolves its own Pending override in the router (ADR 0025).


def build_profile_router(d: GatewayDeps, get_runtime) -> APIRouter:
    """The /api/p/{pid} chat slice. Registration order below is the order these
    handlers had in app.py — see AGENTS.md on route order."""
    r = APIRouter()

    # ---- Chats ----

    @r.get("/chats", response_model=ChatListResponse)
    async def chats(runtime: ProfileRuntime = Depends(get_runtime)):
        """List persisted, resumable conversations (newest first)."""
        return {"chats": await runtime.require_gateway().list_chats()}

    @r.get("/chats/{chat_id}", response_model=TranscriptResponse)
    async def chat_transcript(chat_id: str, runtime: ProfileRuntime = Depends(get_runtime)):
        """The display transcript for a chat, plus its Chat override and the model it
        would run on right now (so the composer's switcher needs no second call)."""
        return {
            "chat_id": chat_id,
            "messages": await runtime.require_gateway().transcript(chat_id),
            "model": await runtime.require_gateway().chat_model(chat_id),
            "effective_model": await runtime.require_gateway().effective_model(chat_id),
        }

    @r.delete("/chats/{chat_id}", response_model=Ok)
    async def delete_chat(chat_id: str, runtime: ProfileRuntime = Depends(get_runtime)):
        """Permanently delete a chat (transcript + full event log). Irreversible."""
        removed = await runtime.require_gateway().delete_chat(chat_id)
        if not removed:
            return Response(status_code=404)
        return {"ok": True}

    @r.patch("/chats/{chat_id}", response_model=Ok)
    async def update_chat(
        chat_id: str, patch: ChatPatch, runtime: ProfileRuntime = Depends(get_runtime)
    ):
        """Rename, star, and/or set the Chat override. 400 on an empty patch, 404 on
        unknown chat."""
        if patch.title is None and patch.starred is None and patch.model is None:
            return JSONResponse({"error": "empty patch"}, status_code=400)
        ok = await runtime.require_gateway().update_chat(
            chat_id, title=patch.title, starred=patch.starred, model=patch.model
        )
        if not ok:
            return Response(status_code=404)
        return {"ok": True}

    # ---- Message ----

    @r.post("/message", response_model=MessageResponse)
    async def message(req: MessageRequest, runtime: ProfileRuntime = Depends(get_runtime)):
        # Durable, inline HITL bound to this chat (answerable from the
        # thread or the strip); the request blocks until answered (or times out).
        asker = chat_asker(runtime, req.chat_id)
        reply = await runtime.require_gateway().send_message(
            req.text, chat_id=req.chat_id, asker=asker
        )
        return MessageResponse(reply=reply, chat_id=req.chat_id)

    return r
