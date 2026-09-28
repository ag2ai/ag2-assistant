"""ACP listeners: install-level, one listener bound to one Profile at creation
(ADR 0031) — never exposure-gated, so this router carries no exposure/default
routes the way gateway/routes/connection.py does for messaging platforms.

Pairs with gateway/schemas/acp.py (the response models) and
web/src/schemas/acp.ts (their zod twins) — same file name in all three trees.
"""

import secrets

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from assistant.connections import AcpConnection
from assistant.gateway.routes.deps import GatewayDeps
from assistant.gateway.schemas.acp import (
    AcpListenerCreatedResponse,
    AcpListenerListResponse,
    AcpListenerOut,
    AcpListenerTokenRotatedResponse,
)
from assistant.gateway.schemas.primitives import Ok


class AcpListenerCreateRequest(BaseModel):
    profile: str
    # Omitted (or null) registers a stdio listener: no socket, no token, nothing
    # started here — its client launches `ag2-assistant acp --connection <name>`
    # itself. A port registers the WebSocket door and starts it at once.
    port: int | None = None
    name: str = ""  # blank takes the next free "ACP"/"ACP 2"/... default name
    token: str = ""  # blank generates one, returned once in the response


def _generate_token() -> str:
    """A fresh shared secret for a listener that did not bring its own."""
    return secrets.token_urlsafe(24)


def build_router(d: GatewayDeps) -> APIRouter:
    """The ACP listener routes: list, create (+start), stop/start/rotate, delete."""
    r = APIRouter()

    def _entry(listener: AcpConnection) -> AcpListenerOut:
        """One listener as the API shows it: identity, whether it is actually
        live right now, why not, and a token presence flag — never the value."""
        cid = listener.connection.id
        return AcpListenerOut(
            id=cid,
            name=listener.connection.name,
            profile=listener.profile,
            port=listener.port,
            running=cid in d.manager.acp_listeners,
            error=d.manager.acp_listener_errors.get(cid),
            has_token=bool(d.connection_store.acp_token_for(cid)),
        )

    @r.get("/api/acp/listeners", response_model=AcpListenerListResponse)
    async def list_acp_listeners():
        """Every configured ACP listener, in creation order."""
        return {"listeners": [_entry(c) for c in d.connection_store.list_acp_connections()]}

    @r.post("/api/acp/listeners", response_model=AcpListenerCreatedResponse)
    async def create_acp_listener(req: AcpListenerCreateRequest):
        """Register a listener fixed to ``profile`` and start it at once; one that
        will not start (bad profile, taken port) still records why and comes back
        200 — the same honesty pattern as a channel Connection. Unknown/archived
        profile → 400.

        A request with no ``port`` registers a stdio listener instead: there is no
        socket to bind and no upgrade request to carry a token, so nothing is
        started and no secret is minted. The record exists so
        ``ag2-assistant acp --connection <name>`` can name it, which is what files
        that client's sessions under their own id rather than the shared
        ``acp:stdio``."""
        stdio = req.port is None
        token = "" if stdio else ((req.token or "").strip() or _generate_token())
        try:
            listener = d.connection_store.create_acp_connection(
                req.profile, name=req.name, port=req.port, token=token
            )
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        if not stdio:
            await d.manager.start_acp_listener(listener.connection.id)
        return {"listener": _entry(listener), "token": token}

    @r.delete("/api/acp/listeners/{cid}", response_model=Ok)
    async def delete_acp_listener(cid: str):
        """Stop the listener if it is live and forget it with its token.
        Unknown → 404."""
        if d.connection_store.get_acp_connection(cid) is None:
            return JSONResponse({"error": f"unknown acp listener: {cid}"}, status_code=404)
        await d.manager.stop_acp_listener(cid)
        d.connection_store.delete_acp_connection(cid)
        return {"ok": True}

    @r.post("/api/acp/listeners/{cid}/stop", response_model=AcpListenerOut)
    async def stop_acp_listener(cid: str):
        """Stop this listener, dropping its clients cleanly. Unknown → 404;
        already stopped is a no-op, not an error."""
        listener = d.connection_store.get_acp_connection(cid)
        if listener is None:
            return JSONResponse({"error": f"unknown acp listener: {cid}"}, status_code=404)
        await d.manager.stop_acp_listener(cid)
        return _entry(listener)

    @r.post("/api/acp/listeners/{cid}/start", response_model=AcpListenerOut)
    async def start_acp_listener(cid: str):
        """Start this listener on its bound profile's running agent. A failure
        (port taken, profile not running) records the reason and stays down;
        still answers 200. Unknown → 404."""
        listener = d.connection_store.get_acp_connection(cid)
        if listener is None:
            return JSONResponse({"error": f"unknown acp listener: {cid}"}, status_code=404)
        await d.manager.start_acp_listener(cid)
        return _entry(listener)

    @r.post("/api/acp/listeners/{cid}/rotate-token", response_model=AcpListenerTokenRotatedResponse)
    async def rotate_acp_listener_token(cid: str):
        """Mint a fresh shared secret and restart the listener on it so every
        existing connection is invalidated at once. Unknown → 404."""
        listener = d.connection_store.get_acp_connection(cid)
        if listener is None:
            return JSONResponse({"error": f"unknown acp listener: {cid}"}, status_code=404)
        token = _generate_token()
        d.connection_store.set_acp_token(cid, token)
        await d.manager.restart_acp_listener(cid)
        return {"listener": _entry(listener), "token": token}

    return r
