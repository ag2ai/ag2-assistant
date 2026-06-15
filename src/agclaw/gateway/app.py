"""FastAPI facade over the AGClaw gateway.

Exposes a plain REST + WebSocket API so any UI client (web, desktop, mobile) can
drive the agent without knowing anything about AG2. The gateway is created on
app startup and torn down on shutdown.

Endpoints:
  GET  /api/health              -> gateway status
  POST /api/message             -> {reply} for a {text, session_id?} message
  WS   /api/ws                  -> send {text, session_id?}, receive {type, ...}
"""

import asyncio
import contextlib
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse, Response
from pydantic import BaseModel

from agclaw.config import Config
from agclaw.gateway.core import Gateway
from agclaw.hitl import GatewayAsker, HitlServer, add_hitl_routes

_STATIC_DIR = Path(__file__).parent / "static"
_UI_FILE = _STATIC_DIR / "index.html"


class MessageRequest(BaseModel):
    text: str
    session_id: str = "default"
    platform: str | None = None


class MessageResponse(BaseModel):
    reply: str
    session_id: str


class CredentialsUpload(BaseModel):
    content: str  # raw OAuth client JSON


class TaskRequest(BaseModel):
    text: str
    channel: str = "web"


class AnswerRequest(BaseModel):
    answer: str


