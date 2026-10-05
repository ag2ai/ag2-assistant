"""Gateway core — chat management over the AG2 Assistant agent.

The Gateway exposes a simple `send_message(text, chat_id)` surface that any
facade (REST/WebSocket/channel) can call. Each chat is a persistent AG2
`Stream` keyed by `chat_id`: its event history carries the multi-turn
conversation, and after every turn the events are written to disk via AG2's
`EventLogWriter`. On restart (or a new connection to an existing chat) the
events are reloaded into a fresh stream, so conversations are **resumable** — the
agent keeps full context, not just a text transcript. A lightweight display
transcript is stored alongside so UIs can render the history.

One shared `Agent` backs all chats; isolation comes from the per-chat
stream, never crossing histories.
"""

import asyncio
import contextlib
import copy
import json
import logging
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from urllib.parse import quote

from ag2 import Agent
from ag2.a2ui import A2UIMessageEvent
from ag2.agent import AgentRun
from ag2.context import ConversationContext
from ag2.events import (
    ModelMessageChunk,
    ModelRequest,
    ModelResponse,
    UsageEvent,
)
from ag2.knowledge import SqliteKnowledgeStore
from ag2.knowledge.constants import LOG_PREFIX
from ag2.knowledge.log import EventLogWriter
from ag2.plugin import Plugin
from ag2.stream import MemoryStream
from ag2.tools import LocalEnvironment

from assistant import onboarding
from assistant import title as title_mod
from assistant.a2ui import (
    CardCatalog,
    durable_surfaces_from_messages,
    tolerant_a2ui_middleware,
)
from assistant.a2ui_skill import a2ui_available
from assistant.agent import (
    ACP_PROVIDERS,
    BEHAVIOR_GUIDANCE,
    RefreshingModelConfig,
    _build_middleware,
    build_skills_install_tools,
    build_skills_plugin,
    build_skills_runtime,
    cheap_model,
    create_agent,
    model_config,
    universal_turn_prompt,
)
from assistant.codex_auth import CodexAuth, CodexAuthError
from assistant.coding.detect import parse_bridge
from assistant.config import Config, load_config
from assistant.events import TurnCancelled, TurnFailed
from assistant.folders import FolderStore
from assistant.gateway.repair import repair_stream_history, wait_reply
from assistant.gateway.tasks_service import TaskService
from assistant.gateway.wire import is_binary_event
from assistant.hitl import Asker, build_hitl_hook
from assistant.integrations.google_auth import GoogleAuth
from assistant.llm_configs import LlmConfigStore
from assistant.memory import build_profile_store
from assistant.observability import (
    capture_failure,
    log_suppressed,
    setup_logging,
)
from assistant.peers import PeerStore
from assistant.permissions import PermissionManager, PermissionStore
from assistant.resources import ProfileResources, ResourceLease, SharedEnvironment, fingerprint
from assistant.secrets import KEY_ENV, OLLAMA_BASE_ENV, SecretStore
from assistant.self_tools import build_self_tools
from assistant.settings import profile_settings
from assistant.storage import SerialStore
from assistant.system_tools import build_system_tools
from assistant.tools import build_agent_tools, docker_environment
from assistant.tools.mcp import build_mcp_tools
from assistant.usage import UsageLedger
from assistant.voice import build_voice_agent

_TRANSCRIPT_PREFIX = "/transcript/"

logger = logging.getLogger("ag2assistant.gateway")

# Bounds for the on-demand "Mentioned in N threads" scan (ADR 0014): a huge chat
# history can't stall the request (streams examined) nor flood the popover (rows
# returned). Truncation is logged, never silent.
_MENTIONS_STREAM_CAP = 2000
_MENTIONS_RESULT_CAP = 50
# An event-log segment file is ``{stream_id}.jsonl`` or ``{stream_id}.dropped-N.jsonl``.
_DROPPED_SEGMENT_RE = re.compile(r"\.dropped-\d+$")


def _log_stream_id(entry: str) -> str:
    """The base stream id behind an event-log filename (``LOG_PREFIX`` entry): strip
    the ``.jsonl`` suffix and any ``.dropped-N`` segment marker
    (``task-run:r1.dropped-2.jsonl`` → ``task-run:r1``). Non-log entries → ""."""
    if not entry.endswith(".jsonl"):
        return ""
    return _DROPPED_SEGMENT_RE.sub("", entry[: -len(".jsonl")])


def _task_summary(t: dict) -> str:
    """Concise voice-grounding snapshot of a task's current state (TaskService v2's
    dict shape: name/prompt/model/schedule_desc/paused/runs) — mirrors what the
    get_task system tool reports, for the voice agent's ambient task_context."""
    lines = [
        f"{t['id']} · {t['name']}" + (" · PAUSED" if t["paused"] else ""),
        f"prompt: {t['prompt']}",
        f"schedule: {t['schedule_desc']}",
    ]
    last = t.get("last_run")
    if last:
        lines.append(f"last run: {last['status']} · {last['summary'] or last['error']}")
    return "\n".join(lines)


def _conversation_events() -> tuple:
    """Event types a voice client renders itself as spoken transcript (so they are
    NOT re-forwarded as structured events during voice delegation). Imported lazily
    so a missing optional event type can't break module import."""
    return (ModelRequest, ModelMessageChunk, ModelResponse)


_CONVERSATION_EVENTS = _conversation_events()


def _failure_text(exc: BaseException) -> str:
    """A short, user-facing reason a turn ended in an error.

    The traceback belongs in the debug record (``capture_failure``), not the chat —
    this is only what the thread shows, so it stays one plain sentence.
    """
    if isinstance(exc, asyncio.TimeoutError):
        return "The turn timed out before it finished."
    detail = " ".join(str(exc).split())[:200]  # collapse multi-line provider blobs
    return f"The turn failed: {detail}" if detail else "The turn failed unexpectedly."


def is_internal_stream(chat_id: str) -> bool:
    """Streams that are a thread of something else (a task run) — they render on
    their own page and must not appear in the Chats list. ``task:`` covers legacy
    records from the pre-redesign model."""
    return chat_id.startswith("task-run:") or chat_id.startswith("task:")


@dataclass
class _ActiveTurn:
    """A Chat-lock holder preparing or running a Turn, with its cancellation reason.
    The task drives preparation until the AG2 run's result task takes over."""

    run: AgentRun | None
    task: "asyncio.Task"
    cancelled: bool = False
    reason: str = "Stopped"


@dataclass
class _AgentGeneration:
    agent: Agent
    resources: ResourceLease

    async def aclose(self) -> None:
        await self.resources.aclose()


