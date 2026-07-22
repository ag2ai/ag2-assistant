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
    POST /api/profiles/{pid}/restore         -> un-archive + boot live (ADR 0003)
    DELETE /api/profiles/{pid}               -> archive (guardrails §4.9); ?purge=true hard-deletes an archived profile
    GET  /api/channels                       -> {platform: {profile, token_present, active, error}} (install-level)
    POST /api/channels                       -> bind {platform, profile:pid|null}; hot-applies; returns updated entry
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
import secrets as _secrets
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlsplit

import ag2
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
from ag2.stream import MemoryStream
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

from assistant import __version__, codex_auth, live_configs, llm_configs, secrets, voice_providers
from assistant import feedback as feedback_learner
from assistant import profiles as profiles_mod
from assistant.agent import model_config
from assistant.attachments import build_input
from assistant.config import Config, load_config
from assistant.events import Attachment, FeedbackCleared, FeedbackGiven
from assistant.filesearch import list_folder_dir, search_corpus
from assistant.folders import READ_WRITE, DuplicatePath, FolderStore
from assistant.gateway.profile_manager import (
    _CHANNEL_TOKENS,
    ArchivedProfile,
    ProfileManager,
    ProfileRuntime,
    UnknownProfile,
)
from assistant.gateway.stream_bridge import StreamBridge
from assistant.gateway.wire import to_wire
from assistant.hitl import DurableAsker, GatewayAsker, NullAsker, add_hitl_routes
from assistant.integrations import google_auth
from assistant.memory import read_profile, read_universal, write_profile, write_universal
from assistant.observability import log_suppressed
from assistant.onboarding import identity_document
from assistant.permissions import PermissionStore, command_rule, shell_prefix
from assistant.secrets import KEY_ENV
from assistant.settings import profile_settings
from assistant.tasks import TaskStoreCorruptionError
from assistant.tools.mcp import build_mcp_tools
from assistant.voice import synthesize_preview
from assistant.workspace import (
    _MAX_WRITE_BYTES,
    delete,
    etag_for_path,
    list_all_dirs,
    list_dirs,
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
# module constant so tests can monkeypatch it down (they use a fake Agent, so the
# real value only bounds a genuinely wedged provider call).
_LLM_TEST_TIMEOUT_S = 30.0


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
        "it now with create_task (pass schedule_kind='once'/'cron' with at/cron if "
        "they gave a time or cadence, otherwise leave it 'manual'); only ask a brief "
        "clarifying question if something essential is missing. Confirm what you created."
    ),
}


class MessageRequest(BaseModel):
    text: str
    chat_id: str = "default"
    platform: str | None = None


class MessageResponse(BaseModel):
    reply: str
    chat_id: str


class ChatPatch(BaseModel):
    """Partial chat-metadata update: rename and/or star. Absent field = unchanged."""

    title: str | None = None
    starred: bool | None = None


class CredentialsUpload(BaseModel):
    content: str  # raw OAuth client JSON


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


class TaskCreate(BaseModel):
    # Empty name triggers the service's cheap-model auto-naming from the prompt.
    name: str = ""
    prompt: str
    model: str | None = None
    schedule: dict | None = None
    description: str = ""


class TaskPatch(BaseModel):
    name: str | None = None
    prompt: str | None = None
    model: str | None = None  # "" clears back to the profile default
    schedule: dict | None = None
    paused: bool | None = None
    starred: bool | None = None
    description: str | None = None


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


class AnswerRequest(BaseModel):
    answer: str


class OnboardedRequest(BaseModel):
    value: bool = True


class FocusesRequest(BaseModel):
    focuses: list[str] = []


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


class KeyRequest(BaseModel):
    provider: str
    value: str = ""  # empty clears the key


class LlmConfigRequest(BaseModel):
    """Create/update body for a named LLM configuration. A model's key is its
    ``secret_id`` reference (a Secret in the secrets store). ``api_key`` is
    DRAFT-TEST ONLY: the /test endpoints use a typed value directly ("" tests as
    if no Secret resolved); create/update ignore it. ``id`` is only read by the
    draft-test endpoint (create/update take the id from the URL path)."""

    id: str | None = None
    name: str
    type: str
    model: str
    base_url: str = ""
    host: str = ""
    secret_id: str = ""
    api_key: str | None = None
    options: dict = Field(default_factory=dict)
    activate: bool = False


class LiveConfigRequest(BaseModel):
    """Create/update body for a named live (voice) configuration. A config's key is
    its ``secret_id`` reference (a Secret in the secrets store). ``api_key`` is
    DRAFT-TEST ONLY (a typed value is used directly, never persisted). ``id`` is
    only read by the draft-test endpoint. ``voice`` is optional on save (defaults
    to the provider's default voice; usually changed via the voice picker, not
    this body)."""

    id: str | None = None
    name: str
    provider: str
    model: str = ""
    voice: str = ""
    secret_id: str = ""
    api_key: str | None = None
    activate: bool = False


class SecretCreateRequest(BaseModel):
    """Create body for a Secret (named reusable API key — CONTEXT.md "Secrets").
    ``value`` is write-only: no endpoint ever returns it (views carry a last-4
    hint). ``default`` requires a provider tag."""

    name: str
    value: str
    provider: str = ""
    default: bool = False


class SecretUpdateRequest(BaseModel):
    """Partial update for a Secret — None leaves a field unchanged. Rotating
    ``value`` re-keys every model referencing this Secret."""

    name: str | None = None
    value: str | None = None
    provider: str | None = None
    default: bool | None = None


class CodexCodeRequest(BaseModel):
    """Headless ChatGPT-subscription sign-in: a pasted auth code + its flow state."""

    state: str
    code: str


class VoiceProviderRequest(BaseModel):
    provider: str


class ChannelBindRequest(BaseModel):
    platform: str
    profile: str | None = None  # pid to bind to, or null to disable


class ChannelTokenRequest(BaseModel):
    platform: str
    tokens: dict[str, str] = Field(default_factory=dict)  # {ENV_NAME: value_or_empty}


class MemoryRequest(BaseModel):
    text: str


class IdentityRequest(BaseModel):
    """Identity answers collected in web onboarding (all optional). Seed the shared
    universal "who the user is" doc, replacing the CLI first-chat interview."""

    name: str | None = None
    location: str | None = None
    hours: str | None = None
    style: str | None = None


class ProfileCreateRequest(BaseModel):
    name: str
    accent: str


class ProfileUpdateRequest(BaseModel):
    name: str | None = None
    accent: str | None = None


class ProfileArchiveRequest(BaseModel):
    new_default: str | None = None


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


class PermissionCommandAddRequest(BaseModel):
    tool: str
    prefix: str | None = None  # shell command prefix (e.g. "git"), or null for whole-tool


class PermissionCommandDeleteRequest(BaseModel):
    rule: str  # canonical rule string, e.g. "run_shell_command(git *)" or "run_code"


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
    return profile_settings(runtime.config.data_dir)


def _chat_asker(runtime: ProfileRuntime, chat_id: str):
    """Durable, inline HITL for a chat turn: the agent's question persists as an
    Inquiry and surfaces inline on this chat's stream (InquiryRaised),
    answerable from the thread or the strip. Falls back to the transient HITL
    registry if the inquiry store isn't available."""
    inquiries = getattr(runtime.tasks, "inquiries", None) if runtime.tasks is not None else None
    if inquiries is None:
        return GatewayAsker(runtime.hitl)
    return DurableAsker(NullAsker(), inquiries, chat=chat_id)


