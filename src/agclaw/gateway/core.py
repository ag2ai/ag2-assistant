"""Gateway core — session management over the AGClaw agent.

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
import json
from datetime import datetime
from urllib.parse import quote

from agclaw.agent import create_agent, universal_turn_prompt
from agclaw.config import Config, load_config

REPLY_TIMEOUT = 240.0
_TRANSCRIPT_PREFIX = "/transcript/"


def sanitize_history(events: list) -> list:
    """Repair a loaded event history before resume so the provider doesn't reject it.

    INTERIM guard: AG2 compaction can cut mid tool-call/result cycle, leaving a
    half-cycle at the head whose issuing ModelResponse was dropped. Gemini then
    400s ("function call/response must be adjacent") on resume. We (1) strip the
    orphaned leading tool/usage remnants that appear before the first real model
    turn (the broken cycle), and (2) defensively drop any other orphaned tool
    result. Remove once the AG2 compaction/mapper fix is pinned.
    """
    if not events:
        return events
    try:
        from autogen.beta.events import (
            ModelRequest,
            ModelResponse,
            ToolCallEvent,
            ToolCallsEvent,
            ToolResultEvent,
            ToolResultsEvent,
            UsageEvent,
        )
    except Exception:
        return events

    tool_types = (ToolCallEvent, ToolCallsEvent, ToolResultEvent, ToolResultsEvent, UsageEvent)
    # Before the first USER turn we keep only non-conversational events (e.g.
    # CompactionSummary); model/tool/result/usage events there are remnants of a
    # turn whose user start was compacted away — and Gemini requires the contents
    # to begin at a user turn (or after a function response).
    drop_in_lead = (ModelResponse,) + tool_types

    # 1) Require the conversation to begin at a user turn (ModelRequest). Drop any
    #    leading model/tool remnants before it; keep a leading CompactionSummary.
    first_user = next((i for i, e in enumerate(events) if isinstance(e, ModelRequest)), None)
    if first_user is None:
        stripped = [e for e in events if not isinstance(e, drop_in_lead)]
    else:
        lead = [e for e in events[:first_user] if not isinstance(e, drop_in_lead)]
        stripped = lead + list(events[first_user:])

    # 2) Defensive: drop any tool result whose call id isn't present anywhere.
    call_ids: set[str] = set()
    for e in stripped:
        tc = getattr(e, "tool_calls", None)
        for c in (getattr(tc, "calls", None) or []):
            if getattr(c, "id", None):
                call_ids.add(c.id)
        if isinstance(e, ToolCallsEvent):
            for c in (e.calls or []):
                if getattr(c, "id", None):
                    call_ids.add(c.id)
        if isinstance(e, ToolCallEvent) and getattr(e, "id", None):
            call_ids.add(e.id)

    def _results(e):
        if isinstance(e, ToolResultsEvent):
            return list(getattr(e, "results", []) or [])
        if isinstance(e, ToolResultEvent):
            return [e]
        return []

    out = []
    for e in stripped:
        items = _results(e)
        if items and not all(getattr(r, "parent_id", None) in call_ids for r in items):
            continue
        out.append(e)
    return out


class Gateway:
    """Manages per-session, resumable conversations with the AGClaw agent."""

    def __init__(
        self,
        config: Config | None = None,
        memory: bool = True,
        platform: str = "gateway",
        onboard: bool = True,
        persist: bool = True,
        task_starter=None,
        schedule_starter=None,
        task_service=None,
    ) -> None:
        self._config = config or load_config()
        self._memory = memory
        self._platform = platform
        self._onboard = onboard
        self._persist = persist
        self._task_starter = task_starter  # lets the chat agent spawn background tasks
        self._schedule_starter = schedule_starter  # ...and schedule them for later
        self._tasks = task_service  # gives the universal agent its system tools
        self._onboarding_done = False
        self._agent = None
        self._permissions = None
        self._event_store = None
        self._writer = None
        # session_id -> live Stream; plus which sessions we've hydrated from disk
        self._streams: dict[str, object] = {}
        self._loaded: set[str] = set()
        self._locks: dict[str, asyncio.Lock] = {}

    async def start(self) -> None:
        """Create the shared agent and (optionally) the on-disk session store."""
        from agclaw.observability import setup_logging
        from agclaw.permissions import PermissionStore

        setup_logging(self._config)  # rolling log + failure capture for debugging

        # The one universal agent: capability tools + system tools (know/do
        # everything) + compaction to keep long conversations bounded.
        extra_tools = None
        if self._tasks is not None:
            from agclaw.system_tools import build_system_tools

            extra_tools = build_system_tools(self._tasks, chats=self)
        # Note: create/schedule come from the system tools (extra_tools), so we
        # don't also wire start_task/schedule_task here (that duplicated names).
        self._agent = create_agent(
            self._config, memory=self._memory, platform=self._platform,
            extra_tools=extra_tools, compact=self._memory,
        )
        self._permissions = PermissionStore()

        if self._persist:
            from autogen.beta.knowledge import SqliteKnowledgeStore
            from autogen.beta.knowledge.log import EventLogWriter

            self._config.data_dir.mkdir(parents=True, exist_ok=True)
            self._event_store = SqliteKnowledgeStore(
                str(self._config.data_dir / "sessions.db")
            )
            self._writer = EventLogWriter(self._event_store)

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
        from autogen.beta.context import ConversationContext

        stream = await self.stream_for(session_id)
        try:
            await ConversationContext(stream=stream).send(event)
        except Exception:
            return
        if self._writer is not None:
            try:
                await self._writer.persist(
                    session_id, list(await stream.history.get_events())
                )
            except Exception:
                pass  # persistence is best-effort; the live event still went out

    async def _get_stream(self, session_id: str):
        """Return the session's live Stream, hydrating from disk on first use."""
        from autogen.beta.stream import MemoryStream

        stream = self._streams.get(session_id)
        if stream is None:
            stream = MemoryStream(id=session_id)
            self._streams[session_id] = stream
            if self._writer is not None and session_id not in self._loaded:
                try:
                    events = sanitize_history(await self._writer.load(session_id))
                    if events:
                        await stream.history.replace(events)
                except Exception:
                    pass  # a corrupt/absent log just starts a fresh stream
                self._loaded.add(session_id)
        return stream

    async def send_message(
        self,
        text: str,
        session_id: str = "default",
        asker=None,
        attachments: list | None = None,
        surface: str = "",
        on_tool=None,
    ) -> str:
        """Send a user message to the universal agent and return its reply.

        Each session_id keeps its own persistent, resumable history (a web chat, a
        task's page, a channel — all the same agent, different streams). `surface`
        is a short paragraph describing where the user is asking (and any local
        state, e.g. a task snapshot) so the one agent has the right context.

        `asker` binds human-in-the-loop questions/permission prompts to the
        surface that made the request. `attachments` are AG2 multimodal `Input`s.
        `on_tool` is an optional async callback ``(name) -> None`` invoked with each
        tool the agent calls during the turn, so a UI can show progress.
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
            try:
                if on_tool is None:
                    reply = await asyncio.wait_for(ask_coro, timeout=REPLY_TIMEOUT)
                else:
                    reply = await self._ask_watching_tools(stream, ask_coro, on_tool)
            except Exception as exc:
                # snapshot the error + the exact history shape that triggered it
                from agclaw.observability import capture_failure

                await capture_failure(
                    self._config, session_id=session_id, surface=surface,
                    user_text=text, error=exc, stream=stream,
                )
                raise
            await self._persist_turn(session_id, stream, text, reply.body)
            return reply.body

    async def _ask_watching_tools(self, stream, ask_coro, on_tool):
        """Run a turn while reporting each tool the agent invokes.

        Uses AG2's stream subscription — the same event mechanism observers are
        built on, but scoped to *this session's* stream so it can't cross-talk
        with other sessions sharing the one universal agent. The subscriber fires
        live as tool-call events are emitted; we unsubscribe when the turn ends.
        """
        from autogen.beta.events import ToolCallEvent, ToolCallsEvent

        # Each call is emitted twice — once as a ToolCallsEvent batch and once as a
        # provider ToolCallEvent (e.g. GeminiToolCallEvent), both sharing the same
        # tool-call id. Dedupe by that id so a single call reports once.
        seen: set[str] = set()

        async def report(event):  # event injected positionally by the stream
            if isinstance(event, ToolCallsEvent):
                calls = event.calls
            elif isinstance(event, ToolCallEvent):
                calls = [event]
            else:
                return
            for c in calls:
                name = getattr(c, "name", "") or ""
                if not name:
                    continue
                cid = getattr(c, "id", "") or ""
                if cid and cid in seen:
                    continue
                if cid:
                    seen.add(cid)
                try:
                    await on_tool(name)
                except Exception:
                    pass

        sub_id = stream.subscribe(report)
        try:
            return await asyncio.wait_for(ask_coro, timeout=REPLY_TIMEOUT)
        finally:
            stream.unsubscribe(sub_id)

    async def build_voice_agent(self, session_id: str = "default",
                                voice: str = "Puck", task_id: str | None = None,
                                chat_session: str | None = None, on_tool=None,
                                on_task=None):
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
        from agclaw.system_tools import format_task
        from agclaw.voice import build_voice_agent

        task_context = ""
        if task_id:
            node = await self._tasks.get_task(task_id)
            if node:
                task_context = (
                    "You are currently on a task's page. When the user says "
                    f"\"this task\" they mean task {task_id}. Current state:\n"
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
            # Capture any tasks the universal agent spawns this turn (create_task /
            # schedule_task append to started_tasks_var) and report them so the WS
            # can show a task card live — same mechanism as the text chat.
            import agclaw.agent as agent_mod

            spawned: list = []
            token = agent_mod.started_tasks_var.set(spawned)
            try:
                reply = await self.send_message(
                    request, session_id=session, surface=surface, on_tool=on_tool,
                )
            finally:
                agent_mod.started_tasks_var.reset(token)
            if on_task:
                for st in spawned:
                    try:
                        await on_task(st)
                    except Exception:
                        pass
            return reply

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

        return build_voice_agent(self._config, self._tasks, delegate,
                                 voice=voice, task_context=task_context)

    async def _recent_transcript(self, session_id: str, turns: int = 6) -> str:
        """A short plain-text snippet of the last few chat turns, for voice grounding."""
        try:
            msgs = await self.transcript(session_id)
        except Exception:
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
            await self._writer.persist(
                session_id, list(await stream.history.get_events())
            )
            await self._append_transcript(session_id, user_text, reply_text)
        except Exception:
            pass  # persistence is best-effort; never fail the user's turn

    def _transcript_path(self, session_id: str) -> str:
        return f"{_TRANSCRIPT_PREFIX}{quote(session_id, safe='')}.json"

    async def _append_transcript(self, session_id, user_text, reply_text) -> None:
        path = self._transcript_path(session_id)
        doc = {"session_id": session_id, "messages": [], "updated": ""}
        if await self._event_store.exists(path):
            try:
                doc = json.loads(await self._event_store.read(path))
            except Exception:
                pass
        doc["session_id"] = session_id
        doc["messages"].append({"role": "user", "text": user_text})
        doc["messages"].append({"role": "agent", "text": reply_text})
        doc["updated"] = datetime.now().astimezone().isoformat()
        await self._event_store.write(path, json.dumps(doc))

    async def transcript(self, session_id: str) -> list[dict]:
        """The display transcript (role/text turns) for a session."""
        if self._event_store is None:
            return []
        path = self._transcript_path(session_id)
        if not await self._event_store.exists(path):
            return []
        try:
            return json.loads(await self._event_store.read(path)).get("messages", [])
        except Exception:
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
                doc = json.loads(
                    await self._event_store.read(_TRANSCRIPT_PREFIX + entry)
                )
            except Exception:
                continue
            msgs = doc.get("messages", [])
            first_user = next((m["text"] for m in msgs if m["role"] == "user"), "")
            out.append({
                "session_id": doc.get("session_id", ""),
                "updated": doc.get("updated", ""),
                "preview": first_user[:80],
                "turns": len(msgs) // 2,
            })
        out.sort(key=lambda s: s["updated"], reverse=True)
        return out

    async def _maybe_onboard(self, asker) -> None:
        """Run first-run onboarding once, via the asker that made this request."""
        if (
            self._onboarding_done
            or not self._onboard
            or not self._memory
            or asker is None
        ):
            return
        self._onboarding_done = True  # set first: never double-prompt, even on error
        from agclaw.onboarding import needs_onboarding, run_onboarding

        try:
            if await needs_onboarding():
                await run_onboarding(asker)
        except Exception:
            pass  # onboarding is best-effort; never block the actual message

    def _ask_kwargs(self, asker) -> dict:
        """Per-turn hitl_hook + dependencies bound to this request's asker."""
        from agclaw.permissions import PermissionManager

        deps: dict = {
            PermissionManager: PermissionManager(
                self._permissions, asker, sandbox=self._config.tools.sandbox
            )
        }
        out: dict = {"dependencies": deps}
        if asker is not None:
            from agclaw.hitl import build_hitl_hook

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
