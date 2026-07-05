"""FastAPI facade over the AG2 Assistant ProfileManager.

Exposes a plain REST + WebSocket API so any UI client (web, desktop, mobile) can
drive the agent without knowing anything about AG2. The app OWNS a
``ProfileManager``: it is constructed (not started) by the caller, passed in, and
started/stopped in the app lifespan. Each request that touches profile-owned state
resolves ``{pid}`` to a ``ProfileRuntime`` (gateway + task service + settings) via
the ``get_runtime`` dependency; unknown → 404, archived → 410.

Route map:
  Global (unprefixed):
    GET  /api/health                         -> process status (first running runtime)
    GET  /api/status                         -> [{pid, busy, running_tasks}] activity badges
    POST /api/secrets/key                    -> save a provider key (global secrets); reloads ALL runtimes
    POST /api/onboarded                      -> set the install-level onboarding flag
    GET  /api/profiles                       -> {profiles, active_default, onboarded} (§3.5 contract)
    POST /api/profiles                       -> create {name, palette, workspace?}; boots live
    POST /api/profiles/{pid}                 -> rename / palette / workspace (workspace reloads runtime)
    DELETE /api/profiles/{pid}               -> archive (guardrails §4.9)
    GET  /api/google/*                       -> account-level OAuth (shared like keys)
    GET  /api/fs/list                        -> generic folder browser (pickers)
    GET  /hitl/{req_id}, POST .../answer     -> styled HITL pages over a cross-profile dispatcher
    static: /, /{name}.svg, /favicon.ico, /voices/{name}.wav, /app*, catch-all

  Profile-scoped (under /api/p/{pid}):
    GET  sessions, sessions/{sid}
    POST message
    GET/POST tasks* (all/schedule/{id}/cancel/rerun/seen/archive/chat)
    GET  inquiries/pending, POST inquiries/{id}/answer
    GET  hitl/pending
    WS   stream, WS voice; GET voice/voices, POST voice/select, POST voice/preview
    GET/POST settings, settings/mcp*, settings/project-folder, settings/llm, settings/voice_provider
    GET/POST memory
    GET  files, GET/DELETE files/raw
    GET  usage
"""