def create_app(
    config: Config | None = None,
    memory: bool = True,
    platform: str = "gateway",
    gateway: Gateway | None = None,
    persist: bool = True,
) -> FastAPI:
    """Build the FastAPI app.

    If `gateway` is provided (e.g. shared with channels in `agclaw run`), it's
    used as-is and its lifecycle is owned by the caller. Otherwise the app
    creates and manages its own gateway.
    """
    owns_gateway = gateway is None
    if gateway is None:
        gateway = Gateway(
            config=config, memory=memory, platform=platform, persist=persist
        )

    from agclaw.gateway.tasks_service import TaskService

    tasks = TaskService(config=config)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if owns_gateway:
            await gateway.start()
        await tasks.start()
        app.state.gateway = gateway
        app.state.tasks = tasks
        try:
            yield
        finally:
            await tasks.close()
            if owns_gateway:
                await gateway.close()

    app = FastAPI(title="AGClaw Gateway", version="0.1.0", lifespan=lifespan)

    # Shared HITL registry: the gateway serves the styled /hitl/{id} pages and an
    # answer endpoint, so permission/HITL prompts can be answered by any client.
    hitl = HitlServer()
    app.state.hitl = hitl
    app.state.google_flows = {}  # state token -> in-progress OAuth flow
    add_hitl_routes(app, hitl)

    @app.get("/", response_class=HTMLResponse)
    async def ui() -> str:
        """The reference web chat client (vanilla JS over the REST/WS + HITL API)."""
        try:
            return _UI_FILE.read_text(encoding="utf-8")
        except OSError:
            return "<h1>AGClaw</h1><p>UI asset missing.</p>"

    @app.get("/{name}.svg")
    async def favicon_svg(name: str):
        if name in ("faviconlight", "favicondark"):
            return FileResponse(_STATIC_DIR / f"{name}.svg", media_type="image/svg+xml")
        return Response(status_code=404)

    @app.get("/favicon.ico")
    async def favicon_ico():
        # browsers that request /favicon.ico directly get the light AG2 mark
        return FileResponse(_STATIC_DIR / "faviconlight.svg", media_type="image/svg+xml")

    @app.get("/api/health")
    async def health() -> dict:
        return app.state.gateway.status()

    @app.get("/api/hitl/pending")
    async def hitl_pending() -> dict:
        """Open HITL questions for a UI client to render and answer."""
        return {"pending": app.state.hitl.pending_list()}

    @app.get("/api/google/status")
    async def google_status() -> dict:
        from agclaw.integrations import google_auth

        return {
            "configured": google_auth.is_configured(),
            "signed_in": google_auth.has_token(),
            "email": google_auth.account_email(),
        }

    @app.post("/api/google/credentials")
    async def google_credentials(payload: CredentialsUpload) -> dict:
        """Save an uploaded OAuth client JSON (so users avoid the filesystem)."""
        from agclaw.integrations import google_auth

        try:
            google_auth.save_credentials_json(payload.content)
        except Exception as exc:
            return {"ok": False, "error": str(exc)}
        return {"ok": True}

    @app.post("/api/google/login_url")
    async def google_login_url(request: Request) -> dict:
        """Build a Google consent URL whose redirect returns to this gateway.

        The user opens the URL (web button or a channel link). AGCLAW_PUBLIC_URL
        overrides the redirect base when the gateway is reachable at a public URL
        (so the round-trip can complete from another device).
        """
        from agclaw.integrations import google_auth

        if not google_auth.is_configured():
            return {"ok": False, "error": "No OAuth client configured."}
        base = os.environ.get("AGCLAW_PUBLIC_URL") or str(request.base_url)
        redirect_uri = base.rstrip("/") + "/api/google/callback"
        try:
            auth_url, state, flow = await asyncio.to_thread(
                google_auth.make_login_flow, redirect_uri
            )
        except Exception as exc:
            return {"ok": False, "error": str(exc)}
        app.state.google_flows[state] = flow
        return {"ok": True, "auth_url": auth_url}

    @app.get("/api/google/callback", response_class=HTMLResponse)
    async def google_callback(state: str = "", code: str = "", error: str = ""):
        from agclaw.integrations import google_auth

        def _page(title, msg):
            return (
                f"<!doctype html><meta charset=utf-8><title>{title}</title>"
                "<body style='font-family:system-ui;max-width:520px;margin:14vh auto;"
                "text-align:center;color:#171717'>"
                f"<h1 style='color:#f95339'>{title}</h1><p>{msg}</p>"
                "<p style='color:#737373'>You can close this tab.</p></body>"
            )

        if error:
            return HTMLResponse(_page("Cancelled", f"Google returned: {error}"))
        flow = app.state.google_flows.pop(state, None)
        if flow is None or not code:
            return HTMLResponse(_page("Expired", "This sign-in link is no longer valid."))
        try:
            email = await asyncio.to_thread(google_auth.complete_login, flow, code)
        except Exception as exc:
            return HTMLResponse(_page("Sign-in failed", str(exc)))
        return HTMLResponse(_page("Connected ✓", f"AGClaw is now connected to {email}."))

    @app.post("/api/google/logout")
    async def google_logout() -> dict:
        from agclaw.integrations import google_auth

        return {"ok": google_auth.logout()}

    @app.get("/api/sessions")
    async def sessions() -> dict:
        """List persisted, resumable conversations (newest first)."""
        return {"sessions": await app.state.gateway.list_sessions()}

    @app.get("/api/sessions/{session_id}")
    async def session_transcript(session_id: str) -> dict:
        """The display transcript for a session, for the UI to restore."""
        return {
            "session_id": session_id,
            "messages": await app.state.gateway.transcript(session_id),
        }

    # --- Tasks + durable HITL inquiries ---

    @app.get("/api/tasks")
    async def list_tasks() -> dict:
        """Top-level tasks (newest first) for the Tasks view."""
        return {"tasks": await app.state.tasks.list_tasks()}

    @app.post("/api/tasks")
    async def create_task(req: TaskRequest) -> dict:
        """Kick off a task — intake (clarifying questions) runs in the background
        and surfaces as inquiries to answer."""
        task_id = await app.state.tasks.submit_request(req.text, channel=req.channel)
        return {"id": task_id}

    @app.get("/api/tasks/{task_id}")
    async def get_task(task_id: str):
        task = await app.state.tasks.get_task(task_id)
        if task is None:
            return Response(status_code=404)
        return {"task": task}

    @app.post("/api/tasks/{task_id}/cancel")
    async def cancel_task(task_id: str) -> dict:
        ok = await app.state.tasks.cancel(task_id)
        return {"ok": ok}

    @app.get("/api/inquiries/pending")
    async def inquiries_pending(task_id: str | None = None) -> dict:
        """Open HITL inquiries (clarifications / approvals) awaiting an answer."""
        return {"pending": await app.state.tasks.pending_inquiries(task_id)}

    @app.post("/api/inquiries/{inquiry_id}/answer")
    async def answer_inquiry(inquiry_id: str, req: AnswerRequest):
        ok = await app.state.tasks.answer_inquiry(inquiry_id, req.answer)
        if not ok:
            return Response(status_code=404)
        return {"ok": True}

    @app.post("/api/message", response_model=MessageResponse)
    async def message(req: MessageRequest) -> MessageResponse:
        # REST clients answer prompts by polling /api/hitl/pending and POSTing
        # /hitl/{id}/answer; the request blocks until answered (or times out).
        asker = GatewayAsker(app.state.hitl)
        reply = await app.state.gateway.send_message(
            req.text, session_id=req.session_id, asker=asker
        )
        return MessageResponse(reply=reply, session_id=req.session_id)

    @app.websocket("/api/ws")
    async def ws(websocket: WebSocket) -> None:
        await websocket.accept()
        try:
            while True:
                data = await websocket.receive_json()
                # An answer frame can arrive any time (e.g. to a prior prompt).
                if data.get("type") == "answer" and data.get("id"):
                    app.state.hitl.answer(data["id"], data.get("answer", ""))
                    continue

                text = data.get("text", "")
                session_id = data.get("session_id", "default")
                attachments = _decode_attachments(data.get("attachments"))
                if not text and attachments:
                    text = "Here is a file I'm sharing with you."
                if not text:
                    await websocket.send_json(
                        {"type": "error", "message": "missing 'text'"}
                    )
                    continue
                await websocket.send_json({"type": "thinking", "session_id": session_id})

                async def on_question(req_id, question, path, sid=session_id):
                    await websocket.send_json(
                        {
                            "type": "question",
                            "id": req_id,
                            "path": path,
                            "text": question.text,
                            "detail": question.detail,
                            "options": question.options,
                            "kind": question.kind,
                            "session_id": sid,
                        }
                    )

                asker = GatewayAsker(app.state.hitl, on_question=on_question)
                task = asyncio.create_task(
                    app.state.gateway.send_message(
                        text, session_id=session_id, asker=asker,
                        attachments=attachments,
                    )
                )
                # While the turn runs, keep reading frames (answers / cancel).
                await _drive_turn(websocket, task, app.state.hitl)

                if task.cancelled():
                    await websocket.send_json(
                        {"type": "cancelled", "session_id": session_id}
                    )
                    continue
                try:
                    reply = task.result()
                    await websocket.send_json(
                        {"type": "reply", "text": reply, "session_id": session_id}
                    )
                except TimeoutError:
                    await websocket.send_json({
                        "type": "error",
                        "message": "That took too long and timed out. Try again, "
                        "or a simpler request.",
                        "session_id": session_id,
                    })
                except Exception as exc:  # surface failures to the client
                    await websocket.send_json(
                        {"type": "error", "message": str(exc) or repr(exc),
                         "session_id": session_id}
                    )
        except WebSocketDisconnect:
            return

    return app


