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

# Surface context the client can request by token (kept server-side, not in the UI).
_SURFACES = {
    "new_task": (
        "The user is starting a NEW TASK. If their request is clear enough, create "
        "it now with create_task (or schedule_task if they gave a time / cadence); "
        "only ask a brief clarifying question if something essential is missing. "
        "Confirm what you created."
    ),
}


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


class TaskChatRequest(BaseModel):
    text: str


class ScheduleRequest(BaseModel):
    text: str
    when: str            # ISO 8601 datetime
    recurrence: str | None = None


class ArchiveRequest(BaseModel):
    archived: bool = True


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
    from agclaw.gateway.tasks_service import TaskService

    tasks = TaskService(config=config)

    owns_gateway = gateway is None
    if gateway is None:
        gateway = Gateway(
            config=config, memory=memory, platform=platform, persist=persist,
            task_starter=tasks.submit_request,  # let the chat agent spawn tasks
            schedule_starter=tasks.schedule_task,  # ...and schedule them
            task_service=tasks,  # ...and know/do everything via system tools
        )

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

    @app.get("/api/tasks/all")
    async def list_all_tasks(status: str | None = None) -> dict:
        """Full task history for the listing page (newest first). Optional status
        filter: active / completed / stopped / archived."""
        return {"tasks": await app.state.tasks.list_all(status)}

    @app.post("/api/tasks/schedule")
    async def schedule_task(req: ScheduleRequest) -> dict:
        """Schedule a task for a future time (optionally recurring)."""
        task_id = await app.state.tasks.schedule_task(req.text, req.when, req.recurrence)
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

    @app.post("/api/tasks/{task_id}/archive")
    async def archive_task(task_id: str, req: ArchiveRequest | None = None):
        archived = True if req is None else req.archived
        ok, reason = await app.state.tasks.set_archived(task_id, archived)
        if ok:
            return {"ok": True, "archived": archived}
        if reason == "notfound":
            return Response(status_code=404)
        from fastapi.responses import JSONResponse

        return JSONResponse(
            {"ok": False, "error": "Only finished tasks can be archived — cancel it first to stop it."},
            status_code=409,
        )

    @app.post("/api/tasks/{task_id}/chat")
    async def task_chat(task_id: str, req: TaskChatRequest):
        """Converse about a task — the SAME universal agent, given this task as its
        surface context (it inspects/steers the task via its system tools)."""
        from agclaw.system_tools import format_task

        node = await app.state.tasks.get_task(task_id)
        if node is None:
            return Response(status_code=404)
        surface = (
            f"You are on the page for task {task_id}. The user's messages here are "
            f"usually about THIS task — inspect or steer it with your task tools "
            f"(its id is {task_id}). Current state:\n{format_task(node)}"
        )
        asker = GatewayAsker(app.state.hitl)
        reply = await app.state.gateway.send_message(
            req.text, session_id=f"task:{task_id}", asker=asker, surface=surface,
        )
        return {"reply": reply}

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
                surface = _SURFACES.get(data.get("surface", ""), "")
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

                async def on_tool(name, sid=session_id):
                    await websocket.send_json(
                        {"type": "tool", "name": name, "session_id": sid}
                    )

                # capture any tasks the agent spawns this turn (start_task tool) so
                # we can show a task card; the contextvar is copied into the task.
                import agclaw.agent as agent_mod

                spawned: list = []
                agent_mod.started_tasks_var.set(spawned)
                task = asyncio.create_task(
                    app.state.gateway.send_message(
                        text, session_id=session_id, asker=asker,
                        attachments=attachments, surface=surface, on_tool=on_tool,
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
                    for st in spawned:  # one card per task the agent started
                        await websocket.send_json({
                            "type": "task_card", "id": st["id"],
                            "title": st["title"], "session_id": session_id,
                        })
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

    @app.websocket("/api/voice")
    async def voice_ws(websocket: WebSocket) -> None:
        """Full-duplex voice. The browser streams 16 kHz mono PCM mic frames as
        binary; we feed them to a Gemini Live session and stream back 24 kHz PCM
        speech (binary) plus user/agent transcripts (JSON) for on-screen bubbles."""
        await websocket.accept()
        import uuid

        from autogen.beta.events import (
            ModelMessageChunk,
            ToolCallEvent,
            ToolCallsEvent,
        )
        from autogen.beta.events.voice import (
            RecordedAudioEvent,
            SynthesizedAudioEvent,
            TranscriptionChunkEvent,
            TranscriptionCompletedEvent,
        )

        sid = uuid.uuid4().hex[:8]
        task_id = websocket.query_params.get("task") or None
        chat_session = websocket.query_params.get("session") or None

        async def on_tool(name: str) -> None:  # surface delegated (universal-agent) tools
            with contextlib.suppress(Exception):
                await websocket.send_json({"type": "tool", "name": name})

        async def on_task(st: dict) -> None:  # a task the voice agent just spawned → card
            with contextlib.suppress(Exception):
                await websocket.send_json({
                    "type": "task_card", "id": st["id"], "title": st.get("title", "Task"),
                })

        try:
            agent = await app.state.gateway.build_voice_agent(
                session_id=sid, task_id=task_id, chat_session=chat_session,
                on_tool=on_tool, on_task=on_task,
            )
        except Exception as exc:
            with contextlib.suppress(Exception):
                await websocket.send_json({"type": "error", "message": str(exc)})
                await websocket.close()
            return

        async def pump_audio(context):
            with context.stream.where(SynthesizedAudioEvent).join() as evs:
                async for e in evs:
                    await websocket.send_bytes(e.content)

        async def pump_text(context):
            sel = TranscriptionChunkEvent | TranscriptionCompletedEvent | ModelMessageChunk
            with context.stream.where(sel).join() as evs:
                async for e in evs:
                    if isinstance(e, ModelMessageChunk):
                        frame = {"type": "transcript", "role": "agent", "text": e.content}
                    elif isinstance(e, TranscriptionCompletedEvent):
                        frame = {"type": "transcript", "role": "user", "text": e.content, "final": True}
                    else:
                        frame = {"type": "transcript", "role": "user", "text": e.content}
                    await websocket.send_json(frame)

        async def pump_tools(context):
            # the voice agent's OWN basic tools (delegated tools come via on_tool).
            seen: set[str] = set()  # ToolCallsEvent + provider ToolCallEvent share an id
            with context.stream.where(ToolCallEvent | ToolCallsEvent).join() as evs:
                async for e in evs:
                    calls = e.calls if isinstance(e, ToolCallsEvent) else [e]
                    for c in calls:
                        name = getattr(c, "name", "") or ""
                        cid = getattr(c, "id", "") or ""
                        if not name or name == "ask_assistant":  # hide the plumbing tool
                            continue
                        if cid and cid in seen:
                            continue
                        if cid:
                            seen.add(cid)
                        await on_tool(name)

        async def recv_loop(context):
            while True:
                msg = await websocket.receive()
                if msg.get("type") == "websocket.disconnect":
                    break
                data = msg.get("bytes")
                if data:
                    await context.send(RecordedAudioEvent(data))

        try:
            async with agent.run() as context:
                await websocket.send_json({"type": "ready"})
                jobs = [
                    asyncio.create_task(pump_audio(context)),
                    asyncio.create_task(pump_text(context)),
                    asyncio.create_task(pump_tools(context)),
                    asyncio.create_task(recv_loop(context)),
                ]
                try:
                    await asyncio.wait(jobs, return_when=asyncio.FIRST_COMPLETED)
                finally:
                    for j in jobs:
                        j.cancel()
        except WebSocketDisconnect:
            return
        except Exception as exc:
            with contextlib.suppress(Exception):
                await websocket.send_json({"type": "error", "message": str(exc)})

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