import asyncio
import contextlib
import os
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import (
    APIRouter,
    Depends,
    FastAPI,
    HTTPException,
    Request,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from pydantic import BaseModel, Field

from assistant.gateway.profile_manager import (
    ArchivedProfile,
    ProfileManager,
    ProfileRuntime,
    UnknownProfile,
)
from assistant.hitl import DurableAsker, GatewayAsker, NullAsker, add_hitl_routes

_STATIC_DIR = Path(__file__).parent / "static"

# WebSocket close codes for profile resolution failures (documented, coherent set).
# Chosen to mirror the HTTP status they correspond to (4000 + status), and distinct
# from 4001 = profile-archived-mid-session (§4.9) and 1008 = origin policy violation.
_WS_UNKNOWN_PROFILE = 4404  # {pid} not in registry (≈ 404)
_WS_ARCHIVED_PROFILE = 4410  # {pid} archived (≈ 410)
_WS_PROFILE_ARCHIVED = 4001  # runtime archived while this socket was open (§4.9)


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


class OnboardedRequest(BaseModel):
    value: bool = True


class ProjectFolderRequest(BaseModel):
    path: str


class VoiceRequest(BaseModel):
    voice: str


class MCPServerRequest(BaseModel):
    name: str
    command: str
    args: list[str] | str = Field(default_factory=list)
    env: dict[str, str] | str | None = None
    cwd: str | None = None
    allowed_tools: list[str] | str = Field(default_factory=list)
    blocked_tools: list[str] | str = Field(default_factory=list)
    enabled: bool = True


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


class ProfileCreateRequest(BaseModel):
    name: str
    palette: str
    workspace: str | None = None


class ProfileUpdateRequest(BaseModel):
    name: str | None = None
    palette: str | None = None
    workspace: str | None = None


class ProfileArchiveRequest(BaseModel):
    new_default: str | None = None


class _HitlDispatcher:
    """Global HITL registry facade over every runtime's per-profile HITL registry.

    HITL request ids are globally unique (``uuid4().hex[:12]``), so the styled
    ``/hitl/{req_id}`` pages can stay short + unprefixed while still resolving
    against the right profile. This satisfies the ``add_hitl_routes`` registry
    protocol (``question_for(id)`` / ``answer(id, text)``) by asking each running
    runtime's registry in turn — first non-None wins."""

    def __init__(self, manager: ProfileManager) -> None:
        self._manager = manager

    def _registries(self):
        for runtime in self._manager.runtimes():
            reg = getattr(runtime, "hitl", None)
            if reg is not None:
                yield reg

    def question_for(self, req_id: str):
        for reg in self._registries():
            q = reg.question_for(req_id)
            if q is not None:
                return q
        return None

    def answer(self, req_id: str, answer: str) -> bool:
        for reg in self._registries():
            if reg.answer(req_id, answer):
                return True
        return False


def _runtime_settings(runtime: ProfileRuntime):
    """This profile's Settings, resolved from the runtime's derived config."""
    from assistant.settings import Settings

    return Settings(runtime.config.data_dir / "settings.json")


def _chat_asker(runtime: ProfileRuntime, session_id: str):
    """Durable, inline HITL for a chat turn: the agent's question persists as an
    Inquiry and surfaces inline on this session's stream (InquiryRaised),
    answerable from the thread or the strip. Falls back to the transient HITL
    registry if the inquiry store isn't available."""
    inquiries = getattr(runtime.tasks, "inquiries", None) if runtime.tasks is not None else None
    if inquiries is None:
        return GatewayAsker(runtime.hitl)
    return DurableAsker(NullAsker(), inquiries, session=session_id)


async def _running_tasks(runtime: ProfileRuntime) -> int:
    """Count of RUNNING top-level+subtree tasks (cheap store scan) for activity badges."""
    tasks = runtime.tasks
    store = getattr(tasks, "store", None) if tasks is not None else None
    if store is None:
        return 0
    try:
        from assistant.tasks import TaskStatus

        return sum(1 for t in await store.list_all() if t.status == TaskStatus.RUNNING)
    except Exception:
        return 0


def create_app(profiles: ProfileManager, *, persist: bool = True) -> FastAPI:
    """Build the FastAPI app around a (constructed-but-not-started) ``ProfileManager``.

    The app owns the manager's lifecycle: ``profiles.start()`` runs on lifespan
    startup (migration + boot all unarchived profiles) and ``profiles.close()`` on
    shutdown. ``persist`` is accepted for signature symmetry (the manager itself is
    already configured with its persistence choice).

    ``app.state.profiles`` holds the manager; there is no ``app.state.gateway`` /
    ``app.state.tasks`` — profile-scoped routes resolve a runtime per request.
    """
    manager = profiles

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        await manager.start()  # migration + boot all unarchived profiles (+ channels)
        try:
            yield
        finally:
            await manager.close()

    app = FastAPI(title="AG2 Assistant Gateway", version="0.1.0", lifespan=lifespan)
    app.state.profiles = manager
    app.state.google_flows = {}  # state token -> in-progress OAuth flow

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

    # One global HITL page pair backed by a dispatcher over every runtime's registry.
    add_hitl_routes(app, _HitlDispatcher(manager))

    # ------------------------------------------------------------------ #
    #  get_runtime dependency (profile-scoped routes)                     #
    # ------------------------------------------------------------------ #

    def get_runtime(pid: str, request: Request) -> ProfileRuntime:
        """Resolve ``{pid}`` to its live runtime; map manager errors to HTTP status."""
        try:
            return request.app.state.profiles.get(pid)
        except UnknownProfile:
            raise HTTPException(status_code=404, detail=f"unknown profile: {pid}") from None
        except ArchivedProfile:
            raise HTTPException(status_code=410, detail=f"profile archived: {pid}") from None
        except RuntimeError as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from None

    async def _ws_runtime(websocket: WebSocket, pid: str) -> ProfileRuntime | None:
        """Resolve a runtime for a WS handler, closing the socket with a coherent
        code on failure. WS close codes mirror the HTTP status (4404 unknown,
        4410 archived) so the client can distinguish them; returns None on failure."""
        try:
            return websocket.app.state.profiles.get(pid)
        except UnknownProfile:
            await websocket.close(code=_WS_UNKNOWN_PROFILE, reason="unknown-profile")
        except ArchivedProfile:
            await websocket.close(code=_WS_ARCHIVED_PROFILE, reason="profile-archived")
        except RuntimeError:
            await websocket.close(code=1011, reason="profile-not-running")
        return None

    # ------------------------------------------------------------------ #
    #  Global routes                                                      #
    # ------------------------------------------------------------------ #

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
        """Process-level status: the first running runtime's gateway status, or a
        zero-profile stub (fresh install, §3.5)."""
        runtime = next(manager.runtimes(), None)
        if runtime is None or runtime.gateway is None:
            return {"status": "ok", "profiles": 0}
        return runtime.gateway.status()

    @app.get("/api/status")
    async def status() -> list[dict]:
        """Per-profile activity for badges: busy = agent alive, running_tasks = count
        of RUNNING tasks. Aggregated over the running runtimes."""
        out = []
        for runtime in manager.runtimes():
            gw_status = runtime.gateway.status() if runtime.gateway is not None else {}
            out.append(
                {
                    "pid": runtime.pid,
                    "busy": gw_status.get("status") == "ok",
                    "running_tasks": await _running_tasks(runtime),
                }
            )
        return out

    @app.post("/api/secrets/key")
    async def set_secrets_key(req: KeyRequest) -> dict:
        """Save/clear a provider API key (global secrets). Reloads ALL runtimes so
        every profile's agent picks up the change on its next turn."""
        from assistant import secrets

        if not secrets.set_key(req.provider, req.value):
            return Response(status_code=400)
        for runtime in list(manager.runtimes()):
            with contextlib.suppress(Exception):
                await manager.reload(runtime.pid)
        return {"ok": True}

    @app.post("/api/onboarded")
    async def set_onboarded(req: OnboardedRequest) -> dict:
        """Mark first-run onboarding completed/dismissed (install-level, in the registry)."""
        from assistant import profiles as profiles_mod

        profiles_mod.set_onboarded(req.value)
        return {"ok": True}

    # ---- Profile management (global) ----

    def _profile_view(meta) -> dict:
        return {
            "id": meta.id,
            "name": meta.name,
            "palette": meta.palette,
            "workspace": meta.workspace,
            "created": meta.created,
        }

    @app.get("/api/profiles")
    async def list_profiles() -> dict:
        """The §3.5 contract, present in every state: unarchived profiles, the
        server-side active default, the install-level onboarded flag, and any
        per-runtime channel conflicts. Empty list + null + false on fresh install."""
        from assistant import profiles as profiles_mod

        reg = profiles_mod.load_registry()
        conflicts: dict[str, list[str]] = {}
        for runtime in manager.runtimes():
            if runtime.channel_conflicts:
                conflicts[runtime.pid] = list(runtime.channel_conflicts)
        return {
            "profiles": [
                {**_profile_view(m), "channel_conflicts": conflicts.get(m.id, [])}
                for m in profiles_mod.list_profiles(include_archived=False)
            ],
            "active_default": reg.get("active_default"),
            "onboarded": bool(reg.get("onboarded")),
        }

    @app.post("/api/profiles")
    async def create_profile(req: ProfileCreateRequest):
        """Create a profile (dir + registry) and boot its runtime live (§3.5)."""
        try:
            runtime = await manager.create(req.name, req.palette, workspace=req.workspace)
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        return {"profile": _profile_view(runtime.meta)}

    @app.post("/api/profiles/{pid}")
    async def update_profile(pid: str, req: ProfileUpdateRequest):
        """Rename / set palette (display-only) and/or set workspace (runtime config
        change → reload that runtime). Unknown pid → 404, invalid value → 400."""
        from assistant import profiles as profiles_mod

        if profiles_mod.get_profile(pid) is None:
            return JSONResponse({"error": f"unknown profile: {pid}"}, status_code=404)
        try:
            if req.name is not None:
                profiles_mod.rename_profile(pid, req.name)
            if req.palette is not None:
                profiles_mod.set_palette(pid, req.palette)
            workspace_changed = False
            if req.workspace is not None:
                profiles_mod.set_workspace(pid, req.workspace)
                workspace_changed = True
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        if workspace_changed:
            # runtime config change — reference-swap reload so new turns use it.
            with contextlib.suppress(Exception):
                await manager.reload(pid)
        return {"profile": _profile_view(profiles_mod.get_profile(pid))}

    @app.delete("/api/profiles/{pid}")
    async def archive_profile(pid: str, req: ProfileArchiveRequest | None = None):
        """Archive a profile with the §4.9 guardrails. new_default may come in the
        body. ValueError (guardrail) → 400, unknown → 404, already archived → 410."""
        new_default = req.new_default if req is not None else None
        try:
            await manager.archive(pid, new_default=new_default)
        except UnknownProfile:
            return JSONResponse({"error": f"unknown profile: {pid}"}, status_code=404)
        except ArchivedProfile:
            return JSONResponse({"error": f"profile archived: {pid}"}, status_code=410)
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        return {"ok": True}

    # ---- Google OAuth (global, account-level) ----

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

    @app.get("/api/fs/list")
    async def fs_list(path: str = "") -> dict:
        """List immediate subdirectories of a host path — drives the folder picker. The
        gateway is local + single-user and `_origin_guard` blocks cross-origin, so this is
        safe; dotfolders are hidden. Empty path starts at home."""
        from assistant.workspace import list_dirs

        result = list_dirs(path or str(Path.home()))
        if result is None:
            return {"ok": False, "error": "not a readable directory"}
        return {"ok": True, **result}

    # ------------------------------------------------------------------ #
    #  Profile-scoped router (/api/p/{pid})                              #
    # ------------------------------------------------------------------ #

    p = APIRouter(prefix="/api/p/{pid}")

    def _available_providers() -> dict:
        """Which providers can actually be used right now (key set / Ollama deps)."""
        from assistant import secrets

        st = secrets.status()
        avail = {prov: st[prov]["set"] for prov in ("openai", "gemini", "anthropic")}
        avail["ollama"] = _ollama_installed()
        return avail

    def _ollama_installed() -> bool:
        try:
            from ag2.config import OllamaConfig

            return type(OllamaConfig).__module__ != "unittest.mock"
        except Exception:
            return False

    # ---- Sessions ----

    @p.get("/sessions")
    async def sessions(runtime: ProfileRuntime = Depends(get_runtime)) -> dict:
        """List persisted, resumable conversations (newest first)."""
        return {"sessions": await runtime.gateway.list_sessions()}

    @p.get("/sessions/{session_id}")
    async def session_transcript(
        session_id: str, runtime: ProfileRuntime = Depends(get_runtime)
    ) -> dict:
        """The display transcript for a session, for the UI to restore."""
        return {
            "session_id": session_id,
            "messages": await runtime.gateway.transcript(session_id),
        }

    # ---- Message ----

    @p.post("/message", response_model=MessageResponse)
    async def message(
        req: MessageRequest, runtime: ProfileRuntime = Depends(get_runtime)
    ) -> MessageResponse:
        # Durable, inline HITL bound to this chat session (answerable from the
        # thread or the strip); the request blocks until answered (or times out).
        asker = _chat_asker(runtime, req.session_id)
        reply = await runtime.gateway.send_message(req.text, session_id=req.session_id, asker=asker)
        return MessageResponse(reply=reply, session_id=req.session_id)

    # ---- Tasks + durable HITL inquiries ----

    @p.get("/tasks")
    async def list_tasks(runtime: ProfileRuntime = Depends(get_runtime)) -> dict:
        """Top-level tasks (newest first) for the Tasks view."""
        return {"tasks": await runtime.tasks.list_tasks()}

    @p.post("/tasks")
    async def create_task(req: TaskRequest, runtime: ProfileRuntime = Depends(get_runtime)) -> dict:
        """Kick off a task — intake (clarifying questions) runs in the background
        and surfaces as inquiries to answer."""
        task_id = await runtime.tasks.submit_request(req.text, channel=req.channel)
        return {"id": task_id}

    @p.get("/tasks/all")
    async def list_all_tasks(
        status: str | None = None, runtime: ProfileRuntime = Depends(get_runtime)
    ) -> dict:
        """Full task history for the listing page (newest first). Optional status
        filter: active / completed / stopped / archived."""
        return {"tasks": await runtime.tasks.list_all(status)}

    @p.post("/tasks/schedule")
    async def schedule_task(
        req: ScheduleRequest, runtime: ProfileRuntime = Depends(get_runtime)
    ) -> dict:
        """Schedule a task for a future time (optionally recurring)."""
        task_id = await runtime.tasks.schedule_task(req.text, req.when, req.recurrence)
        return {"id": task_id}

    @p.get("/tasks/{task_id}")
    async def get_task(task_id: str, runtime: ProfileRuntime = Depends(get_runtime)):
        from assistant.tasks import TaskStoreCorruptionError

        try:
            task = await runtime.tasks.get_task(task_id)
        except TaskStoreCorruptionError as exc:
            return JSONResponse({"error": str(exc)}, status_code=500)
        if task is None:
            return Response(status_code=404)
        return {"task": task}

    @p.post("/tasks/{task_id}/cancel")
    async def cancel_task(task_id: str, runtime: ProfileRuntime = Depends(get_runtime)) -> dict:
        ok = await runtime.tasks.cancel(task_id)
        return {"ok": ok}

    @p.post("/tasks/{task_id}/rerun")
    async def rerun_task(task_id: str, runtime: ProfileRuntime = Depends(get_runtime)):
        """Re-run a finished task from a clean start; returns the new run's id."""
        result = await runtime.tasks.rerun(task_id)
        if "error" in result:
            return JSONResponse(result, status_code=400)
        return result

    @p.post("/tasks/{task_id}/seen")
    async def mark_task_seen(task_id: str, runtime: ProfileRuntime = Depends(get_runtime)) -> dict:
        """Mark a task/run as opened (clears its unread highlight in the nav)."""
        ok = await runtime.tasks.mark_seen(task_id)
        return {"ok": ok}

    @p.post("/tasks/{task_id}/archive")
    async def archive_task(
        task_id: str,
        req: ArchiveRequest | None = None,
        runtime: ProfileRuntime = Depends(get_runtime),
    ):
        archived = True if req is None else req.archived
        ok, reason = await runtime.tasks.set_archived(task_id, archived)
        if ok:
            return {"ok": True, "archived": archived}
        if reason == "notfound":
            return Response(status_code=404)
        return JSONResponse(
            {
                "ok": False,
                "error": "Only finished tasks can be archived — cancel it first to stop it.",
            },
            status_code=409,
        )

    @p.post("/tasks/{task_id}/chat")
    async def task_chat(
        task_id: str,
        req: TaskChatRequest,
        runtime: ProfileRuntime = Depends(get_runtime),
    ):
        """Converse about a task — the SAME universal agent, given this task as its
        surface context (it inspects/steers the task via its system tools)."""
        from assistant.system_tools import format_task

        node = await runtime.tasks.get_task(task_id)
        if node is None:
            return Response(status_code=404)
        surface = (
            f"You are on the page for task {task_id}. The user's messages here are "
            f"usually about THIS task — inspect or steer it with your task tools "
            f"(its id is {task_id}). Current state:\n{format_task(node)}"
        )
        asker = _chat_asker(runtime, f"task:{task_id}")
        reply = await runtime.gateway.send_message(
            req.text,
            session_id=f"task:{task_id}",
            asker=asker,
            surface=surface,
        )
        return {"reply": reply}

    @p.get("/inquiries/pending")
    async def inquiries_pending(
        task_id: str | None = None, runtime: ProfileRuntime = Depends(get_runtime)
    ) -> dict:
        """Open HITL inquiries (clarifications / approvals) awaiting an answer."""
        return {"pending": await runtime.tasks.pending_inquiries(task_id)}

    @p.post("/inquiries/{inquiry_id}/answer")
    async def answer_inquiry(
        inquiry_id: str,
        req: AnswerRequest,
        runtime: ProfileRuntime = Depends(get_runtime),
    ):
        ok = await runtime.tasks.answer_inquiry(inquiry_id, req.answer)
        if not ok:
            return Response(status_code=404)
        return {"ok": True}

    # ---- HITL pending (this profile's registry) ----

    @p.get("/hitl/pending")
    async def hitl_pending(runtime: ProfileRuntime = Depends(get_runtime)) -> dict:
        """Open HITL questions in THIS profile's registry, for a UI client to render."""
        return {"pending": runtime.hitl.pending_list()}

    # ---- Voice picker: list voices, select (persist), preview (TTS) ----

    @p.get("/voice/voices")
    async def voice_voices(runtime: ProfileRuntime = Depends(get_runtime)) -> dict:
        from assistant import voice_providers

        settings = _runtime_settings(runtime)
        return {
            "voices": [{"name": n, "style": s} for n, s in settings.voices_for().items()],
            "current": settings.get_voice(),
            "provider": settings.voice_provider(),
            # mic capture rate the client should use, for this profile's provider
            "input_rate": voice_providers.get(settings.voice_provider()).input_rate,
        }

    @p.post("/voice/select")
    async def voice_select(
        req: VoiceRequest, runtime: ProfileRuntime = Depends(get_runtime)
    ) -> dict:
        if not _runtime_settings(runtime).set_voice(req.voice):
            return Response(status_code=400)
        return {"ok": True, "voice": req.voice}

    @p.post("/voice/preview")
    async def voice_preview(req: VoiceRequest, runtime: ProfileRuntime = Depends(get_runtime)):
        from assistant.voice import synthesize_preview

        settings = _runtime_settings(runtime)
        if req.voice not in settings.voices_for():
            return Response(status_code=400)
        try:
            wav = await synthesize_preview(runtime.config, settings, req.voice)
        except Exception as exc:
            return Response(content=str(exc)[:200], status_code=502)
        return Response(content=wav, media_type="audio/wav")

    # ---- Settings ----

    @p.get("/settings")
    async def get_settings(runtime: ProfileRuntime = Depends(get_runtime)) -> dict:
        from assistant import secrets

        cfg = runtime.config
        settings = _runtime_settings(runtime)
        return {
            "keys": secrets.status(),  # per-provider {set, hint} — never raw
            "available": _available_providers(),
            "assistant": {"provider": cfg.llm.provider, "model": cfg.llm.model},
            "voice_provider": settings.voice_provider(),
            "mcp_servers": settings.list_mcp_servers(),
            "project_folder": settings.get_project_folder(),  # repo-files MCP root
            "fs": {  # start roots for the folder picker
                "home": str(Path.home()),
                "cwd": str(Path.cwd()),
                "workspace": str(Path(cfg.workspace_dir).expanduser()),
            },
        }

    async def _mcp_health(server: dict) -> dict:
        from ag2.context import ConversationContext
        from ag2.stream import MemoryStream

        from assistant.tools.mcp import build_mcp_tools

        tools = build_mcp_tools([server])
        if not tools:
            return {"ok": False, "error": "MCP server is disabled"}
        toolkit = tools[0]
        context = ConversationContext(stream=MemoryStream())
        schemas = await toolkit.schemas(context)
        return {
            "ok": True,
            "tools": [
                getattr(getattr(schema, "function", None), "name", "")
                for schema in schemas
                if getattr(getattr(schema, "function", None), "name", "")
            ],
        }

    @p.post("/settings/mcp")
    async def add_mcp_server(
        req: MCPServerRequest, runtime: ProfileRuntime = Depends(get_runtime)
    ) -> dict:
        settings = _runtime_settings(runtime)
        try:
            server = settings.upsert_mcp_server(req.model_dump())
        except ValueError as exc:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)
        await manager.reload(runtime.pid)
        return {"ok": True, "server": server, "mcp_servers": settings.list_mcp_servers()}

    @p.delete("/settings/mcp/{name}")
    async def delete_mcp_server(name: str, runtime: ProfileRuntime = Depends(get_runtime)) -> dict:
        settings = _runtime_settings(runtime)
        if not settings.delete_mcp_server(name):
            return Response(status_code=404)
        await manager.reload(runtime.pid)
        return {"ok": True, "mcp_servers": settings.list_mcp_servers()}

    @p.post("/settings/mcp/{name}/health")
    async def health_mcp_server(name: str, runtime: ProfileRuntime = Depends(get_runtime)) -> dict:
        server = next(
            (
                s
                for s in _runtime_settings(runtime).list_mcp_servers(include_env=True)
                if s["name"] == name
            ),
            None,
        )
        if server is None:
            return Response(status_code=404)
        try:
            return await _mcp_health(server)
        except Exception as exc:
            return {"ok": False, "error": str(exc)[:500]}

    # Read-only subset of @modelcontextprotocol/server-filesystem — the repo-files MCP
    # gets exactly these so the agent can read the project but never write/edit/delete.
    _REPO_FILES_READ_TOOLS = [
        "read_file",
        "read_multiple_files",
        "list_directory",
        "directory_tree",
        "search_files",
        "get_file_info",
        "list_allowed_directories",
    ]

    @p.post("/settings/project-folder")
    async def set_project_folder(
        req: ProjectFolderRequest, runtime: ProfileRuntime = Depends(get_runtime)
    ):
        """Persist the chosen project folder AND seed a read-only `repo-files` MCP pointed
        at it (reusing the MCP-server settings path), then reload so the agent picks it up."""
        settings = _runtime_settings(runtime)
        fp = Path(req.path or "").expanduser()
        if not req.path or not fp.is_dir():
            return JSONResponse({"error": "not a directory"}, status_code=400)
        folder = str(fp.resolve())
        settings.set_project_folder(folder)
        settings.upsert_mcp_server(
            {
                "name": "repo-files",
                "command": "npx",
                "args": ["-y", "@modelcontextprotocol/server-filesystem", folder],
                "allowed_tools": list(_REPO_FILES_READ_TOOLS),
            }
        )
        await manager.reload(runtime.pid)  # new turns get the repo-files tools
        return {"ok": True, "project_folder": folder}

    @p.post("/settings/llm")
    async def set_settings_llm(
        req: LlmRequest, runtime: ProfileRuntime = Depends(get_runtime)
    ) -> dict:
        provider = req.provider.lower()
        if not _available_providers().get(provider):
            hint = (
                "Install with `pip install ag2[ollama]`."
                if provider == "ollama"
                else "Add the provider's API key first."
            )
            return JSONResponse(
                {"ok": False, "error": f"{provider} isn't available. {hint}"}, status_code=409
            )
        _runtime_settings(runtime).set_llm(provider=provider, model=req.model or None)
        await manager.reload(runtime.pid)
        return {"ok": True}

    @p.post("/settings/voice_provider")
    async def set_settings_voice_provider(
        req: VoiceProviderRequest, runtime: ProfileRuntime = Depends(get_runtime)
    ) -> dict:
        provider = req.provider.lower()
        if not _available_providers().get(provider):
            return JSONResponse(
                {"ok": False, "error": f"Add the {provider} API key first."}, status_code=409
            )
        if not _runtime_settings(runtime).set_voice_provider(provider):
            return Response(status_code=400)
        return {"ok": True}

    # ---- Memory: view + edit the learned user profile ----

    @p.get("/memory")
    async def get_memory(runtime: ProfileRuntime = Depends(get_runtime)) -> dict:
        from assistant.memory import read_profile

        return {"text": await read_profile(runtime.config.data_dir / "profile.db")}

    @p.post("/memory")
    async def set_memory(
        req: MemoryRequest, runtime: ProfileRuntime = Depends(get_runtime)
    ) -> dict:
        from assistant.memory import write_profile

        await write_profile(req.text, runtime.config.data_dir / "profile.db")
        return {"ok": True}

    # ---- Workspace (the agent's working file space) ----

    @p.get("/files")
    async def list_workspace_files(runtime: ProfileRuntime = Depends(get_runtime)) -> dict:
        """Files the agent has written in the workspace (for the GUI browser)."""
        from assistant.workspace import list_files

        return {
            "root": str(Path(runtime.config.workspace_dir).expanduser()),
            "files": list_files(runtime.config.workspace_dir),
        }

    @p.get("/files/raw")
    async def workspace_file(
        path: str, download: bool = False, runtime: ProfileRuntime = Depends(get_runtime)
    ):
        """Serve one workspace file (view inline or download), sandboxed to the
        workspace root — a path that escapes it is rejected."""
        from assistant.workspace import resolve

        rp = resolve(runtime.config.workspace_dir, path)
        if rp is None:
            return JSONResponse({"error": "file not found"}, status_code=404)
        disp = "attachment" if download else "inline"
        return FileResponse(rp, headers={"Content-Disposition": f'{disp}; filename="{rp.name}"'})

    @p.delete("/files/raw")
    async def delete_workspace_file(
        path: str, runtime: ProfileRuntime = Depends(get_runtime)
    ) -> dict:
        """Delete one workspace file, sandboxed to the workspace root (same guard as
        serving). Prunes an emptied per-task subfolder afterwards."""
        from assistant.workspace import delete

        if not delete(runtime.config.workspace_dir, path):
            return JSONResponse({"error": "file not found"}, status_code=404)
        return {"ok": True}

    @p.get("/usage")
    async def usage_today(runtime: ProfileRuntime = Depends(get_runtime)) -> dict:
        """Today's token + estimated-cost totals (cost & activity HUD)."""
        return runtime.gateway.usage_today()

    app.include_router(p)

    # ------------------------------------------------------------------ #
    #  Profile-scoped WebSockets (registered directly, not on the router  #
    #  — Starlette APIRouter WS + Depends is fiddly; resolve inline)      #
    # ------------------------------------------------------------------ #

    @app.websocket("/api/p/{pid}/stream")
    async def stream_ws(websocket: WebSocket, pid: str) -> None:
        """Event-stream transport: the client receives the session's events as
        `{event:{type,data}}` — replayed on connect, then live — and sends `{text}`
        turns. Closes with 4001 if the profile is archived mid-session (§4.9)."""
        if not _origin_ok(websocket.headers.get("origin"), websocket.headers.get("host")):
            await websocket.close(code=1008)  # policy violation
            return
        runtime = await _ws_runtime(websocket, pid)
        if runtime is None:
            return
        await websocket.accept()
        from assistant.gateway.stream_bridge import StreamBridge

        # Archive → close this socket with 4001 (§4.9). Tolerant: a closed socket
        # must not error the archive loop (runtime.close suppresses callback errors).
        async def _on_archive():
            with contextlib.suppress(Exception):
                await websocket.close(code=_WS_PROFILE_ARCHIVED, reason="profile-archived")

        runtime.on_close(_on_archive)

        session_id = websocket.query_params.get("session") or "default"
        default_surface = _SURFACES.get(websocket.query_params.get("surface", ""), "")
        bridge = StreamBridge(runtime.gateway, websocket, session_id)

        async def turn_surface() -> str:
            # task threads get a fresh task snapshot each turn so "this task" resolves
            if session_id.startswith("task:"):
                tid = session_id.split(":", 1)[1]
                node = await runtime.tasks.get_task(tid)
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
                    # Chat permission prompts live in this profile's HITL registry;
                    # durable task inquiries (answered inline on a task page) live in
                    # the InquiryStore under a different id — fall back to it so an
                    # inline answer resolves either kind.
                    if not runtime.hitl.answer(iid, ans):
                        with contextlib.suppress(Exception):
                            await runtime.tasks.answer_inquiry(iid, ans)
                    continue
                if data.get("type") == "feedback" and data.get("target_id"):
                    # 👍/👎 + mandatory reason on a generated item. Emit it onto the
                    # session stream (persists/replays → the GUI projects the thumb
                    # state, shows in the AG2 inspector), then fire-and-forget a learner
                    # that distils it into the memory profile (never blocks the socket).
                    from assistant import feedback as feedback_learner
                    from assistant.events import FeedbackGiven

                    sentiment = "down" if data.get("sentiment") == "down" else "up"
                    reason = (data.get("reason") or "").strip()
                    content = data.get("content") or ""
                    request = data.get("request") or ""
                    with contextlib.suppress(Exception):
                        await runtime.gateway.emit_event(
                            session_id,
                            FeedbackGiven(
                                data["target_id"],
                                target_kind=data.get("target_kind", "message"),
                                sentiment=sentiment,
                                reason=reason,
                                content=content[:2000],
                                request=request[:2000],
                            ),
                        )
                    if reason:  # reason is mandatory client-side; only learn when present
                        asyncio.create_task(
                            feedback_learner.learn(
                                runtime.config,
                                sentiment=sentiment,
                                reason=reason,
                                content=content,
                                request=request,
                            )
                        )
                    continue
                text = data.get("text", "")
                raw_atts = data.get("attachments")
                attachments = _decode_attachments(raw_atts)
                if not text and attachments:
                    text = "Here is a file I'm sharing with you."
                if not text:
                    continue
                asker = _chat_asker(runtime, session_id)
                surface = await turn_surface()
                # Persist uploads into the workspace and tell the agent their paths (via
                # surface, so the transcript stays clean) — enables editing/reading them.
                saved = _persist_uploads(runtime.config.workspace_dir, raw_atts)
                if saved:
                    from assistant.events import Attachment

                    surface = (surface + "\n\n" if surface else "") + (
                        "The user attached file(s), saved in the workspace at: "
                        + ", ".join(pth for pth, _ in saved)
                        + ". To edit an uploaded image, call generate_image with "
                        "source_image set to its path; to read an uploaded document, "
                        "read_file that path."
                    )
                    # Surface each upload in the thread (thumbnail / file chip) — emitted
                    # before the turn so it sits with the user's message; persists on the
                    # session stream so it survives reload.
                    for pth, name in saved:
                        with contextlib.suppress(Exception):
                            await runtime.gateway.emit_event(session_id, Attachment(pth, name=name))
                asyncio.create_task(
                    bridge.run_turn(text, asker=asker, attachments=attachments, surface=surface)
                )
        except WebSocketDisconnect:
            return
        finally:
            bridge.close()

    @app.websocket("/api/p/{pid}/voice")
    async def voice_ws(websocket: WebSocket, pid: str) -> None:
        """Full-duplex voice. The browser streams 16 kHz mono PCM mic frames as
        binary; we feed them to a Gemini Live session and stream back 24 kHz PCM
        speech (binary) plus user/agent transcripts (JSON) for on-screen bubbles."""
        if not _origin_ok(websocket.headers.get("origin"), websocket.headers.get("host")):
            await websocket.close(code=1008)  # policy violation
            return
        runtime = await _ws_runtime(websocket, pid)
        if runtime is None:
            return
        await websocket.accept()
        import uuid

        from ag2.events import (
            ModelMessage,
            ModelMessageChunk,
            ModelRequest,
            ModelResponse,
            TextInput,
            ToolCallsEvent,
        )
        from ag2.events.voice import (
            RecordedAudioEvent,
            SynthesizedAudioEvent,
            TranscriptionChunkEvent,
            TranscriptionCompletedEvent,
        )

        from assistant.gateway.wire import to_wire

        # Archive → close this voice socket with 4001 (§4.9), tolerant of a closed sock.
        async def _on_archive():
            with contextlib.suppress(Exception):
                await websocket.close(code=_WS_PROFILE_ARCHIVED, reason="profile-archived")

        runtime.on_close(_on_archive)

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
                    await runtime.gateway.emit_event(
                        persist_session, ModelRequest(parts=[TextInput(content=text)])
                    )

        async def _flush_agent():
            text = "".join(agent_buf).strip()
            agent_buf.clear()
            if persist_session and text:
                with contextlib.suppress(Exception):
                    await runtime.gateway.emit_event(
                        persist_session, ModelResponse(message=ModelMessage(content=text))
                    )

        async def forward_event(event) -> None:
            # Forward a structured AG2 event verbatim (same wire shape as the text
            # StreamBridge) so the voice client folds it with the one shared reducer
            # → tool chips/cards, task cards, deliverables, all "for free".
            with contextlib.suppress(Exception):
                await websocket.send_json({"event": to_wire(event)})

        # The voice agent can hang up the call itself via its end_call tool, which
        # trips this event; wait_end() (below) then ends the job race → teardown.
        end_requested = asyncio.Event()

        try:
            agent = await runtime.gateway.build_voice_agent(
                session_id=sid,
                task_id=task_id,
                chat_session=chat_session,
                on_event=forward_event,  # delegated universal-agent events → voice client
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

        async def pump_events(context):
            # The voice agent's OWN basic tools (delegated universal-agent events
            # come through forward_event). Forward the batch raw — minus the plumbing
            # tools (ask_assistant/end_call) the user shouldn't see — so the client
            # folds chips/cards the same way it does for text. Only the ToolCallsEvent
            # batch is forwarded; the client's reducer ignores the singular event.
            with context.stream.where(ToolCallsEvent).join() as evs:
                async for e in evs:
                    calls = [
                        c
                        for c in e.calls
                        if (getattr(c, "name", "") or "") not in ("ask_assistant", "end_call")
                    ]
                    if calls:
                        await forward_event(ToolCallsEvent(calls=calls))

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
                    asyncio.create_task(pump_events(context)),
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

    # ------------------------------------------------------------------ #
    #  Static assets / SPA shell (global)                                 #
    # ------------------------------------------------------------------ #

    @app.get("/voices/{name}.wav")
    async def voice_sample(name: str):
        """Pre-recorded voice sample (from scripts/record_voice_samples.py), if present.
        404 → the client falls back to live TTS. Profile-agnostic asset (the sample set
        is a static bundle; the per-profile voice choice is served under /api/p/{pid})."""
        f = _STATIC_DIR / "voices" / f"{name}.wav"
        if f.is_file():
            return FileResponse(f, media_type="audio/wav")
        return Response(status_code=404)

    _APP_DIR = _STATIC_DIR / "app"

    @app.get("/app")
    @app.get("/app/{path:path}")
    async def spa_app(path: str = ""):
        """Serve the Vite+Svelte client (built into static/app). Real asset files
        are served as-is; any other /app/* path falls back to index.html so SPA
        deep links (/app/{pid}/c/<id>, /app/{pid}/t/<id>) survive refresh."""
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
        except Exception as exc:
            from assistant.observability import log_suppressed

            log_suppressed("attachment decode", exc, name=a.get("name"))
            continue
        inp = build_input(raw, a.get("name", "file"), a.get("mime"))
        if inp is not None:
            out.append(inp)
    return out


def _persist_uploads(workspace_dir, items) -> list[tuple[str, str]]:
    """Save uploaded files into the workspace (uploads/) so the agent can edit/read
    them by path — returns ``(workspace_path, original_name)`` per saved file."""
    import base64

    from assistant.workspace import write_upload

    out = []
    for a in items or []:
        try:
            raw = base64.b64decode(a.get("data", ""))
        except Exception as exc:
            from assistant.observability import log_suppressed

            log_suppressed("upload decode", exc, name=a.get("name"))
            continue
        if not raw:
            continue
        try:
            name = a.get("name", "file")
            out.append((write_upload(workspace_dir, name, raw), name))
        except Exception as exc:
            from assistant.observability import log_suppressed

            log_suppressed("upload persist", exc, name=name)
    return out
