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
import base64
import contextlib
import os
import tempfile
import uuid
from collections.abc import Callable, Mapping
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlsplit

import httpx
from ag2.a2ui.incoming import A2UIIncomingAction, A2UIIncomingActionResult, parse_incoming_message
from ag2.a2ui.server_action import build_server_action_context, run_server_action
from ag2.config import OllamaConfig
from ag2.context import ConversationContext
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
from ag2.exceptions import SkillDownloadError, SkillError, SkillInstallError
from ag2.stream import MemoryStream
from ag2.tools.skills.skill_search.client import SkillsClient
from fastapi import (
    APIRouter,
    Depends,
    FastAPI,
    File,
    Form,
    HTTPException,
    Request,
    UploadFile,
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
from pydantic import BaseModel, Field

from assistant import (
    __version__,
    provider_catalog,
    voice_providers,
)
from assistant import feedback as feedback_learner
from assistant.a2ui import A2UI_SERVER_ACTIONS
from assistant.agent import build_skills_runtime, bundled_skills_dir, model_config
from assistant.attachments import build_input
from assistant.codex_auth import (
    CodexAuth,
    _capture_code,
)
from assistant.coding.detect import parse_bridge
from assistant.coding.model_catalog import ModelCatalog
from assistant.connections import ConnectionStore
from assistant.events import (
    A2UIActionSubmitted,
    A2UISurfaceDataUpdated,
    Attachment,
    FeedbackCleared,
    FeedbackGiven,
)
from assistant.filesearch import list_folder_dir, search_corpus
from assistant.folders import READ_WRITE, DuplicatePath, FolderStore
from assistant.gateway.profile_manager import (
    ArchivedProfile,
    ProfileManager,
    ProfileRuntime,
    UnknownProfile,
)
from assistant.gateway.routes import (
    chat,
    connection,
    llm,
    permission,
    profile,
    secret,
    settings,
    system,
    task,
)
from assistant.gateway.routes.common import chat_asker, reload_all
from assistant.gateway.routes.deps import GatewayDeps
from assistant.gateway.routes.permission import PermissionCommandDeleteRequest
from assistant.gateway.schemas import ERROR_RESPONSES
from assistant.gateway.stream_bridge import StreamBridge
from assistant.gateway.wire import to_wire
from assistant.hitl import add_hitl_routes
from assistant.integrations.google_auth import GoogleAuth
from assistant.live_configs import LiveConfigStore
from assistant.llm_configs import LlmConfigStore
from assistant.observability import log_suppressed
from assistant.pairing import PairingStore
from assistant.peers import PeerStore
from assistant.permissions import PermissionStore, command_rule, shell_prefix
from assistant.profiles import ProfileRegistry
from assistant.secrets import SecretStore
from assistant.settings import profile_settings
from assistant.skills import (
    DISABLE_OWN,
    ORIGIN_BUNDLED,
    ORIGIN_GLOBAL,
    ORIGIN_PROFILE,
    SUPPRESS_SHARED,
    SkillStateStore,
    skill_origin,
)
from assistant.skills_install import (
    SkillSourceError,
    discover_source,
    install_from_source,
    registry_install,
    registry_search,
)
from assistant.tools.mcp import build_mcp_tools, describe_mcp_error
from assistant.voice import synthesize_preview
from assistant.workspace import (
    _MAX_WRITE_BYTES,
    delete,
    etag_for_path,
    list_all_dirs,
    list_files,
    make_dir,
    mention_forms,
    move,
    resolve,
    save_upload,
    write_text,
    write_upload,
)

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


def _origin_ok(origin: str | None, host: str | None, allowed: set[str] = frozenset()) -> bool:
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


class MkdirRequest(BaseModel):
    """Create an empty Directory (ADR 0007). `path` is workspace-relative for the Files
    space; for a Folder (ADR 0006, ticket 05) it is ABSOLUTE and stays inside a granted
    Folder's subtree. `chat_id` scopes the read_write Grant resolution for a Folder
    mkdir (ignored for a relative path)."""

    path: str
    chat_id: str = ""


class MoveRequest(BaseModel):
    """Move/rename a file or Directory (ADR 0007). `from`/`to` are workspace-relative
    for a Files-space move; for a Folder move (ADR 0006, ticket 04) both are ABSOLUTE
    and must resolve under the SAME readable Folder root (no cross-Root move).
    `chat_id` scopes the Grant resolution for a Folder move (ignored for a relative
    move — the Files space is profile-sandboxed)."""

    from_: str = Field(alias="from")
    to: str
    chat_id: str = ""

    model_config = {"populate_by_name": True}


def _unquote_etag(value: str | None) -> str | None:
    """The raw content token inside an ``If-Match`` header value — its weak-``W/``
    prefix and surrounding quotes stripped (ADR 0011), or None when absent."""
    if value is None:
        return None
    value = value.strip()
    if value.startswith("W/"):
        value = value[2:]
    return value.strip('"')


async def _scope_task_id(runtime: ProfileRuntime, chat_id: str) -> str:
    """The task whose Folder Grants the ``chat_id`` scope token names: ``task:{id}`` (an
    open Task page) directly, ``task-run:{run_id}`` (a run thread) via ``get_run``, else
    ``""`` (a real chat id or none) — ADR 0006/0013."""
    if chat_id.startswith("task:"):
        return chat_id.removeprefix("task:")
    if chat_id.startswith("task-run:"):
        with contextlib.suppress(Exception):
            run = await runtime.tasks.get_run(chat_id.removeprefix("task-run:"))
            return (run or {}).get("task_id") or ""
    return ""


def _resolve_folder(
    runtime: ProfileRuntime, path: str, chat_id: str, task_id: str = ""
) -> tuple[Path | None, str | None]:
    """``(readable Folder root containing ``path``, its effective mode)`` via the one
    Folder resolver, or ``(None, None)`` when there's no gateway or the absolute
    ``path`` resolves under no granted root. Read authorizes on any non-``None`` mode;
    a mutation additionally requires ``read_write`` (the caller checks). ``task_id``
    (decoded from the Thread scope) admits the open Task/run's task-scope Grants. The
    confining root is the sandbox base every absolute ``/files/*`` mutation passes to
    the workspace helpers, so ``within-subtree only`` falls out for free (ADR 0006/0013)."""
    gw = runtime.gateway
    folders = gw.folders if gw is not None else None
    if folders is None:
        return None, None
    return folders.resolve_within(path, runtime.config.data_dir.name, chat_id, task_id)


def _folder_write_base(
    runtime: ProfileRuntime,
    path: str,
    chat_id: str,
    *,
    task_id: str = "",
    miss_status: int,
    miss_msg: str,
) -> tuple[Path | None, JSONResponse | None]:
    """The sandbox base for an ABSOLUTE ``/files/*`` MUTATION (tickets 04–05): the
    confining readable Folder root when ``path`` resolves under a ``read_write`` Grant,
    else ``(None, <deny response>)`` — the caller's ``miss_status``/``miss_msg`` (e.g.
    404 "file not found", 400 "invalid path") when the path is under no granted root,
    always ``403`` "read-only folder" when the covering Grant is ``read``. Every
    absolute mutation branch funnels through here so the read_write gate + base
    selection is written once (ADR 0006)."""
    root, mode = _resolve_folder(runtime, path, chat_id, task_id)
    if root is None:
        return None, JSONResponse({"error": miss_msg}, status_code=miss_status)
    if mode != READ_WRITE:
        return None, JSONResponse({"error": "read-only folder"}, status_code=403)
    return root, None


def _mutation_base(
    runtime: ProfileRuntime,
    path: str,
    chat_id: str,
    *,
    task_id: str = "",
    miss_status: int,
    miss_msg: str,
) -> tuple[Path | None, JSONResponse | None]:
    """The sandbox base for a ``/files/*`` mutation, branching on ``os.path.isabs``: the
    workspace for a relative path, else the ``read_write`` Folder root (or a deny response)."""
    if os.path.isabs(path):
        return _folder_write_base(
            runtime, path, chat_id, task_id=task_id, miss_status=miss_status, miss_msg=miss_msg
        )
    return runtime.config.workspace_dir, None


def _resolve_file_path(
    runtime: ProfileRuntime, path: str, chat_id: str, task_id: str = ""
) -> tuple[Path | None, str | None]:
    """Resolve a ``/files/*`` ``path`` to ``(existing file, effective mode)``, branching
    on ``os.path.isabs`` — the sole discriminator between a Files-space file and a
    Folder file. A relative path keeps today's workspace sandbox untouched (mode
    ``read_write`` — the user owns their Files space); an absolute path authorizes via
    the one Folder resolver (``read`` suffices for a GET, ``task_id`` admitting the open
    Task/run's grants) and is confirmed to be a real file. ``(None, None)`` on any
    denial/miss — the caller turns that into the shared 404 shape (ADR 0006/0013). The
    mode rides back so the GET can advertise it to the client's edit-affordance gating
    (ticket 04)."""
    if not os.path.isabs(path):
        rp = resolve(runtime.config.workspace_dir, path)
        return (rp, READ_WRITE) if rp is not None else (None, None)
    root, mode = _resolve_folder(runtime, path, chat_id, task_id)
    if root is None:
        return None, None
    rp = Path(path).expanduser().resolve()
    return (rp, mode) if rp.is_file() else (None, None)


class FocusesRequest(BaseModel):
    focuses: list[str] = []


class ModelOverrideRequest(BaseModel):
    # A selection into the shared install-wide list; empty string clears the override
    # (→ back to the install-wide Active). Used for both the Text and Live switchers.
    config_id: str = ""


class ReplyTimeoutRequest(BaseModel):
    reply_timeout_s: float = Field(gt=0, le=3600)


class VoiceRequest(BaseModel):
    voice: str
    # When set, the voice op targets a named live config (its provider/key, and
    # select persists onto that config) instead of the profile's legacy voice setting.
    config_id: str | None = None


class MCPServerRequest(BaseModel):
    name: str
    command: str
    args: list[str] | str = Field(default_factory=list)
    env: dict[str, str] | str | None = None
    cwd: str | None = None
    allowed_tools: list[str] | str = Field(default_factory=list)
    blocked_tools: list[str] | str = Field(default_factory=list)
    enabled: bool = True


class VoiceProviderRequest(BaseModel):
    provider: str


class FolderCreateRequest(BaseModel):
    path: str
    name: str = ""


class FolderUpdateRequest(BaseModel):
    name: str | None = None
    path: str | None = None


class FolderGrantRequest(BaseModel):
    profile: str
    chat_id: str = ""
    task_id: str = ""
    mode: str


class FolderGrantDeleteRequest(BaseModel):
    profile: str
    chat_id: str = ""
    task_id: str = ""


class SkillStateRequest(BaseModel):
    # Enabled (True) / Disabled (False). Install-wide for a Bundled/Global skill via
    # /api/skills/{name}/state; per-profile for a Profile skill via /api/p/{pid}/skills/{name}/state.
    enabled: bool


class SkillSearchRequest(BaseModel):
    query: str
    limit: int = 10


class SkillInstallRequest(BaseModel):
    # A registry install (ADR 0017 t04) passes ``install_id`` (from a search hit).
    # A git install (t05) passes ``git_url`` + the chosen ``names``. The target is the
    # surface, not a field here: the Application route lands Global, the profile route
    # lands in that profile.
    install_id: str | None = None
    git_url: str | None = None
    names: list[str] | None = None


class SkillDiscoverRequest(BaseModel):
    # Scan a git URL (t05) for every SKILL.md without installing. Upload discovery uses
    # the multipart route instead (a file can't ride a JSON body).
    git_url: str | None = None


class PermissionCommandAddRequest(BaseModel):
    tool: str
    prefix: str | None = None  # shell command prefix (e.g. "git"), or null for whole-tool


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
    cfg = runtime.config
    return profile_settings(cfg.data_dir, voice_provider=cfg.voice_provider)


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

    # ---- Permissions (global, install-wide: one command store shared by every profile) ----

    def _permissions_store():
        """A fresh PermissionStore over the install-wide file. mtime self-refresh
        means live turns pick up any change on their next query — no manager.reload()."""
        return PermissionStore(paths.root / "permissions.json")

    def _permissions_snapshot(store) -> dict:
        return {"commands": store.granted_commands()}

    @app.get("/api/permissions")
    async def get_permissions() -> dict:
        """The install-wide permission state (command rules)."""
        return _permissions_snapshot(_permissions_store())

    @app.post("/api/permissions/commands")
    async def grant_permission_command(req: PermissionCommandAddRequest):
        """Grant a command rule. The rule string is built SERVER-SIDE via command_rule()
        so the frontend can't produce malformed syntax; a prefix that the matcher would
        never honour (fails the shell_prefix charset) is rejected 400 rather than minting
        a dead rule."""
        if not req.tool.strip():
            return JSONResponse({"error": "tool is required"}, status_code=400)
        prefix = req.prefix.strip() if req.prefix else None
        if prefix and shell_prefix(prefix) != prefix:
            return JSONResponse(
                {"error": f"invalid command prefix: {req.prefix!r}"}, status_code=400
            )
        store = _permissions_store()
        try:
            # grant_command re-parses the built rule (a tool name with spaces/parens
            # fails) and refuses bare grants on shell tools — both are 400s, not 500s.
            store.grant_command(command_rule(req.tool.strip(), prefix))
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        return {"ok": True, **_permissions_snapshot(store)}

    @app.delete("/api/permissions/commands")
    async def revoke_permission_command(req: PermissionCommandDeleteRequest):
        """Revoke a command rule by its canonical string. 404 if absent."""
        if not req.rule.strip():
            return JSONResponse({"error": "rule is required"}, status_code=400)
        store = _permissions_store()
        if not store.revoke_command(req.rule):
            return JSONResponse({"error": f"not granted: {req.rule}"}, status_code=404)
        return {"ok": True, **_permissions_snapshot(store)}

    # ---- Folders + Grants (global: the install-wide Folder registry, ADR 0006) ----

    def _folder_store():
        """A fresh FolderStore over the install-wide file. mtime self-refresh means
        live turns pick up any change on their next check — no manager.reload()."""
        return FolderStore(paths.root / "folders.json")

    def _folders_snapshot(store) -> dict:
        return {"folders": store.list_folders()}

    @app.get("/api/folders")
    async def get_folders() -> dict:
        """Every Folder with its path-exists badge and its Grants."""
        return _folders_snapshot(_folder_store())

    @app.post("/api/folders")
    async def create_folder(req: FolderCreateRequest):
        """Register a directory as a Folder. 400 for a non-directory; 409 with a
        pointer when the resolved path is already registered (path-unique)."""
        fp = Path(req.path or "").expanduser()
        if not req.path.strip() or not fp.is_dir():
            return JSONResponse({"error": "not a directory"}, status_code=400)
        store = _folder_store()
        try:
            view = store.create_folder(req.path, name=req.name)
        except DuplicatePath as exc:
            return JSONResponse({"error": str(exc), "existing": exc.existing}, status_code=409)
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        return {"ok": True, "folder": view, **_folders_snapshot(store)}

    @app.post("/api/folders/{fid}")
    async def update_folder(fid: str, req: FolderUpdateRequest):
        """Rename and/or repoint a Folder. 404 unknown; 409 path collision."""
        store = _folder_store()
        try:
            view = store.update_folder(fid, name=req.name, path=req.path)
        except KeyError:
            return JSONResponse({"error": f"unknown folder: {fid}"}, status_code=404)
        except DuplicatePath as exc:
            return JSONResponse({"error": str(exc), "existing": exc.existing}, status_code=409)
        return {"ok": True, "folder": view, **_folders_snapshot(store)}

    @app.delete("/api/folders/{fid}")
    async def delete_folder(fid: str):
        """Delete a Folder — always allowed; every Grant to it is revoked instantly."""
        store = _folder_store()
        if not store.delete_folder(fid):
            return JSONResponse({"error": f"unknown folder: {fid}"}, status_code=404)
        return {"ok": True, **_folders_snapshot(store)}

    @app.post("/api/folders/{fid}/grants")
    async def set_folder_grant(fid: str, req: FolderGrantRequest):
        """Upsert one Grant: (profile, task, chat) → mode. Empty chat_id+task_id = profile-scope."""
        store = _folder_store()
        try:
            store.set_grant(
                fid, req.mode, profile=req.profile, chat_id=req.chat_id, task_id=req.task_id
            )
        except KeyError:
            return JSONResponse({"error": f"unknown folder: {fid}"}, status_code=404)
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        return {"ok": True, **_folders_snapshot(store)}

    @app.delete("/api/folders/{fid}/grants")
    async def revoke_folder_grant(fid: str, req: FolderGrantDeleteRequest):
        """Revoke one Grant: (profile, task, chat) → mode. Empty chat_id+task_id = profile-scope.
        404 when no such Grant exists."""
        store = _folder_store()
        if not store.revoke_grant(
            fid, profile=req.profile, chat_id=req.chat_id, task_id=req.task_id
        ):
            return JSONResponse({"error": "no such grant"}, status_code=404)
        # A Folder left with no grants is garbage-collected inside revoke_grant, so it
        # is uniform across every revoke path (CLI, API, task deletion) — see FolderStore._gc.
        return {"ok": True, **_folders_snapshot(store)}

    # ---- Skills (install-wide Enable/Disable, ADR 0016) ----

    def _skill_store() -> SkillStateStore:
        """A fresh SkillStateStore over the install-wide file. mtime self-refresh
        means a live turn's next build sees any change — same shape as _folder_store."""
        return SkillStateStore(paths.root / "skills.json")

    def _installwide_skills() -> list[dict]:
        """The install-wide projection: every Bundled + Global skill with its name,
        description, origin, and install-wide ``enabled`` state.

        Discovery uses the ROOT config (skills_dir is the Global layer there;
        ``with_profile`` repoints it per profile) plus the bundled first-party dir,
        so this is genuinely install-wide — not any one profile's view. Origin is
        read from each skill's on-disk location: under the bundled dir → bundled,
        otherwise → global.
        """
        store = _skill_store()
        bundled_root = bundled_skills_dir()
        runtime = build_skills_runtime(manager.config)
        rows = [
            {
                "name": s.name,
                "description": s.metadata.description,
                "origin": skill_origin(s.location, bundled_root),
                "enabled": not store.is_disabled(s.name),
            }
            for s in runtime.skills
        ]
        # Deletable (Global, user-installed) first, then read-only Bundled — each group
        # by name — so the rows a user can act on sit at the top.
        return sorted(rows, key=lambda r: (r["origin"] == ORIGIN_BUNDLED, r["name"]))

    def _skills_snapshot() -> dict:
        return {"skills": _installwide_skills()}

    def _profile_skill_rows(runtime) -> list[dict]:
        """The active-profile projection (ADR 0016 ticket 02): every skill VISIBLE to
        this profile — inherited Bundled/Global (Suppressible here) plus the profile's
        OWN skills (Enable/Disable here) — each carrying origin, install-wide
        ``enabled``, per-profile ``suppressed``, and the resolved ``available``.

        Built through the one resolution seam (``SkillStateStore.is_available``) so a
        row can never disagree with the catalog the profile's agent actually gets: a
        skill Disabled install-wide reads unavailable here too.
        """
        store = _skill_store()
        bundled_root = bundled_skills_dir()
        profile = runtime.pid
        rows: dict[str, dict] = {}
        # Inherited shared layers (Global + Bundled), discovered from the Root config.
        for s in build_skills_runtime(manager.config).skills:
            rows[s.name] = {
                "name": s.name,
                "description": s.metadata.description,
                "origin": skill_origin(s.location, bundled_root),
                "enabled": not store.is_disabled(s.name),
            }
        # The profile's OWN skills (under its skills_dir) shadow a shared skill of the
        # same name (catalog precedence Profile > Global > Bundled). A Profile skill has
        # no install-wide Disable — it lives in one profile, toggled per-profile only.
        prof_skills_dir = runtime.config.skills_dir.resolve()
        for s in build_skills_runtime(runtime.config).skills:
            loc = Path(s.location).resolve() if s.location else None
            if loc is None or not (prof_skills_dir == loc or prof_skills_dir in loc.parents):
                continue  # bundled (extra_paths) — already covered by the shared pass
            rows[s.name] = {
                "name": s.name,
                "description": s.metadata.description,
                "origin": ORIGIN_PROFILE,
                "enabled": True,
            }
        for r in rows.values():
            kind = DISABLE_OWN if r["origin"] == ORIGIN_PROFILE else SUPPRESS_SHARED
            r["suppressed"] = store.is_suppressed(r["name"], profile, kind=kind)
            r["available"] = store.is_available(r["name"], profile, origin=r["origin"])
        order = {ORIGIN_BUNDLED: 0, ORIGIN_GLOBAL: 1, ORIGIN_PROFILE: 2}
        return sorted(rows.values(), key=lambda r: (order.get(r["origin"], 9), r["name"]))

    @app.get("/api/skills")
    async def get_skills() -> dict:
        """The install-wide skill projection: Bundled + Global skills with origin
        and their install-wide Enabled/Disabled state (drives Application → Skills)."""
        return _skills_snapshot()

    @app.post("/api/skills/{name}/state")
    async def set_skill_state(name: str, req: SkillStateRequest):
        """Enable/Disable a Bundled or Global skill install-wide. Fans out a reload
        to every live runtime so the catalog changes everywhere at once — an
        in-flight turn finishes on the old catalog, the next turn sees the change.
        404 for a name that is not an install-wide skill."""
        known = {s["name"] for s in _installwide_skills()}
        if name not in known:
            return JSONResponse({"error": f"unknown skill: {name}"}, status_code=404)
        _skill_store().set_enabled(name, req.enabled)
        await reload_all(manager)  # install-wide change → every profile's agent rebuilds
        return {"ok": True, **_skills_snapshot()}

    def _remove_skill_dir(runtime, name: str) -> None:
        """Remove skill ``name`` by its REAL on-disk directory. The lenient loader
        permits a skill whose frontmatter ``name`` differs from its directory
        (``weather-helper/`` with ``name: weather``); ``runtime.remove`` assumes
        dir == name and would 404 such a hand-placed skill, leaving it undeletable from
        the UI. Resolve the actual dir via the loader, then remove by its basename so
        the runtime's path-traversal guard still applies."""
        skill_dir = runtime.get_path(name)  # SkillError if the name isn't on disk
        runtime.remove(skill_dir.name)

    @app.delete("/api/skills/{name}")
    async def delete_skill(name: str):
        """Delete a **Global** skill from disk install-wide, then cascade-purge its
        state (install-wide Disable + every profile's Suppression) so a later same-named
        re-install resolves default-on everywhere — no ghost. Fans out a reload to all
        live runtimes. A **Bundled** skill is first-party/read-only → 409 (not deletable);
        an unknown name → 404. Mirrors DELETE /api/folders/{id}'s grant cascade."""
        config = manager.config
        store = _skill_store()
        runtime = build_skills_runtime(config)
        bundled_root = bundled_skills_dir()
        row = next((s for s in runtime.skills if s.name == name), None)
        if row is None:
            return JSONResponse({"error": f"unknown skill: {name}"}, status_code=404)
        if skill_origin(row.location, bundled_root) == ORIGIN_BUNDLED:
            return JSONResponse(
                {"error": f"{name} is a first-party skill and can't be deleted"},
                status_code=409,
            )
        try:
            _remove_skill_dir(runtime, name)
        except (SkillError, FileNotFoundError, ValueError) as exc:
            return JSONResponse({"error": str(exc)}, status_code=404)
        runtime.invalidate()
        store.purge(name)  # drop Disable + every shared Suppression of this name
        await reload_all(manager)
        return {"ok": True, **_skills_snapshot()}

    # ---- Installing skills from Settings (registry / git / upload — ADR 0017) ----
    # The target is the SURFACE: the /api/skills* routes below install into the Global
    # layer and fan out; the mirrored /api/p/{pid}/skills* routes install into the active
    # profile and reload only it. Both delegate to skills_install over the right runtime.

    _MAX_UPLOAD_BYTES = 50 * 1024 * 1024  # 50 MB raw upload cap

    async def _save_upload(upload: UploadFile, tmp_dir: Path) -> Path:
        """Stream an uploaded file into ``tmp_dir`` in bounded chunks and return its path
        (original name preserved so discover/install can tell a .zip from a SKILL.md).
        Caps the total read so a huge upload can't exhaust RAM before it ever reaches the
        unpacker; the archive's UNCOMPRESSED size is capped again at unpack."""
        name = os.path.basename(upload.filename or "upload") or "upload"
        dest = tmp_dir / name
        total = 0
        with dest.open("wb") as f:
            while chunk := await upload.read(1024 * 1024):
                total += len(chunk)
                if total > _MAX_UPLOAD_BYTES:
                    raise SkillSourceError("upload is too large")
                f.write(chunk)
        return dest

    # Every install/discover failure maps to a 400 with the exception message. Discover
    # only raises SkillSourceError; catching the superset is harmless and keeps one tuple.
    _SKILL_INSTALL_ERRORS = (SkillSourceError, SkillDownloadError, SkillInstallError)

    async def _install_from_req(runtime, req: SkillInstallRequest) -> dict:
        """Install into ``runtime``'s layer from a registry id (t04) or a git source
        (t05). Raises one of ``_SKILL_INSTALL_ERRORS``. Shared by both surfaces — only
        the target runtime and the reload differ."""
        if req.install_id:
            return {
                "installed": [await registry_install(runtime, req.install_id, client=skills_client)]
            }
        if req.git_url:
            # git clone + copytree are blocking (up to a 120s clone timeout); off-load
            # them so a slow/hanging remote never freezes the whole gateway.
            installed = await asyncio.to_thread(
                install_from_source, runtime, req.names or [], git_url=req.git_url
            )
            return {"installed": installed}
        raise SkillSourceError("provide a registry install_id or a git_url")

    async def _discover_git(git_url: str | None) -> dict:
        """Scan a git URL for every SKILL.md (no install). The clone is blocking (120s
        timeout) so it runs off the event loop. Shared by both surfaces' discover routes."""
        return {"skills": await asyncio.to_thread(discover_source, git_url=git_url)}

    async def _install_upload_into(runtime, file: UploadFile, names: str) -> dict:
        """Install the selected (comma-separated) ``names`` from an uploaded source into
        ``runtime``. Shared by both surfaces' install-upload routes."""
        wanted = [n.strip() for n in (names or "").split(",") if n.strip()]
        with tempfile.TemporaryDirectory(prefix="skill-up-") as td:
            path = await _save_upload(file, Path(td))
            # Unpack + copytree are blocking → run off-loop.
            installed = await asyncio.to_thread(
                install_from_source,
                runtime,
                wanted,
                upload_path=path,
                filename=file.filename or "",
            )
            return {"installed": installed}

    async def _discover_upload_file(file: UploadFile) -> dict:
        """Discover skills in an uploaded SKILL.md / zipped folder (no install)."""
        with tempfile.TemporaryDirectory(prefix="skill-up-") as td:
            path = await _save_upload(file, Path(td))
            skills = await asyncio.to_thread(  # blocking unpack + scan → off-loop
                discover_source, upload_path=path, filename=file.filename or ""
            )
            return {"skills": skills}

    @app.post("/api/skills/search")
    async def search_skills(req: SkillSearchRequest):
        """Proxy a skills.sh registry search → ``{results:[{name, install_id,
        description, installs}]}``. Target-agnostic: both surfaces search through here,
        then install via their own (Global vs Profile) install route."""
        try:
            return {"results": await registry_search(req.query, req.limit, client=skills_client)}
        except Exception as exc:  # a registry/network failure shouldn't 500 the page
            return JSONResponse({"error": f"search failed: {exc}"}, status_code=502)

    @app.post("/api/skills/discover")
    async def discover_skills(req: SkillDiscoverRequest):
        """Scan a git URL for every SKILL.md (no install) → ``{skills:[{name,
        description}]}`` for the checklist. 400 for an unreachable/invalid source."""
        try:
            return await _discover_git(req.git_url)
        except SkillSourceError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)

    @app.post("/api/skills/discover-upload")
    async def discover_skills_upload(file: UploadFile = File(...)):
        """Discover skills in an uploaded SKILL.md / zipped folder (no install)."""
        try:
            return await _discover_upload_file(file)
        except SkillSourceError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)

    @app.post("/api/skills/install")
    async def install_skill(req: SkillInstallRequest):
        """Install into the **Global** layer from a registry id or a git URL + names,
        then fan out a reload so every profile sees it next turn. A name collision in the
        target replaces the prior skill. 400 on a bad source (nothing half-installed)."""
        try:
            result = await _install_from_req(build_skills_runtime(manager.config), req)
        except _SKILL_INSTALL_ERRORS as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        await reload_all(manager)
        return {"ok": True, **result, **_skills_snapshot()}

    @app.post("/api/skills/install-upload")
    async def install_skill_upload(file: UploadFile = File(...), names: str = Form(...)):
        """Install selected skills from an uploaded source into the **Global** layer.
        ``names`` is a comma-separated list (multipart can't carry a JSON array)."""
        try:
            result = await _install_upload_into(build_skills_runtime(manager.config), file, names)
        except _SKILL_INSTALL_ERRORS as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        await reload_all(manager)
        return {"ok": True, **result, **_skills_snapshot()}

    # Profiles (the registry every client boots from) and Connections (an instance
    # of a messaging platform with its exposure, pairing and group tables) are both
    # install-level, and live in gateway/routes/profile.py and connection.py.
    app.include_router(profile.build_router(deps))
    app.include_router(connection.build_router(deps))

    # ------------------------------------------------------------------ #
    #  Profile-scoped router (/api/p/{pid})                              #
    # ------------------------------------------------------------------ #

    p = APIRouter(prefix="/api/p/{pid}", responses=ERROR_RESPONSES)

    def _available_providers() -> dict:
        """Which providers have a usable key right now — key-only. This is what the
        VOICE endpoints need (the realtime APIs always talk to the provider's own
        endpoint, so a base_url never makes a provider available). Assistant model
        availability is per-config now and lives in the named LLM configs store."""
        st = secret_store.status(secret_env())
        avail = {prov: st[prov]["set"] for prov in ("openai", "gemini", "anthropic")}
        avail["ollama"] = _ollama_installed()
        return avail

    def _ollama_installed() -> bool:
        try:
            return type(OllamaConfig).__module__ != "unittest.mock"
        except Exception:
            return False

    # The moved slices of /api/p/{pid}: this profile's memory document and today's
    # spend, and the health roll-up. Included here, after the collaborators the
    # settings module borrows — all three paths are literal, so nothing is shadowed.
    p.include_router(system.build_profile_router(deps, get_runtime))
    p.include_router(
        settings.build_profile_router(
            deps,
            get_runtime,
            secret_env=secret_env,
            available_providers=_available_providers,
            runtime_settings=_runtime_settings,
        )
    )

    # The moved /api/p/{pid} domains. All four paths families here are literal or
    # unambiguously distinct, so including them ahead of the rest of `p` shadows
    # nothing; the task-scoped permission pair sits in its own module because a
    # module follows its zod twin (TaskRules is declared in permission.ts).
    p.include_router(chat.build_profile_router(deps, get_runtime))
    p.include_router(task.build_profile_router(deps, get_runtime))
    p.include_router(permission.build_profile_router(deps, get_runtime))

    # ---- HITL pending (this profile's registry) ----

    @p.get("/hitl/pending")
    async def hitl_pending(runtime: ProfileRuntime = Depends(get_runtime)) -> dict:
        """Open HITL questions in THIS profile's registry, for a UI client to render."""
        return {"pending": runtime.hitl.pending_list()}

    # ---- Voice picker: list voices, select (persist), preview (TTS) ----

    @p.get("/voice/voices")
    async def voice_voices(
        config_id: str | None = None, runtime: ProfileRuntime = Depends(get_runtime)
    ) -> dict:
        """The voice catalogue + current selection. Scoped to a named live config when
        ``config_id`` is given (its provider + persisted voice); otherwise the profile's
        legacy voice-provider setting."""
        settings = _runtime_settings(runtime)
        entry = live_store.get_config(config_id) if config_id else None
        provider = entry["provider"] if entry else settings.voice_provider()
        p_v = voice_providers.get(provider)
        current = entry.get("voice") if entry else settings.get_voice(provider)
        return {
            "voices": [{"name": n, "style": s} for n, s in p_v.voices.items()],
            "current": current,
            "provider": provider,
            "input_rate": p_v.input_rate,  # mic capture rate the client should use
        }

    @p.post("/voice/select")
    async def voice_select(
        req: VoiceRequest, runtime: ProfileRuntime = Depends(get_runtime)
    ) -> dict:
        """Persist the chosen voice — onto the named live config when ``config_id`` is
        given, else the profile's legacy per-provider voice setting."""
        if req.config_id:
            if not live_store.set_voice(req.config_id, req.voice):
                return Response(status_code=400)
        elif not _runtime_settings(runtime).set_voice(req.voice):
            return Response(status_code=400)
        return {"ok": True, "voice": req.voice}

    @p.post("/voice/preview")
    async def voice_preview(req: VoiceRequest, runtime: ProfileRuntime = Depends(get_runtime)):
        settings = _runtime_settings(runtime)
        entry = live_store.get_config(req.config_id) if req.config_id else None
        provider = entry["provider"] if entry else None
        api_key = live_store.resolve_key(entry, secret_env()) if entry else ""
        # Validate the voice against the target provider's catalogue.
        catalog = voice_providers.get(provider or settings.voice_provider()).voices
        if req.voice not in catalog:
            return Response(status_code=400)
        try:
            wav = await synthesize_preview(
                runtime.config, settings, req.voice, provider=provider, api_key=api_key
            )
        except Exception as exc:
            return Response(content=str(exc)[:200], status_code=502)
        return Response(content=wav, media_type="audio/wav")

    # ---- Settings ----

    @p.get("/settings")
    async def get_settings(runtime: ProfileRuntime = Depends(get_runtime)) -> dict:
        cfg = runtime.config
        settings = _runtime_settings(runtime)
        keys = secret_store.status(secret_env())
        # Per-profile model Active override (ADR 0015): report BOTH this profile's
        # override id (None when inherited or dangling) and the EFFECTIVE Active id, so
        # each header switcher can render the current choice + mark it
        # inherited-vs-overridden without a second fetch. A dangling override reads as
        # no override → the install-wide Active (matching the resolution layer).
        llm_ovr = llm_store.resolved_override(settings.get_llm_override()) or None
        live_ovr = settings.get_live_override()
        live_ovr = live_ovr if (live_ovr and live_store.get_config(live_ovr)) else None
        return {
            "keys": keys,  # per-provider {set, hint} — never raw
            # Voice runs on the provider's own realtime endpoint, so a base_url
            # never makes it available — keys only.
            "voice_available": {prov: keys[prov]["set"] for prov in ("gemini", "openai")},
            # Display-only view of the resolved assistant model (the active named LLM
            # config, derived onto cfg.llm). Managed via /api/llm-configs, not here.
            "assistant": {"provider": cfg.llm.provider, "model": cfg.llm.model},
            # Per-profile Text/Live Active override + effective Active (drives the
            # Profiles-header switchers). override=None → inherits the install-wide.
            "llm_override": llm_ovr,
            "llm_active": llm_store.effective_active_id(llm_ovr) or None,
            "live_override": live_ovr,
            "live_active": live_ovr or live_store.active_id(),
            "codex": codex.status(),  # ChatGPT-subscription sign-in state
            "voice_provider": settings.voice_provider(),
            "mcp_servers": settings.list_mcp_servers(),
            "focuses": settings.get_focuses(),  # per-profile persona focus areas
            "reply_timeout_s": cfg.gateway.reply_timeout_s,
            "fs": {  # start roots for the folder picker
                "home": str(paths.home),
                "cwd": str(Path.cwd()),
                "workspace": str(Path(cfg.workspace_dir).expanduser()),
            },
        }

    async def _mcp_health(server: dict) -> dict:
        tools = build_mcp_tools([server])
        if not tools:
            return {"ok": False, "error": "MCP server is disabled"}
        toolkit = tools[0]
        context = ConversationContext(stream=MemoryStream())
        try:
            schemas = await toolkit.schemas(context)
            error = toolkit.last_error
        finally:
            # This throwaway toolkit's persistent session would otherwise hold the
            # server process alive until idle expiry.
            await toolkit.aclose()
        # Discovery reports failures rather than raising: an unreachable server
        # arrives here as a live toolkit offering zero tools.
        if error is not None:
            return {"ok": False, "error": describe_mcp_error(error)[:500]}
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

    @p.post("/settings/focuses")
    async def set_focuses(
        req: FocusesRequest, runtime: ProfileRuntime = Depends(get_runtime)
    ) -> dict:
        """Persist this profile's focus areas (a persona attribute injected into the
        agent's context), then reload so the reference-swapped agent picks up the new
        context line on its next turn."""
        settings = _runtime_settings(runtime)
        focuses = settings.set_focuses(req.focuses)
        await manager.reload(runtime.pid)  # context change → next turn gets the line
        return {"ok": True, "focuses": focuses}

    @p.post("/settings/llm-override")
    async def set_llm_override(
        req: ModelOverrideRequest, runtime: ProfileRuntime = Depends(get_runtime)
    ) -> dict:
        """Set (or clear, when ``config_id`` is empty) this profile's Active Text model
        override — a selection into the shared install-wide ``llm_configs`` list, NOT the
        install-wide Active (the composer switcher still owns that). Reloads this
        profile's runtime so its next message uses the new model. Unknown id → 404."""
        settings = _runtime_settings(runtime)
        cid = (req.config_id or "").strip()
        if cid and llm_store.get_config(cid) is None:
            return JSONResponse({"ok": False, "error": f"unknown config: {cid}"}, status_code=404)
        settings.set_llm_override(cid)
        await manager.reload(runtime.pid)  # next turn's agent is built from the new model
        return {"ok": True, "llm_override": cid or None}

    @p.post("/settings/live-override")
    async def set_live_override(
        req: ModelOverrideRequest, runtime: ProfileRuntime = Depends(get_runtime)
    ) -> dict:
        """Set (or clear) this profile's Active Live (voice) model override — a
        selection into the shared install-wide ``live_configs`` list. Read fresh by the
        voice session at connect, so it takes effect on the NEXT voice session (no
        runtime reload needed). Unknown id → 404."""
        settings = _runtime_settings(runtime)
        cid = (req.config_id or "").strip()
        if cid and live_store.get_config(cid) is None:
            return JSONResponse({"ok": False, "error": f"unknown config: {cid}"}, status_code=404)
        settings.set_live_override(cid)
        return {"ok": True, "live_override": cid or None}

    @p.post("/settings/reply-timeout")
    async def set_reply_timeout(
        req: ReplyTimeoutRequest, runtime: ProfileRuntime = Depends(get_runtime)
    ) -> dict:
        """Persist this profile's chat-turn timeout and reload its runtime."""
        timeout = _runtime_settings(runtime).set_reply_timeout(req.reply_timeout_s)
        await manager.reload(runtime.pid)
        return {"ok": True, "reply_timeout_s": timeout}

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

    # ---- Skills (per-profile: Suppression of shared skills + own-skill state, ADR 0016) ----
    # These scope to the profile in the URL (mirrors the /settings per-profile routes),
    # not the global install-wide /api/skills page. A change reloads ONLY this profile
    # (manager.reload(pid)); the install-wide toggle at /api/skills fans out to all.

    @p.get("/skills")
    async def profile_skills(runtime: ProfileRuntime = Depends(get_runtime)) -> dict:
        """This profile's resolved skill projection — inherited Bundled/Global skills
        (Suppressible here) and the profile's own skills (Enable/Disable here)."""
        return {"skills": _profile_skill_rows(runtime)}

    async def _suppress(name: str, runtime, suppressed: bool) -> dict:
        # Shared by the suppress/un-suppress routes: validate against the projection
        # (built once), flip the per-profile off-record, reload only this profile.
        if name not in {r["name"] for r in _profile_skill_rows(runtime)}:
            return JSONResponse({"error": f"unknown skill: {name}"}, status_code=404)
        # A Suppression of an inherited shared skill — tagged SHARED so a same-named
        # Global Delete's purge clears it (but never a Profile skill's own off-state).
        _skill_store().set_suppressed(name, runtime.pid, suppressed, kind=SUPPRESS_SHARED)
        await manager.reload(runtime.pid)
        return {"ok": True, "skills": _profile_skill_rows(runtime)}

    @p.post("/skills/{name}/suppress")
    async def suppress_skill(name: str, runtime: ProfileRuntime = Depends(get_runtime)) -> dict:
        """Suppress an inherited (Bundled/Global) skill for THIS profile only — off
        here, untouched everywhere else. Reloads only this profile so its next turn
        drops the skill; other profiles never rebuild. 404 for a name not visible here."""
        return await _suppress(name, runtime, True)

    @p.delete("/skills/{name}/suppress")
    async def unsuppress_skill(name: str, runtime: ProfileRuntime = Depends(get_runtime)) -> dict:
        """Clear this profile's Suppression of a shared skill — back to inherited "on".
        Reloads only this profile. 404 for a name not visible here."""
        return await _suppress(name, runtime, False)

    @p.post("/skills/{name}/state")
    async def set_profile_skill_state(
        name: str, req: SkillStateRequest, runtime: ProfileRuntime = Depends(get_runtime)
    ) -> dict:
        """Enable/Disable a skill this profile OWNS, scoped to the profile — its own
        Disable never leaves it (stored as the same per-profile off-record Suppression
        uses). Reloads only this profile. 404 unless ``name`` is a Profile skill here
        (a shared Bundled/Global skill is Suppressed, not Disabled, per profile)."""
        row = next((r for r in _profile_skill_rows(runtime) if r["name"] == name), None)
        if row is None or row["origin"] != ORIGIN_PROFILE:
            return JSONResponse({"error": f"not a profile skill: {name}"}, status_code=404)
        # A Disable of THIS profile's own skill — tagged OWN so a same-named Global
        # purge leaves it intact; only this copy's Delete clears it.
        _skill_store().set_suppressed(name, runtime.pid, not req.enabled, kind=DISABLE_OWN)
        await manager.reload(runtime.pid)
        return {"ok": True, "skills": _profile_skill_rows(runtime)}

    @p.delete("/skills/{name}")
    async def delete_profile_skill(
        name: str, runtime: ProfileRuntime = Depends(get_runtime)
    ) -> dict:
        """Delete one of THIS profile's own Profile skills from disk — removed for this
        profile only; other profiles never rebuild. Clears this profile's off-record for
        the name so a same-named re-install here is default-on. 404 for an unknown name;
        409 for a shared Bundled/Global skill (delete a Global skill from Application →
        Skills, which cascades; Bundled is never deletable)."""
        row = next((r for r in _profile_skill_rows(runtime) if r["name"] == name), None)
        if row is None:
            return JSONResponse({"error": f"unknown skill: {name}"}, status_code=404)
        if row["origin"] != ORIGIN_PROFILE:
            return JSONResponse(
                {"error": f"{name} isn't this profile's own skill — can't delete it here"},
                status_code=409,
            )
        prof_runtime = build_skills_runtime(runtime.config)
        try:
            _remove_skill_dir(prof_runtime, name)
        except (SkillError, FileNotFoundError, ValueError) as exc:
            return JSONResponse({"error": str(exc)}, status_code=404)
        prof_runtime.invalidate()
        # Clear only THIS profile's OWN off-record for the name (kind=OWN): a Profile
        # skill lives in one profile. Leaving a same-named SHARED Suppression standing
        # keeps a shadowed Global skill suppressed after the copy is gone — and this
        # never touches the Global skill's install-wide/other-profile state.
        _skill_store().set_suppressed(name, runtime.pid, False, kind=DISABLE_OWN)
        await manager.reload(runtime.pid)
        return {"ok": True, "skills": _profile_skill_rows(runtime)}

    # ---- Install into THIS profile (registry / git / upload — ADR 0017) ----
    # Same delegation as the Global /api/skills* install routes, but the target is the
    # profile's own skills dir (build_skills_runtime over runtime.config) and only this
    # profile reloads. Registry search is target-agnostic → done via GLOBAL /api/skills/search.

    @p.post("/skills/install")
    async def install_profile_skill(
        req: SkillInstallRequest, runtime: ProfileRuntime = Depends(get_runtime)
    ) -> dict:
        """Install into THIS profile from a registry id or a git URL + names; reloads
        only this profile. Collision in the profile's dir replaces the prior skill. Same
        body as the Global route (``_install_from_req``) — only the target + reload differ."""
        try:
            result = await _install_from_req(build_skills_runtime(runtime.config), req)
        except _SKILL_INSTALL_ERRORS as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        await manager.reload(runtime.pid)
        return {"ok": True, **result, "skills": _profile_skill_rows(runtime)}

    @p.post("/skills/discover")
    async def discover_profile_skills(
        req: SkillDiscoverRequest, runtime: ProfileRuntime = Depends(get_runtime)
    ) -> dict:
        """Scan a git URL for every SKILL.md (no install) for the profile's checklist."""
        try:
            return await _discover_git(req.git_url)
        except SkillSourceError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)

    @p.post("/skills/discover-upload")
    async def discover_profile_skills_upload(
        file: UploadFile = File(...), runtime: ProfileRuntime = Depends(get_runtime)
    ) -> dict:
        try:
            return await _discover_upload_file(file)
        except SkillSourceError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)

    @p.post("/skills/install-upload")
    async def install_profile_skill_upload(
        file: UploadFile = File(...),
        names: str = Form(...),
        runtime: ProfileRuntime = Depends(get_runtime),
    ) -> dict:
        """Install selected skills from an uploaded source into THIS profile."""
        try:
            result = await _install_upload_into(build_skills_runtime(runtime.config), file, names)
        except _SKILL_INSTALL_ERRORS as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        await manager.reload(runtime.pid)
        return {"ok": True, **result, "skills": _profile_skill_rows(runtime)}

    # ---- Workspace (the agent's working file space) ----

    @p.get("/files")
    async def list_workspace_files(
        path: str = "", chat_id: str = "", runtime: ProfileRuntime = Depends(get_runtime)
    ):
        """List a file tree, branching on ``os.path.isabs`` (the sole discriminator).

        With no ``path`` (or a relative one) — the profile's whole Files space: files
        plus every Directory (so the tree can show empty Directories the files-only
        listing omits). Shared read+write; agent writes and user uploads land here
        alike (ADR 0007).

        With an ABSOLUTE ``path`` — ONE Directory level inside a granted **Folder** (a
        directory outside the Root), authorized through the one resolver (``read``
        suffices), scoped to the open Thread (``chat_id``, plus the task decoded from it
        so a Task page/run sees its task-scope Folders — ADR 0006/0013), with the usual
        noise Directories pruned. The tree lazy-expands one level per call. A
        denied/missing path is a 404 — the same shape either branch."""
        if path and os.path.isabs(path):
            gw = runtime.gateway
            folders = gw.folders if gw is not None else None
            task_id = await _scope_task_id(runtime, chat_id)
            mode = (
                folders.mode_for_path(path, runtime.config.data_dir.name, chat_id, task_id)
                if folders is not None
                else None
            )
            if mode is None:
                return JSONResponse({"error": "not found"}, status_code=404)
            listing = list_folder_dir(path)
            if listing is None:
                return JSONResponse({"error": "not found"}, status_code=404)
            # This level's own resolved mode (not the root's) so the tree derives each
            # nested Directory's write affordances from the Grant that actually covers
            # it — a read_write Folder nested under a read root reads as writable when
            # descended into (ticket 04, "affordances derived from the resolved mode").
            return {**listing, "mode": mode}

        return {
            "root": str(Path(runtime.config.workspace_dir).expanduser()),
            "files": list_files(runtime.config.workspace_dir),
            "dirs": list_all_dirs(runtime.config.workspace_dir),
        }

    @p.get("/folders/roots")
    async def list_folder_roots(
        chat_id: str = "", runtime: ProfileRuntime = Depends(get_runtime)
    ) -> dict:
        """The Folder roots browsable in the open Thread — the tree's Thread-scoped
        Folder section (ADR 0013). Each root: ``{id, name, path (absolute), mode,
        exists}``, resolved through the same ``mode_for`` truth the ``@``-picker and the
        agent's reads share, scoped by ``chat_id`` plus the task decoded from it (a Task
        page carries ``task:{id}``, a run thread ``task-run:{run_id}``), so the open
        Task/run's task-scope Folders surface, not just profile grants (absent scope →
        profile-level grants only). A missing-path Folder is included as a badged,
        repointable root (``exists: false``), never an error."""
        gw = runtime.gateway
        folders = gw.folders if gw is not None else None
        if folders is None:
            return {"roots": []}
        task_id = await _scope_task_id(runtime, chat_id)
        return {"roots": folders.granted_roots(runtime.config.data_dir.name, chat_id, task_id)}

    @p.get("/files/search")
    async def search_workspace_files(
        q: str = "", chat_id: str = "", runtime: ProfileRuntime = Depends(get_runtime)
    ) -> dict:
        """The ``@``-picker's corpus search: a bounded, ranked list of files matching
        `q` across the profile's Files space **and** every Folder this profile∪task∪chat
        can read, each with an ABSOLUTE `path` the agent's ``read_file`` can open.
        Ranked filename-first; a blank/no-match query yields an empty list, not an
        error. Honors the same ``mode_for`` resolution the agent's reads use, so a
        denied file is never surfaced (ADR 0006/0012)."""
        gw = runtime.gateway
        # The Thread's scope carries its task in the chat_id slot (a run thread's
        # ``task-run:{run_id}``, a Task page's ``task:{id}``) — decode it so the picker
        # sees the task-scoped Folder grants too (the one shared decoder).
        task_id = await _scope_task_id(runtime, chat_id)
        return {
            "results": search_corpus(
                runtime.config.workspace_dir,
                q,
                folders=gw.folders if gw is not None else None,
                profile=runtime.config.data_dir.name,
                chat_id=chat_id,
                task_id=task_id,
            )
        }

    @p.get("/files/mentions")
    async def file_mentions(
        path: str = "", chat_id: str = "", runtime: ProfileRuntime = Depends(get_runtime)
    ) -> dict:
        """The preview rail's "Mentioned in N threads" backlink (ADR 0014): the
        current profile's Threads (Chats + Task Runs) whose transcript mentions the
        previewed file, newest-first. ``path`` is the previewed file's path (relative
        = Files-space, absolute = Folder); its OR-set of forms (absolute + workspace-
        relative) is loose-substring-scanned over each stream's transcript + event
        log. Read-only over this profile's own store — no auth/grant check beyond the
        profile boundary. ``chat_id`` is accepted for signature parity with the other
        ``/files`` routes but not needed (the scan is profile-wide)."""
        gw = runtime.gateway
        forms = mention_forms(runtime.config.workspace_dir, path)
        if gw is None or not forms:
            return {"threads": []}
        return {"threads": await gw.threads_mentioning(forms)}

    @p.post("/files/upload")
    async def upload_workspace_files(
        files: list[UploadFile] = File(...),
        dir: str = Form(""),
        chat_id: str = Form(""),
        runtime: ProfileRuntime = Depends(get_runtime),
    ):
        """Upload one or more files into `dir` (root when empty), auto-suffixing name
        clashes so nothing is overwritten (ADR 0007). A RELATIVE `dir` targets the
        Files-space sandbox (unchanged). An ABSOLUTE `dir` targets a Folder Directory:
        it authorizes through the one resolver requiring `read_write` (a `read`-only
        Folder is `403`), scoped to the open Thread (`chat_id` + the task decoded from
        it), with the upload confined to that Folder's subtree (ticket 05). A `dir` that
        escapes its root is rejected `400`."""
        task_id = await _scope_task_id(runtime, chat_id)
        base, deny = _mutation_base(
            runtime,
            dir,
            chat_id,
            task_id=task_id,
            miss_status=400,
            miss_msg="invalid target directory",
        )
        if deny is not None:
            return deny

        saved: list[str] = []
        for f in files:
            data = await f.read()
            rel = save_upload(base, f.filename or "file", data, dir)
            if rel is None:
                return JSONResponse({"error": "invalid target directory"}, status_code=400)
            saved.append(rel)
        return {"ok": True, "saved": saved}

    @p.post("/files/mkdir")
    async def mkdir_workspace(req: MkdirRequest, runtime: ProfileRuntime = Depends(get_runtime)):
        """Create an empty Directory. 409 if it already exists (no clobber), 400 on a
        traversal escape / empty path. A RELATIVE `path` lands in the Files-space
        sandbox (unchanged). An ABSOLUTE `path` creates a Folder Directory: it
        authorizes through the one resolver requiring `read_write` (a `read`-only Folder
        is `403`), scoped to the open Thread (`chat_id` + the task decoded from it),
        confined to that Folder's subtree (ticket 05)."""
        task_id = await _scope_task_id(runtime, req.chat_id)
        base, deny = _mutation_base(
            runtime,
            req.path,
            req.chat_id,
            task_id=task_id,
            miss_status=400,
            miss_msg="invalid path",
        )
        if deny is not None:
            return deny

        status, rel = make_dir(base, req.path)
        if status == "ok":
            return {"ok": True, "path": rel}
        code, msg = (409, "directory exists") if status == "exists" else (400, "invalid path")
        return JSONResponse({"error": msg}, status_code=code)

    @p.post("/files/move")
    async def move_workspace(req: MoveRequest, runtime: ProfileRuntime = Depends(get_runtime)):
        """Move/rename a file or Directory. 409 if the destination already exists
        (never overwrites, ADR 0007), 404 if the source is missing, 400 on a
        traversal escape.

        A RELATIVE ``from`` moves within the Files-space sandbox (unchanged). An
        ABSOLUTE ``from`` is a Folder move: it authorizes through the one resolver
        requiring ``read_write`` (a ``read``-only Folder is ``403``), scoped to
        ``chat_id``, and is confined to the source's own readable Folder root — so a
        ``to`` that resolves outside it (another root, the Files space, or a relative
        target) is rejected ``400`` (no cross-Root move, ticket 04)."""
        # Cross-space/cross-Root guard: an absolute (Folder) source's target must itself
        # be absolute; move() then confines it under the source's root.
        if os.path.isabs(req.from_) and not os.path.isabs(req.to):
            return JSONResponse({"error": "invalid path"}, status_code=400)
        task_id = await _scope_task_id(runtime, req.chat_id)
        base, deny = _mutation_base(
            runtime,
            req.from_,
            req.chat_id,
            task_id=task_id,
            miss_status=404,
            miss_msg="source not found",
        )
        if deny is not None:
            return deny

        outcome = move(base, req.from_, req.to)
        if outcome == "ok":
            return {"ok": True}
        status, msg = {
            "exists": (409, "destination exists"),
            "not_found": (404, "source not found"),
            "invalid": (400, "invalid path"),
        }[outcome]
        return JSONResponse({"error": msg}, status_code=status)

    @p.get("/files/raw")
    async def workspace_file(
        path: str,
        download: bool = False,
        chat_id: str = "",
        runtime: ProfileRuntime = Depends(get_runtime),
    ):
        """Serve one file (view inline or download). A RELATIVE ``path`` is a
        Files-space file, sandboxed to the workspace root (ADR 0007). An ABSOLUTE
        ``path`` is a Folder file (a file inside a granted Folder outside the Root):
        authorized through the one resolver (``read`` suffices), scoped to the open
        Thread's ``chat_id`` (ADR 0006/0013). Either way carries an ``ETag``
        content-version token (ADR 0011) an in-place ``PUT`` echoes back as
        ``If-Match``. A denied/missing path is a 404 — the same shape either branch.
        The response also carries ``X-File-Mode`` (``read``|``read_write``) — the
        effective Grant mode — so the client gates its edit/rename/delete affordances
        off the same server truth a mutation is enforced against (ticket 04)."""
        task_id = await _scope_task_id(runtime, chat_id)
        rp, mode = _resolve_file_path(runtime, path, chat_id, task_id)
        if rp is None:
            return JSONResponse({"error": "file not found"}, status_code=404)
        disp = "attachment" if download else "inline"
        # Let FileResponse build Content-Disposition so a non-ASCII filename (user
        # uploads keep their original name) is RFC 5987-encoded, not latin-1 crashed.
        resp = FileResponse(rp, filename=rp.name, content_disposition_type=disp)
        etag = etag_for_path(rp)
        if etag is not None:
            resp.headers["ETag"] = f'"{etag}"'  # RFC 7232 quoted-string
        if mode is not None:
            resp.headers["X-File-Mode"] = mode
        return resp

    @p.put("/files/raw")
    async def write_workspace_file(
        path: str,
        request: Request,
        chat_id: str = "",
        runtime: ProfileRuntime = Depends(get_runtime),
    ):
        """Overwrite an existing file's contents in place from the request body
        (UTF-8), using ``If-Match`` as the base ETag (ADR 0011). ``200`` + the new
        ``ETag`` on success, ``404`` if the path is missing, ``409`` if ``If-Match``
        is stale, ``400`` on a traversal/invalid path; omitting ``If-Match`` forces
        past the compare.

        A RELATIVE ``path`` writes in the Files-space sandbox (unchanged). An ABSOLUTE
        ``path`` is a Folder file: it authorizes through the one resolver requiring
        ``read_write`` (a ``read``-only Folder file is ``403``), scoped to the open
        Thread's ``chat_id``, and the write is confined to that Folder's own subtree
        (ticket 04)."""
        # Resolve the sandbox base BEFORE buffering the body: an unauthorized Folder
        # write is refused without reading its (capped) payload.
        task_id = await _scope_task_id(runtime, chat_id)
        base, deny = _mutation_base(
            runtime, path, chat_id, task_id=task_id, miss_status=404, miss_msg="file not found"
        )
        if deny is not None:
            return deny

        # Stream the body under a hard cap so an oversize (or lying Content-Length) PUT
        # can't buffer unboundedly into memory before the size check (DoS guard).
        chunks: list[bytes] = []
        total = 0
        async for chunk in request.stream():
            total += len(chunk)
            if total > _MAX_WRITE_BYTES:
                return JSONResponse({"error": "file too large"}, status_code=413)
            chunks.append(chunk)
        try:
            content = b"".join(chunks).decode("utf-8")
        except UnicodeDecodeError:
            return JSONResponse({"error": "body must be valid UTF-8"}, status_code=400)
        if_match = request.headers.get("if-match")
        base_token = _unquote_etag(if_match)  # strip weak prefix + quotes to the raw token
        status, new_tag = write_text(
            base,
            path,
            content,
            base_token=base_token,
            force=if_match is None,  # no If-Match ⇒ forced overwrite (bypass compare)
        )
        if status == "ok":
            headers = {"ETag": f'"{new_tag}"'}
            return JSONResponse({"ok": True, "etag": new_tag}, headers=headers)
        code, msg = {
            "not_found": (404, "file not found"),
            "conflict": (409, "file changed on disk"),
            "invalid": (400, "invalid path"),
            "too_large": (413, "file too large"),
        }[status]
        return JSONResponse({"error": msg}, status_code=code)

    @p.delete("/files/raw")
    async def delete_workspace_file(
        path: str, chat_id: str = "", runtime: ProfileRuntime = Depends(get_runtime)
    ):
        """Delete a file — or a Directory and everything in it, recursively (ADR
        0007). A RELATIVE ``path`` is sandboxed to the Files-space root (unchanged). An
        ABSOLUTE ``path`` is a Folder file/Directory: it authorizes through the one
        resolver requiring ``read_write`` (a ``read``-only Folder is ``403``), scoped
        to ``chat_id``, and the delete is confined to that Folder's own subtree
        (emptied parents pruned up to — never including — the Folder root, ticket 04)."""
        task_id = await _scope_task_id(runtime, chat_id)
        base, deny = _mutation_base(
            runtime, path, chat_id, task_id=task_id, miss_status=404, miss_msg="file not found"
        )
        if deny is not None:
            return deny

        if not delete(base, path):
            return JSONResponse({"error": "file not found"}, status_code=404)
        return {"ok": True}

    app.include_router(p)

    # ------------------------------------------------------------------ #
    #  Profile-scoped WebSockets (registered directly, not on the router  #
    #  — Starlette APIRouter WS + Depends is fiddly; resolve inline)      #
    # ------------------------------------------------------------------ #

    @app.websocket("/api/p/{pid}/stream")
    async def stream_ws(websocket: WebSocket, pid: str) -> None:
        """Event-stream transport: the client receives the chat's events as
        `{event:{type,data}}` — replayed on connect, then live — and sends `{text}`
        turns. Closes with 4001 if the profile is archived mid-session (§4.9)."""
        if not _origin_ok(
            websocket.headers.get("origin"), websocket.headers.get("host"), allowed_origins
        ):
            await websocket.close(code=1008)  # policy violation
            return
        runtime = await _ws_runtime(websocket, pid)
        if runtime is None:
            return
        await websocket.accept()

        # Archive → close this socket with 4001 (§4.9). Tolerant: a closed socket
        # must not error the archive loop (runtime.close suppresses callback errors).
        async def _on_archive():
            with contextlib.suppress(Exception):
                await websocket.close(code=_WS_PROFILE_ARCHIVED, reason="profile-archived")

        runtime.on_close(_on_archive)

        chat_id = websocket.query_params.get("chat") or "default"
        # A run's chat (stream "task-run:<id>") IS a plain chat — TaskService seeds
        # its task/run framing into the FIRST turn's surface at start_run time, so
        # this transport needs no per-task branch here; every turn gets the same
        # surface a normal chat would.
        default_surface = _SURFACES.get(websocket.query_params.get("surface", ""), "")
        bridge = StreamBridge(runtime.gateway, websocket, chat_id)

        try:
            await bridge.open()
            while True:
                data = await websocket.receive_json()
                if data.get("type") == "a2ui":
                    # A2UI clicks use AG2's standard action envelope. Only actions
                    # explicitly registered by this app may mutate durable state.
                    parsed = parse_incoming_message(data.get("message"))
                    if not isinstance(parsed, A2UIIncomingActionResult):
                        await websocket.send_json(
                            {"type": "error", "message": "Invalid A2UI action."}
                        )
                        continue
                    click = parsed.action
                    action = A2UI_SERVER_ACTIONS.get(click.name)
                    if not click.surface_id or not click.source_component_id:
                        await websocket.send_json(
                            {"type": "error", "message": "Unsupported A2UI action."}
                        )
                        continue
                    if action is None:
                        # AG2's standard fallback for an undeclared Button action is
                        # an agent turn. Preserve its supplied state first, then give
                        # the agent a concise, structured description of the click.
                        await runtime.gateway.emit_event(
                            chat_id,
                            A2UISurfaceDataUpdated(
                                click.surface_id,
                                data=click.context if isinstance(click.context, dict) else {},
                            ),
                        )
                        await runtime.gateway.emit_event(
                            chat_id,
                            A2UIActionSubmitted(click.surface_id, action_name=click.name),
                        )
                        action_text = (
                            f"[[A2UI_ACTION]] The user clicked the A2UI action '{click.name}' on surface "
                            f"'{click.surface_id}'. Its current values are: {click.context}. "
                            "Carry out the requested action and respond to the user."
                        )
                        asyncio.create_task(
                            bridge.run_turn(
                                action_text,
                                asker=chat_asker(runtime, chat_id),
                                surface=default_surface,
                            )
                        )
                        continue
                    # The surface id is transport metadata, never trusted from a model
                    # supplied context object. The registered handler receives it as a
                    # normal argument after the standard AG2 action parsing step.
                    click = A2UIIncomingAction(
                        name=click.name,
                        surface_id=click.surface_id,
                        source_component_id=click.source_component_id,
                        timestamp=click.timestamp,
                        context={**click.context, "surface_id": click.surface_id},
                        response_request=click.response_request,
                    )
                    messages = await run_server_action(
                        action,
                        click,
                        version=data.get("message", {}).get("version", "v1.0"),
                        context=build_server_action_context(runtime.gateway._agent),
                    )
                    for message in messages:
                        update = message.get("updateDataModel")
                        if update and update.get("surfaceId") == click.surface_id:
                            value = update.get("value")
                            if update.get("path", "/") == "/" and isinstance(value, dict):
                                await runtime.gateway.emit_event(
                                    chat_id, A2UISurfaceDataUpdated(click.surface_id, data=value)
                                )
                    continue
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
                if data.get("type") == "cancel":
                    # Stop the turn running on this chat. The gateway cancels the
                    # task driving AG2's run, which AG2 propagates into the turn; a
                    # TurnCancelled event comes back out through the bridge. A no-op
                    # when nothing is in flight.
                    await runtime.gateway.cancel_turn(chat_id)
                    continue
                if data.get("type") == "feedback" and data.get("target_id"):
                    # 👍/👎 + mandatory reason on a generated item. Emit it onto the
                    # chat stream (persists/replays → the GUI projects the thumb
                    # state, shows in the AG2 inspector), then fire-and-forget a learner
                    # that distils it into the memory profile (never blocks the socket).
                    sentiment = "down" if data.get("sentiment") == "down" else "up"
                    reason = (data.get("reason") or "").strip()
                    content = data.get("content") or ""
                    request = data.get("request") or ""
                    with contextlib.suppress(Exception):
                        await runtime.gateway.emit_event(
                            chat_id,
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
                if data.get("type") == "feedback_clear" and data.get("target_id"):
                    # Retract a rating (thumb toggled off). Emit onto the stream so the
                    # cleared state persists/replays; no learner — unmarking takes back
                    # only the visible thumb, never the memory it already taught.
                    with contextlib.suppress(Exception):
                        await runtime.gateway.emit_event(
                            chat_id,
                            FeedbackCleared(
                                data["target_id"],
                                target_kind=data.get("target_kind", "message"),
                            ),
                        )
                    continue
                text = data.get("text", "")
                raw_atts = data.get("attachments")
                attachments = _decode_attachments(raw_atts)
                if not text and attachments:
                    text = "Here is a file I'm sharing with you."
                if not text:
                    continue
                asker = chat_asker(runtime, chat_id)
                surface = default_surface
                # Persist uploads into the workspace and tell the agent their paths (via
                # surface, so the transcript stays clean) — enables editing/reading them.
                saved = _persist_uploads(runtime.config.workspace_dir, raw_atts)
                if saved:
                    surface = (surface + "\n\n" if surface else "") + (
                        "The user attached file(s), saved in the workspace at: "
                        + ", ".join(pth for pth, _ in saved)
                        + ". To edit an uploaded image, call generate_image with "
                        "source_image set to its path; to read an uploaded document, "
                        "read_file that path."
                    )
                    # Surface each upload in the thread (thumbnail / file chip) — emitted
                    # before the turn so it sits with the user's message; persists on the
                    # chat stream so it survives reload.
                    for pth, name in saved:
                        with contextlib.suppress(Exception):
                            await runtime.gateway.emit_event(chat_id, Attachment(pth, name=name))
                # Typed while the agent is still working? Feed the live turn instead of
                # queueing a second one behind it — AG2 drains the message before the
                # turn's next model call, so the user steers the work in progress.
                if await runtime.gateway.feed_message(text, chat_id, attachments):
                    # The agent won't echo it until it drains the inbox, which can be a
                    # whole tool round away. Ack now so the thread can show it as queued
                    # rather than leaving the user wondering if it landed. Transient: the
                    # durable record is the DrainedModelRequest AG2 emits on the drain.
                    with contextlib.suppress(Exception):
                        await websocket.send_json({"type": "queued", "text": text, "chat": chat_id})
                else:
                    asyncio.create_task(
                        bridge.run_turn(
                            text,
                            asker=asker,
                            attachments=attachments,
                            surface=surface,
                            attachment_names=tuple(name for _, name in saved),
                            # The composer's switcher is live before the chat exists;
                            # its choice rides the first frame (ADR 0025).
                            chat_model=str(data.get("model") or ""),
                        )
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
        if not _origin_ok(
            websocket.headers.get("origin"), websocket.headers.get("host"), allowed_origins
        ):
            await websocket.close(code=1008)  # policy violation
            return
        runtime = await _ws_runtime(websocket, pid)
        if runtime is None:
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
        last_role = {"v": None}  # "user" | "agent"

        async def _flush_user():
            text = "".join(user_buf).strip()
            user_buf.clear()
            if persist_chat and text:
                with contextlib.suppress(Exception):
                    await runtime.gateway.emit_event(
                        persist_chat, ModelRequest(parts=[TextInput(content=text)])
                    )

        async def _flush_agent():
            text = "".join(agent_buf).strip()
            agent_buf.clear()
            if persist_chat and text:
                with contextlib.suppress(Exception):
                    await runtime.gateway.emit_event(
                        persist_chat, ModelResponse(message=ModelMessage(content=text))
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


def _decode_attachments(items) -> list:
    """Turn UI attachment frames ({name, mime, data:b64}) into AG2 inputs."""
    out = []
    for a in items or []:
        try:
            raw = base64.b64decode(a.get("data", ""))
        except Exception as exc:
            log_suppressed("attachment decode", exc, name=a.get("name"))
            continue
        inp = build_input(raw, a.get("name", "file"), a.get("mime"))
        if inp is not None:
            out.append(inp)
    return out


def _persist_uploads(workspace_dir, items) -> list[tuple[str, str]]:
    """Save uploaded files into the workspace (uploads/) so the agent can edit/read
    them by path — returns ``(workspace_path, original_name)`` per saved file."""
    out = []
    for a in items or []:
        try:
            raw = base64.b64decode(a.get("data", ""))
        except Exception as exc:
            log_suppressed("upload decode", exc, name=a.get("name"))
            continue
        if not raw:
            continue
        try:
            name = a.get("name", "file")
            out.append((write_upload(workspace_dir, name, raw), name))
        except Exception as exc:
            log_suppressed("upload persist", exc, name=name)
    return out
