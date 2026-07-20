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
import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from urllib.parse import quote

from assistant.agent import create_agent, universal_turn_prompt
from assistant.config import Config, load_config
from assistant.events import TurnCancelled

_TRANSCRIPT_PREFIX = "/transcript/"


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
    from ag2.events import ModelMessageChunk, ModelRequest, ModelResponse

    return (ModelRequest, ModelMessageChunk, ModelResponse)


_CONVERSATION_EVENTS = _conversation_events()


def is_internal_stream(chat_id: str) -> bool:
    """Streams that are a thread of something else (a task run) — they render on
    their own page and must not appear in the Chats list. ``task:`` covers legacy
    records from the pre-redesign model."""
    return chat_id.startswith("task-run:") or chat_id.startswith("task:")


@dataclass
class _ActiveTurn:
    """A turn in flight, so other coroutines can steer or stop it.

    `run` is AG2's ``AgentRun`` — its ``enqueue`` feeds the running turn (drained
    before the turn's next model call). `task` is *our* task awaiting
    ``run.result()``: AG2 cancels the turn when that await is cancelled, which is
    the framework's cancellation contract (``AgentRun.result``). `cancelled` marks
    the cancel as ours, so ``send_message`` can tell a user stop apart from an
    ambient cancellation (WS disconnect, shutdown) it must re-raise.
    """

    run: object
    task: "asyncio.Task"
    cancelled: bool = False
    reason: str = "Stopped"


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
    ) -> None:
        self._config = config or load_config()
        self._memory = memory
        self._platform = platform
        self._onboard = onboard
        self._persist = persist
        self._tasks = task_service  # gives the universal agent its system tools
        # How reload() re-resolves config. For a profile runtime this re-reads that
        # profile's registry entry + settings on every call (§4.1), so workspace/model
        # edits are picked up; for bare construction it defaults to load_config (the
        # global root config, profile-agnostic).
        self._config_factory = config_factory or load_config
        self._onboarding_done = False
        self._agent = None
        # Last ChatGPT-subscription access token baked into the agent, so a pre-turn
        # refresh only rebuilds the (cached) agent when the token actually rotated.
        self._codex_token: str | None = None
        self._permissions = None
        self._folders = None
        self._event_store = None
        self._writer = None
        # chat_id -> live Stream; plus which chats we've hydrated from disk
        self._streams: dict[str, object] = {}
        self._loaded: set[str] = set()
        # llm_config_id -> its cached per-model Agent (built lazily in _agent_for;
        # cleared on reload() so a settings/config change doesn't keep serving stale
        # per-task agents alongside the rebuilt default one).
        self._model_agents: dict[str, object] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        # chat_id -> the turn currently running on it (feed_message / cancel_turn)
        self._active: dict[str, _ActiveTurn] = {}
        from assistant.usage import UsageLedger

        # Per-profile daily token/cost tally for the activity HUD.
        self._usage = UsageLedger(self._config.data_dir / "usage.json")

    @property
    def config(self) -> Config:
        """The gateway's live config — re-resolved on ``reload()`` (so a profile
        runtime's workspace/model edits are reflected here after a reload)."""
        return self._config

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

    def _make_agent(self, cfg=None):
        """Build a universal agent: capability + system tools (know/do everything) +
        compaction. Used by start()/reload() for the default agent, and by
        `_agent_for` (with an overridden ``cfg``) to build a per-task-model agent."""
        cfg = cfg or self._config
        extra_tools = None
        if self._tasks is not None:
            from assistant.settings import profile_settings
            from assistant.system_tools import build_system_tools

            # create/update/run/delete come from the system tools, so we don't also
            # wire duplicate task actions here. `platform` lets those tools note (on
            # channels) that follow-up questions go to the web app.
            # The voice get/set tools read/write THIS profile's settings.
            settings = profile_settings(cfg.data_dir)
            extra_tools = build_system_tools(
                self._tasks, settings, chats=self, platform=self._platform
            )
        return create_agent(
            cfg,
            memory=self._memory,
            platform=self._platform,
            extra_tools=extra_tools,
            compact=self._memory,
        )

    def _agent_for(self, llm_config_id: str | None):
        """The turn's agent: the profile default, or a cached per-LLM-config agent
        when a task pins a model. Unknown ids fall back to the default (the task
        may reference a since-deleted configuration — degrade, don't fail)."""
        if not llm_config_id:
            return self._agent
        agent = self._model_agents.get(llm_config_id)
        if agent is not None:
            return agent
        from assistant import llm_configs

        entry = llm_configs.get_config(llm_config_id)
        if entry is None:
            return self._agent
        import copy

        cfg = copy.deepcopy(self._config)
        provider = llm_configs.PROVIDER_OF[entry["type"]]
        cfg.llm.provider = provider
        cfg.llm.model = entry["model"]
        cfg.llm.provider_options[provider] = llm_configs.entry_options(entry)
        cfg.llm.auth_mode = "subscription" if entry["type"] == "openai_subscription" else "api_key"
        agent = self._make_agent(cfg)
        self._model_agents[llm_config_id] = agent
        return agent

    async def _ensure_subscription_fresh(self) -> None:
        """When OpenAI runs in ChatGPT-subscription mode, refresh the OAuth access
        token before a turn and rebuild the cached agent iff the token rotated.

        The token is baked into the agent at build time; OAuth access tokens are
        short-lived, so a long-lived cached agent would go stale. The common case
        (token still valid) is cheap: no refresh, no rebuild. Best-effort — a
        refresh failure surfaces as a normal turn error with a re-login hint."""
        cfg = self._config
        if cfg.llm.provider.lower() != "openai" or cfg.llm.auth_mode != "subscription":
            return
        from assistant import codex_auth

        try:
            creds = await asyncio.to_thread(codex_auth.ensure_fresh)
        except codex_auth.CodexAuthError:
            return  # let the turn fail with model_config's own clear error
        if creds.access_token != self._codex_token:
            self._codex_token = creds.access_token
            if self._agent is not None:
                self._agent = self._make_agent()

    async def start(self) -> None:
        """Create the shared agent and (optionally) the on-disk chat store."""
        from assistant import secrets
        from assistant.observability import setup_logging
        from assistant.permissions import PermissionStore

        secrets.migrate()  # one-shot legacy -> Secret-entity upgrade (idempotent)
        secrets.load_into_env()  # provider keys into env before any agent is built
        setup_logging(self._config)  # rolling log + failure capture for debugging

        self._agent = self._make_agent()
        # Install-wide persistent grant store (config.root_dir holds global files) —
        # grants are global, not per-profile.
        self._permissions = PermissionStore(self._config.root_dir / "permissions.json")

        from assistant.folders import FolderStore

        # Install-wide Folder registry (ADR 0006); Grants are per-profile/per-chat,
        # resolved at check time with this profile's id + the turn's chat_id.
        self._folders = FolderStore(self._config.root_dir / "folders.json")

        if self._persist:
            from ag2.knowledge import SqliteKnowledgeStore
            from ag2.knowledge.log import EventLogWriter

            self._config.data_dir.mkdir(parents=True, exist_ok=True)
            self._event_store = SqliteKnowledgeStore(str(self._config.data_dir / "chats.db"))
            self._writer = EventLogWriter(self._event_store)

    async def reload(self) -> None:
        """Rebuild agents from fresh config + keys after a settings change.

        Reference-swap, deliberately minimal: a turn already running captured the old
        agent and finishes on it (incl. mid-tool-call); the next turn uses the new
        agent and replays the same per-chat Stream (history is in the Streams, not
        the agent). The task service rebuilds its planner/executor too, so scheduled
        work doesn't keep using stale keys. Voice needs no reload (built per voice
        session from env)."""
        from assistant import secrets

        secrets.load_into_env()
        # Re-resolve via the injected factory (a profile runtime's factory re-reads
        # the profile's registry entry + settings; the default is load_config).
        self._config = self._config_factory()
        if self._agent is not None:
            self._agent = self._make_agent()
        # Stale per-model agents were built from the pre-reload config/keys; a task
        # run after this point must get a freshly-built one.
        self._model_agents.clear()
        if self._tasks is not None and hasattr(self._tasks, "reload"):
            await self._tasks.reload()

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
        if active is None or active.task.done():
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
        """Cancel the turn running on this chat; False if none is in flight.

        Cancelling the task that awaits ``AgentRun.result()`` is AG2's cancellation
        contract: ``result()`` propagates the cancel into the turn's driver, and the
        run scope tears down. Whatever the turn already put on the stream (tool calls,
        tool results, partial output) stays — see ``send_message``'s cancel path.
        """
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
        from ag2.context import ConversationContext

        stream = await self.stream_for(chat_id)
        try:
            await ConversationContext(stream=stream).send(event)
        except Exception as exc:
            from assistant.observability import log_suppressed

            log_suppressed("external stream event emit", exc, chat_id=chat_id)
            return
        if self._writer is not None:
            try:
                await self._writer.persist(chat_id, list(await stream.history.get_events()))
            except Exception as exc:
                from assistant.observability import log_suppressed

                log_suppressed("external stream event persist", exc, chat_id=chat_id)
                # Persistence is best-effort; the live event still went out.

    async def _get_stream(self, chat_id: str):
        """Return the chat's live Stream, hydrating from disk on first use."""
        from ag2.stream import MemoryStream

        stream = self._streams.get(chat_id)
        if stream is None:
            stream = MemoryStream(id=chat_id)
            self._streams[chat_id] = stream
            if self._writer is not None and chat_id not in self._loaded:
                try:
                    events = await self._writer.load(chat_id)
                    if events:
                        await stream.history.replace(events)
                except Exception as exc:
                    from assistant.observability import log_suppressed

                    log_suppressed("chat stream hydrate", exc, chat_id=chat_id)
                    # A corrupt/absent log just starts a fresh stream.
                self._loaded.add(chat_id)
        return stream

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
        omitted (voice renders those itself). `llm_config_id` pins the turn to a
        task's chosen model (a cached per-config agent) instead of the profile
        default. `task_id`, when this turn is a task run, scopes any command grant
        the turn mints via "always allow" to that task (survives its future runs)
        instead of persisting it globally; when omitted it is auto-resolved from
        `chat_id` for a run's thread (``task-run:{run_id}``), so a manual reply
        typed there is scoped the same as the run itself, and this also feeds
        folder-grant resolution for that turn.
        """
        if self._agent is None:
            raise RuntimeError("Gateway not started")

        await self._maybe_onboard(asker)
        # Refresh first: subscription mode may rebuild the default agent with a
        # rotated OAuth token, and this turn must run on the fresh one.
        await self._ensure_subscription_fresh()
        agent = self._agent_for(llm_config_id)

        # A reply typed into a run's thread arrives without task context — resolve
        # it so task-scoped folder/command grants cover manual turns too.
        task_id = task_id or await self._task_for_stream(chat_id)
        extra = self._ask_kwargs(asker, chat_id, task_id or "")
        msg = [text, *(attachments or [])]

        async with self._chat_lock(chat_id):
            stream = await self._get_stream(chat_id)
            # Persist a transcript stub the instant we accept the message, so the
            # chat shows up in list_chats() *during* a long agentic turn — not
            # only after it completes. Without this, a chat in flight lives solely in
            # the web page's local state and vanishes on a profile switch (full-page
            # nav). The completed-turn write below stays the authority (§_persist_turn).
            await self._ensure_transcript_stub(chat_id, text)
            prompt = universal_turn_prompt(self._config, surface)  # refresh per turn
            a2ui_runtime = None
            try:
                from assistant.a2ui import runtime as a2ui_runtime_factory

                a2ui_runtime = a2ui_runtime_factory()
                prompt = [
                    *prompt,
                    a2ui_runtime.system_prompt_section,
                    a2ui_runtime.capabilities_prompt(None),
                ]
            except Exception as exc:
                from assistant.observability import log_suppressed

                log_suppressed("a2ui runtime setup", exc, chat_id=chat_id)
            if a2ui_runtime is not None:
                from assistant.a2ui import tolerant_a2ui_middleware

                # Append a fallback that recovers surfaces when the model omits the
                # <a2ui-json> wrapper (fires only when the runtime's own extraction
                # can't — the two are mutually exclusive per response).
                middleware = (
                    *a2ui_runtime.middleware_factories(),
                    tolerant_a2ui_middleware(a2ui_runtime.parser),
                )
            else:
                middleware = ()
            usage_handle = self._watch_usage(stream)  # tally this turn's tokens (HUD)
            a2ui_handle = self._watch_a2ui(stream)
            try:
                # `run` is `ask` with the turn left observable (`ask` is literally
                # run-then-result). We drive `result()` in a task we own, which is what
                # makes the turn steerable while it runs: `feed_message` enqueues onto
                # the run's inbox, `cancel_turn` cancels this task — AG2 propagates that
                # into the turn.
                async with agent.run(
                    *msg,
                    stream=stream,
                    prompt=prompt,
                    middleware=middleware,
                    **extra,
                ) as run:
                    turn = asyncio.ensure_future(run.result())
                    active = _ActiveTurn(run, turn)
                    self._active[chat_id] = active
                    try:
                        if on_event is None:
                            reply = await asyncio.wait_for(
                                turn, timeout=self._config.gateway.reply_timeout_s
                            )
                        else:
                            reply = await self._forwarding_events(stream, turn, on_event)
                    except asyncio.CancelledError:
                        if not active.cancelled:
                            raise  # not a user stop (disconnect, shutdown) — let it fly
                        # A stopped turn keeps what it already did: the tool calls and
                        # results are on the stream, so persist them and mark the stop.
                        await self._persist_turn(chat_id, stream, text, "")
                        await self.emit_event(chat_id, TurnCancelled(chat_id, reason=active.reason))
                        return ""
                    finally:
                        self._active.pop(chat_id, None)
            except Exception as exc:
                # snapshot the error + the exact history shape that triggered it
                from assistant.observability import capture_failure

                await capture_failure(
                    self._config,
                    chat_id=chat_id,
                    surface=surface,
                    user_text=text,
                    error=exc,
                    stream=stream,
                )
                raise
            else:
                await self._emit_a2ui_surfaces(stream, a2ui_handle)
                await self._persist_turn(chat_id, stream, text, reply.body)
                return reply.body
            finally:
                self._unwatch_a2ui(stream, a2ui_handle)
                self._record_usage(stream, usage_handle)  # always tally, even on error

    def _watch_usage(self, stream):
        """Subscribe to this turn's UsageEvents; returns (sub_id, collected list).
        Finalized by _record_usage when the turn ends."""
        from ag2.events import UsageEvent

        collected: list = []

        async def collect(event):  # event injected positionally by the stream
            if isinstance(event, UsageEvent) and event.usage:
                collected.append(event.usage)

        return stream.subscribe(collect), collected

    def _record_usage(self, stream, handle) -> None:
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
            self._usage.record(self._config.llm.model, prompt, completion, total or None)

    def _watch_a2ui(self, stream):
        from ag2.a2ui import A2UIMessageEvent

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
        from ag2.context import ConversationContext

        from assistant.a2ui import durable_surfaces_from_messages
        from assistant.observability import log_suppressed

        context = ConversationContext(stream=stream)
        for surface in durable_surfaces_from_messages(messages):
            try:
                await context.send(surface)
            except Exception as exc:
                log_suppressed("a2ui durable surface emit", exc, surface_id=surface.surface_id)

    async def _forwarding_events(self, stream, turn, on_event):
        """Await a driving turn, forwarding the agent's structured events to `on_event`.

        Uses AG2's stream subscription — the same event mechanism observers and the
        StreamBridge are built on, scoped to *this chat's* stream so it can't
        cross-talk with other chats sharing the one universal agent. Every event
        EXCEPT the conversation ones (those a voice client renders itself as spoken
        transcript) and binary audio is forwarded verbatim, so the client folds it
        with the same reducer the text path uses — tool chips/cards, task cards,
        deliverables, inquiries all appear without per-field plumbing.
        """
        from assistant.gateway.wire import is_binary_event

        async def report(event):  # event injected positionally by the stream
            if isinstance(event, _CONVERSATION_EVENTS) or is_binary_event(event):
                return  # transcript/audio are the voice channel's own to render
            try:
                await on_event(event)
            except Exception as exc:
                from assistant.observability import log_suppressed

                log_suppressed("voice event forward", exc, event=type(event).__name__)

        sub_id = stream.subscribe(report)
        try:
            return await asyncio.wait_for(turn, timeout=self._config.gateway.reply_timeout_s)
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
        from assistant.settings import profile_settings
        from assistant.voice import build_voice_agent

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

        assistant_tools = [getattr(t, "name", "") for t in getattr(self._agent, "tools", [])]
        assistant_tools = [n for n in assistant_tools if n]
        return build_voice_agent(
            self._config,
            profile_settings(self._config.data_dir),
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
            from assistant.observability import log_suppressed

            log_suppressed("recent transcript load", exc, chat_id=chat_id)
            return ""
        out = []
        for m in msgs[-turns:]:
            who = "User" if m.get("role") == "user" else "Assistant"
            text = (m.get("text") or "").strip()
            if text:
                out.append(f"{who}: {text}")
        return "\n".join(out)

    async def _persist_turn(self, chat_id, stream, user_text, reply_text) -> None:
        """Write the chat's events + a display transcript to disk."""
        if self._writer is None:
            return
        try:
            await self._writer.persist(chat_id, list(await stream.history.get_events()))
            await self._append_transcript(chat_id, user_text, reply_text)
        except Exception as exc:
            from assistant.observability import log_suppressed

            log_suppressed("turn persistence", exc, chat_id=chat_id)
            # Persistence is best-effort; never fail the user's turn.

    def _transcript_path(self, chat_id: str) -> str:
        return f"{_TRANSCRIPT_PREFIX}{quote(chat_id, safe='')}.json"

    async def _ensure_transcript_stub(self, chat_id, user_text) -> None:
        """Write a minimal transcript doc as soon as a user message is accepted, so
        the chat is listable *during* the turn (not only after it completes).

        Only writes when there is NO doc yet (a brand-new chat's first turn) — a
        later turn's chat is already listed, and its stub would be indistinguishable
        from a lone pending user message, so we leave the existing doc untouched. The
        completing turn's ``_append_transcript`` fills in the agent reply in place.
        Called under the chat lock, so it never races the completion write. Best-
        effort: a persistence hiccup here must not fail the user's turn."""
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
            await self._event_store.write(path, json.dumps(doc))
        except Exception as exc:
            from assistant.observability import log_suppressed

            log_suppressed("transcript stub write", exc, chat_id=chat_id)

    async def _append_transcript(self, chat_id, user_text, reply_text) -> None:
        path = self._transcript_path(chat_id)
        doc = {"chat_id": chat_id, "messages": [], "updated": ""}
        if await self._event_store.exists(path):
            try:
                doc = json.loads(await self._event_store.read(path))
            except Exception as exc:
                from assistant.observability import log_suppressed

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
        await self._event_store.write(path, json.dumps(doc))
        # After the FIRST complete exchange, name the chat once (async, non-blocking —
        # like ChatGPT/Claude). A single revision: only when there's no title yet.
        if len(doc["messages"]) == 2 and not doc.get("title"):
            asyncio.create_task(self._title_chat(chat_id, user_text, reply_text))

    async def _title_chat(self, chat_id, user_text, reply_text) -> None:
        """Generate and persist a one-shot chat title (best-effort, never overwrite)."""
        from assistant.title import generate_title

        try:
            title = await generate_title(self._config, user_text, reply_text)
        except Exception as exc:
            from assistant.observability import log_suppressed

            log_suppressed("chat title generation", exc, chat_id=chat_id)
            return
        if not title:
            return
        path = self._transcript_path(chat_id)
        try:
            # This task runs after _append_transcript's lock is released; take the
            # chat lock so a late-returning titler can't clobber a concurrent user
            # rename/star landing between our read and write.
            async with self._chat_lock(chat_id):
                doc = json.loads(await self._event_store.read(path))
                if doc.get("title"):  # already named (single revision) — leave it
                    return
                doc["title"] = title
                await self._event_store.write(path, json.dumps(doc))
        except Exception as exc:
            from assistant.observability import log_suppressed

            log_suppressed("chat title persist", exc, chat_id=chat_id)

    async def transcript(self, chat_id: str) -> list[dict]:
        """The display transcript (role/text turns) for a chat."""
        if self._event_store is None:
            return []
        path = self._transcript_path(chat_id)
        if not await self._event_store.exists(path):
            return []
        try:
            return json.loads(await self._event_store.read(path)).get("messages", [])
        except Exception as exc:
            from assistant.observability import log_suppressed

            log_suppressed("transcript read", exc, chat_id=chat_id)
            return []

    async def list_chats(self) -> list[dict]:
        """List persisted chats (id, last update, preview), newest first."""
        if self._event_store is None:
            return []
        out = []
        for entry in await self._event_store.list(_TRANSCRIPT_PREFIX):
            if not entry.endswith(".json"):
                continue
            try:
                doc = json.loads(await self._event_store.read(_TRANSCRIPT_PREFIX + entry))
            except Exception as exc:
                from assistant.observability import log_suppressed

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

    async def delete_chat(self, chat_id: str) -> bool:
        """Permanently delete a chat: its display transcript AND its full AG2 event
        log (main + any dropped segments), then evict the in-memory stream so a stale
        copy can't re-persist it. Irreversible by design — the GUI gates it behind a
        confirm. Returns True if anything was removed.
        """
        if self._event_store is None:
            return False
        from ag2.knowledge.constants import LOG_PREFIX

        async with self._chat_lock(chat_id):
            removed = False
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
            self._loaded.discard(chat_id)
        self._locks.pop(chat_id, None)
        return removed

    async def update_chat(
        self, chat_id: str, *, title: str | None = None, starred: bool | None = None
    ) -> bool:
        """Partial metadata update on a persisted chat: rename and/or star.

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
            doc = json.loads(await self._event_store.read(path))
            if title is not None and title.strip():
                # Auto-titler caps at 80 (_clean_title); 200 gives user renames
                # headroom while still bounding the doc.
                doc["title"] = title.strip()[:200]
            if starred is not None:
                doc["starred"] = bool(starred)
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
        from assistant.onboarding import needs_onboarding, run_onboarding

        user_store_path = self._config.root_dir / "user.db"  # shared universal memory
        try:
            if await needs_onboarding(user_store_path):
                await run_onboarding(asker, user_store_path)
        except Exception as exc:
            from assistant.observability import log_suppressed

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

    def _ask_kwargs(self, asker, chat_id: str = "", task_id: str = "") -> dict:
        """Per-turn hitl_hook + dependencies bound to this request's asker, chat, and
        (for a task run) task — so an "always allow" this turn mints persists
        task-scoped rather than globally (see PermissionManager.task_id)."""
        from assistant.permissions import PermissionManager

        deps: dict = {
            PermissionManager: PermissionManager(
                self._permissions,
                asker,
                sandbox=self._config.tools.sandbox,
                folders=self._folders,
                profile=self._config.data_dir.name,
                chat_id=chat_id,
                workspace_dir=self._config.workspace_dir,
                task_id=task_id,
            )
        }
        out: dict = {"dependencies": deps}
        if asker is not None:
            from assistant.hitl import Asker, build_hitl_hook

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
        """Release in-memory chat state (persisted chats stay on disk)."""
        self._streams.clear()
        self._locks.clear()
        self._loaded.clear()
        self._agent = None


def build_gateway(
    config: Config | None = None,
    *,
    memory: bool = True,
    platform: str = "gateway",
    persist: bool = True,
    config_factory: Callable[[], Config] | None = None,
) -> "tuple[Gateway, object]":
    """Canonical construction: a Gateway wired to its TaskService, so the universal
    agent gets the task system tools (create/schedule/query). Used by the web app and
    every channel command. Returns ``(gateway, task_service)``; the caller starts both
    and wires ``task_service.set_emitter(gateway.emit_event)``.

    ``config_factory`` (optional) is threaded into both the Gateway and TaskService so
    their ``reload()`` re-resolves config the same way — a profile runtime passes one
    that re-reads that profile's registry + settings (§4.1); when omitted both fall
    back to ``load_config`` (the profile-agnostic root config)."""
    from assistant.gateway.tasks_service import TaskService

    config = config or load_config()
    tasks = TaskService(config=config, config_factory=config_factory)
    gateway = Gateway(
        config=config,
        memory=memory,
        platform=platform,
        persist=persist,
        task_service=tasks,
        config_factory=config_factory,
    )
    return gateway, tasks
