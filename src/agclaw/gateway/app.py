"""FastAPI facade over the AGClaw gateway.

Exposes a plain REST + WebSocket API so any UI client (web, desktop, mobile) can
drive the agent without knowing anything about AG2. The gateway is created on
app startup and torn down on shutdown.

Endpoints:
  GET  /api/health              -> gateway status
  POST /api/message             -> {reply} for a {text, session_id?} message
  WS   /api/ws                  -> send {text, session_id?}, receive {type, ...}
"""

from contextlib import asynccontextmanager

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from pydantic import BaseModel

from agclaw.config import Config
from agclaw.gateway.core import Gateway


class MessageRequest(BaseModel):
    text: str
    session_id: str = "default"
    platform: str | None = None


class MessageResponse(BaseModel):
    reply: str
    session_id: str


def create_app(
    config: Config | None = None,
    memory: bool = True,
    platform: str = "gateway",
    gateway: Gateway | None = None,
) -> FastAPI:
    """Build the FastAPI app.

    If `gateway` is provided (e.g. shared with channels in `agclaw run`), it's
    used as-is and its lifecycle is owned by the caller. Otherwise the app
    creates and manages its own gateway.
    """
    owns_gateway = gateway is None
    if gateway is None:
        gateway = Gateway(config=config, memory=memory, platform=platform)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if owns_gateway:
            await gateway.start()
        app.state.gateway = gateway
        try:
            yield
        finally:
            if owns_gateway:
                await gateway.close()

    app = FastAPI(title="AGClaw Gateway", version="0.1.0", lifespan=lifespan)

    @app.get("/api/health")
    async def health() -> dict:
        return app.state.gateway.status()

    @app.post("/api/message", response_model=MessageResponse)
    async def message(req: MessageRequest) -> MessageResponse:
        reply = await app.state.gateway.send_message(req.text, session_id=req.session_id)
        return MessageResponse(reply=reply, session_id=req.session_id)

    @app.websocket("/api/ws")
    async def ws(websocket: WebSocket) -> None:
        await websocket.accept()
        try:
            while True:
                data = await websocket.receive_json()
                text = data.get("text", "")
                session_id = data.get("session_id", "default")
                if not text:
                    await websocket.send_json(
                        {"type": "error", "message": "missing 'text'"}
                    )
                    continue
                await websocket.send_json({"type": "thinking", "session_id": session_id})
                try:
                    reply = await app.state.gateway.send_message(
                        text, session_id=session_id
                    )
                    await websocket.send_json(
                        {"type": "reply", "text": reply, "session_id": session_id}
                    )
                except Exception as exc:  # surface failures to the client
                    await websocket.send_json(
                        {"type": "error", "message": str(exc), "session_id": session_id}
                    )
        except WebSocketDisconnect:
            return

    return app
