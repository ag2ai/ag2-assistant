"""Gateway core — session management over the AG2 Assistant agent.

The Gateway exposes a simple `send_message(text, session_id)` surface that any
facade (REST/WebSocket/channel) can call. Each session is a persistent AG2
`Stream` keyed by `session_id`: its event history carries the multi-turn
conversation, and after every turn the events are written to disk via AG2's
`EventLogWriter`. On restart (or a new connection to an existing session) the
events are reloaded into a fresh stream, so conversations are **resumable** — the
agent keeps full context, not just a text transcript. A lightweight display
transcript is stored alongside so UIs can render the history.

One shared `Agent` backs all sessions; isolation comes from the per-session
stream, never crossing histories.
"""

import asyncio
import contextlib
import json
from collections.abc import Callable
from datetime import datetime
from urllib.parse import quote

from assistant.agent import create_agent, universal_turn_prompt
from assistant.config import Config, load_config

REPLY_TIMEOUT = 240.0
_TRANSCRIPT_PREFIX = "/transcript/"


def _conversation_events() -> tuple:
    """Event types a voice client renders itself as spoken transcript (so they are
    NOT re-forwarded as structured events during voice delegation). Imported lazily
    so a missing optional event type can't break module import."""
    from ag2.events import ModelMessageChunk, ModelRequest, ModelResponse

    return (ModelRequest, ModelMessageChunk, ModelResponse)


_CONVERSATION_EVENTS = _conversation_events()