def _decode_attachments(items) -> list:
    """Turn UI attachment frames ({name, mime, data:b64}) into AG2 inputs."""
    import base64

    from agclaw.attachments import build_input

    out = []
    for a in items or []:
        try:
            raw = base64.b64decode(a.get("data", ""))
        except Exception:
            continue
        inp = build_input(raw, a.get("name", "file"), a.get("mime"))
        if inp is not None:
            out.append(inp)
    return out


async def _drive_turn(websocket: WebSocket, task: asyncio.Task, hitl) -> None:
    """Run a turn while concurrently accepting HITL answer / cancel frames.

    Lets the client answer a `question` frame on the same WebSocket the turn is
    streaming on (the turn is blocked awaiting that answer), or stop the turn
    with a `cancel` frame.
    """
    while not task.done():
        recv = asyncio.create_task(websocket.receive_json())
        done, _ = await asyncio.wait(
            {task, recv}, return_when=asyncio.FIRST_COMPLETED
        )
        if recv in done:
            try:
                msg = recv.result()
            except WebSocketDisconnect:
                task.cancel()
                raise
            if msg.get("type") == "answer" and msg.get("id"):
                hitl.answer(msg["id"], msg.get("answer", ""))
            elif msg.get("type") == "cancel":
                task.cancel()
            # other frames mid-turn are ignored (one turn at a time per socket)
        else:
            recv.cancel()
            with contextlib.suppress(Exception, asyncio.CancelledError):
                await recv
    # Let the task settle (so task.cancelled()/result() are accurate) without
    # re-raising here — the caller inspects task.result()/cancelled() and sends
    # the appropriate reply/error/cancelled frame.
    with contextlib.suppress(BaseException):
        await task
