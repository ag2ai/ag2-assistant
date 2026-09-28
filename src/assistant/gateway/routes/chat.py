"""Chats: list the resumable conversations, read one back, rename/star/retarget
it, delete it — and send a message into one.

Pairs with gateway/schemas/chat.py (the response models) and
web/src/schemas/chat.ts (their zod twins) — same file name in all three trees.

Also joins the ACP origin onto a chat row/transcript: a Peer with
``platform="acp"`` names the listener Connection a chat arrived on, and
``LIVE_SESSIONS`` (acp/chats.py) says whether a remote client is driving it right
now — see ADR 0034.
"""

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel

from assistant.acp.chats import LIVE_SESSIONS
from assistant.connections import ConnectionStore
from assistant.gateway.profile_manager import ProfileRuntime
from assistant.gateway.routes.common import chat_asker
from assistant.gateway.routes.deps import GatewayDeps
from assistant.gateway.schemas import (
    ChatListResponse,
    MessageResponse,
    Ok,
    TranscriptResponse,
)
from assistant.peers import Peer, PeerStore
from assistant.profiles import ACP_PLATFORM


def _origin_fields(
    chat_id: str, peer: Peer, connections: ConnectionStore, live_ids: frozenset[str]
) -> dict:
    """The three ``origin_*`` fields for a chat an ACP Peer owns."""
    listener = connections.get_acp_connection(peer.connection)
    name = listener.connection.name if listener is not None else peer.connection
    return {
        "origin_platform": ACP_PLATFORM,
        "origin_name": name,
        "origin_live": chat_id in live_ids,
    }


def _with_origin(rows: list[dict], peers: PeerStore, connections: ConnectionStore) -> list[dict]:
    """Join ACP origin onto every row that names an ACP chat — one PeerStore read
    for the whole list, not per row."""
    acp_peers = {
        chat: peer
        for peer in peers.list_peers()
        if peer.platform == ACP_PLATFORM
        for chat in peer.chats
    }
    if not acp_peers:
        return rows
    live_ids = LIVE_SESSIONS.live_chat_ids()
    return [
        {**row, **_origin_fields(row["chat_id"], acp_peers[row["chat_id"]], connections, live_ids)}
        if row["chat_id"] in acp_peers
        else row
        for row in rows
    ]


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

    @r.get("/chats", response_model=ChatListResponse, response_model_exclude_unset=True)
    async def chats(runtime: ProfileRuntime = Depends(get_runtime)):
        """List persisted, resumable conversations (newest first), each carrying
        its ACP origin when a Peer owns it."""
        rows = await runtime.require_gateway().list_chats()
        return {"chats": _with_origin(rows, d.peer_store, d.connection_store)}

    @r.get("/chats/{chat_id}", response_model=TranscriptResponse, response_model_exclude_unset=True)
    async def chat_transcript(chat_id: str, runtime: ProfileRuntime = Depends(get_runtime)):
        """The display transcript for a chat, plus its Chat override, the model it
        would run on right now (so the composer's switcher needs no second call),
        and its ACP origin when a Peer owns it."""
        gw = runtime.require_gateway()
        row = {
            "chat_id": chat_id,
            "messages": await gw.transcript(chat_id),
            "model": await gw.chat_model(chat_id),
            "effective_model": await gw.effective_model(chat_id),
        }
        peer = d.peer_store.peer_for_chat(chat_id)
        if peer is not None and peer.platform == ACP_PLATFORM:
            live_ids = LIVE_SESSIONS.live_chat_ids()
            row.update(_origin_fields(chat_id, peer, d.connection_store, live_ids))
        return row

    @r.post("/chats/{chat_id}/acp/close", response_model=Ok)
    async def close_acp_session(chat_id: str, runtime: ProfileRuntime = Depends(get_runtime)):
        """Owner's kill switch on a live ACP chat: the remote client is dropped
        cleanly, the Chat and its transcript stay (ADR 0034). 404 when ``chat_id``
        names no ACP chat, 409 when it is one but no session is live right now."""
        peer = d.peer_store.peer_for_chat(chat_id)
        if peer is None or peer.platform != ACP_PLATFORM:
            return Response(status_code=404)
        if not await LIVE_SESSIONS.close(chat_id):
            return JSONResponse({"error": "no live session on this chat"}, status_code=409)
        return {"ok": True}

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