class Gateway:
    """Manages per-session, resumable conversations with the AG2 Assistant agent."""

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
        self._permissions = None
        self._event_store = None
        self._writer = None
        # session_id -> live Stream; plus which sessions we've hydrated from disk
        self._streams: dict[str, object] = {}
        self._loaded: set[str] = set()
        self._locks: dict[str, asyncio.Lock] = {}
        from assistant.usage import UsageLedger

        # Per-profile daily token/cost tally for the activity HUD.
        self._usage = UsageLedger(self._config.data_dir / "usage.json")

    @property
    def config(self) -> Config:
        """The gateway's live config — re-resolved on ``reload()`` (so a profile
        runtime's workspace/model edits are reflected here after a reload)."""
        return self._config

    def usage_today(self) -> dict:
        """Today's token + estimated-cost totals (for the cost & activity HUD)."""
        return self._usage.today()

    def _make_agent(self):
        """Build the one universal agent: capability + system tools (know/do
        everything) + compaction. Used by start() and reload()."""
        extra_tools = None
        if self._tasks is not None:
            from assistant.settings import Settings
            from assistant.system_tools import build_system_tools

            # create/schedule come from the system tools, so we don't also wire
            # start_task/schedule_task here (that duplicated names). `platform` lets
            # those tools note (on channels) that follow-up questions go to the web app.
            # The voice get/set tools read/write THIS profile's settings.
            settings = Settings(self._config.data_dir / "settings.json")
            extra_tools = build_system_tools(
                self._tasks, settings, chats=self, platform=self._platform
            )
        return create_agent(
            self._config,
            memory=self._memory,
            platform=self._platform,
            extra_tools=extra_tools,
            compact=self._memory,
        )

    async def start(self) -> None:
        """Create the shared agent and (optionally) the on-disk session store."""
        from assistant import secrets
        from assistant.observability import setup_logging
        from assistant.permissions import PermissionStore

        secrets.load_into_env()  # provider keys into env before any agent is built
        setup_logging(self._config)  # rolling log + failure capture for debugging

        self._agent = self._make_agent()
        # Per-profile persistent grant store (config.data_dir is the profile dir).
        self._permissions = PermissionStore(self._config.data_dir / "permissions.json")

        if self._persist:
            from ag2.knowledge import SqliteKnowledgeStore
            from ag2.knowledge.log import EventLogWriter

            self._config.data_dir.mkdir(parents=True, exist_ok=True)
            self._event_store = SqliteKnowledgeStore(str(self._config.data_dir / "sessions.db"))
            self._writer = EventLogWriter(self._event_store)

    async def reload(self) -> None:
        """Rebuild agents from fresh config + keys after a settings change.

        Reference-swap, deliberately minimal: a turn already running captured the old
        agent and finishes on it (incl. mid-tool-call); the next turn uses the new
        agent and replays the same per-session Stream (history is in the Streams, not
        the agent). The task service rebuilds its planner/executor too, so scheduled
        work doesn't keep using stale keys. Voice needs no reload (built per session
        from env)."""
        from assistant import secrets

        secrets.load_into_env()
        # Re-resolve via the injected factory (a profile runtime's factory re-reads
        # the profile's registry entry + settings; the default is load_config).
        self._config = self._config_factory()
        if self._agent is not None:
            self._agent = self._make_agent()
        if self._tasks is not None and hasattr(self._tasks, "reload"):
            await self._tasks.reload()

    def _session_lock(self, session_id: str) -> asyncio.Lock:
        lock = self._locks.get(session_id)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[session_id] = lock
        return lock

    async def stream_for(self, session_id: str):
        """The session's live, persisted, resumable event stream — the source the
        event bridge replays and subscribes to. Same cached object send_message
        uses, so events from a turn are caught by the bridge's subscription."""
        return await self._get_stream(session_id)

    async def emit_event(self, session_id: str, event) -> None:
        """Emit an event onto a session's stream from outside an agent turn (the
        pattern AG2's own SoundDeviceRecorder uses). It reaches any live bridge
        subscriber and is persisted so it survives reload. Best-effort."""
        from ag2.context import ConversationContext

        stream = await self.stream_for(session_id)
        try:
            await ConversationContext(stream=stream).send(event)
        except Exception as exc:
            from assistant.observability import log_suppressed

            log_suppressed("external stream event emit", exc, session_id=session_id)
            return
        if self._writer is not None:
            try:
                await self._writer.persist(session_id, list(await stream.history.get_events()))
            except Exception as exc:
                from assistant.observability import log_suppressed

                log_suppressed("external stream event persist", exc, session_id=session_id)
                # Persistence is best-effort; the live event still went out.

    async def _get_stream(self, session_id: str):
        """Return the session's live Stream, hydrating from disk on first use."""
        from ag2.stream import MemoryStream

        stream = self._streams.get(session_id)
        if stream is None:
            stream = MemoryStream(id=session_id)
            self._streams[session_id] = stream
            if self._writer is not None and session_id not in self._loaded:
                try:
                    events = await self._writer.load(session_id)
                    if events:
                        await stream.history.replace(events)
                except Exception as exc:
                    from assistant.observability import log_suppressed

                    log_suppressed("session stream hydrate", exc, session_id=session_id)
                    # A corrupt/absent log just starts a fresh stream.
                self._loaded.add(session_id)
        return stream

    async def send_message(
        self,
        text: str,
        session_id: str = "default",
        asker=None,
        attachments: list | None = None,
        surface: str = "",
        on_event=None,
    ) -> str:
        """Send a user message to the universal agent and return its reply.

        Each session_id keeps its own persistent, resumable history (a web chat, a
        task's page, a channel — all the same agent, different streams). `surface`
        is a short paragraph describing where the user is asking (and any local
        state, e.g. a task snapshot) so the one agent has the right context.

        `asker` binds human-in-the-loop questions/permission prompts to the
        surface that made the request. `attachments` are AG2 multimodal `Input`s.
        `on_event` is an optional async callback ``(event) -> None`` that receives
        the agent's structured events (tool calls, task cards, deliverables, …) raw
        as they're emitted — the voice channel forwards them so its client folds
        them with the same reducer the text path uses. Conversation/audio events are
        omitted (voice renders those itself).
        """
        if self._agent is None:
            raise RuntimeError("Gateway not started")

        await self._maybe_onboard(asker)

        extra = self._ask_kwargs(asker)
        msg = [text, *(attachments or [])]

        async with self._session_lock(session_id):
            stream = await self._get_stream(session_id)
            prompt = universal_turn_prompt(self._config, surface)  # refresh per turn
            ask_coro = self._agent.ask(*msg, stream=stream, prompt=prompt, **extra)
            usage_handle = self._watch_usage(stream)  # tally this turn's tokens (HUD)
            try:
                try:
                    if on_event is None:
                        reply = await asyncio.wait_for(ask_coro, timeout=REPLY_TIMEOUT)
                    else:
                        reply = await self._ask_forwarding_events(stream, ask_coro, on_event)
                except Exception as exc:
                    # snapshot the error + the exact history shape that triggered it
                    from assistant.observability import capture_failure

                    await capture_failure(
                        self._config,
                        session_id=session_id,
                        surface=surface,
                        user_text=text,
                        error=exc,
                        stream=stream,
                    )
                    raise
                await self._persist_turn(session_id, stream, text, reply.body)
                return reply.body
            finally:
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

    async def _ask_forwarding_events(self, stream, ask_coro, on_event):
        """Run a turn, forwarding the agent's structured events raw to `on_event`.

        Uses AG2's stream subscription — the same event mechanism observers and the
        StreamBridge are built on, scoped to *this session's* stream so it can't
        cross-talk with other sessions sharing the one universal agent. Every event
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
            return await asyncio.wait_for(ask_coro, timeout=REPLY_TIMEOUT)
        finally:
            stream.unsubscribe(sub_id)

    async def build_voice_agent(
        self,
        session_id: str = "default",
        voice: str | None = None,
        task_id: str | None = None,
        chat_session: str | None = None,
        on_event=None,
        on_end=None,
    ):
        """A LiveAgent (Gemini Live) for a browser voice session. Its heavy work
        delegates to this same universal agent so the spoken conversation shares
        the app's tools, memory, and continuity.

        Crucially, voice rides the SAME conversation the user is looking at: on a
        task page (`task_id`) it binds that task; in the main chat (`chat_session`)
        it delegates onto that chat's session — so "this task" / "the one you just
        made" resolve against what was actually said there. We also seed the voice
        agent with a short recent-conversation snapshot for immediate grounding."""
        if self._tasks is None:
            raise RuntimeError("Voice needs the task service")
        from assistant.settings import Settings
        from assistant.system_tools import format_task
        from assistant.voice import build_voice_agent

        task_context = ""
        if task_id:
            node = await self._tasks.get_task(task_id)
            if node:
                task_context = (
                    "You are currently on a task's page. When the user says "
                    f'"this task" they mean task {task_id}. Current state:\n'
                    f"{format_task(node)}"
                )
        elif chat_session:
            recent = await self._recent_transcript(chat_session)
            if recent:
                task_context = (
                    "You're continuing an ongoing chat (the user may have been "
                    "typing before switching to voice). Recent conversation:\n"
                    f"{recent}\n\nWhen they refer back to any of it, delegate to "
                    "ask_assistant, which shares this full conversation."
                )

        async def _send_capturing(request: str, session: str, surface: str) -> str:
            # The universal agent's structured events (tool calls, the TaskCreated a
            # create_task/schedule_task emits, deliverables, …) are forwarded raw via
            # on_event so the voice client folds them with the same reducer as text —
            # no separate task-capture path needed.
            return await self.send_message(
                request,
                session_id=session,
                surface=surface,
                on_event=on_event,
            )

        async def delegate(request: str) -> str:
            if task_id:
                node = await self._tasks.get_task(task_id)  # fresh each call
                snap = format_task(node) if node else f"(task {task_id})"
                return await _send_capturing(
                    request,
                    f"task:{task_id}",  # share the task's universal stream
                    "The user is talking to you by voice while viewing this "
                    f"task; act on THIS task (id {task_id}) when they refer to "
                    f"it. Answer plainly and briefly so it can be spoken.\n\n{snap}",
                )
            # Main chat: delegate onto the SAME session so the universal agent has
            # the full text history (e.g. a task the user just created by typing).
            return await _send_capturing(
                request,
                chat_session or f"voice:{session_id}",
                "The user is talking to you by voice in this chat; the voice "
                "assistant asked you to handle this. Use the conversation history "
                "for any references like 'this task'. Answer plainly and briefly "
                "so it can be spoken aloud.",
            )

        assistant_tools = [getattr(t, "name", "") for t in getattr(self._agent, "tools", [])]
        assistant_tools = [n for n in assistant_tools if n]
        return build_voice_agent(
            self._config,
            Settings(self._config.data_dir / "settings.json"),
            self._tasks,
            delegate,
            voice=voice,
            task_context=task_context,
            on_end=on_end,
            assistant_tools=assistant_tools,
        )

    async def _recent_transcript(self, session_id: str, turns: int = 6) -> str:
        """A short plain-text snippet of the last few chat turns, for voice grounding."""
        try:
            msgs = await self.transcript(session_id)
        except Exception as exc:
            from assistant.observability import log_suppressed

            log_suppressed("recent transcript load", exc, session_id=session_id)
            return ""
        out = []
        for m in msgs[-turns:]:
            who = "User" if m.get("role") == "user" else "Assistant"
            text = (m.get("text") or "").strip()
            if text:
                out.append(f"{who}: {text}")
        return "\n".join(out)

    async def _persist_turn(self, session_id, stream, user_text, reply_text) -> None:
        """Write the session's events + a display transcript to disk."""
        if self._writer is None:
            return
        try:
            await self._writer.persist(session_id, list(await stream.history.get_events()))
            await self._append_transcript(session_id, user_text, reply_text)
        except Exception as exc:
            from assistant.observability import log_suppressed

            log_suppressed("turn persistence", exc, session_id=session_id)
            # Persistence is best-effort; never fail the user's turn.

    def _transcript_path(self, session_id: str) -> str:
        return f"{_TRANSCRIPT_PREFIX}{quote(session_id, safe='')}.json"

    async def _append_transcript(self, session_id, user_text, reply_text) -> None:
        path = self._transcript_path(session_id)
        doc = {"session_id": session_id, "messages": [], "updated": ""}
        if await self._event_store.exists(path):
            try:
                doc = json.loads(await self._event_store.read(path))
            except Exception as exc:
                from assistant.observability import log_suppressed

                log_suppressed("existing transcript read", exc, session_id=session_id)
        doc["session_id"] = session_id
        doc["messages"].append({"role": "user", "text": user_text})
        doc["messages"].append({"role": "agent", "text": reply_text})
        doc["updated"] = datetime.now().astimezone().isoformat()
        await self._event_store.write(path, json.dumps(doc))
        # After the FIRST complete exchange, name the chat once (async, non-blocking —
        # like ChatGPT/Claude). A single revision: only when there's no title yet.
        if len(doc["messages"]) == 2 and not doc.get("title"):
            asyncio.create_task(self._title_session(session_id, user_text, reply_text))

    async def _title_session(self, session_id, user_text, reply_text) -> None:
        """Generate and persist a one-shot chat title (best-effort, never overwrite)."""
        from assistant.title import generate_title

        try:
            title = await generate_title(self._config, user_text, reply_text)
        except Exception as exc:
            from assistant.observability import log_suppressed

            log_suppressed("chat title generation", exc, session_id=session_id)
            return
        if not title:
            return
        path = self._transcript_path(session_id)
        try:
            doc = json.loads(await self._event_store.read(path))
            if doc.get("title"):  # already named (single revision) — leave it
                return
            doc["title"] = title
            await self._event_store.write(path, json.dumps(doc))
        except Exception as exc:
            from assistant.observability import log_suppressed

            log_suppressed("chat title persist", exc, session_id=session_id)

    async def transcript(self, session_id: str) -> list[dict]:
        """The display transcript (role/text turns) for a session."""
        if self._event_store is None:
            return []
        path = self._transcript_path(session_id)
        if not await self._event_store.exists(path):
            return []
        try:
            return json.loads(await self._event_store.read(path)).get("messages", [])
        except Exception as exc:
            from assistant.observability import log_suppressed

            log_suppressed("transcript read", exc, session_id=session_id)
            return []

    async def list_sessions(self) -> list[dict]:
        """List persisted sessions (id, last update, preview), newest first."""
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

                log_suppressed("session listing transcript read", exc, entry=entry)
                continue
            msgs = doc.get("messages", [])
            first_user = next((m["text"] for m in msgs if m["role"] == "user"), "")
            out.append(
                {
                    "session_id": doc.get("session_id", ""),
                    "updated": doc.get("updated", ""),
                    "title": doc.get("title", ""),  # LLM-named after the first exchange
                    "preview": first_user[:80],
                    "turns": len(msgs) // 2,
                }
            )
        out.sort(key=lambda s: s["updated"], reverse=True)
        return out

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

    def _ask_kwargs(self, asker) -> dict:
        """Per-turn hitl_hook + dependencies bound to this request's asker."""
        from assistant.permissions import PermissionManager

        deps: dict = {
            PermissionManager: PermissionManager(
                self._permissions, asker, sandbox=self._config.tools.sandbox
            )
        }
        out: dict = {"dependencies": deps}
        if asker is not None:
            from assistant.hitl import build_hitl_hook

            out["hitl_hook"] = build_hitl_hook(asker)
        return out

    def status(self) -> dict:
        """Lightweight status snapshot for health endpoints."""
        return {
            "status": "ok" if self._agent is not None else "stopped",
            "model": self._config.llm.model,
            "memory": self._memory,
            "platform": self._platform,
            "sessions": len(self._streams),
        }

    async def close(self) -> None:
        """Release in-memory session state (persisted sessions stay on disk)."""
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
