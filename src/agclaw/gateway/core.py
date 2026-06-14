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

from agclaw.agent import create_agent, turn_prompt
from agclaw.config import Config, load_config

REPLY_TIMEOUT = 120.0
_TRANSCRIPT_PREFIX = "/transcript/"


class Gateway:
    """Manages per-session, resumable conversations with the AGClaw agent."""

    def __init__(
        self,
        config: Config | None = None,
        memory: bool = True,
        platform: str = "gateway",
        onboard: bool = True,
        persist: bool = True,
    ) -> None:
        self._config = config or load_config()
        self._memory = memory
        self._platform = platform
        self._onboard = onboard
        self._persist = persist
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
        from agclaw.permissions import PermissionStore

        self._agent = create_agent(
            self._config, memory=self._memory, platform=self._platform
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

    async def _get_stream(self, session_id: str):
        """Return the session's live Stream, hydrating from disk on first use."""
        from autogen.beta.stream import MemoryStream

        stream = self._streams.get(session_id)
        if stream is None:
            stream = MemoryStream(id=session_id)
            self._streams[session_id] = stream
            if self._writer is not None and session_id not in self._loaded:
                try:
                    events = await self._writer.load(session_id)
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
    ) -> str:
        """Send a user message and return the agent's reply.

        Each session_id keeps its own persistent, resumable history. Calls within
        a session are serialised so the conversation stays consistent.

        `asker` binds human-in-the-loop questions/permission prompts to the
        surface that made the request. `attachments` are AG2 multimodal `Input`s
        sent alongside the text.
        """
        if self._agent is None:
            raise RuntimeError("Gateway not started")

        await self._maybe_onboard(asker)

        extra = self._ask_kwargs(asker)
        msg = [text, *(attachments or [])]

        async with self._session_lock(session_id):
            stream = await self._get_stream(session_id)
            prompt = turn_prompt(self._config)  # refresh date/time each turn
            reply = await asyncio.wait_for(
                self._agent.ask(*msg, stream=stream, prompt=prompt, **extra),
                timeout=REPLY_TIMEOUT,
            )
            await self._persist_turn(session_id, stream, text, reply.body)
            return reply.body

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

        deps: dict = {PermissionManager: PermissionManager(self._permissions, asker)}
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
