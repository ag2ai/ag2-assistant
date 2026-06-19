"""FastAPI facade over the AG2 Assistant gateway.

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
from urllib.parse import urlsplit

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from pydantic import BaseModel

from assistant.config import Config
from assistant.gateway.core import Gateway
from assistant.hitl import GatewayAsker, HitlServer, add_hitl_routes

_STATIC_DIR = Path(__file__).parent / "static"


def _allowed_origins() -> set[str]:
    """Extra browser origins to accept besides same-origin. Comma-separated in
    AG2ASSISTANT_ALLOWED_ORIGINS — an escape hatch for proxied/remote demos."""
    raw = os.environ.get("AG2ASSISTANT_ALLOWED_ORIGINS", "")
    return {o.strip().rstrip("/") for o in raw.split(",") if o.strip()}


def _origin_ok(origin: str | None, host: str | None) -> bool:
    """Whether a request may proceed, guarding against cross-origin browser
    access to a locally-bound gateway.

    Browsers always send `Origin` on cross-origin fetch/WebSocket, so:
      - no Origin (curl, server-to-server, top-level navigations) → allowed;
      - Origin whose host:port matches the Host header (same-origin) → allowed;
      - Origin in the configured allowlist → allowed;
      - anything else (a page on another site reaching the gateway) → rejected.
    """
    if not origin:
        return True
    origin = origin.rstrip("/")
    if origin in _allowed_origins():
        return True
    return bool(host) and urlsplit(origin).netloc == host


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
    when: str  # ISO 8601 datetime
    recurrence: str | None = None


class ArchiveRequest(BaseModel):
    archived: bool = True


class VoiceRequest(BaseModel):
    voice: str


class KeyRequest(BaseModel):
    provider: str
    value: str = ""  # empty clears the key


class LlmRequest(BaseModel):
    provider: str
    model: str = ""


class VoiceProviderRequest(BaseModel):
    provider: str


class MemoryRequest(BaseModel):
    text: str


def create_app(
    config: Config | None = None,
    memory: bool = True,
    platform: str = "gateway",
    gateway: Gateway | None = None,
    persist: bool = True,
) -> FastAPI:
    """Build the FastAPI app.

    If `gateway` is provided (e.g. shared with channels in `ag2assistant run`), it's
    used as-is and its lifecycle is owned by the caller. Otherwise the app
    creates and manages its own gateway.
    """
    from assistant.config import load_config
    from assistant.gateway.tasks_service import TaskService

    config = config or load_config()  # resolve once so routes have a real Config
    tasks = TaskService(config=config)

    owns_gateway = gateway is None
    if gateway is None:
        gateway = Gateway(
            config=config,
            memory=memory,
            platform=platform,
            persist=persist,
            task_service=tasks,  # the agent knows/does everything via system tools
        )

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if owns_gateway:
            await gateway.start()
        tasks.set_emitter(getattr(gateway, "emit_event", None))  # lifecycle → AG2 stream
        await tasks.start()
        app.state.gateway = gateway
        app.state.tasks = tasks
        try:
            yield
        finally:
            await tasks.close()
            if owns_gateway:
                await gateway.close()

    app = FastAPI(title="AG2 Assistant Gateway", version="0.1.0", lifespan=lifespan)

    @app.middleware("http")
    async def _origin_guard(request: Request, call_next):
        """Reject cross-origin browser requests to the API. Same-origin and
        non-browser (no Origin) requests pass; WebSocket routes guard separately
        (Starlette doesn't run HTTP middleware for them)."""
        if request.url.path.startswith("/api/") and not _origin_ok(
            request.headers.get("origin"), request.headers.get("host")
        ):
            return JSONResponse({"error": "cross-origin request rejected"}, status_code=403)
        return await call_next(request)

    # Shared HITL registry: the gateway serves the styled /hitl/{id} pages and an
    # answer endpoint, so permission/HITL prompts can be answered by any client.
    hitl = HitlServer()
    app.state.hitl = hitl
    app.state.google_flows = {}  # state token -> in-progress OAuth flow
    add_hitl_routes(app, hitl)

    @app.get("/")
    async def ui():
        """The web UI is the Svelte client at /app."""
        from fastapi.responses import RedirectResponse

        return RedirectResponse(url="/app/", status_code=307)

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
        from assistant.integrations import google_auth

        return {
            "configured": google_auth.is_configured(),
            "signed_in": google_auth.has_token(),
            "email": google_auth.account_email(),
        }

    @app.post("/api/google/credentials")
    async def google_credentials(payload: CredentialsUpload) -> dict:
        """Save an uploaded OAuth client JSON (so users avoid the filesystem)."""
        from assistant.integrations import google_auth

        try:
            google_auth.save_credentials_json(payload.content)
        except Exception as exc:
            return {"ok": False, "error": str(exc)}
        return {"ok": True}

    @app.post("/api/google/login_url")
    async def google_login_url(request: Request) -> dict:
        """Build a Google consent URL whose redirect returns to this gateway.

        The user opens the URL (web button or a channel link). AG2ASSISTANT_PUBLIC_URL
        overrides the redirect base when the gateway is reachable at a public URL
        (so the round-trip can complete from another device).
        """
        from assistant.integrations import google_auth

        if not google_auth.is_configured():
            return {"ok": False, "error": "No OAuth client configured."}
        base = os.environ.get("AG2ASSISTANT_PUBLIC_URL") or str(request.base_url)
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
        from assistant.integrations import google_auth

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
        return HTMLResponse(_page("Connected ✓", f"AG2 Assistant is now connected to {email}."))

    @app.post("/api/google/logout")
    async def google_logout() -> dict:
        from assistant.integrations import google_auth

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

    @app.post("/api/tasks/{task_id}/seen")
    async def mark_task_seen(task_id: str) -> dict:
        """Mark a task/run as opened (clears its unread highlight in the nav)."""
        ok = await app.state.tasks.mark_seen(task_id)
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
            {
                "ok": False,
                "error": "Only finished tasks can be archived — cancel it first to stop it.",
            },
            status_code=409,
        )

    @app.post("/api/tasks/{task_id}/chat")
    async def task_chat(task_id: str, req: TaskChatRequest):
        """Converse about a task — the SAME universal agent, given this task as its
        surface context (it inspects/steers the task via its system tools)."""
        from assistant.system_tools import format_task

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
            req.text,
            session_id=f"task:{task_id}",
            asker=asker,
            surface=surface,
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

    # --- Voice picker: list voices, select (persist), preview (TTS) ---

    @app.get("/api/voice/voices")
    async def voice_voices() -> dict:
        from assistant import settings, voice_providers

        return {
            "voices": [{"name": n, "style": s} for n, s in settings.voices_for().items()],
            "current": settings.get_voice(),
            "provider": settings.voice_provider(),
            "input_rate": voice_providers.get().input_rate,  # mic capture rate the client should use
        }

    @app.post("/api/voice/select")
    async def voice_select(req: VoiceRequest) -> dict:
        from assistant import settings

        if not settings.set_voice(req.voice):
            return Response(status_code=400)
        return {"ok": True, "voice": req.voice}

    @app.get("/voices/{name}.wav")
    async def voice_sample(name: str):
        """Pre-recorded voice sample (from scripts/record_voice_samples.py), if present.
        404 → the client falls back to live TTS via /api/voice/preview."""
        from assistant import settings

        f = _STATIC_DIR / "voices" / f"{name}.wav"
        if name in settings.voices_for() and f.is_file():
            return FileResponse(f, media_type="audio/wav")
        return Response(status_code=404)

    @app.post("/api/voice/preview")
    async def voice_preview(req: VoiceRequest):
        from assistant import settings
        from assistant.voice import synthesize_preview

        if req.voice not in settings.voices_for():
            return Response(status_code=400)
        try:
            wav = await synthesize_preview(config, req.voice)
        except Exception as exc:
            return Response(content=str(exc)[:200], status_code=502)
        return Response(content=wav, media_type="audio/wav")

    # --- Settings: API keys + assistant/voice provider selection ---

    def _ollama_installed() -> bool:
        try:
            from autogen.beta.config import OllamaConfig

            return type(OllamaConfig).__module__ != "unittest.mock"
        except Exception:
            return False

    def _available_providers() -> dict:
        """Which providers can actually be used right now (key set / Ollama deps)."""
        from assistant import secrets

        st = secrets.status()
        avail = {p: st[p]["set"] for p in ("openai", "gemini", "anthropic")}
        avail["ollama"] = _ollama_installed()
        return avail

    @app.get("/api/settings")
    async def get_settings() -> dict:
        from assistant import secrets, settings
        from assistant.config import load_config

        cfg = load_config()
        return {
            "keys": secrets.status(),  # per-provider {set, hint} — never raw
            "available": _available_providers(),
            "assistant": {"provider": cfg.llm.provider, "model": cfg.llm.model},
            "voice_provider": settings.voice_provider(),
        }

    @app.post("/api/settings/key")
    async def set_settings_key(req: KeyRequest) -> dict:
        from assistant import secrets

        if not secrets.set_key(req.provider, req.value):
            return Response(status_code=400)
        await app.state.gateway.reload()  # new turns pick up the key; voice next session
        return {"ok": True}

    @app.post("/api/settings/llm")
    async def set_settings_llm(req: LlmRequest) -> dict:
        from assistant import settings

        provider = req.provider.lower()
        if not _available_providers().get(provider):
            from fastapi.responses import JSONResponse

            hint = (
                "Install with `pip install ag2[ollama]`."
                if provider == "ollama"
                else "Add the provider's API key first."
            )
            return JSONResponse(
                {"ok": False, "error": f"{provider} isn't available. {hint}"}, status_code=409
            )
        settings.set_llm(provider=provider, model=req.model or None)
        await app.state.gateway.reload()
        return {"ok": True}

    @app.post("/api/settings/voice_provider")
    async def set_settings_voice_provider(req: VoiceProviderRequest) -> dict:
        from assistant import settings

        provider = req.provider.lower()
        if not _available_providers().get(provider):
            from fastapi.responses import JSONResponse

            return JSONResponse(
                {"ok": False, "error": f"Add the {provider} API key first."}, status_code=409
            )
        if not settings.set_voice_provider(provider):
            return Response(status_code=400)
        return {"ok": True}

    # --- Memory: view + edit the learned user profile ---

    @app.get("/api/memory")
    async def get_memory() -> dict:
        from assistant.memory import read_profile

        return {"text": await read_profile()}

    @app.post("/api/memory")
    async def set_memory(req: MemoryRequest) -> dict:
        from assistant.memory import write_profile

        await write_profile(req.text)
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

    @app.websocket("/api/stream")
    async def stream_ws(websocket: WebSocket) -> None:
        """Event-stream protocol (the redesign's transport): the client receives
        the session's events as `{event:{type,data}}` — replayed on connect, then
        live — and sends `{text}` turns. Old /api/ws stays during migration."""
        if not _origin_ok(websocket.headers.get("origin"), websocket.headers.get("host")):
            await websocket.close(code=1008)  # policy violation
            return
        await websocket.accept()
        from assistant.gateway.stream_bridge import StreamBridge

        session_id = websocket.query_params.get("session") or "default"
        default_surface = _SURFACES.get(websocket.query_params.get("surface", ""), "")
        bridge = StreamBridge(app.state.gateway, websocket, session_id)

        async def turn_surface() -> str:
            # task threads get a fresh task snapshot each turn so "this task" resolves
            if session_id.startswith("task:"):
                tid = session_id.split(":", 1)[1]
                node = await app.state.tasks.get_task(tid)
                if node:
                    from assistant.system_tools import format_task

                    return (
                        "The user is viewing this task; act on THIS task when "
                        f"they refer to it.\n\n{format_task(node)}"
                    )
            return default_surface

        try:
            await bridge.open()
            while True:
                data = await websocket.receive_json()
                if data.get("type") == "answer" and data.get("id"):
                    iid, ans = data["id"], data.get("answer", "")
                    # Chat permission prompts live in the HitlServer; durable task
                    # inquiries (answered inline on a task page) live in the
                    # InquiryStore under a different id — fall back to it so an
                    # inline answer resolves either kind.
                    if not app.state.hitl.answer(iid, ans):
                        with contextlib.suppress(Exception):
                            await app.state.tasks.answer_inquiry(iid, ans)
                    continue
                text = data.get("text", "")
                attachments = _decode_attachments(data.get("attachments"))
                if not text and attachments:
                    text = "Here is a file I'm sharing with you."
                if not text:
                    continue
                asker = GatewayAsker(app.state.hitl)
                surface = await turn_surface()
                asyncio.create_task(
                    bridge.run_turn(text, asker=asker, attachments=attachments, surface=surface)
                )
        except WebSocketDisconnect:
            return
        finally:
            bridge.close()

    @app.websocket("/api/voice")
    async def voice_ws(websocket: WebSocket) -> None:
        """Full-duplex voice. The browser streams 16 kHz mono PCM mic frames as
        binary; we feed them to a Gemini Live session and stream back 24 kHz PCM
        speech (binary) plus user/agent transcripts (JSON) for on-screen bubbles."""
        if not _origin_ok(websocket.headers.get("origin"), websocket.headers.get("host")):
            await websocket.close(code=1008)  # policy violation
            return
        await websocket.accept()
        import uuid

        from autogen.beta.events import (
            ModelMessage,
            ModelMessageChunk,
            ModelRequest,
            ModelResponse,
            TextInput,
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
        # persist spoken transcripts onto the surface's stream so they survive reload
        # and become shared conversation history (None → bare voice session, skip).
        persist_session = f"task:{task_id}" if task_id else chat_session
        # Persist spoken turns by ROLE ALTERNATION, not the "completed" event (Gemini
        # doesn't fire it reliably): accumulate each side's chunks and flush a turn
        # when the other side starts speaking → alternating ModelRequest/ModelResponse.
        user_buf: list[str] = []
        agent_buf: list[str] = []
        last_role = {"v": None}  # "user" | "agent"

        async def _flush_user():
            text = "".join(user_buf).strip()
            user_buf.clear()
            if persist_session and text:
                with contextlib.suppress(Exception):
                    await app.state.gateway.emit_event(
                        persist_session, ModelRequest(parts=[TextInput(content=text)])
                    )

        async def _flush_agent():
            text = "".join(agent_buf).strip()
            agent_buf.clear()
            if persist_session and text:
                with contextlib.suppress(Exception):
                    await app.state.gateway.emit_event(
                        persist_session, ModelResponse(message=ModelMessage(content=text))
                    )

        async def on_tool(name: str) -> None:  # surface delegated (universal-agent) tools
            with contextlib.suppress(Exception):
                await websocket.send_json({"type": "tool", "name": name})

        async def on_task(st: dict) -> None:  # a task the voice agent just spawned → card
            with contextlib.suppress(Exception):
                await websocket.send_json(
                    {
                        "type": "task_card",
                        "id": st["id"],
                        "title": st.get("title", "Task"),
                    }
                )

        # The voice agent can hang up the call itself via its end_call tool, which
        # trips this event; wait_end() (below) then ends the job race → teardown.
        end_requested = asyncio.Event()

        try:
            agent = await app.state.gateway.build_voice_agent(
                session_id=sid,
                task_id=task_id,
                chat_session=chat_session,
                on_tool=on_tool,
                on_task=on_task,
                on_end=end_requested.set,
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
            # ModelResponse marks the end of an agent turn for BOTH providers
            # (Gemini turn_complete / OpenAI response.done) — the provider-agnostic
            # turn boundary that separates one spoken reply from the next.
            sel = (
                TranscriptionChunkEvent
                | TranscriptionCompletedEvent
                | ModelMessageChunk
                | ModelResponse
            )
            with context.stream.where(sel).join() as evs:
                async for e in evs:
                    if isinstance(e, ModelResponse):  # agent turn ended
                        if last_role["v"] == "agent":
                            await _flush_agent()  # persist the spoken turn
                        last_role["v"] = None
                        # tell the client to close the bubble so the next reply is fresh
                        await websocket.send_json({"type": "turn_end", "role": "agent"})
                        continue
                    if isinstance(e, ModelMessageChunk):  # agent speaking
                        if last_role["v"] == "user":
                            await _flush_user()  # user turn ended
                        last_role["v"] = "agent"
                        agent_buf.append(e.content)
                        frame = {"type": "transcript", "role": "agent", "text": e.content}
                    else:  # user (chunk or completed)
                        if last_role["v"] == "agent":
                            await _flush_agent()  # agent turn ended
                        last_role["v"] = "user"
                        user_buf.append(e.content)
                        frame = {"type": "transcript", "role": "user", "text": e.content}
                        if isinstance(e, TranscriptionCompletedEvent):
                            frame["final"] = True
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
                        if not name or name in ("ask_assistant", "end_call"):  # hide plumbing tools
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

        async def wait_end():
            # The agent called end_call. Let the spoken goodbye drain before we tear
            # the session down (the client closes its playback context on WS close).
            await end_requested.wait()
            await asyncio.sleep(2.5)

        try:
            async with agent.run() as context:
                await websocket.send_json({"type": "ready"})
                jobs = [
                    asyncio.create_task(pump_audio(context)),
                    asyncio.create_task(pump_text(context)),
                    asyncio.create_task(pump_tools(context)),
                    asyncio.create_task(recv_loop(context)),
                    asyncio.create_task(wait_end()),
                ]
                try:
                    await asyncio.wait(jobs, return_when=asyncio.FIRST_COMPLETED)
                finally:
                    for j in jobs:
                        j.cancel()
                    await _flush_user()  # persist whichever turn was pending
                    await _flush_agent()
        except WebSocketDisconnect:
            with contextlib.suppress(Exception):
                await _flush_user()
                await _flush_agent()
            return
        except Exception as exc:
            with contextlib.suppress(Exception):
                await websocket.send_json({"type": "error", "message": str(exc)})

    _APP_DIR = _STATIC_DIR / "app"

    @app.get("/app")
    @app.get("/app/{path:path}")
    async def spa_app(path: str = ""):
        """Serve the Vite+Svelte client (built into static/app). Real asset files
        are served as-is; any other /app/* path falls back to index.html so SPA
        deep links (/app/c/<id>, /app/t/<id>) survive refresh."""
        index = _APP_DIR / "index.html"
        if not index.exists():
            return HTMLResponse(
                "<h1>AG2 Assistant</h1><p>New UI not built. Run: cd web && npm install && npm run build</p>"
            )
        f = (_APP_DIR / path).resolve()
        if path and f.is_file() and str(f).startswith(str(_APP_DIR.resolve())):
            return FileResponse(f)
        return FileResponse(index)

    @app.get("/{full_path:path}")
    async def spa(full_path: str):
        """Any other path → the Svelte app at /app (unknown /api paths 404)."""
        if full_path.startswith("api/"):
            return Response(status_code=404)
        from fastapi.responses import RedirectResponse

        return RedirectResponse(url="/app/", status_code=307)

    return app


def _decode_attachments(items) -> list:
    """Turn UI attachment frames ({name, mime, data:b64}) into AG2 inputs."""
    import base64

    from assistant.attachments import build_input

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