async def _activity(runtime: ProfileRuntime) -> tuple[int, int]:
    """Per-profile activity for the chip badges, from a single store scan (v2:
    a Task is standing config, a Run is one execution — this scans runs, not tasks).

    Returns ``(running, unseen_done)``:
      * ``running``     — runs not yet in a terminal state (RUNNING or NEEDS_INPUT).
      * ``unseen_done`` — finished, not-yet-opened runs: the count behind the chip's
        "unread results" dot. Mirrors the nav's per-row unread marker (``isUnread`` =
        terminal status && not seen), rolled up to the profile.
    """
    tasks = runtime.tasks
    store = getattr(tasks, "store", None) if tasks is not None else None
    if store is None:
        return 0, 0
    try:
        from assistant.tasks import RunStatus

        runs = await store.list_runs()
        running = sum(1 for r in runs if r.status not in RunStatus.TERMINAL)
        unseen_done = sum(1 for r in runs if r.status in RunStatus.TERMINAL and r.seen_at is None)
        return running, unseen_done
    except Exception:
        return 0, 0


def create_app(profiles: ProfileManager, *, persist: bool = True) -> FastAPI:
    """Build the FastAPI app around a (constructed-but-not-started) ``ProfileManager``.

    The app owns the manager's lifecycle: ``profiles.start()`` runs on lifespan
    startup (boot all unarchived profiles) and ``profiles.close()`` on
    shutdown. ``persist`` is accepted for signature symmetry (the manager itself is
    already configured with its persistence choice).

    ``app.state.profiles`` holds the manager; there is no ``app.state.gateway`` /
    ``app.state.tasks`` — profile-scoped routes resolve a runtime per request.
    """
    manager = profiles

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        await manager.start()  # boot all unarchived profiles (+ channels)
        try:
            yield
        finally:
            await manager.close()

    app = FastAPI(title="AG2 Assistant Gateway", version=__version__, lifespan=lifespan)
    app.state.profiles = manager
    app.state.google_flows = {}  # state token -> in-progress OAuth flow
    app.state.codex_flows = {}  # state token -> PKCE verifier (ChatGPT-subscription login)

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

    @app.get("/api/usage")
    async def usage() -> dict:
        """Install-wide token/cost roll-up across ALL running profiles (for the HUD's
        "all profiles" total). ``profiles`` is one ``usage_today()`` snapshot per
        running runtime (with its ``pid``/``name``); ``total`` sums the numeric fields.

        ``total.priced`` is true only when EVERY contributing profile is priced — an
        unpriced profile means its tokens carry no cost, so the summed ``cost`` is an
        underestimate and the flag says so (matching the per-profile flag semantics and
        the HUD's "no price set" fallback). Archived profiles aren't running, so they're
        naturally excluded. Zero profiles → empty list + a zeroed total.
        """
        rows = []
        total = {"prompt": 0.0, "completion": 0.0, "total": 0.0, "cost": 0.0}
        all_priced = True
        any_profile = False
        for runtime in manager.runtimes():
            if runtime.gateway is None:
                continue
            any_profile = True
            today = runtime.gateway.usage_today()
            rows.append({"pid": runtime.pid, "name": runtime.meta.name, **today})
            for k in total:
                total[k] += today.get(k) or 0
            if not today.get("priced"):
                all_priced = False
        # Zero profiles (or none priced) → not priced. With profiles present, priced iff
        # every one is priced (an unpriced profile makes the summed cost incomplete).
        total["priced"] = bool(any_profile and all_priced)
        return {"profiles": rows, "total": total}

    @app.get("/api/status")
    async def status() -> list[dict]:
        """Per-profile activity for badges: busy = agent alive, running_tasks = count
        of RUNNING tasks, unseen_done = finished-but-not-yet-opened root tasks (the
        chip's unread-results dot). Aggregated over the running runtimes."""
        out = []
        for runtime in manager.runtimes():
            gw_status = runtime.gateway.status() if runtime.gateway is not None else {}
            running, unseen_done = await _activity(runtime)
            out.append(
                {
                    "pid": runtime.pid,
                    "busy": gw_status.get("status") == "ok",
                    "running_tasks": running,
                    "unseen_done": unseen_done,
                }
            )
        return out

    @app.post("/api/secrets/key")
    async def set_secrets_key(req: KeyRequest) -> dict:
        """Save/clear a provider API key (global secrets). Reloads ALL runtimes so
        every profile's agent picks up the change on its next turn."""
        if not secrets.set_key(req.provider, req.value):
            return Response(status_code=400)
        await _reload_all()
        return {"ok": True}

    # ---- Secrets: named reusable API keys (CONTEXT.md "Secrets", ADR 0005).
    # Registered AFTER /api/secrets/key so the literal "key" segment keeps routing
    # to the provider-key handler, not /{sid}. ----

    def _secret_views() -> list[dict]:
        """Safe views + the names of the model configs referencing each Secret
        (drives the "used by N models" delete confirm)."""
        llm = llm_configs.list_configs()
        live = live_configs.list_configs()
        out = []
        for s in secrets.list_secrets():
            used = [c.get("name", "") for c in llm if c.get("secret_id") == s["id"]]
            used += [c.get("name", "") for c in live if c.get("secret_id") == s["id"]]
            out.append({**s, "used_by": used})
        return out

    @app.get("/api/secrets")
    async def list_secrets_api() -> dict:
        """Every Secret as a safe view — name/provider/default/hint/used_by, never
        the raw value."""
        return {"secrets": _secret_views()}

    @app.post("/api/secrets")
    async def create_secret_api(req: SecretCreateRequest):
        """Create a Secret. 409 + the existing Secret's view when the value is
        already stored (unique by value — the model form snaps to ``existing``).
        Reloads all runtimes (a new Default changes env-derived keys)."""
        try:
            view = secrets.create_secret(
                req.name, req.value, provider=req.provider, default=req.default
            )
        except secrets.DuplicateValue as exc:
            return JSONResponse(
                {"ok": False, "error": str(exc), "existing": exc.existing}, status_code=409
            )
        except ValueError as exc:
            return JSONResponse({"ok": False, "error": str(exc)[:300]}, status_code=400)
        await _reload_all()
        return {"ok": True, "secret": view}

    @app.post("/api/secrets/{sid}")
    async def update_secret_api(sid: str, req: SecretUpdateRequest):
        """Partial update (rename / rotate / retag / set-default). 404 unknown, 409
        duplicate value, 400 bad input. Rotating re-keys every referencing model."""
        try:
            view = secrets.update_secret(
                sid, name=req.name, value=req.value, provider=req.provider, default=req.default
            )
        except KeyError:
            return JSONResponse({"ok": False, "error": f"unknown secret: {sid}"}, status_code=404)
        except secrets.DuplicateValue as exc:
            return JSONResponse(
                {"ok": False, "error": str(exc), "existing": exc.existing}, status_code=409
            )
        except ValueError as exc:
            return JSONResponse({"ok": False, "error": str(exc)[:300]}, status_code=400)
        await _reload_all()
        return {"ok": True, "secret": view}

    @app.delete("/api/secrets/{sid}")
    async def delete_secret_api(sid: str):
        """Delete a Secret (404 unknown). Always allowed — referencing configs
        degrade down the resolution order; deleting a Default pops its env var."""
        if not secrets.delete_secret(sid):
            return JSONResponse({"ok": False, "error": f"unknown secret: {sid}"}, status_code=404)
        await _reload_all()
        return {"ok": True}

    # ---- Named LLM configurations (install-wide list + single active selection) ----

    async def _reload_all() -> None:
        """Reference-swap reload of every running runtime so all profiles' agents pick
        up an LLM change on their next turn (the same loop set_secrets_key uses)."""
        for runtime in list(manager.runtimes()):
            with contextlib.suppress(Exception):
                await manager.reload(runtime.pid)

    def _llm_entry_view(entry: dict, active: str | None) -> dict:
        """One config as the API exposes it: the stored fields, the referenced
        Secret's view (or a dangling-reference flag) and the provider's shared env
        key summary — never the raw values — plus ``key_source`` naming which one an
        actual call would send. That triple is what lets the UI say honestly why a
        keyless-looking config still works (shared fallback / no key needed)."""
        provider = llm_configs.PROVIDER_OF.get(entry["type"], "")
        shared = secrets.status().get(provider, {})
        sec_view = secrets.get_secret(entry.get("secret_id", ""))
        sec = (
            {"id": sec_view["id"], "name": sec_view["name"], "hint": sec_view["hint"]}
            if sec_view
            else None
        )
        view = {
            "id": entry["id"],
            "name": entry["name"],
            "type": entry["type"],
            "model": entry["model"],
            "base_url": entry.get("base_url", ""),
            "host": entry.get("host", ""),
            "options": entry.get("options", {}),
            "secret_id": entry.get("secret_id", ""),
            "secret": sec,
            "secret_missing": bool(entry.get("secret_id")) and sec is None,
            "key_source": llm_configs.key_source(
                entry
            ),  # secret | shared | not_needed | none | subscription
            "images": llm_configs.image_capable(entry),  # drives the row's "images" chip
            "shared_key": {
                "env": KEY_ENV.get(provider, ""),
                "set": bool(shared.get("set")),
                "hint": shared.get("hint", ""),
            },
            "active": entry["id"] == active,
        }
        if entry["type"] == "openai_subscription":
            # The chip/form need the live ChatGPT sign-in state without a second
            # fetch. Lazy + guarded so a missing/broken codex_auth reads as signed-out.
            try:
                view["signed_in"] = bool(codex_auth.status().get("signed_in"))
            except Exception:
                view["signed_in"] = False
        return view

    def _llm_env_override() -> dict | None:
        """The env pin banner payload: whichever of AG2ASSISTANT_LLM_PROVIDER /
        AG2ASSISTANT_MODEL is set (they override any active config in load_config), or
        None when neither is set."""
        out = {}
        if v := os.environ.get("AG2ASSISTANT_LLM_PROVIDER"):
            out["provider"] = v
        if v := os.environ.get("AG2ASSISTANT_MODEL"):
            out["model"] = v
        return out or None

    def _llm_probe_config(entry: dict):
        """A throwaway Config carrying just the entry's derived provider/model/options,
        for the dry-construct + test round-trip. Streaming off (a one-shot probe)."""
        probe = Config()
        probe.llm.streaming = False
        probe.llm.provider = llm_configs.PROVIDER_OF[entry["type"]]
        probe.llm.model = entry["model"]
        probe.llm.provider_options[probe.llm.provider] = llm_configs.entry_options(entry)
        # Subscription mode is carried on auth_mode (not provider_options), so mirror
        # apply_active here — otherwise the probe would test the key path with no key.
        if entry["type"] == "openai_subscription":
            probe.llm.auth_mode = "subscription"
        return probe

    @app.get("/api/llm-configs")
    async def list_llm_configs() -> dict:
        """The install-wide named LLM configs, the active id, and any env override that
        pins provider/model over them (drives the 'pinned by env' UI banner)."""
        active = llm_configs.active_id()
        return {
            "configs": [_llm_entry_view(e, active) for e in llm_configs.list_configs()],
            "active": active,
            "env_override": _llm_env_override(),
        }

    async def _save_llm_config(req: LlmConfigRequest, cid: str | None):
        """Shared create/update: dry-construct the derived model_config BEFORE
        persisting (a bad type/kwarg fails here, 400 + the constructor's message, not on
        the agent's next turn), then save the entry, optionally activate, and reload
        every runtime. 404 when updating an unknown id."""
        entry = {
            "name": req.name,
            "type": req.type,
            "model": req.model,
            "base_url": req.base_url,
            "host": req.host,
            "secret_id": req.secret_id,
            "options": req.options,
        }
        if cid is not None:
            if llm_configs.get_config(cid) is None:
                return JSONResponse(
                    {"ok": False, "error": f"unknown config: {cid}"}, status_code=404
                )
            entry["id"] = cid
        # Validate shape + derived construction before anything is written.
        try:
            probe_entry = llm_configs._clean_entry(entry)
            probe_entry.setdefault("id", cid or "")
            model_config(_llm_probe_config(probe_entry))
        except Exception as exc:
            return JSONResponse({"ok": False, "error": str(exc)[:300]}, status_code=400)
        saved = llm_configs.save_config(entry)
        if req.activate:
            llm_configs.set_active(saved["id"])
        await _reload_all()
        active = llm_configs.active_id()
        return {"ok": True, "config": _llm_entry_view(saved, active), "active": active}

    async def _ping_entry(entry: dict, draft_key: str | None = None):
        """The PONG round-trip shared by the saved-config and draft tests: build the
        derived config (streaming off, no tools/memory) and make ONE real call.
        ``draft_key`` overrides the key resolution for an unsaved edit: a typed value
        is used directly, "" tests as if the stored key were cleared (base_url configs
        then get the placeholder — the same thing a save would produce). A working
        reply → ``{ok, reply, latency_ms}``; ANY failure (construction, auth,
        timeout) → 502 ``{ok:false, error}``."""
        started = time.monotonic()
        try:
            probe = _llm_probe_config(entry)
            if draft_key is not None:
                opts = probe.llm.provider_options[probe.llm.provider]
                opts.pop("api_key", None)
                if draft_key:
                    opts["api_key"] = draft_key
                elif entry.get("base_url"):
                    opts["api_key"] = "unused"  # mirror entry_options' placeholder
            agent = ag2.Agent("ping", config=model_config(probe))
            reply = await asyncio.wait_for(
                agent.ask("Reply with exactly: PONG"), timeout=_LLM_TEST_TIMEOUT_S
            )
        except Exception as exc:
            return JSONResponse({"ok": False, "error": str(exc)[:300]}, status_code=502)
        return {
            "ok": True,
            "reply": (getattr(reply, "body", "") or "")[:200],
            "latency_ms": int((time.monotonic() - started) * 1000),
        }

    @app.post("/api/llm-configs")
    async def create_llm_config(req: LlmConfigRequest):
        """Create a new named LLM configuration."""
        return await _save_llm_config(req, None)

    @app.post("/api/llm-configs/test")
    async def test_llm_config_draft(req: LlmConfigRequest):
        """Test a DRAFT configuration exactly as entered in the editor, WITHOUT saving.
        Registered before the /{cid} routes so the literal "test" segment isn't
        captured as an id. ``req.id`` (when editing an existing config) lets a blank
        key field fall back to that config's stored key, matching what a save would
        produce; a typed ``api_key`` is used directly and never persisted."""
        try:
            entry = llm_configs._clean_entry(
                {
                    "id": req.id or "",
                    "name": req.name or "draft",
                    "type": req.type,
                    "model": req.model,
                    "base_url": req.base_url,
                    "host": req.host,
                    "secret_id": req.secret_id or "",
                    "options": req.options,
                }
            )
        except ValueError as exc:
            return JSONResponse({"ok": False, "error": str(exc)[:300]}, status_code=400)
        entry.setdefault("id", "")
        return await _ping_entry(entry, draft_key=req.api_key)

    @app.post("/api/llm-configs/{cid}")
    async def update_llm_config(cid: str, req: LlmConfigRequest):
        """Update an existing named LLM configuration (404 if unknown)."""
        return await _save_llm_config(req, cid)

    @app.delete("/api/llm-configs/{cid}")
    async def delete_llm_config(cid: str):
        """Delete a config (404 if unknown). Deleting the active one moves active to the
        next remaining config (or none — flat defaults). Reloads every runtime so the
        new active takes effect (referenced Secrets are independent and untouched)."""
        if llm_configs.get_config(cid) is None:
            return JSONResponse({"ok": False, "error": f"unknown config: {cid}"}, status_code=404)
        llm_configs.delete_config(cid)
        await _reload_all()
        return {"ok": True}

    @app.post("/api/llm-configs/{cid}/use")
    async def use_llm_config(cid: str):
        """Make ``cid`` the active configuration and reload every runtime (404 unknown)."""
        if not llm_configs.set_active(cid):
            return JSONResponse({"ok": False, "error": f"unknown config: {cid}"}, status_code=404)
        await _reload_all()
        return {"ok": True}

    @app.post("/api/llm-configs/{cid}/test")
    async def test_llm_config(cid: str):
        """Real PONG round-trip against a SAVED config, exercising the exact runtime
        key-resolution path. 404 if unknown; result shape per ``_ping_entry``."""
        entry = llm_configs.get_config(cid)
        if entry is None:
            return JSONResponse({"ok": False, "error": f"unknown config: {cid}"}, status_code=404)
        return await _ping_entry(entry)

    # ---- Named LIVE (voice) configurations — the spoken counterpart of the LLM
    # configs above. Install-wide list + single active selection, read fresh by the
    # voice session at connect (so no runtime reload on change). ----

    def _live_entry_view(entry: dict, active: str | None) -> dict:
        """One live config as the API exposes it: stored fields + the referenced
        Secret's view (or a dangling-reference flag) and the provider's shared env
        key summary (never the raw values) + ``key_source`` naming which one a
        session sends."""
        provider = entry["provider"]
        shared = secrets.status().get(provider, {})
        sec_view = secrets.get_secret(entry.get("secret_id", ""))
        sec = (
            {"id": sec_view["id"], "name": sec_view["name"], "hint": sec_view["hint"]}
            if sec_view
            else None
        )
        return {
            "id": entry["id"],
            "name": entry["name"],
            "provider": provider,
            "model": entry["model"],
            "voice": entry.get("voice", ""),
            "secret_id": entry.get("secret_id", ""),
            "secret": sec,
            "secret_missing": bool(entry.get("secret_id")) and sec is None,
            "key_source": live_configs.key_source(entry),  # secret | shared | none
            "shared_key": {
                "env": KEY_ENV.get(provider, ""),
                "set": bool(shared.get("set")),
                "hint": shared.get("hint", ""),
            },
            "active": entry["id"] == active,
        }

    async def _ping_live(entry: dict, draft_key: str | None = None):
        """Models-list key probe (the live-config 'Test'): call the provider's cheap
        ``check`` with the resolved key. ``draft_key`` overrides for an unsaved edit —
        None uses the stored/shared key, "" tests as if the stored key were cleared, a
        value tests that key directly. Ok → ``{ok, reply, latency_ms}``; any failure →
        502 ``{ok:false, error}``."""
        if draft_key is None:
            key = live_configs.resolve_key(entry)
        elif draft_key:
            key = draft_key
        else:
            key = live_configs._shared_key(entry.get("provider", ""))
        started = time.monotonic()
        try:
            await asyncio.wait_for(
                voice_providers.get(entry["provider"]).check(key), timeout=_LLM_TEST_TIMEOUT_S
            )
        except Exception as exc:
            return JSONResponse({"ok": False, "error": str(exc)[:300]}, status_code=502)
        return {"ok": True, "reply": "OK", "latency_ms": int((time.monotonic() - started) * 1000)}

    @app.get("/api/live-configs")
    async def list_live_configs() -> dict:
        """The install-wide named live configs, the active id, and the provider catalog
        (default model/voice per provider) that seeds the add-form and templates."""
        active = live_configs.active_id()
        return {
            "configs": [_live_entry_view(e, active) for e in live_configs.list_configs()],
            "active": active,
            "providers": [
                {
                    "name": n,
                    "default_model": voice_providers.get(n).realtime_model,
                    "default_voice": voice_providers.get(n).default_voice,
                }
                for n in voice_providers.names()
            ],
        }

    async def _save_live_config(req: LiveConfigRequest, cid: str | None):
        """Shared create/update: validate (bad provider/voice → 400), save, optionally
        activate. 404 when updating an unknown id. A blank ``voice`` on update keeps
        the config's existing voice (it's set via the picker, not this form) rather
        than resetting to the provider default."""
        existing = live_configs.get_config(cid) if cid is not None else None
        if cid is not None and existing is None:
            return JSONResponse({"ok": False, "error": f"unknown config: {cid}"}, status_code=404)
        voice = req.voice
        if not voice and existing and existing.get("provider") == req.provider:
            voice = existing.get("voice", "")
        entry = {
            "name": req.name,
            "provider": req.provider,
            "model": req.model,
            "voice": voice,
            "secret_id": req.secret_id,
        }
        if cid is not None:
            entry["id"] = cid
        try:
            saved = live_configs.save_config(entry)
        except ValueError as exc:
            return JSONResponse({"ok": False, "error": str(exc)[:300]}, status_code=400)
        except KeyError:
            return JSONResponse({"ok": False, "error": f"unknown config: {cid}"}, status_code=404)
        if req.activate:
            live_configs.set_active(saved["id"])
        active = live_configs.active_id()
        return {"ok": True, "config": _live_entry_view(saved, active), "active": active}

    @app.post("/api/live-configs")
    async def create_live_config(req: LiveConfigRequest):
        """Create a new named live configuration."""
        return await _save_live_config(req, None)

    @app.post("/api/live-configs/test")
    async def test_live_config_draft(req: LiveConfigRequest):
        """Probe a DRAFT live config as entered, WITHOUT saving. Registered before the
        /{cid} routes so "test" isn't captured as an id. ``req.id`` lets a blank key
        field fall back to that config's stored key; a typed key is used directly."""
        try:
            entry = live_configs._clean_entry(
                {
                    "id": req.id or "",
                    "name": req.name or "draft",
                    "provider": req.provider,
                    "model": req.model,
                    "voice": req.voice,
                    "secret_id": req.secret_id or "",
                }
            )
        except ValueError as exc:
            return JSONResponse({"ok": False, "error": str(exc)[:300]}, status_code=400)
        entry.setdefault("id", "")
        return await _ping_live(entry, draft_key=req.api_key)

    @app.post("/api/live-configs/{cid}")
    async def update_live_config(cid: str, req: LiveConfigRequest):
        """Update an existing named live configuration (404 if unknown)."""
        return await _save_live_config(req, cid)

    @app.delete("/api/live-configs/{cid}")
    async def delete_live_config(cid: str):
        """Delete a live config (404 if unknown). Deleting the active one moves active to
        the next remaining config (or none — legacy fallback). Referenced Secrets are
        independent and untouched."""
        if live_configs.get_config(cid) is None:
            return JSONResponse({"ok": False, "error": f"unknown config: {cid}"}, status_code=404)
        live_configs.delete_config(cid)
        return {"ok": True}

    @app.post("/api/live-configs/{cid}/use")
    async def use_live_config(cid: str):
        """Make ``cid`` the active live configuration (404 if unknown)."""
        if not live_configs.set_active(cid):
            return JSONResponse({"ok": False, "error": f"unknown config: {cid}"}, status_code=404)
        return {"ok": True}

    @app.post("/api/live-configs/{cid}/test")
    async def test_live_config(cid: str):
        """Models-list key probe against a SAVED live config. 404 if unknown."""
        entry = live_configs.get_config(cid)
        if entry is None:
            return JSONResponse({"ok": False, "error": f"unknown config: {cid}"}, status_code=404)
        return await _ping_live(entry)

    @app.post("/api/onboarded")
    async def set_onboarded(req: OnboardedRequest) -> dict:
        """Mark first-run onboarding completed/dismissed (install-level, in the registry)."""
        profiles_mod.set_onboarded(req.value)
        return {"ok": True}

    # ---- Universal memory: the shared "who the user is" doc (root/user.db) ----

    def _user_store_path() -> Path:
        """The install-wide universal memory DB — the SAME file every profile's agent
        reads (``root_dir/user.db``). Profile-agnostic, so resolved from the root config."""
        return load_config().root_dir / "user.db"

    @app.get("/api/memory")
    async def get_universal_memory() -> dict:
        """Read the shared universal "who the user is" document (identity facts injected
        into EVERY profile's context). Mirrors the per-profile GET /api/p/{pid}/memory."""
        return {"text": await read_universal(_user_store_path())}

    @app.post("/api/memory")
    async def set_universal_memory(req: MemoryRequest) -> dict:
        """Replace the shared universal document (a user edit from any profile's Settings →
        Memory). Read fresh per turn, so all profiles' agents pick it up next turn."""
        await write_universal(req.text, _user_store_path())
        return {"ok": True}

    @app.post("/api/identity")
    async def seed_identity(req: IdentityRequest) -> dict:
        """Seed the universal "who the user is" doc from web-onboarding identity answers
        (name/location/hours/style, all optional). Formats them with the SAME
        `identity_document` helper the CLI interview uses, so both surfaces produce an
        identical doc. Onboarding semantics: this only ever *seeds* — if the universal
        store already holds a doc it is left untouched (returns ``seeded: false``), and
        if every field is empty nothing is written (also ``seeded: false``). This is why
        a web-onboarded user's first chat never triggers the in-chat interview: the
        store is already seeded, so `needs_onboarding` is false."""
        doc = identity_document(req.model_dump())
        if not doc:
            return {"ok": True, "seeded": False, "reason": "empty"}
        path = _user_store_path()
        if (await read_universal(path)).strip():
            return {"ok": True, "seeded": False, "reason": "exists"}
        await write_universal(doc, path)
        return {"ok": True, "seeded": True}

    # ---- Permissions (global, install-wide: one command store shared by every profile) ----

    def _permissions_store():
        """A fresh PermissionStore over the install-wide file. mtime self-refresh
        means live turns pick up any change on their next query — no manager.reload()."""
        return PermissionStore(load_config().root_dir / "permissions.json")

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
        return FolderStore(load_config().root_dir / "folders.json")

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
        return {"ok": True, **_folders_snapshot(store)}

    # ---- Profile management (global) ----

    def _profile_view(meta) -> dict:
        return {
            "id": meta.id,
            "name": meta.name,
            "accent": meta.accent,
            "workspace": meta.workspace,
            "created": meta.created,
        }

    @app.get("/api/profiles")
    async def list_profiles() -> dict:
        """The §3.5 contract, present in every state: unarchived profiles, the
        server-side active default, and the install-level onboarded flag. Empty list +
        null + false on fresh install. Channel bindings are install-level now — see
        GET /api/channels."""
        reg = profiles_mod.load_registry()
        allp = profiles_mod.list_profiles(include_archived=True)
        return {
            "profiles": [_profile_view(m) for m in allp if not m.archived],
            # Archived profiles for the Settings "Archived" section (ADR 0003): restore
            # or permanently delete them. Empty on a fresh install / when none archived.
            "archived": [_profile_view(m) for m in allp if m.archived],
            "active_default": reg.get("active_default"),
            "onboarded": bool(reg.get("onboarded")),
            # App version rides the boot payload so the UI needn't make a second request.
            "version": __version__,
        }

    @app.post("/api/profiles")
    async def create_profile(req: ProfileCreateRequest):
        """Create a profile (dir + registry) and boot its runtime live (§3.5)."""
        try:
            runtime = await manager.create(req.name, req.accent)
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        return {"profile": _profile_view(runtime.meta)}

    @app.post("/api/profiles/{pid}")
    async def update_profile(pid: str, req: ProfileUpdateRequest):
        """Rename and/or set accent (both display-only, registry-level). Unknown pid →
        404, invalid value → 400."""
        if profiles_mod.get_profile(pid) is None:
            return JSONResponse({"error": f"unknown profile: {pid}"}, status_code=404)
        try:
            if req.name is not None:
                profiles_mod.rename_profile(pid, req.name)
            if req.accent is not None:
                profiles_mod.set_accent(pid, req.accent)
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        return {"profile": _profile_view(profiles_mod.get_profile(pid))}

    @app.delete("/api/profiles/{pid}")
    async def archive_or_purge_profile(
        pid: str, purge: bool = False, req: ProfileArchiveRequest | None = None
    ):
        """Soft-archive by default; hard-delete when ``?purge=true`` (ADR 0003).

        Archive: §4.9 guardrails — ValueError (guardrail) → 400, unknown → 404, already
        archived → 410. new_default may come in the body.

        Purge (``?purge=true``): permanently erase an ALREADY-archived profile. Unknown →
        404; a live (not-yet-archived) profile → 409 (archive-first). The explicit flag
        makes the soft→hard escalation deliberate."""
        if purge:
            try:
                await manager.purge(pid)
            except UnknownProfile:
                return JSONResponse({"error": f"unknown profile: {pid}"}, status_code=404)
            except ValueError as exc:
                return JSONResponse({"error": str(exc)}, status_code=409)
            return {"ok": True}

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

    @app.post("/api/profiles/{pid}/restore")
    async def restore_profile(pid: str):
        """Un-archive a profile and boot it live (ADR 0003). Unknown → 404; a live
        (non-archived) profile → 409; a boot failure rolls the archive flag back and
        surfaces 500."""
        try:
            runtime = await manager.restore(pid)
        except UnknownProfile:
            return JSONResponse({"error": f"unknown profile: {pid}"}, status_code=404)
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=409)
        except Exception as exc:  # boot failed; manager already rolled back to archived
            return JSONResponse({"error": f"could not restore profile: {exc}"}, status_code=500)
        return {"profile": _profile_view(runtime.meta)}

    # ---- Channels (global, install-level: platform → one profile or disabled) ----

    def _channel_entry(platform: str, pid: str | None) -> dict:
        """The install-level state of one platform: which profile owns it (or null),
        whether its token env is present, whether it is live on that runtime, and the
        last start error (or null)."""
        active = False
        if pid is not None:
            runtime = manager.runtimes_by_id().get(pid)
            active = bool(runtime and platform in runtime.channels)
        return {
            "profile": pid,
            "token_present": all(os.environ.get(e) for e in _CHANNEL_TOKENS[platform]),
            "active": active,
            "error": manager.channel_errors.get(platform),
        }

    @app.get("/api/channels")
    async def list_channels() -> dict:
        """Install-level channel bindings: ``{platform: {profile, token_present, active,
        error}}``. A fresh (zero-profile) install returns all profiles null."""
        return {
            platform: _channel_entry(platform, pid)
            for platform, pid in profiles_mod.channel_bindings().items()
        }

    @app.post("/api/channels")
    async def bind_channel(req: ChannelBindRequest):
        """Assign a platform to a profile (or disable it with profile:null) and hot-apply
        it. Returns the updated platform entry. Unknown platform → 400; unknown/archived
        pid → 400. The binding persists even if the channel fails to start (bad/missing
        token): ``active`` reports live state, ``error`` explains any failure."""
        try:
            await manager.bind_channel(req.platform, req.profile)
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        return {req.platform: _channel_entry(req.platform, req.profile)}

    @app.post("/api/channels/token")
    async def set_channel_token(req: ChannelTokenRequest):
        """Save/clear channel bot token(s) for a platform (global secrets, like provider
        keys) and re-apply the channel live. Body: ``{platform, tokens:{ENV_NAME: value}}``
        — an empty value clears that token. Only env names valid for the platform are
        accepted; an unknown platform or env name → 400. Saving sets os.environ, so the
        bound channel is restarted (stopped, then started if all tokens are now present).
        Returns the updated GET /api/channels entry. Token values are never echoed."""
        platform = req.platform
        if platform not in profiles_mod.CHANNEL_PLATFORMS:
            return JSONResponse({"error": f"unknown channel platform: {platform}"}, status_code=400)
        valid = set(profiles_mod.CHANNEL_TOKEN_ENVS[platform])
        unknown = set(req.tokens) - valid
        if unknown:
            return JSONResponse(
                {"error": f"invalid token env(s) for {platform}: {', '.join(sorted(unknown))}"},
                status_code=400,
            )
        for env_name, value in req.tokens.items():
            secrets.set_channel_token(env_name, value)
        # Reconcile the live channel with the new tokens if the platform is bound.
        bound = profiles_mod.channel_bindings().get(platform)
        if bound is not None:
            with contextlib.suppress(Exception):
                await manager.restart_channel(platform)
        return {platform: _channel_entry(platform, bound)}

    # ---- Google OAuth (global, account-level) ----

    @app.get("/api/google/status")
    async def google_status() -> dict:
        return {
            "configured": google_auth.is_configured(),
            "signed_in": google_auth.has_token(),
            "email": google_auth.account_email(),
        }

    @app.post("/api/google/credentials")
    async def google_credentials(payload: CredentialsUpload) -> dict:
        """Save an uploaded OAuth client JSON (so users avoid the filesystem)."""
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
        # Google tools are gated on has_token() at agent build time — reference-swap
        # reload every runtime so Gmail/Calendar/Drive attach on the next turn.
        for runtime in list(manager.runtimes()):
            with contextlib.suppress(Exception):
                await manager.reload(runtime.pid)
        return HTMLResponse(_page("Connected ✓", f"AG2 Assistant is now connected to {email}."))

    @app.post("/api/google/logout")
    async def google_logout() -> dict:
        ok = google_auth.logout()
        # Drop the Google tools from every runtime immediately (same gate, reversed).
        for runtime in list(manager.runtimes()):
            with contextlib.suppress(Exception):
                await manager.reload(runtime.pid)
        return {"ok": ok}

    # ---- OpenAI ChatGPT-subscription OAuth ("Sign in with ChatGPT") ----
    # Unofficial / gray-area vs OpenAI ToS — see assistant.codex_auth. The flow is a
    # loopback (localhost:1455) OAuth; the gateway is local + single-user, so it can
    # run the callback capture itself. Headless setups paste the code via /submit.

    async def _reload_all_runtimes() -> None:
        for runtime in list(manager.runtimes()):
            with contextlib.suppress(Exception):
                await manager.reload(runtime.pid)

    @app.get("/api/codex/status")
    async def codex_status() -> dict:
        return codex_auth.status()

    @app.post("/api/codex/login_url")
    async def codex_login_url() -> dict:
        """Begin a ChatGPT sign-in: return the consent URL for the UI to open, and
        start a background loopback listener (localhost:1455) that completes the flow
        when OpenAI redirects back. The UI polls GET /api/codex/status."""
        verifier, challenge = codex_auth.generate_pkce()
        state = _secrets.token_urlsafe(24)
        app.state.codex_flows[state] = verifier
        url = codex_auth.build_authorize_url(challenge, state)

        async def _complete() -> None:
            try:
                code = await asyncio.to_thread(codex_auth._capture_code, state)
            except Exception:
                return  # loopback failed/timed out — leave the flow for /submit (headless)
            if app.state.codex_flows.pop(state, None) is None:
                return  # already completed via /submit
            try:
                await asyncio.to_thread(codex_auth.exchange_code, code, verifier)
            except Exception:
                return
            await _reload_all_runtimes()

        asyncio.create_task(_complete())
        return {"ok": True, "auth_url": url, "state": state}

    @app.post("/api/codex/submit")
    async def codex_submit(payload: CodexCodeRequest) -> dict:
        """Headless fallback: exchange a manually pasted auth code for the flow's
        pending PKCE verifier. Used when the loopback callback can't reach the box
        (e.g. Docker/remote) — the user copies the ``code`` from the redirect URL."""
        verifier = app.state.codex_flows.pop(payload.state, None)
        if verifier is None:
            return JSONResponse(
                {"ok": False, "error": "unknown or expired sign-in"}, status_code=400
            )
        # Accept either the bare code or the whole redirect URL the user copied out
        # of the browser's address bar (even off the "connection refused" page).
        code = codex_auth.extract_auth_code(payload.code)
        try:
            await asyncio.to_thread(codex_auth.exchange_code, code, verifier)
        except codex_auth.CodexAuthError as exc:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)
        await _reload_all_runtimes()
        return {"ok": True}

    @app.post("/api/codex/logout")
    async def codex_logout() -> dict:
        ok = codex_auth.logout()
        await _reload_all_runtimes()
        return {"ok": ok}

    @app.get("/api/fs/list")
    async def fs_list(path: str = "") -> dict:
        """List immediate subdirectories of a host path — drives the folder picker. The
        gateway is local + single-user and `_origin_guard` blocks cross-origin, so this is
        safe; dotfolders are hidden. Empty path starts at home."""
        result = list_dirs(path or str(Path.home()))
        if result is None:
            return {"ok": False, "error": "not a readable directory"}
        return {"ok": True, **result}

    # ------------------------------------------------------------------ #
    #  Profile-scoped router (/api/p/{pid})                              #
    # ------------------------------------------------------------------ #

    p = APIRouter(prefix="/api/p/{pid}")

    def _available_providers() -> dict:
        """Which providers have a usable key right now — key-only. This is what the
        VOICE endpoints need (the realtime APIs always talk to the provider's own
        endpoint, so a base_url never makes a provider available). Assistant model
        availability is per-config now and lives in the named LLM configs store."""
        st = secrets.status()
        avail = {prov: st[prov]["set"] for prov in ("openai", "gemini", "anthropic")}
        avail["ollama"] = _ollama_installed()
        return avail

    def _ollama_installed() -> bool:
        try:
            return type(OllamaConfig).__module__ != "unittest.mock"
        except Exception:
            return False

    # ---- Chats ----

    @p.get("/chats")
    async def chats(runtime: ProfileRuntime = Depends(get_runtime)) -> dict:
        """List persisted, resumable conversations (newest first)."""
        return {"chats": await runtime.gateway.list_chats()}

    @p.get("/chats/{chat_id}")
    async def chat_transcript(chat_id: str, runtime: ProfileRuntime = Depends(get_runtime)) -> dict:
        """The display transcript for a chat, for the UI to restore."""
        return {
            "chat_id": chat_id,
            "messages": await runtime.gateway.transcript(chat_id),
        }

    @p.delete("/chats/{chat_id}")
    async def delete_chat(chat_id: str, runtime: ProfileRuntime = Depends(get_runtime)) -> dict:
        """Permanently delete a chat (transcript + full event log). Irreversible."""
        removed = await runtime.gateway.delete_chat(chat_id)
        if not removed:
            return Response(status_code=404)
        return {"ok": True}

    @p.patch("/chats/{chat_id}")
    async def update_chat(
        chat_id: str, patch: ChatPatch, runtime: ProfileRuntime = Depends(get_runtime)
    ) -> dict:
        """Rename and/or star a chat. 400 on an empty patch, 404 on unknown chat."""
        if patch.title is None and patch.starred is None:
            return JSONResponse({"error": "empty patch"}, status_code=400)
        ok = await runtime.gateway.update_chat(chat_id, title=patch.title, starred=patch.starred)
        if not ok:
            return Response(status_code=404)
        return {"ok": True}

    # ---- Message ----

    @p.post("/message", response_model=MessageResponse)
    async def message(
        req: MessageRequest, runtime: ProfileRuntime = Depends(get_runtime)
    ) -> MessageResponse:
        # Durable, inline HITL bound to this chat (answerable from the
        # thread or the strip); the request blocks until answered (or times out).
        asker = _chat_asker(runtime, req.chat_id)
        reply = await runtime.gateway.send_message(req.text, chat_id=req.chat_id, asker=asker)
        return MessageResponse(reply=reply, chat_id=req.chat_id)

    # ---- Tasks (config) + Runs (each run is a chat on stream task-run:<id>) ----

    @p.get("/tasks")
    async def list_tasks(runtime: ProfileRuntime = Depends(get_runtime)) -> dict:
        """Task rows for the drawer (needs-input first, then newest)."""
        return {"tasks": await runtime.tasks.list_tasks()}

    @p.post("/tasks")
    async def create_task(req: TaskCreate, runtime: ProfileRuntime = Depends(get_runtime)):
        """Create a task; empty ``name`` auto-generates one from the prompt
        (service-side). 422 with {error} on a bad schedule/model."""
        try:
            task = await runtime.tasks.create_task(
                name=req.name,
                prompt=req.prompt,
                model=req.model,
                schedule=req.schedule,
                description=req.description,
            )
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=422)
        return {"task": task}

    @p.get("/tasks/{task_id}")
    async def get_task(task_id: str, runtime: ProfileRuntime = Depends(get_runtime)):
        try:
            task = await runtime.tasks.get_task(task_id)
        except TaskStoreCorruptionError as exc:
            return JSONResponse({"error": str(exc)}, status_code=500)
        if task is None:
            return Response(status_code=404)
        return {"task": task}

    @p.patch("/tasks/{task_id}")
    async def update_task(
        task_id: str, req: TaskPatch, runtime: ProfileRuntime = Depends(get_runtime)
    ):
        """Edit any subset of task fields; model='' clears to the profile default."""
        patch = {k: v for k, v in req.model_dump().items() if v is not None}
        if req.model == "":  # explicit clear back to the profile default
            patch["model"] = None
        if not patch and req.model != "":
            return JSONResponse({"error": "empty patch"}, status_code=400)
        try:
            task = await runtime.tasks.update_task(task_id, **patch)
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=422)
        if task is None:
            return Response(status_code=404)
        return {"task": task}

    @p.delete("/tasks/{task_id}")
    async def delete_task(task_id: str, runtime: ProfileRuntime = Depends(get_runtime)):
        """Delete the task, its runs, and their chat streams. Irreversible."""
        if not await runtime.tasks.delete_task(task_id):
            return Response(status_code=404)
        return {"ok": True}

    @p.post("/tasks/{task_id}/run")
    async def run_task(task_id: str, runtime: ProfileRuntime = Depends(get_runtime)):
        """Run now — start a run immediately; the schedule is unchanged."""
        run = await runtime.tasks.start_run(task_id, trigger="manual")
        if run is None:
            return Response(status_code=404)
        return {"run": await runtime.tasks.get_run(run.id)}

    @p.get("/tasks/{task_id}/runs")
    async def list_runs(task_id: str, runtime: ProfileRuntime = Depends(get_runtime)):
        """The task's run history (newest first), as on the task page."""
        task = await runtime.tasks.get_task(task_id)
        if task is None:
            return Response(status_code=404)
        return {"runs": task["runs"]}

    @p.get("/tasks/{task_id}/permissions")
    async def task_permissions(task_id: str, runtime: ProfileRuntime = Depends(get_runtime)):
        """This task's own granted command rules — never the global set (mirrors
        ``GET /api/permissions``, scoped via ``task_id``). 404 on an unknown task."""
        if await runtime.tasks.get_task(task_id) is None:
            return Response(status_code=404)
        return {"rules": runtime.gateway.permissions.granted_commands(task_id=task_id)}

    @p.delete("/tasks/{task_id}/permissions")
    async def revoke_task_permission(
        task_id: str,
        req: PermissionCommandDeleteRequest,
        runtime: ProfileRuntime = Depends(get_runtime),
    ):
        """Revoke one of this task's own command rules by its canonical string
        (mirrors ``DELETE /api/permissions/commands``, scoped via ``task_id``).
        404 on an unknown task; an absent/already-revoked rule is a plain
        ``{"ok": false}`` — the task-scoped set is small enough that a client
        double-revoking isn't an error worth a 404."""
        if await runtime.tasks.get_task(task_id) is None:
            return Response(status_code=404)
        ok = runtime.gateway.permissions.revoke_command(req.rule, task_id=task_id)
        return {"ok": ok}

    @p.get("/runs/{run_id}")
    async def get_run(run_id: str, runtime: ProfileRuntime = Depends(get_runtime)):
        """One run's durable header (status/summary/task name) for the run page."""
        run = await runtime.tasks.get_run(run_id)
        if run is None:
            return Response(status_code=404)
        return {"run": run}

    @p.post("/runs/{run_id}/stop")
    async def stop_run(run_id: str, runtime: ProfileRuntime = Depends(get_runtime)) -> dict:
        """Stop a live run; whatever it already produced stays in its thread."""
        return {"ok": await runtime.tasks.stop_run(run_id)}

    @p.post("/runs/{run_id}/seen")
    async def run_seen(run_id: str, runtime: ProfileRuntime = Depends(get_runtime)) -> dict:
        """Mark a finished run opened (clears its unread highlight)."""
        return {"ok": await runtime.tasks.mark_run_seen(run_id)}

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
    async def voice_voices(
        config_id: str | None = None, runtime: ProfileRuntime = Depends(get_runtime)
    ) -> dict:
        """The voice catalogue + current selection. Scoped to a named live config when
        ``config_id`` is given (its provider + persisted voice); otherwise the profile's
        legacy voice-provider setting."""
        settings = _runtime_settings(runtime)
        entry = live_configs.get_config(config_id) if config_id else None
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
            if not live_configs.set_voice(req.config_id, req.voice):
                return Response(status_code=400)
        elif not _runtime_settings(runtime).set_voice(req.voice):
            return Response(status_code=400)
        return {"ok": True, "voice": req.voice}

    @p.post("/voice/preview")
    async def voice_preview(req: VoiceRequest, runtime: ProfileRuntime = Depends(get_runtime)):
        settings = _runtime_settings(runtime)
        entry = live_configs.get_config(req.config_id) if req.config_id else None
        provider = entry["provider"] if entry else None
        api_key = live_configs.resolve_key(entry) if entry else ""
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
        keys = secrets.status()
        return {
            "keys": keys,  # per-provider {set, hint} — never raw
            # Voice runs on the provider's own realtime endpoint, so a base_url
            # never makes it available — keys only.
            "voice_available": {prov: keys[prov]["set"] for prov in ("gemini", "openai")},
            # Display-only view of the resolved assistant model (the active named LLM
            # config, derived onto cfg.llm). Managed via /api/llm-configs, not here.
            "assistant": {"provider": cfg.llm.provider, "model": cfg.llm.model},
            "codex": codex_auth.status(),  # ChatGPT-subscription sign-in state
            "voice_provider": settings.voice_provider(),
            "mcp_servers": settings.list_mcp_servers(),
            "focuses": settings.get_focuses(),  # per-profile persona focus areas
            "reply_timeout_s": cfg.gateway.reply_timeout_s,
            "fs": {  # start roots for the folder picker
                "home": str(Path.home()),
                "cwd": str(Path.cwd()),
                "workspace": str(Path(cfg.workspace_dir).expanduser()),
            },
        }

    @p.get("/health")
    async def profile_health(runtime: ProfileRuntime = Depends(get_runtime)) -> dict:
        """Cheap, at-a-glance health of this profile's subsystems — the source for
        the UI's status dot. Presence/liveness signals ONLY: no MCP subprocess
        spawns, no provider pings, so it's cheap enough to poll on a short cycle.
        MCP servers are listed (config only) and probed on demand by the client via
        ``/settings/mcp/{name}/health``.

        ``overall`` rolls up the *core* signals: ``down`` if the agent isn't alive or
        the configured provider has no key (the agent can't run); ``warn`` if a
        channel bound to this profile failed to start; else ``ok``. Google and the
        scheduler are informational and never move ``overall``.
        """
        checks: list[dict] = []

        # Assistant agent — liveness (agent object built + not closed).
        gw = runtime.gateway.status() if runtime.gateway is not None else {"status": "stopped"}
        agent_ok = gw.get("status") == "ok"
        checks.append(
            {
                "id": "agent",
                "label": "Assistant",
                "state": "ok" if agent_ok else "down",
                "detail": f"model {gw.get('model')}" if agent_ok else "not running",
            }
        )

        # LLM provider — the active named config must be usable (per-config key, a
        # base_url compat server, Ollama, or the provider's env key). When the store is
        # empty we fall back to the flat provider's key check (fresh install / CLI).
        entry = llm_configs.active_config()
        if entry is not None:
            key_set = llm_configs.usable(entry)
            detail = f"{entry['name']} · {entry['model']}"
        else:
            provider = runtime.config.llm.provider
            key_set = _available_providers().get(provider, False)
            detail = f"{provider} · {'key set' if key_set else 'no key'}"
        checks.append(
            {
                "id": "provider",
                "label": "LLM key",
                "state": "ok" if key_set else "down",
                "detail": detail,
            }
        )

        # MCP servers — config only; the client probes each on panel open.
        mcp_servers = _runtime_settings(runtime).list_mcp_servers()
        enabled = [s for s in mcp_servers if s.get("enabled", True)]
        checks.append(
            {
                "id": "mcp",
                "label": "MCP servers",
                "state": "ok" if enabled else "off",
                "detail": (
                    f"{len(enabled)} configured"
                    if enabled
                    else ("all disabled" if mcp_servers else "none configured")
                ),
                "servers": [
                    {"name": s["name"], "enabled": s.get("enabled", True)} for s in mcp_servers
                ],
            }
        )

        # Messaging channels bound to THIS profile (start-time active/error).
        items = []
        for platform, bound_pid in profiles_mod.channel_bindings().items():
            if bound_pid != runtime.pid:
                continue
            entry = _channel_entry(platform, bound_pid)
            items.append(
                {
                    "platform": platform,
                    "active": entry["active"],
                    "error": entry["error"],
                    "token_present": entry["token_present"],
                }
            )
        ch_error = any(it["error"] for it in items)
        checks.append(
            {
                "id": "channels",
                "label": "Messaging",
                "state": "off" if not items else ("warn" if ch_error else "ok"),
                # Surface the ACTUAL failure reason (e.g. "Improper token…"), not a
                # generic "error" — the panel shows this, so it must say what to fix.
                "detail": (
                    ", ".join(
                        (it["error"] or f"{it['platform']} active")
                        if (it["error"] or it["active"])
                        else f"{it['platform']} idle"
                        for it in items
                    )
                    or "none bound"
                ),
                "items": items,
            }
        )

        # Google — informational (file-presence: configured / signed in).
        signed_in = google_auth.has_token()
        email = google_auth.account_email()
        checks.append(
            {
                "id": "google",
                "label": "Google",
                "state": "ok" if signed_in else "off",
                "detail": (
                    (f"signed in as {email}" if email else "signed in")
                    if signed_in
                    else (
                        "configured — not signed in"
                        if google_auth.is_configured()
                        else "not connected"
                    )
                ),
            }
        )

        # Task scheduler — informational; single-leader across processes.
        sched_running = bool(getattr(runtime.tasks, "scheduler_running", False))
        checks.append(
            {
                "id": "scheduler",
                "label": "Task scheduler",
                "state": "ok",
                "detail": "running" if sched_running else "running in another process",
            }
        )

        core_down = any(c["state"] == "down" for c in checks if c["id"] in ("agent", "provider"))
        core_warn = any(c["state"] == "warn" for c in checks if c["id"] == "channels")
        overall = "down" if core_down else ("warn" if core_warn else "ok")
        return {"overall": overall, "checks": checks}

    async def _mcp_health(server: dict) -> dict:
        tools = build_mcp_tools([server])
        if not tools:
            return {"ok": False, "error": "MCP server is disabled"}
        toolkit = tools[0]
        context = ConversationContext(stream=MemoryStream())
        try:
            schemas = await toolkit.schemas(context)
        finally:
            # This throwaway toolkit's persistent session would otherwise hold the
            # server process alive until idle expiry.
            await toolkit.aclose()
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

    # ---- Memory: view + edit THIS profile's persona memory (profile.db) ----
    # (The shared universal "who the user is" doc is the global GET/POST /api/memory.)

    @p.get("/memory")
    async def get_memory(runtime: ProfileRuntime = Depends(get_runtime)) -> dict:
        return {"text": await read_profile(runtime.config.data_dir / "profile.db")}

    @p.post("/memory")
    async def set_memory(
        req: MemoryRequest, runtime: ProfileRuntime = Depends(get_runtime)
    ) -> dict:
        await write_profile(req.text, runtime.config.data_dir / "profile.db")
        return {"ok": True}

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
        """Event-stream transport: the client receives the chat's events as
        `{event:{type,data}}` — replayed on connect, then live — and sends `{text}`
        turns. Closes with 4001 if the profile is archived mid-session (§4.9)."""
        if not _origin_ok(websocket.headers.get("origin"), websocket.headers.get("host")):
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
                asker = _chat_asker(runtime, chat_id)
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
