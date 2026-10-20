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
    GET  /api/status                         -> [{pid, busy, running_tasks, unseen_done}] activity badges
    GET  /api/usage                          -> {profiles:[{pid,name,...}], total} install-wide roll-up
    POST /api/secrets/key                    -> save a provider key (upserts its Default Secret); reloads ALL runtimes
    GET/POST /api/secrets[/{sid}] + DELETE   -> Secret CRUD (named reusable API keys); reloads ALL runtimes
    GET  /api/llm-configs                     -> named LLM configs (install-wide) + active + env_override
    POST /api/llm-configs[/{cid}]             -> create/update a config (dry-construct → save → reload ALL)
    DELETE /api/llm-configs/{cid}             -> delete a config (409 if active); reloads ALL
    POST /api/llm-configs/{cid}/use           -> set the active config; reloads ALL
    POST /api/llm-configs/{cid}/test          -> real PONG round-trip (502 on failure)
    POST /api/llm-configs/test                -> same round-trip for an UNSAVED editor draft
    POST /api/onboarded                      -> set the install-level onboarding flag
    GET/POST /api/memory                     -> universal "who the user is" doc (shared root/user.db)
    POST /api/identity                       -> seed universal doc from web onboarding (name/location/hours/style); seed-only, never clobbers
    GET  /api/profiles                       -> {profiles, archived, active_default, onboarded} (§3.5 contract)
    POST /api/profiles                       -> create {name, accent}; boots live
    POST /api/profiles/{pid}                 -> rename / accent (display-only)
    POST /api/profiles/{pid}/exposure        -> {surface, exposed}; withdraw a profile from a surface
    POST /api/profiles/{pid}/restore         -> un-archive + boot live (ADR 0003)
    DELETE /api/profiles/{pid}               -> archive (guardrails §4.9); ?purge=true hard-deletes an archived profile
    GET  /api/connections                    -> {connections: [{id, platform, name, tokens, default_profile, active, error, paired_accounts}]}
    POST /api/connections                    -> {platform, name, tokens} create + start; returns the new entry
    POST /api/connections/{cid}              -> {name}; rename a Connection
    POST /api/connections/{cid}/token        -> {tokens}; replace token(s) + restart, rolled back on failure
    DELETE /api/connections/{cid}            -> stop + forget it and its Peers, pairing, default and exposure
    POST /api/connections/{cid}/default      -> set {profile:pid|null} for one Connection; returns updated entry
    GET  /api/connections/{cid}/exposure     -> {surfaces, exposure: {pid: {surface: bool}}, default_profile}
    POST /api/connections/{cid}/exposure     -> {profile, surface, exposed}; withdraw a profile from one surface
    GET  /api/connections/{cid}/pairing      -> {accounts, code} — who may reach this one Connection
    POST /api/connections/{cid}/pairing      -> {value}; pair by numeric id or @handle
    DELETE /api/connections/{cid}/pairing/{key} -> withdraw one entry from this Connection
    POST /api/connections/{cid}/pairing/code -> mint this Connection's one live code
    GET  /api/connections/{cid}/groups       -> this Connection's group Peers + the profiles they may be pinned to
    POST /api/connections/{cid}/groups/{chat_id}/profile -> re-point one group (ADR 0022)
    GET  /api/google/*                       -> account-level OAuth (shared like keys)
    GET  /api/fs/list                        -> generic folder browser (pickers)
    GET  /hitl/{req_id}, POST .../answer     -> styled HITL pages over a cross-profile dispatcher
    static: /, /{name}.svg, /favicon.ico, /voices/{name}.wav, /app*, catch-all

  Profile-scoped (under /api/p/{pid}):
    GET  chats, chats/{cid}
    POST message
    GET/POST tasks* (all/schedule/{id}/cancel/rerun/seen/archive/chat)
    GET  inquiries/pending, POST inquiries/{id}/answer
    GET  hitl/pending
    WS   stream, WS voice; GET voice/voices, POST voice/select, POST voice/preview
    GET/POST settings, settings/mcp*, settings/focuses, settings/voice_provider
    GET/POST memory                          -> THIS profile's persona memory (profiles/<id>/profile.db)
    GET  files (files+dirs), GET/PUT/DELETE files/raw (GET emits ETag; PUT in-place
         write with If-Match, ADR 0011; delete: file or Directory, recursive)
    POST files/upload (multipart → target dir), files/mkdir, files/move ({from,to}) — ADR 0007
    GET  usage
"""

import asyncio
import contextlib
import uuid
from collections.abc import Callable, Mapping
from collections.abc import Set as AbstractSet
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlsplit

import httpx
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
from ag2.tools.skills.skill_search.client import SkillsClient
from fastapi import (
    APIRouter,
    FastAPI,
    HTTPException,
    Request,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.responses import (
    FileResponse,
    HTMLResponse,
    JSONResponse,
    RedirectResponse,
    Response,
)

from assistant import (
    __version__,
    provider_catalog,
    voice_providers,
)
from assistant.agent import model_config
from assistant.codex_auth import (
    CodexAuth,
    _capture_code,
)
from assistant.coding.detect import parse_bridge
from assistant.coding.model_catalog import ModelCatalog
from assistant.connections import ConnectionStore
from assistant.gateway.profile_manager import (
    ArchivedProfile,
    ProfileManager,
    ProfileRuntime,
    UnknownProfile,
)
from assistant.gateway.routes import (
    acp,
    agui,
    card,
    card_instance,
    card_source,
    chat,
    connection,
    file,
    folder,
    llm,
    permission,
    profile,
    screen,
    secret,
    settings,
    skill,
    system,
    task,
)
from assistant.gateway.routes.deps import GatewayDeps
from assistant.gateway.schemas import ERROR_RESPONSES
from assistant.gateway.wire import as_drawn, to_wire
from assistant.hitl import add_hitl_routes
from assistant.integrations.google_auth import GoogleAuth
from assistant.live_configs import LiveConfigStore
from assistant.llm_configs import LlmConfigStore
from assistant.pairing import PairingStore
from assistant.peers import PeerStore
from assistant.profiles import ProfileRegistry
from assistant.secrets import SecretStore

_STATIC_DIR = Path(__file__).parent / "static"

# WebSocket close codes for profile resolution failures (documented, coherent set).
# Chosen to mirror the HTTP status they correspond to (4000 + status), and distinct
# from 4001 = profile-archived-mid-session (§4.9) and 1008 = origin policy violation.
_WS_UNKNOWN_PROFILE = 4404  # {pid} not in registry (≈ 404)
_WS_ARCHIVED_PROFILE = 4410  # {pid} archived (≈ 410)
_WS_PROFILE_ARCHIVED = 4001  # runtime archived while this socket was open (§4.9)

# Wall-clock ceiling on the POST /api/llm-configs/{cid}/test PONG round-trip. A
# Default ceiling on a provider "Test" call; `create_app(llm_probe_timeout_s=…)`
# overrides it. The real value only bounds a genuinely wedged provider call.
_LLM_TEST_TIMEOUT_S = 30.0


async def _live_key_probe(provider: str, api_key: str) -> None:
    """Production live-config key probe: the provider's own cheap check. Raises on
    a bad or absent key."""
    await voice_providers.get(provider).check(api_key)


def _allowed_origins(env: Mapping[str, str]) -> set[str]:
    """Extra browser origins to accept besides same-origin. Comma-separated in
    AG2ASSISTANT_ALLOWED_ORIGINS — an escape hatch for proxied/remote demos."""
    raw = env.get("AG2ASSISTANT_ALLOWED_ORIGINS", "")
    return {o.strip().rstrip("/") for o in raw.split(",") if o.strip()}


def _origin_ok(
    origin: str | None, host: str | None, allowed: AbstractSet[str] = frozenset()
) -> bool:
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
    if origin in allowed:
        return True
    return bool(host) and urlsplit(origin).netloc == host


# Surface context the client can request by token (kept server-side, not in the UI).
_SURFACES = {
    "new_task": (
        "The user is starting a NEW TASK. If their request is clear enough, create "
        "it now with create_task (pass schedule_kind='once'/'cron' with at/cron if "
        "they gave a time or cadence, otherwise leave it 'manual'); only ask a brief "
        "clarifying question if something essential is missing. Confirm what you created."
    ),
}


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


def create_app(
    profiles: ProfileManager,
    *,
    persist: bool = True,
    code_reader: Callable[[str], str] = _capture_code,
    codex_client: httpx.Client | None = None,
    google: GoogleAuth | None = None,
    llm_probe: Callable = model_config,
    llm_probe_timeout_s: float = _LLM_TEST_TIMEOUT_S,
    llm_catalog_probe: Callable = provider_catalog.probe_provider_models,
    live_probe: Callable = _live_key_probe,
    skills_client: SkillsClient | None = None,
    card_source_tools: dict[str, Callable] | None = None,
    card_source_executor: Callable | None = None,
) -> FastAPI:
    """Build the FastAPI app around a (constructed-but-not-started) ``ProfileManager``.

    The app owns the manager's lifecycle: ``profiles.start()`` runs on lifespan
    startup (boot all unarchived profiles) and ``profiles.close()`` on
    shutdown. ``persist`` is accepted for signature symmetry (the manager itself is
    already configured with its persistence choice).

    ``app.state.profiles`` holds the manager; there is no ``app.state.gateway`` /
    ``app.state.tasks`` — profile-scoped routes resolve a runtime per request.

    ``code_reader`` is how the ChatGPT sign-in flow waits for the OAuth redirect: the
    default runs a real loopback listener, so it is injected rather than reached for.
    ``codex_client`` is the HTTP client that flow's token exchange goes out on,
    ``google`` is the Google integration the /api/google/* routes drive, and
    ``live_probe`` is the voice-provider key probe behind the live-config "Test",
    ``llm_catalog_probe`` the provider model-list probe behind the Model field's
    combobox (one async callable over a resolved provider identity).
    ``skills_client`` is the skills.sh registry client the search/install routes use
    (omitted: ag2's own, going to the live registry).
    """
    manager = profiles
    # Install-level stores, all hanging off the manager's layout. Built once here;
    # each one re-reads its file per call, so a write is visible to the next request.
    paths = manager.paths
    registry = ProfileRegistry(paths)
    secret_store = SecretStore(paths)
    llm_store = LlmConfigStore(paths)
    live_store = LiveConfigStore(paths)
    connection_store = ConnectionStore(paths, manager.env)
    pairing_store = PairingStore(paths)
    peer_store = PeerStore(paths)
    codex = CodexAuth(paths, client=codex_client)
    google = google if google is not None else GoogleAuth(paths)
    allowed_origins = _allowed_origins(manager.env)
    # Host facts for the coding-agent routes: where ACP adapters live and whether a
    # host bridge stands in for local spawns. Both come from the install config, so
    # no route reads the process environment.
    search_path = manager.config.search_path
    acp_bridge = parse_bridge(manager.config.acp_bridge, manager.config.acp_bridge_token)
    # One catalog per app: it owns its TTL cache, so no state leaks between installs.
    catalog = ModelCatalog(search_path=search_path, bridge=acp_bridge)
    # Everything above, in one parcel for the route modules under gateway/routes/.
    deps = GatewayDeps(
        manager=manager,
        paths=paths,
        registry=registry,
        secret_store=secret_store,
        llm_store=llm_store,
        live_store=live_store,
        connection_store=connection_store,
        pairing_store=pairing_store,
        peer_store=peer_store,
        codex=codex,
        google=google,
        catalog=catalog,
        acp_bridge=acp_bridge,
        search_path=search_path,
        allowed_origins=allowed_origins,
    )

    def secret_env() -> dict[str, str]:
        """Provider/channel keys as an actual call would see them: the saved secrets
        layered over the install config's ambient slice. Recomputed per call — a key
        can be saved or cleared mid-session."""
        return secret_store.merged_env(manager.config.secret_env)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        await manager.start()  # boot all unarchived profiles (+ channels)
        try:
            yield
        finally:
            await manager.close()

    app = FastAPI(
        title="AG2 Assistant Gateway",
        version=__version__,
        lifespan=lifespan,
        # Every error body in app.py is {"error": str}; documenting the codes once
        # here (and once on the profile router) covers all 130 JSON routes.
        responses=ERROR_RESPONSES,
    )
    app.state.profiles = manager
    app.state.google_flows = {}  # state token -> in-progress OAuth flow
    app.state.codex_flows = {}  # state token -> PKCE verifier (ChatGPT-subscription login)

    @app.middleware("http")
    async def _origin_guard(request: Request, call_next):
        """Reject cross-origin browser requests to the API. Same-origin and
        non-browser (no Origin) requests pass; WebSocket routes guard separately
        (Starlette doesn't run HTTP middleware for them)."""
        if request.url.path.startswith("/api/") and not _origin_ok(
            request.headers.get("origin"), request.headers.get("host"), allowed_origins
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

    # The install-wide status surfaces (health, usage, activity, coding agents,
    # the folder picker, memory, identity) live in gateway/routes/system.py.
    app.include_router(system.build_router(deps, code_reader=code_reader))

    # Secrets (named reusable API keys) live in gateway/routes/secret.py; the named
    # LLM and live (voice) configurations in gateway/routes/llm.py. Both are included
    # here, in the order they were declared, so /api/secrets/key stays ahead of
    # /api/secrets/{sid} and the two /test literals ahead of their /{cid}.
    app.include_router(secret.build_router(deps))
    app.include_router(
        llm.build_router(
            deps,
            secret_env=secret_env,
            llm_probe=llm_probe,
            llm_probe_timeout_s=llm_probe_timeout_s,
            llm_catalog_probe=llm_catalog_probe,
            live_probe=live_probe,
        )
    )

    # The install-wide command-rule store — one permissions.json every profile
    # shares — lives in gateway/routes/permission.py, beside the task-scoped pair
    # already included on `p` below.
    app.include_router(permission.build_router(deps))

    # Folders — the install-wide registry of directories outside the Root and the
    # Grants that reach them (ADR 0006) — live in gateway/routes/folder.py. The one
    # profile-scoped Folder surface (/folders/roots) is included on `p` below.
    app.include_router(folder.build_router(deps))

    # Skills — the install-wide Enable/Disable projection (ADR 0016) and the
    # registry/git/upload installs (ADR 0017) — live in gateway/routes/skill.py,
    # together with their per-profile mirrors, included on `p` below.
    app.include_router(skill.build_router(deps, skills_client=skills_client))
    app.include_router(card.build_router(deps))

    # Profiles (the registry every client boots from) and Connections (an instance
    # of a messaging platform with its exposure, pairing and group tables) are both
    # install-level, and live in gateway/routes/profile.py and connection.py.
    app.include_router(profile.build_router(deps))
    app.include_router(connection.build_router(deps))

    # ACP listeners: install-level, one-Profile-per-listener, never
    # exposure-gated (ADR 0031) — its own router beside Connections rather than
    # folded into connection.py, which every consumer of CHANNEL_PLATFORMS assumes
    # is a token-bearing messaging adapter.
    app.include_router(acp.build_router(deps))

    # ------------------------------------------------------------------ #
    #  Profile-scoped router (/api/p/{pid})                              #
    # ------------------------------------------------------------------ #

    p = APIRouter(prefix="/api/p/{pid}", responses=ERROR_RESPONSES)

    # This profile's memory document and today's spend, then everything behind the
    # gear: the settings panel, the health roll-up, the MCP list and the voice
    # picker. No path family below shadows another — every one is either a literal
    # or unambiguously distinct — so include order here is documentation, not
    # dispatch: it follows the order these routes were declared in.
    p.include_router(system.build_profile_router(deps, get_runtime))
    p.include_router(settings.build_profile_router(deps, get_runtime, secret_env=secret_env))

    # Chats and the message turn, tasks with their runs and inquiries (plus the
    # transient HITL questions, whose zod twin lives in task.ts), and the
    # task-scoped permission pair — in its own module because a module follows its
    # zod twin (TaskRules is declared in permission.ts).
    p.include_router(chat.build_profile_router(deps, get_runtime))
    p.include_router(agui.build_profile_router(deps, get_runtime, surfaces=_SURFACES))
    p.include_router(card.build_profile_router(deps, get_runtime))
    p.include_router(card_instance.build_profile_router(deps, get_runtime))
    p.include_router(screen.build_profile_router(get_runtime))
    p.include_router(
        card_source.build_profile_router(
            get_runtime, tools=card_source_tools, executor=card_source_executor
        )
    )
    p.include_router(task.build_profile_router(deps, get_runtime))
    p.include_router(permission.build_profile_router(deps, get_runtime))

    # The profile's own Skills slice — Suppression of shared skills, own-skill state,
    # and installs that land in this profile only — rides with the install-wide
    # surface in gateway/routes/skill.py: one domain seen from two scopes.
    p.include_router(skill.build_profile_router(deps, get_runtime, skills_client=skills_client))

    # The profile's file surfaces — its Files space, the granted Folders beside it,
    # the @-picker corpus and the preview backlink — live in gateway/routes/file.py.
    # /folders/roots rides with them because it answers the same tree, but it is
    # declared in routes/folder.py, following its zod twin (FolderRoots in folder.ts).
    p.include_router(file.build_profile_router(deps, get_runtime))
    p.include_router(folder.build_profile_router(deps, get_runtime))

    app.include_router(p)

    # ------------------------------------------------------------------ #
    #  Profile-scoped WebSockets (registered directly, not on the router  #
    #  — Starlette APIRouter WS + Depends is fiddly; resolve inline)      #
    # ------------------------------------------------------------------ #

    @app.websocket("/api/p/{pid}/card-sources/stream")
    async def file_sources_ws(websocket: WebSocket, pid: str) -> None:
        """Forward typed file source events to views in the current Profile."""
        if not _origin_ok(
            websocket.headers.get("origin"), websocket.headers.get("host"), allowed_origins
        ):
            await websocket.close(code=1008)
            return
        runtime = await _ws_runtime(websocket, pid)
        if runtime is None:
            return
        await websocket.accept()
        stream = runtime.require_gateway().card_sources.file_stream

        async def forward(event):
            with contextlib.suppress(WebSocketDisconnect, RuntimeError, OSError):
                await websocket.send_json({"event": to_wire(event)})

        async def close():
            with contextlib.suppress(WebSocketDisconnect, RuntimeError, OSError):
                await websocket.close(code=_WS_PROFILE_ARCHIVED)

        runtime.on_close(close)
        subscription = stream.subscribe(forward)
        try:
            await websocket.send_json({"type": "ready", "chat": ""})
            while True:
                await websocket.receive_text()
        except WebSocketDisconnect:
            pass
        finally:
            stream.unsubscribe(subscription)
            runtime.off_close(close)

    @app.websocket("/api/p/{pid}/voice")
    async def voice_ws(websocket: WebSocket, pid: str) -> None:
        """Full-duplex voice. The browser streams 16 kHz mono PCM mic frames as
        binary; we feed them to a Gemini Live session and stream back 24 kHz PCM
        speech (binary) plus user/agent transcripts (JSON) for on-screen bubbles."""
        if not _origin_ok(
            websocket.headers.get("origin"), websocket.headers.get("host"), allowed_origins
        ):
            await websocket.close(code=1008)  # policy violation
            return
        runtime = await _ws_runtime(websocket, pid)
        if runtime is None:
            return
        gateway = runtime.gateway  # set for any runtime the manager hands out
        if gateway is None:
            await websocket.close(code=1011, reason="profile-not-running")
            return
        await websocket.accept()

        # Archive → close this voice socket with 4001 (§4.9), tolerant of a closed sock.
        async def _on_archive():
            with contextlib.suppress(Exception):
                await websocket.close(code=_WS_PROFILE_ARCHIVED, reason="profile-archived")

        runtime.on_close(_on_archive)

        sid = uuid.uuid4().hex[:8]
        # A run is a plain chat now, so voice has one binding to make: the chat
        # it's continuing (its stream carries task/run framing already, same as
        # any other chat). persist_chat mirrors that stream so spoken transcripts
        # survive reload and join the shared history (None → bare voice session).
        origin_chat = websocket.query_params.get("chat") or None
        persist_chat = origin_chat
        # Persist spoken turns by ROLE ALTERNATION, not the "completed" event (Gemini
        # doesn't fire it reliably): accumulate each side's chunks and flush a turn
        # when the other side starts speaking → alternating ModelRequest/ModelResponse.
        user_buf: list[str] = []
        agent_buf: list[str] = []
        last_role: dict[str, str | None] = {"v": None}  # "user" | "agent"
        user_chunked = {"v": False}  # the utterance in flight already arrived as chunks

        async def _flush_user():
            text = "".join(user_buf).strip()
            user_buf.clear()
            if persist_chat and text:
                with contextlib.suppress(Exception):
                    await gateway.emit_event(
                        persist_chat, ModelRequest(parts=[TextInput(content=text)])
                    )

        async def _flush_agent():
            text = "".join(agent_buf).strip()
            agent_buf.clear()
            if persist_chat and text:
                with contextlib.suppress(Exception):
                    await gateway.emit_event(
                        persist_chat, ModelResponse(message=ModelMessage(content=text))
                    )

        async def forward_event(event) -> None:
            # Forward a structured AG2 event verbatim (same wire shape as the text
            # StreamBridge) so the voice client folds it with the one shared reducer
            # → tool chips/cards, task cards, deliverables, all "for free".
            with contextlib.suppress(Exception):
                await websocket.send_json(
                    {"event": to_wire(as_drawn(event, gateway.catalog, gateway.card_sources))}
                )

        # The voice agent can hang up the call itself via its end_call tool, which
        # trips this event; wait_end() (below) then ends the job race → teardown.
        end_requested = asyncio.Event()

        try:
            agent = await gateway.build_voice_agent(
                voice_id=sid,
                origin_chat=origin_chat,
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
                        frame: dict[str, str | bool] = {
                            "type": "transcript",
                            "role": "agent",
                            "text": e.content,
                        }
                    elif isinstance(e, TranscriptionCompletedEvent) and user_chunked["v"]:
                        # Chunks already carried this utterance; the completed text only
                        # closes the bubble (it can land after the agent started).
                        user_chunked["v"] = False
                        frame = {"type": "transcript", "role": "user", "text": e.content}
                        frame["final"] = True
                    else:  # user chunk, or a completed with no chunks before it
                        if last_role["v"] == "agent":
                            await _flush_agent()  # agent turn ended
                        last_role["v"] = "user"
                        user_buf.append(e.content)
                        user_chunked["v"] = isinstance(e, TranscriptionChunkEvent)
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

    @app.api_route("/api/{full_path:path}", methods=["POST", "PUT", "PATCH", "DELETE"])
    async def api_not_found(full_path: str):
        """Any unmatched /api/* write → 404 (not Starlette's default 405, which the
        GET SPA catch-all below would otherwise force). Keeps 'route is gone' honest
        across every method: a retired route 404s whether it's read or written."""
        return Response(status_code=404)

    @app.get("/{full_path:path}")
    async def spa(full_path: str):
        """Any other path → the Svelte app at /app (unknown /api paths 404)."""
        if full_path.startswith("api/"):
            return Response(status_code=404)
        return RedirectResponse(url="/app/", status_code=307)

    return app