class Gateway:
    """Manages per-chat, resumable conversations with the AG2 Assistant agent."""

    def __init__(
        self,
        config: Config | None = None,
        memory: bool = True,
        platform: str = "gateway",
        onboard: bool = True,
        persist: bool = True,
        task_service=None,
        config_factory: Callable[[], Config] | None = None,
        agent_factory: Callable | None = None,
        title_factory: Callable | None = None,
        model_factory: Callable | None = None,
        environment_factory: Callable | None = None,
        subscription_auth: CodexAuth | None = None,
        google: GoogleAuth | None = None,
    ) -> None:
        self._config = config or load_config()
        self._memory = memory
        self._platform = platform
        self._onboard = onboard
        self._persist = persist
        self._tasks = task_service  # gives the universal agent its system tools
        # How the turn agent and the one-shot chat titler are built. Injected so a
        # caller (or a test) decides what an agent is; both default to production.
        self._agent_factory = agent_factory or create_agent
        self._model_factory = model_factory
        self._environment_factory = environment_factory or docker_environment
        self._subscription_auth = subscription_auth or CodexAuth(self._config.paths)
        self._google = google or GoogleAuth(self._config.paths)
        self._resources = ProfileResources()
        self._title_factory = title_factory or title_mod.default_titler
        # Profile runtimes re-read registry settings at the actual Turn boundary.
        self._config_factory = config_factory or (lambda: self._config)
        # This profile's Cards and the A2UI runtime over them; both re-read from
        # the three Card directories whenever one of their files changes.
        self._catalog = CardCatalog(self._config)
        self._onboarding_done = False
        self._agent: Agent | None = None
        self._knowledge_store: SqliteKnowledgeStore | None = None
        self._acp_generation: _AgentGeneration | None = None
        self._preparation_lock = asyncio.Lock()
        self._permissions: PermissionStore | None = None
        self._folders: FolderStore | None = None
        self._event_store: SerialStore | None = None
        self._writer: EventLogWriter | None = None
        # async (chat_id, user_text, reply, origin=…) -> None, called once a turn
        # completes: the router pushes it to the Peer Attached to that chat.
        self._mirror: Callable | None = None
        # chat_id -> live Stream; plus the log text each one was last read from or written as
        self._streams: dict[str, MemoryStream] = {}
        self._synced: dict[str, str | None] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        # chat_id -> the turn currently running on it (feed_message / cancel_turn)
        self._active: dict[str, _ActiveTurn] = {}
        self._invocations: set[asyncio.Task] = set()
        self._background: set[asyncio.Task] = set()
        self._closing = False
        # Per-profile daily token/cost tally for the activity HUD.
        self._usage = UsageLedger(
            self._config.data_dir / "usage.json",
            pricing_path=self._config.paths.root / "pricing.json",
        )

    def set_mirror(self, mirror) -> None:
        """Register who receives this gateway's completed turns (ADR 0020)."""
        self._mirror = mirror

    def set_question_mirror(self, questions) -> None:
        """Register who receives this gateway's questions — ``ask``/``retract``. The
        durable inquiry store announces them, so the task service holds the hook."""
        if self._tasks is not None:
            self._tasks.set_question_mirror(questions)

    async def answer_inquiry(
        self, inquiry: str, text: str = "", *, option: int | None = None
    ) -> bool:
        """Resolve one of this profile's persisted inquiries — by the index of a tapped
        option, or by replied text. False when there is none left to resolve."""
        store = getattr(self._tasks, "inquiries", None) if self._tasks is not None else None
        if store is None:
            return False
        inq = await store.get(inquiry)
        if inq is None or inq.is_terminal:
            return False
        if option is not None:
            if not 0 <= option < len(inq.options):
                return False
            text = inq.options[option]
        return await store.answer(inquiry, text) is not None

    @property
    def config(self) -> Config:
        """The configuration resolved by the latest preparation or runtime refresh."""
        return self._config

    @property
    def catalog(self) -> CardCatalog:
        """This profile's Card catalog, over its three Card directories."""
        return self._catalog

    @property
    def folders(self):
        """This install's Folder/Grant store (the same instance the turn-level
        ``PermissionManager`` resolves against) — so the ``@``-picker's search can
        honor exactly the access the agent's own reads would (ADR 0006/0012)."""
        return self._folders

    @property
    def permissions(self):
        """Install-wide PermissionStore — task routes/service read task-scoped rules."""
        return self._permissions

    def usage_today(self) -> dict:
        """Today's token + estimated-cost totals (for the cost & activity HUD)."""
        return self._usage.today()

    def require_agent(self) -> Agent:
        """This profile's default agent, for a caller running outside a turn.
        Set between ``start()`` and ``close()``, which is the whole of a live runtime."""
        if self._agent is None:
            raise RuntimeError("Gateway not started")
        return self._agent

    async def acp_agent(self) -> Agent:
        """The separately owned, constructor-complete Agent for deferred ACP serving."""
        if self._acp_generation is None:
            lease = self._resources.lease()
            try:
                async with self._preparation_lock:
                    if self._acp_generation is not None:
                        await lease.aclose()
                        return self._acp_generation.agent
                    config, _, extra, runtime = await self._prepare_turn(lease, "acp-listener")
                    middleware = _build_middleware(config)
                    if runtime is not None:
                        middleware.extend(runtime.middleware_factories())
                    agent = self._agent_factory(
                        config,
                        memory=self._memory,
                        platform=self._platform,
                        knowledge_store=self._knowledge_store,
                        compact=self._memory,
                        invocation_only=True,
                        extra_tools=extra["tools"],
                        extra_plugins=[*extra["plugins"], Plugin(middleware=middleware)],
                        default_model=extra["config"],
                        model_factory=lambda cfg, model=None: self._held_model(cfg, lease, model),
                    )
                    self._acp_generation = _AgentGeneration(agent, lease)
            except BaseException:
                await lease.aclose()
                raise
        return self._acp_generation.agent

    def _system_tools(self, cfg: Config) -> list:
        if self._tasks is None:
            return []
        settings = profile_settings(cfg.data_dir, voice_provider=cfg.voice_provider)
        return build_system_tools(
            self._tasks,
            settings,
            chats=self,
            platform=self._platform,
            peers=PeerStore(cfg.paths),
        )

    def _model_key(self, cfg: Config, model: str | None = None) -> str:
        llm = cfg.llm.model_dump(
            exclude={
                "builtin_tools",
                "aggregate_model",
                "call_timeout_s",
                "call_retries",
                "silence_alert_s",
                "silence_halt_s",
            }
        )
        llm["model"] = model or cfg.llm.model
        llm["provider_options"] = cfg.llm.provider_options.get(cfg.llm.provider, {})
        credential_names = (
            []
            if cfg.llm.provider in ACP_PROVIDERS or cfg.llm.auth_mode == "subscription"
            else [OLLAMA_BASE_ENV]
            if cfg.llm.provider == "ollama"
            else [KEY_ENV.get(cfg.llm.provider, cfg.llm.api_key_env)]
        )
        return fingerprint(
            [
                llm,
                {key: cfg.secret_env[key] for key in credential_names if key in cfg.secret_env},
                cfg.workspace_dir,
                cfg.search_path,
                cfg.acp_bridge,
                cfg.acp_bridge_token,
            ]
        )

    def _held_model(self, cfg: Config, lease: ResourceLease, model: str | None = None):
        factory = self._model_factory or model_config
        if cfg.llm.provider in ACP_PROVIDERS:
            return lease.acquire("model", self._model_key(cfg, model), lambda: factory(cfg, model))
        return factory(cfg, model)

    def _constructor_key(self, cfg: Config) -> str:
        background = self._model_key(cfg, cheap_model(cfg)) if self._memory else None
        return fingerprint(
            [
                cfg.agent.name,
                cfg.memory.model_dump() if self._memory else None,
                background,
                cfg.llm.silence_alert_s,
                cfg.llm.silence_halt_s,
            ]
        )

    async def _make_agent(self, cfg: Config):
        held = self._resources.lease()
        try:
            agent = self._agent_factory(
                cfg,
                memory=self._memory,
                platform=self._platform,
                knowledge_store=self._knowledge_store,
                compact=self._memory,
                invocation_only=True,
                model_factory=lambda config, model=None: (
                    RefreshingModelConfig(config, self._model_factory or model_config, model)
                    if config.llm.auth_mode == "subscription"
                    else self._held_model(config, held, model)
                ),
            )
        except BaseException:
            await held.aclose()
            raise
        return _AgentGeneration(agent, held)

    async def _select_agent(self, cfg: Config, lease: ResourceLease):
        key = self._constructor_key(cfg)
        generation = await lease.acquire_async(
            "agent", key, lambda: self._make_agent(copy.deepcopy(cfg))
        )
        self._agent = generation.agent
        await self._resources.reconcile("agent", [key])
        return generation.agent

    async def _resolve_turn_model(
        self, chat_id: str, llm_config_id: str | None, chat_model: str = ""
    ) -> str | None:
        """The shared model configuration this turn runs on: env pin > ``llm_config_id``
        > Chat override > the Task's model (a Run's thread only) > None, the Active.

        ``chat_model`` is a client's selection for a Chat that does not exist yet, and
        applies only to the turn that creates it (ADR 0025)."""
        if self._config.llm.env_pinned:
            return None
        if llm_config_id:
            return llm_config_id
        doc = await self._read_chat_doc(chat_id, "chat model read")
        chosen = doc.get("model") or (chat_model.strip() if not doc else "")
        # A dangling override — its configuration deleted — degrades to the layer
        # directly beneath it. One that exists but cannot run is not dangling.
        override = LlmConfigStore(self._config.paths).resolved_override(chosen)
        return override or await self._task_model_for_stream(chat_id) or None

    async def _task_model_for_stream(self, chat_id: str) -> str:
        """The model the Task behind a run stream chose — '' for any other stream, or
        when the run/task is gone or the task names no model of its own."""
        task_id = await self._task_for_stream(chat_id)
        if not task_id or self._tasks is None:
            return ""
        try:
            task = await self._tasks.get_task(task_id)
        except Exception as exc:
            log_suppressed("run thread task model lookup", exc, chat_id=chat_id)
            return ""
        return (task or {}).get("model") or ""

    def _active_model_id(self) -> str:
        """The configuration id Active for this profile: its Active override when that
        resolves, else the install-wide Active. '' when neither does."""
        return LlmConfigStore(self._config.paths).effective_active_id(
            profile_settings(self._config.data_dir).get_llm_override()
        )

    async def effective_model(self, chat_id: str) -> str:
        """What a message sent to this chat right now would run on, so a client can
        render it without resolving the chain: a configuration id, or the pinned model
        name under an env pin. '' when neither governs the turn."""
        if self._config.llm.env_pinned:
            return self._config.llm.model
        resolved = await self._resolve_turn_model(chat_id, None)
        store = LlmConfigStore(self._config.paths)
        return store.resolved_override(resolved) or self._active_model_id()

    async def start(self) -> None:
        """Create the shared agent and (optionally) the on-disk chat store."""
        # One-shot legacy -> Secret-entity upgrade (idempotent). Provider keys reach
        # the agent through ``config.secret_env``, resolved with the config.
        SecretStore(self._config.paths).migrate()
        setup_logging(self._config)  # rolling log + failure capture for debugging

        if self._memory:
            self._knowledge_store = self._resources.acquire(
                "memory",
                "memory",
                lambda: build_profile_store(self._config.data_dir / "profile.db"),
            ).value
        async with self._resources.lease() as lease:
            await self._select_agent(self._config, lease)
        # Install-wide persistent grant store (config.root_dir holds global files) —
        # grants are global, not per-profile.
        self._permissions = PermissionStore(self._config.root_dir / "permissions.json")

        # Install-wide Folder registry (ADR 0006); Grants are per-profile/per-chat,
        # resolved at check time with this profile's id + the turn's chat_id.
        self._folders = FolderStore(self._config.root_dir / "folders.json")

        if self._persist:
            self._config.data_dir.mkdir(parents=True, exist_ok=True)
            self._event_store = SerialStore(
                self._resources.acquire(
                    "history",
                    "history",
                    lambda: SqliteKnowledgeStore(str(self._config.data_dir / "chats.db")),
                ).value
            )
            self._writer = EventLogWriter(self._event_store)

    async def refresh(self) -> None:
        """Refresh runtime configuration and retire obsolete owned resources."""
        async with self._preparation_lock:
            self._config = self._config_factory()
            await self._reconcile_resources(self._config)
        if self._tasks is not None and hasattr(self._tasks, "refresh"):
            await self._tasks.refresh()

    def _chat_lock(self, chat_id: str) -> asyncio.Lock:
        lock = self._locks.get(chat_id)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[chat_id] = lock
        return lock

    async def stream_for(self, chat_id: str):
        """The chat's live, persisted, resumable event stream — the source the
        event bridge replays and subscribes to. Same cached object send_message
        uses, so events from a turn are caught by the bridge's subscription."""
        return await self._get_stream(chat_id)

    def is_running(self, chat_id: str = "default") -> bool:
        """Whether a turn is in flight on this chat — what a message sent now would be
        fed into rather than starting a second one."""
        active = self._active.get(chat_id)
        return active is not None and not active.task.done()

    async def feed_message(self, text: str, chat_id: str = "default", attachments=None) -> bool:
        """Feed a message into the turn already running on this chat.

        AG2's ``AgentRun.enqueue`` appends to the turn's inbox; the agent loop drains
        it before its next model call (and once more when the model has nothing left
        to do), so the *running* turn sees the message and can change course — no
        second turn, no waiting for the first to finish. The drained message is
        re-emitted on the stream, so it shows in the thread and persists like any
        other user message.

        Returns False when nothing is in flight, so the caller runs it as a new turn.
        """
        active = self._active.get(chat_id)
        if active is None or active.run is None or active.task.done():
            return False
        active.run.enqueue(text, *(attachments or []))
        # The turn may have finished between the check and the enqueue, which would
        # leave the message sitting in the stream inbox until some later turn drained
        # it. Take it back and let the caller run it as a fresh turn instead.
        stream = await self._get_stream(chat_id)
        if active.task.done() and stream.pending_messages:
            stream.pending_messages.clear()
            return False
        return True

    async def cancel_turn(self, chat_id: str = "default", reason: str = "Stopped") -> bool:
        """Stop preparation or execution on this Chat, preserving emitted work.
        Return False when no Turn is in flight."""
        active = self._active.get(chat_id)
        if active is None or active.task.done():
            return False
        active.cancelled = True
        active.reason = reason
        active.task.cancel()
        return True

    async def emit_event(self, chat_id: str, event) -> None:
        """Emit an event onto a chat's stream from outside an agent turn (the
        pattern AG2's own SoundDeviceRecorder uses). It reaches any live bridge
        subscriber and is persisted so it survives reload. Best-effort."""
        stream = await self.stream_for(chat_id)
        try:
            await ConversationContext(stream=stream).send(event)
        except Exception as exc:
            log_suppressed("external stream event emit", exc, chat_id=chat_id)
            return
        if self._writer is not None:
            try:
                await self._persist_log(chat_id, stream)
            except Exception as exc:
                log_suppressed("external stream event persist", exc, chat_id=chat_id)
                # Persistence is best-effort; the live event still went out.

    async def _get_stream(self, chat_id: str):
        """Return the chat's live Stream, re-read from disk whenever no turn is running
        here and its log differs from what this process last read or wrote."""
        # AG2 declares ``StreamId = uuid.UUID`` and only interpolates it into a log path;
        # our ids are strings that name those files, so the ignores below have no fix here.
        stream = self._streams.get(chat_id)
        if stream is None:
            stream = MemoryStream(id=chat_id)  # type: ignore[arg-type]
            self._streams[chat_id] = stream
        if self._writer is not None and chat_id not in self._active:
            raw = None
            try:
                raw = await self._read_log(chat_id)
                if chat_id not in self._synced or self._synced[chat_id] != raw:
                    events = await self._writer.load(chat_id)  # type: ignore[arg-type]
                    await stream.history.replace(events)
            except Exception as exc:
                log_suppressed("chat stream hydrate", exc, chat_id=chat_id)
                # A corrupt log keeps whatever the stream already holds.
            self._synced[chat_id] = raw
        return stream

    async def _read_log(self, chat_id: str) -> str | None:
        """The chat's raw event log as stored, or None when it has none."""
        assert self._event_store is not None
        return await self._event_store.read(f"{LOG_PREFIX}{chat_id}.jsonl")

    async def _persist_log(self, chat_id: str, stream) -> None:
        """Write the stream's events as the chat's log and remember the stored text."""
        assert self._writer is not None
        await self._writer.persist(chat_id, list(await stream.history.get_events()))  # type: ignore[arg-type]
        self._synced[chat_id] = await self._read_log(chat_id)

    async def _refresh_subscription(self, config: Config) -> None:
        if config.llm.provider == "openai" and config.llm.auth_mode == "subscription":
            auth = self._subscription_auth
            try:
                await asyncio.to_thread(auth.ensure_fresh)
            except CodexAuthError:
                pass

    async def _reconcile_resources(self, config: Config) -> None:
        servers = profile_settings(config.data_dir).list_mcp_servers(include_env=True)
        await self._resources.reconcile(
            "mcp", [fingerprint(server) for server in servers if server.get("enabled", True)]
        )
        docker_key = fingerprint([config.tools.docker_image, config.tools.docker_network])
        await self._resources.reconcile(
            "docker", [docker_key] if config.tools.sandbox == "docker" else []
        )
        configs = [config]
        store = LlmConfigStore(config.paths)
        for entry in store.list_configs():
            current = copy.deepcopy(config)
            store.derive_onto(current, entry)
            configs.append(current)
        model_keys = [
            self._model_key(current) for current in configs if current.llm.provider in ACP_PROVIDERS
        ]
        if self._memory and config.llm.provider in ACP_PROVIDERS:
            model_keys.append(self._model_key(config, cheap_model(config)))
        await self._resources.reconcile("model", model_keys)

    async def _prepare_turn(
        self,
        lease: ResourceLease,
        chat_id: str,
        llm_config_id: str | None = None,
        chat_model: str = "",
        asker=None,
        task_id=None,
        surface: str = "",
    ):
        self._config = self._config_factory()
        profile_config = copy.deepcopy(self._config)
        effective = copy.deepcopy(self._config)
        selected = await self._resolve_turn_model(chat_id, llm_config_id, chat_model)
        if selected:
            store = LlmConfigStore(effective.paths)
            entry = store.get_config(selected)
            if entry is not None:
                store.derive_onto(effective, entry)
        await self._refresh_subscription(effective)
        await self._refresh_subscription(profile_config)
        await self._reconcile_resources(profile_config)
        agent = await self._select_agent(profile_config, lease)
        task_id = task_id or await self._task_for_stream(chat_id)
        extra = self._ask_kwargs(asker, chat_id, task_id or "", config=effective)
        docker_key = fingerprint([effective.tools.docker_image, effective.tools.docker_network])

        def mcp_factory(servers):
            return [
                lease.acquire("mcp", fingerprint(server), lambda: build_mcp_tools([server])[0])
                for server in servers
                if server.get("enabled", True)
            ]

        def environment_factory(**kwargs):
            return lease.acquire(
                "docker", docker_key, lambda: SharedEnvironment(self._environment_factory(**kwargs))
            )

        def local_environment_factory():
            return lease.acquire("local", "local", lambda: SharedEnvironment(LocalEnvironment()))

        tools = build_agent_tools(
            effective.llm.config_type,
            sandbox=effective.tools.sandbox,
            docker_image=effective.tools.docker_image,
            docker_network=effective.tools.docker_network,
            workspace_dir=effective.workspace_dir,
            config=effective,
            builtin=effective.llm.builtin_tools,
            mcp_factory=mcp_factory,
            environment_factory=environment_factory,
            local_environment_factory=local_environment_factory,
            google=self._google,
        )
        settings = profile_settings(effective.data_dir, voice_provider=effective.voice_provider)
        tools.extend(build_self_tools(effective, settings))
        tools.extend(self._system_tools(effective))
        skill_runtime = build_skills_runtime(effective)
        tools.extend(build_skills_install_tools(effective, build_skills_runtime(effective)))
        held_cards = self._catalog.hold()
        plugins = [build_skills_plugin(effective, skill_runtime, catalog=held_cards, snapshot=True)]
        guidance = [
            effective.agent.system_prompt,
            *universal_turn_prompt(
                effective,
                surface,
                google=any(
                    tool.name in {"gmail_search", "calendar_list_events", "drive_search"}
                    for tool in tools
                ),
            ),
        ]
        if BEHAVIOR_GUIDANCE in agent.system_prompt:
            guidance = [part for part in guidance if part != BEHAVIOR_GUIDANCE]
        a2ui_runtime = held_cards.runtime() if a2ui_available(effective) else None
        if a2ui_runtime is not None:
            guidance.append(a2ui_runtime.capabilities_prompt(None))
        plugins.append(Plugin(prompt=guidance))
        extra["config"] = self._held_model(effective, lease)
        extra["tools"] = tools
        extra["plugins"] = plugins
        return effective, agent, extra, a2ui_runtime

    async def send_message(
        self,
        text: str,
        chat_id: str = "default",
        asker=None,
        attachments: list | None = None,
        surface: str = "",
        on_event=None,
        llm_config_id: str | None = None,
        task_id: str | None = None,
        origin: str = "",
        attachment_names: tuple[str, ...] = (),
        chat_model: str = "",
    ) -> str:
        """Run a message with Profile shutdown and invocation scope ownership."""
        if self._closing:
            raise RuntimeError("Gateway closing")
        task = asyncio.current_task()
        if task is not None:
            self._invocations.add(task)
        try:
            return await self._send_message(
                text,
                chat_id,
                asker,
                attachments,
                surface,
                on_event,
                llm_config_id,
                task_id,
                origin,
                attachment_names,
                chat_model,
            )
        finally:
            if task is not None:
                self._invocations.discard(task)

    async def _send_message(
        self,
        text: str,
        chat_id: str = "default",
        asker=None,
        attachments: list | None = None,
        surface: str = "",
        on_event=None,
        llm_config_id: str | None = None,
        task_id: str | None = None,
        origin: str = "",
        attachment_names: tuple[str, ...] = (),
        chat_model: str = "",
    ) -> str:
        """Send a user message to the universal agent and return its reply.

        Each chat_id keeps its own persistent, resumable history (a web chat, a
        task's page, a channel — all the same agent, different streams). `surface`
        is a short paragraph describing where the user is asking (and any local
        state, e.g. a task snapshot) so the one agent has the right context.

        `asker` binds human-in-the-loop questions/permission prompts to the
        surface that made the request. `attachments` are AG2 multimodal `Input`s.
        `on_event` is an optional async callback ``(event) -> None`` that receives
        the agent's structured events (tool calls, task cards, deliverables, …) raw
        as they're emitted — the voice channel forwards them so its client folds
        them with the same reducer the text path uses. Conversation/audio events are
        omitted (voice renders those itself). `llm_config_id` names a task's chosen model
        outright, and outranks the Chat override in `_resolve_turn_model` (ADR 0025).
        `task_id`, when this turn is a task run, scopes any command grant
        the turn mints via "always allow" to that task (survives its future runs)
        instead of persisting it globally; when omitted it is auto-resolved from
        `chat_id` for a run's thread (``task-run:{run_id}``), so a manual reply
        typed there is scoped the same as the run itself, and this also feeds
        folder-grant resolution for that turn. `origin` names the Peer this message
        was written from, when a Channel wrote it — the mirror never sends a Peer its
        own turn back (ADR 0020). `attachment_names` are what those attachments are
        called, so the mirror can name a file instead of carrying it. `chat_model` becomes
        the override of the Chat this message creates, and is ignored on one that exists.
        """
        if self._agent is None:
            raise RuntimeError("Gateway not started")

        await self._maybe_onboard(asker)
        msg = [text, *(attachments or [])]

        async with self._chat_lock(chat_id):
            stream = await self._get_stream(chat_id)
            # Heal a history whose last turn died between a tool call and its
            # result (timeout/crash) — left as is, the dangling call makes the
            # provider reject every later turn of this chat.
            await repair_stream_history(stream, chat_id)
            # Persist a transcript stub the instant we accept the message, so the
            # chat shows up in list_chats() *during* a long agentic turn — not
            # only after it completes. Without this, a chat in flight lives solely in
            # the web page's local state and vanishes on a profile switch (full-page
            # nav). The completed-turn write below stays the authority (§_persist_turn).
            await self._ensure_transcript_stub(chat_id, text, chat_model)
            async with self._resources.lease() as lease:
                invocation = asyncio.current_task()
                assert invocation is not None
                active = _ActiveTurn(None, invocation)
                self._active[chat_id] = active
                a2ui_handle = None
                effective = self._config
                usage_handle = self._watch_usage(stream)
                hitl_pending = getattr(asker, "has_pending", None)
                try:
                    async with self._preparation_lock:
                        effective, agent, extra, a2ui_runtime = await self._prepare_turn(
                            lease, chat_id, llm_config_id, chat_model, asker, task_id, surface
                        )
                    a2ui_handle = None
                    if a2ui_runtime is not None:
                        # Append a fallback that recovers surfaces when the model omits the
                        # <a2ui-json> wrapper (fires only when the runtime's own extraction
                        # can't — the two are mutually exclusive per response).
                        middleware = (
                            *a2ui_runtime.middleware_factories(),
                            tolerant_a2ui_middleware(a2ui_runtime.parser, a2ui_runtime.cards),
                        )
                        a2ui_handle = self._watch_a2ui(stream)
                    else:
                        middleware = ()
                    # `run` is `ask` with the turn left observable (`ask` is literally
                    # run-then-result). We drive `result()` in a task we own, which is what
                    # makes the turn steerable while it runs: `feed_message` enqueues onto
                    # the run's inbox, `cancel_turn` cancels this task — AG2 propagates that
                    # into the turn.
                    async with agent.run(
                        *msg,
                        stream=stream,
                        middleware=(*_build_middleware(effective), *middleware),
                        **extra,
                    ) as run:
                        turn = asyncio.ensure_future(run.result())
                        active.run, active.task = run, turn
                        if on_event is None:
                            reply = await wait_reply(
                                turn,
                                timeout=effective.gateway.reply_timeout_s,
                                hitl_pending=hitl_pending,
                            )
                        else:
                            reply = await self._forwarding_events(
                                stream,
                                turn,
                                on_event,
                                hitl_pending=hitl_pending,
                                timeout=effective.gateway.reply_timeout_s,
                            )
                except asyncio.CancelledError:
                    if active.cancelled:
                        await self.emit_event(chat_id, TurnCancelled(chat_id, reason=active.reason))
                    await self._persist_turn(chat_id, stream, text, "")
                    if not active.cancelled:
                        raise
                    return ""
                except Exception as exc:
                    # snapshot the error + the exact history shape that triggered it
                    await capture_failure(
                        effective,
                        chat_id=chat_id,
                        surface=surface,
                        user_text=text,
                        error=exc,
                        stream=stream,
                    )
                    # A failed turn keeps its work. Without this the turn's events are
                    # never written at all, and since the thread renders from the event
                    # log the whole chat opens blank — the user loses the record of work
                    # that actually happened (tasks created, files written). Emit the
                    # marker FIRST: _persist_turn snapshots the history, so an event sent
                    # after it would not make the log.
                    await self.emit_event(chat_id, TurnFailed(chat_id, error=_failure_text(exc)))
                    await self._persist_turn(chat_id, stream, text, "")
                    raise
                else:
                    if a2ui_handle is not None:
                        await self._emit_a2ui_surfaces(stream, a2ui_handle)
                    await self._persist_turn(chat_id, stream, text, reply.body)
                    await self._mirror_turn(chat_id, text, reply.body, origin, attachment_names)
                    return reply.body
                finally:
                    self._active.pop(chat_id, None)
                    if a2ui_handle is not None:
                        self._unwatch_a2ui(stream, a2ui_handle)
                    self._record_usage(
                        stream, usage_handle, effective.llm.model
                    )  # always tally, even on error

    def _watch_usage(self, stream):
        """Subscribe to this turn's UsageEvents; returns (sub_id, collected list).
        Finalized by _record_usage when the turn ends."""
        collected: list = []

        async def collect(event):  # event injected positionally by the stream
            if isinstance(event, UsageEvent) and event.usage:
                collected.append(event.usage)

        return stream.subscribe(collect), collected

    def _record_usage(self, stream, handle, model: str) -> None:
        """Unsubscribe and add this turn's summed tokens to the daily ledger."""
        sub_id, collected = handle
        with contextlib.suppress(Exception):
            stream.unsubscribe(sub_id)
        if not collected:
            return
        prompt = sum(u.prompt_tokens or 0 for u in collected)
        completion = sum(u.completion_tokens or 0 for u in collected)
        total = sum(u.total_tokens or 0 for u in collected)
        with contextlib.suppress(Exception):
            self._usage.record(model, prompt, completion, total or None)

    def _watch_a2ui(self, stream):
        collected: list = []

        async def collect(event):  # event injected positionally by the stream
            if isinstance(event, A2UIMessageEvent):
                collected.append(event.message)

        return stream.subscribe(collect), collected

    def _unwatch_a2ui(self, stream, handle) -> None:
        sub_id, _ = handle
        with contextlib.suppress(Exception):
            stream.unsubscribe(sub_id)

    async def _emit_a2ui_surfaces(self, stream, handle) -> None:
        _, messages = handle
        if not messages:
            return
        context = ConversationContext(stream=stream)
        for surface in durable_surfaces_from_messages(messages):
            try:
                await context.send(surface)
            except Exception as exc:
                log_suppressed("a2ui durable surface emit", exc, surface_id=surface.surface_id)

    async def _forwarding_events(self, stream, turn, on_event, hitl_pending=None, timeout=None):
        """Await a driving turn, forwarding the agent's structured events to `on_event`.

        Uses AG2's stream subscription — the same event mechanism observers and the
        StreamBridge are built on, scoped to *this chat's* stream so it can't
        cross-talk with other chats sharing the one universal agent. Every event
        EXCEPT the conversation ones (those a voice client renders itself as spoken
        transcript) and binary audio is forwarded verbatim, so the client folds it
        with the same reducer the text path uses — tool chips/cards, task cards,
        deliverables, inquiries all appear without per-field plumbing.
        """

        async def report(event):  # event injected positionally by the stream
            if isinstance(event, _CONVERSATION_EVENTS) or is_binary_event(event):
                return  # transcript/audio are the voice channel's own to render
            try:
                await on_event(event)
            except Exception as exc:
                log_suppressed("voice event forward", exc, event=type(event).__name__)

        sub_id = stream.subscribe(report)
        try:
            return await wait_reply(
                turn,
                timeout=timeout or self._config.gateway.reply_timeout_s,
                hitl_pending=hitl_pending,
            )
        finally:
            stream.unsubscribe(sub_id)

    async def build_voice_agent(
        self,
        voice_id: str = "default",
        voice: str | None = None,
        task_id: str | None = None,
        origin_chat: str | None = None,
        on_event=None,
        on_end=None,
    ):
        """A LiveAgent (Gemini Live) for a browser voice session. Its heavy work
        delegates to this same universal agent so the spoken conversation shares
        the app's tools, memory, and continuity. `voice_id` is a per-connection
        id, only used to key the `voice:<id>` fallback stream when the call is
        bound to neither a task nor a chat.

        Crucially, voice rides the SAME conversation the user is looking at: on a
        task page (`task_id`) it binds that task; in the main chat (`origin_chat`)
        it delegates onto that chat's stream — so "this task" / "the one you just
        made" resolve against what was actually said there. We also seed the voice
        agent with a short recent-conversation snapshot for immediate grounding."""
        if self._tasks is None:
            raise RuntimeError("Voice needs the task service")

        task_context = ""
        if task_id:
            node = await self._tasks.get_task(task_id)
            if node:
                task_context = (
                    "You are currently on a task's page. When the user says "
                    f'"this task" they mean task {task_id}. Current state:\n'
                    f"{_task_summary(node)}"
                )
        elif origin_chat:
            recent = await self._recent_transcript(origin_chat)
            if recent:
                task_context = (
                    "You're continuing an ongoing chat (the user may have been "
                    "typing before switching to voice). Recent conversation:\n"
                    f"{recent}\n\nWhen they refer back to any of it, delegate to "
                    "ask_assistant, which shares this full conversation."
                )

        async def _send_capturing(request: str, chat: str, surface: str) -> str:
            # The universal agent's structured events (tool calls, the TaskCreated a
            # create_task emits, …) are forwarded raw via on_event so the voice
            # client folds them with the same reducer as text — no separate
            # task-capture path needed.
            return await self.send_message(
                request,
                chat_id=chat,
                surface=surface,
                on_event=on_event,
            )

        async def delegate(request: str) -> str:
            if task_id:
                node = await self._tasks.get_task(task_id)  # fresh each call
                snap = _task_summary(node) if node else f"(task {task_id})"
                return await _send_capturing(
                    request,
                    f"task:{task_id}",  # share the task's universal stream
                    "The user is talking to you by voice while viewing this "
                    f"task; act on THIS task (id {task_id}) when they refer to "
                    f"it. Answer plainly and briefly so it can be spoken.\n\n{snap}",
                )
            # Main chat: delegate onto the SAME chat so the universal agent has
            # the full text history (e.g. a task the user just created by typing).
            return await _send_capturing(
                request,
                origin_chat or f"voice:{voice_id}",
                "The user is talking to you by voice in this chat; the voice "
                "assistant asked you to handle this. Use the conversation history "
                "for any references like 'this task'. Answer plainly and briefly "
                "so it can be spoken aloud.",
            )

        async with self._resources.lease() as lease:
            async with self._preparation_lock:
                _, _, contributions, _ = await self._prepare_turn(lease, origin_chat or "voice")
            assistant_tools = [tool.name for tool in contributions["tools"] if tool.name]
        return build_voice_agent(
            self._config,
            profile_settings(self._config.data_dir, voice_provider=self._config.voice_provider),
            self._tasks,
            delegate,
            voice=voice,
            task_context=task_context,
            on_end=on_end,
            assistant_tools=assistant_tools,
        )

    async def _recent_transcript(self, chat_id: str, turns: int = 6) -> str:
        """A short plain-text snippet of the last few chat turns, for voice grounding."""
        try:
            msgs = await self.transcript(chat_id)
        except Exception as exc:
            log_suppressed("recent transcript load", exc, chat_id=chat_id)
            return ""
        out = []
        for m in msgs[-turns:]:
            who = "User" if m.get("role") == "user" else "Assistant"
            text = (m.get("text") or "").strip()
            if text:
                out.append(f"{who}: {text}")
        return "\n".join(out)

    async def _mirror_turn(self, chat_id, user_text, reply_text, origin, files=()) -> None:
        """Hand the completed turn to the mirror — only the message, the names of the
        files on it, and the answer, never the turn's own events. Best-effort: a
        platform push never fails a turn."""
        if self._mirror is None:
            return
        try:
            await self._mirror(chat_id, user_text, reply_text, origin=origin, files=tuple(files))
        except Exception as exc:
            log_suppressed("chat mirror", exc, chat_id=chat_id)

    async def _persist_turn(self, chat_id, stream, user_text, reply_text) -> None:
        """Write the chat's events + a display transcript to disk."""
        if self._writer is None:
            return
        try:
            await self._persist_log(chat_id, stream)
            await self._append_transcript(chat_id, user_text, reply_text)
        except Exception as exc:
            log_suppressed("turn persistence", exc, chat_id=chat_id)
            # Persistence is best-effort; never fail the user's turn.

    def _transcript_path(self, chat_id: str) -> str:
        return f"{_TRANSCRIPT_PREFIX}{quote(chat_id, safe='')}.json"

    async def _ensure_transcript_stub(self, chat_id, user_text, chat_model: str = "") -> None:
        """Write a minimal transcript doc as soon as a user message is accepted, so
        the chat is listable *during* the turn (not only after it completes).

        Only writes when there is NO doc yet (a brand-new chat's first turn) — a
        later turn's chat is already listed, and its stub would be indistinguishable
        from a lone pending user message, so we leave the existing doc untouched. The
        completing turn's ``_append_transcript`` fills in the agent reply in place.
        Called under the chat lock, so it never races the completion write. Best-
        effort: a persistence hiccup here must not fail the user's turn.

        ``chat_model`` is recorded as the new Chat's own override (ADR 0025)."""
        if self._writer is None or self._event_store is None:
            return
        path = self._transcript_path(chat_id)
        try:
            if await self._event_store.exists(path):
                return  # chat already listed — nothing to stub
            doc = {
                "chat_id": chat_id,
                "messages": [{"role": "user", "text": user_text}],
                "updated": datetime.now().astimezone().isoformat(),
                "title": None,  # named after the first exchange completes
            }
            if chat_model.strip():
                doc["model"] = chat_model.strip()
            await self._event_store.write(path, json.dumps(doc))
        except Exception as exc:
            log_suppressed("transcript stub write", exc, chat_id=chat_id)

    async def _append_transcript(self, chat_id, user_text, reply_text) -> None:
        store = self._event_store
        if store is None:
            return
        path = self._transcript_path(chat_id)
        doc = {"chat_id": chat_id, "messages": [], "updated": ""}
        if await store.exists(path):
            try:
                doc = json.loads(await store.read(path) or "")
            except Exception as exc:
                log_suppressed("existing transcript read", exc, chat_id=chat_id)
        doc["chat_id"] = chat_id
        msgs = doc.get("messages", [])
        # If a stub for THIS turn is present (a trailing lone user message with the
        # same text, no agent reply), complete it in place rather than re-appending —
        # otherwise the user message would be duplicated. Any other tail means this is
        # a genuinely new turn, so append the full user+agent pair as before.
        if msgs and msgs[-1].get("role") == "user" and msgs[-1].get("text") == user_text:
            doc["messages"] = [*msgs, {"role": "agent", "text": reply_text}]
        else:
            doc["messages"] = [
                *msgs,
                {"role": "user", "text": user_text},
                {"role": "agent", "text": reply_text},
            ]
        doc["updated"] = datetime.now().astimezone().isoformat()
        await store.write(path, json.dumps(doc))
        # After the FIRST complete exchange, name the chat once (async, non-blocking —
        # like ChatGPT/Claude). A single revision: only when there's no title yet.
        if len(doc["messages"]) == 2 and not doc.get("title"):
            task = asyncio.create_task(self._title_chat(chat_id, user_text, reply_text))
            self._background.add(task)
            task.add_done_callback(self._background.discard)

    async def _title_chat(self, chat_id, user_text, reply_text) -> None:
        """Generate and persist a one-shot chat title (best-effort, never overwrite)."""
        try:
            # The titler takes the profile's config, never the turn's: a Chat override
            # does not reach the cheap model (ADR 0025).
            title = await title_mod.generate_title(
                self._config, user_text, reply_text, agent_factory=self._title_factory
            )
        except Exception as exc:
            log_suppressed("chat title generation", exc, chat_id=chat_id)
            return
        store = self._event_store
        if not title or store is None:
            return
        path = self._transcript_path(chat_id)
        try:
            # This task runs after _append_transcript's lock is released; take the
            # chat lock so a late-returning titler can't clobber a concurrent user
            # rename/star landing between our read and write.
            async with self._chat_lock(chat_id):
                doc = json.loads(await store.read(path) or "")
                if doc.get("title"):  # already named (single revision) — leave it
                    return
                doc["title"] = title
                await store.write(path, json.dumps(doc))
        except Exception as exc:
            log_suppressed("chat title persist", exc, chat_id=chat_id)

    async def _read_chat_doc(self, chat_id: str, what: str) -> dict:
        """A chat's transcript document, or {} when there is no store, no document, or
        it does not parse (logged as ``what``)."""
        if self._event_store is None:
            return {}
        path = self._transcript_path(chat_id)
        if not await self._event_store.exists(path):
            return {}
        try:
            return json.loads(await self._event_store.read(path) or "")
        except Exception as exc:
            log_suppressed(what, exc, chat_id=chat_id)
            return {}

    async def transcript(self, chat_id: str) -> list[dict]:
        """The display transcript (role/text turns) for a chat."""
        return (await self._read_chat_doc(chat_id, "transcript read")).get("messages", [])

    async def list_chats(self) -> list[dict]:
        """List persisted chats (id, last update, preview), newest first."""
        if self._event_store is None:
            return []
        out = []
        for entry in await self._event_store.list(_TRANSCRIPT_PREFIX):
            if not entry.endswith(".json"):
                continue
            try:
                doc = json.loads(await self._event_store.read(_TRANSCRIPT_PREFIX + entry) or "")
            except Exception as exc:
                log_suppressed("chat listing transcript read", exc, entry=entry)
                continue
            if is_internal_stream(doc.get("chat_id", "")):
                continue
            msgs = doc.get("messages", [])
            first_user = next((m["text"] for m in msgs if m["role"] == "user"), "")
            out.append(
                {
                    "chat_id": doc.get("chat_id", ""),
                    "updated": doc.get("updated", ""),
                    # LLM-named after the first exchange; None on an in-flight stub —
                    # normalise to "" so the drawer falls back to the preview cleanly.
                    "title": doc.get("title") or "",
                    # user-set star; absent on old/new docs → False
                    "starred": bool(doc.get("starred")),
                    "preview": first_user[:80],
                    # Completed exchanges only; a lone in-flight user message is 0 turns.
                    "turns": len(msgs) // 2,
                }
            )
        out.sort(key=lambda s: s["updated"], reverse=True)
        return out

    async def threads_mentioning(self, paths: list[str]) -> list[dict]:
        """Threads (this profile's Chats + Task Runs) whose transcript mentions any
        of ``paths`` — the reverse link behind the preview rail's "Mentioned in N
        threads" backlink (ADR 0014).

        Loose full-path substring over each stream's **display transcript** AND its
        raw **event log** (main + dropped segments) — so a produced deliverable /
        attachment path, which lives only in a log event and never in the display
        messages, still matches. On-demand, no index, always fresh. A ``task:`` page
        holds config, not a transcript, and is skipped; ``task-run:`` runs and plain
        chats are kept and classified by their stream-id. Rows are newest-first and
        bounded (``_MENTIONS_STREAM_CAP`` streams scanned, ``_MENTIONS_RESULT_CAP``
        rows returned) so a large history can't stall the request."""
        if self._event_store is None or not paths:
            return []
        # Enumerate candidate streams: transcript docs (chats + the meta for a row)
        # unioned with event-log files (catches run streams and produced-path-only
        # mentions that never reach a display transcript).
        docs: dict[str, dict] = {}
        logs: dict[str, list[str]] = {}
        order: list[str] = []
        seen: set[str] = set()

        def _add(sid: str) -> None:
            if sid and sid not in seen:
                seen.add(sid)
                order.append(sid)

        for entry in await self._event_store.list(_TRANSCRIPT_PREFIX):
            if not entry.endswith(".json"):
                continue
            try:
                doc = json.loads(await self._event_store.read(_TRANSCRIPT_PREFIX + entry) or "")
            except Exception as exc:
                log_suppressed("mentions transcript read", exc, entry=entry)
                continue
            sid = doc.get("chat_id", "")
            if sid:
                docs[sid] = doc
                _add(sid)
        for entry in await self._event_store.list(LOG_PREFIX):
            sid = _log_stream_id(entry)
            if not sid:
                continue
            logs.setdefault(sid, []).append(f"{LOG_PREFIX}{entry}")
            _add(sid)

        # Cap the work, but keep the *most recent* streams when truncating — so the
        # "newest first" guarantee still holds on a huge history (a blind enumeration-
        # order slice could drop recent threads for old ones). A log-only stream has
        # no transcript ``updated`` and sorts last (rare: a produced path with no turn).
        order.sort(key=lambda s: (docs.get(s) or {}).get("updated", ""), reverse=True)
        truncated = len(order) > _MENTIONS_STREAM_CAP
        if truncated:
            order = order[:_MENTIONS_STREAM_CAP]

        rows: list[dict] = []
        for sid in order:
            # A Task PAGE stream is config chatter, not a transcript — skip it. Runs
            # and chats are real, openable Threads.
            if sid.startswith("task:"):
                continue
            corpus = await self._stream_corpus(sid, docs.get(sid), logs.get(sid, []))
            if not any(p in corpus for p in paths):
                continue
            row = await self._mention_row(sid, docs.get(sid))
            if row is not None:
                rows.append(row)
        rows.sort(key=lambda r: r.get("updated") or "", reverse=True)
        if truncated:
            logger.info("threads_mentioning: stream scan truncated at %d", _MENTIONS_STREAM_CAP)
        return rows[:_MENTIONS_RESULT_CAP]

    async def _stream_corpus(self, sid: str, doc: dict | None, log_paths: list[str]) -> str:
        """Concatenated searchable text for one stream: its display-transcript
        message text plus its raw event-log segments. Best-effort — an unreadable
        segment contributes nothing rather than failing the whole scan."""
        parts: list[str] = []
        if doc is not None:
            parts.extend(m.get("text", "") for m in doc.get("messages", []))
        store = self._event_store
        if store is not None:
            for path in log_paths:
                try:
                    parts.append(await store.read(path) or "")
                except Exception as exc:
                    log_suppressed("mentions log read", exc, stream_id=sid)
        return "\n".join(p for p in parts if p)

    async def _mention_row(self, sid: str, doc: dict | None) -> dict | None:
        """Build one popover row for a matched stream. A ``task-run:`` stream is a
        Run — enriched with its parent Task name + run start time (title falls back
        to the task name); everything else is a plain Chat."""
        if sid.startswith("task-run:"):
            info: dict | None = None
            if self._tasks is not None:
                try:
                    info = await self._tasks.get_run(sid.removeprefix("task-run:"))
                except Exception as exc:
                    log_suppressed("mentions run lookup", exc, stream_id=sid)
            info = info or {}
            task_name = info.get("task_name", "")
            started = info.get("started_at", "")
            ended = info.get("ended_at", "") or ""
            title = task_name or (doc.get("title") if doc else "") or "Task run"
            return {
                "stream_id": sid,
                "kind": "run",
                "title": title,
                "updated": (doc.get("updated") if doc else "") or ended or started,
                "task_id": info.get("task_id", ""),
                "task_name": task_name,
                "run_started_at": started,
            }
        doc = doc or {}
        msgs = doc.get("messages", [])
        first_user = next((m["text"] for m in msgs if m.get("role") == "user"), "")
        return {
            "stream_id": sid,
            "kind": "chat",
            "title": doc.get("title") or first_user[:80] or "Chat",
            "updated": doc.get("updated", ""),
        }

    async def delete_chat(self, chat_id: str) -> bool:
        """Permanently delete a chat: its display transcript, its full AG2 event log
        (main + any dropped segments), and any ACP replay history behind it, then evict
        the in-memory stream so a stale copy can't re-persist it. Irreversible by
        design — the GUI gates it behind a confirm. Returns True if anything was removed.

        The ACP history has to go with it: it carries this chat's id, so a session
        resumed after the delete would rehydrate that id and write the Chat back.
        """
        from assistant.acp.chats import purge_history_for_chat

        if self._event_store is None:
            return False
        async with self._chat_lock(chat_id):
            removed = await purge_history_for_chat(self._event_store, chat_id)
            paths = [self._transcript_path(chat_id), f"{LOG_PREFIX}{chat_id}.jsonl"]
            # dropped-turn segments are "<sid>.dropped-N.jsonl" under LOG_PREFIX
            for entry in await self._event_store.list(LOG_PREFIX):
                if entry.startswith(f"{chat_id}.dropped-") and entry.endswith(".jsonl"):
                    paths.append(f"{LOG_PREFIX}{entry}")
            for path in paths:
                if await self._event_store.exists(path):
                    await self._event_store.delete(path)
                    removed = True
            self._streams.pop(chat_id, None)
            self._synced.pop(chat_id, None)
        self._locks.pop(chat_id, None)
        return removed

    async def chat_model(self, chat_id: str) -> str:
        """This chat's Chat override — '' when it inherits, when the chat is unknown, or
        when the override dangles, which is what the turn resolves it to (ADR 0025)."""
        doc = await self._read_chat_doc(chat_id, "chat model read")
        return LlmConfigStore(self._config.paths).resolved_override(doc.get("model"))

    def text_models(self) -> list[dict]:
        """The install's shared Text models as a client offering one to a Chat reads
        them: each configuration's id and name, plus whether it can run right now —
        the same readiness the browser's switcher greys a row out on."""
        store = LlmConfigStore(self._config.paths)
        bridge = parse_bridge(self._config.acp_bridge, self._config.acp_bridge_token)
        return [
            {
                "id": entry.get("id", ""),
                "name": entry.get("name", ""),
                "model": entry.get("model", ""),
                "ready": store.usable(
                    entry,
                    self._config.secret_env,
                    search_path=self._config.search_path,
                    bridge=bridge,
                ),
            }
            for entry in store.list_configs()
        ]

    async def update_chat(
        self,
        chat_id: str,
        *,
        title: str | None = None,
        starred: bool | None = None,
        model: str | None = None,
    ) -> bool:
        """Partial metadata update on a persisted chat: rename, star, and/or set the
        Chat override (``model``: None = unchanged, '' = clear, else set).

        A user title is authoritative: the auto-titler only fills an empty title,
        so it never overwrites this. Returns False for an unknown chat.
        """
        if self._event_store is None:
            return False
        path = self._transcript_path(chat_id)
        async with self._chat_lock(chat_id):
            if not await self._event_store.exists(path):
                return False
            # Unlike the passive readers, a corrupt doc here should surface, not
            # silently drop the user's edit (and False would read as "unknown chat").
            doc = json.loads(await self._event_store.read(path) or "")
            if title is not None and title.strip():
                # Auto-titler caps at 80 (_clean_title); 200 gives user renames
                # headroom while still bounding the doc.
                doc["title"] = title.strip()[:200]
            if starred is not None:
                doc["starred"] = bool(starred)
            if model is not None:
                # Absent key = inheriting, so clearing removes it rather than
                # recording an empty selection.
                if model.strip():
                    doc["model"] = model.strip()
                else:
                    doc.pop("model", None)
            await self._event_store.write(path, json.dumps(doc))
        return True

    async def _maybe_onboard(self, asker) -> None:
        """Run first-run onboarding once, via the asker that made this request.

        The interview seeds the UNIVERSAL "who the user is" memory (identity facts),
        so it gates on the shared ``root_dir/user.db`` and runs once per install (the
        first chat in whichever profile), not once per profile."""
        if self._onboarding_done or not self._onboard or not self._memory or asker is None:
            return
        self._onboarding_done = True  # set first: never double-prompt, even on error
        user_store_path = self._config.root_dir / "user.db"  # shared universal memory
        try:
            if await onboarding.needs_onboarding(user_store_path):
                answers = await onboarding.run_onboarding(
                    asker, user_store_path, paths=self._config.paths
                )
                if loc := answers.get("location"):
                    self._config.agent.location = loc  # live, not just on the next start
        except Exception as exc:
            log_suppressed("onboarding", exc)
            # Onboarding is best-effort; never block the actual message.

    async def _task_for_stream(self, chat_id: str) -> str:
        """The task behind a run stream (``task-run:{run_id}``) — '' for anything
        else, or when the run/task is gone: a manual reply in an orphaned run's
        thread then resolves like a plain chat instead of erroring."""
        if not chat_id.startswith("task-run:") or self._tasks is None:
            return ""
        try:
            run = await self._tasks.get_run(chat_id.removeprefix("task-run:"))
        except Exception:
            return ""
        return (run or {}).get("task_id") or ""

    def _ask_kwargs(self, asker, chat_id: str = "", task_id: str = "", *, config=None) -> dict:
        """Per-turn hitl_hook + dependencies bound to this request's asker, chat, and
        (for a task run) task — so an "always allow" this turn mints persists
        task-scoped rather than globally (see PermissionManager.task_id)."""

        config = config or self._config
        deps: dict = {
            PermissionManager: PermissionManager(
                self._permissions,
                asker,
                sandbox=config.tools.sandbox,
                folders=self._folders,
                profile=config.data_dir.name,
                chat_id=chat_id,
                workspace_dir=config.workspace_dir,
                task_id=task_id,
            )
        }
        out: dict = {"dependencies": deps}
        if asker is not None:
            # ask_user pulls the turn's asker from dependencies so the model can
            # pose option-carrying Questions (context.input is string-only).
            deps[Asker] = asker
            out["hitl_hook"] = build_hitl_hook(asker)
        return out

    def status(self) -> dict:
        """Lightweight status snapshot for health endpoints."""
        return {
            "status": "ok" if self._agent is not None else "stopped",
            "model": self._config.llm.model,
            "memory": self._memory,
            "platform": self._platform,
            "chats": len(self._streams),
        }

    async def close(self) -> None:
        """Resolve active and queued invocations, then close this Profile's resources."""
        self._closing = True
        tasks = [task for task in self._invocations if task is not asyncio.current_task()]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        background = list(self._background)
        for task in background:
            task.cancel()
        await asyncio.gather(*background, return_exceptions=True)
        async with self._preparation_lock:
            await self._resources.aclose()
            if self._acp_generation is not None:
                await self._acp_generation.aclose()
                self._acp_generation = None
        self._streams.clear()
        self._locks.clear()
        self._synced.clear()
        self._agent = None


def build_gateway(
    config: Config | None = None,
    *,
    memory: bool = True,
    platform: str = "gateway",
    persist: bool = True,
    config_factory: Callable[[], Config] | None = None,
    agent_factory: Callable | None = None,
    title_factory: Callable | None = None,
    summary_factory: Callable | None = None,
    model_factory: Callable | None = None,
    environment_factory: Callable | None = None,
) -> "tuple[Gateway, TaskService]":
    """Build a Gateway and TaskService sharing a Profile configuration resolver."""
    config = config or load_config()
    tasks = TaskService(
        config=config, config_factory=config_factory, summary_factory=summary_factory
    )
    gateway = Gateway(
        config=config,
        memory=memory,
        platform=platform,
        persist=persist,
        task_service=tasks,
        config_factory=config_factory,
        agent_factory=agent_factory,
        title_factory=title_factory,
        model_factory=model_factory,
        environment_factory=environment_factory,
    )
    return gateway, tasks
