"""Persist ACP sessions as ordinary Chats.

Implements ``ag2.history.Storage`` on top of the assistant's own transcript
convention (``gateway/core.py``'s ``/transcript/{chat_id}.json`` docs in the
profile's ``chats.db``), so an ACP conversation shows up in the bound profile's
chat list like any other. A Chat is born lazily, on the first prompt of a
session — never on a bare ``session/new`` — and ``drop_history`` (called by
upstream at session close, idle-TTL expiry, and eviction) only detaches the
live in-memory stream; the Chat and its transcript are never deleted.

``ag2.acp.history.Storage.save_event`` only ever sees a stream id
(``context.stream.id``); the ACP session id it should become a Peer's
``chat_id`` lives on ``AgentSession``, one layer up, with no seam connecting
the two. ``ChatTrackingACPAgent`` below closes that gap by wrapping each
connection's ``SessionStore.create`` to report the pairing the moment both
ids are minted — the one place they exist together (``sessions.py``'s
``SessionStore.create``).

``LIVE_SESSIONS`` is the chat-id -> live-session registry the
gateway's chat routes read to show a "live" badge and offer a close button.
"""

import contextlib
import json
from collections.abc import Awaitable, Callable, Iterable
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import quote
from uuid import UUID

from ag2.acp.agent import ACPAgent
from ag2.acp.sessions import UnknownSessionError
from ag2.context import ConversationContext
from ag2.events import BaseEvent, ModelRequest, ModelResponse, TextInput
from ag2.knowledge import SqliteKnowledgeStore

from assistant.paths import Paths
from assistant.peers import PeerStore
from assistant.storage import SerialStore, now_iso

if TYPE_CHECKING:
    import acp
    from ag2.acp.agent import _ConnectionScope
    from ag2.acp.sessions import SessionStore

# Must match gateway/core.py's `_TRANSCRIPT_PREFIX` exactly — this is the
# convention the web UI's chat list and transcript reader already use.
_TRANSCRIPT_PREFIX = "/transcript/"

__all__ = ("LIVE_SESSIONS", "ChatBackedStorage", "ChatTrackingACPAgent", "LiveSessionRegistry")


def _transcript_path(chat_id: str) -> str:
    return f"{_TRANSCRIPT_PREFIX}{quote(chat_id, safe='')}.json"


def _request_text(event: ModelRequest) -> str:
    """The user-visible text of a ``ModelRequest`` (non-text parts are dropped —
    the display transcript only ever carried text, same as Gateway's own)."""
    return "\n".join(
        part.content for part in event.parts if isinstance(part, TextInput) and part.content
    )


def _is_final_response(event: ModelResponse) -> bool:
    """Whether ``event`` ends its turn's model-call loop (``ag2.agent._execute_turn``'s
    own condition for returning rather than looping on tool calls)."""
    return not event.tool_calls or event.response_force


class _StreamState:
    """One session's live slice: raw events (for get_history/set_history fidelity)
    plus the Chat it has been born into and the user text awaiting a reply."""

    __slots__ = ("events", "chat_id", "pending_user_text")

    def __init__(self) -> None:
        self.events: list[BaseEvent] = []
        self.chat_id: str | None = None
        self.pending_user_text: list[str] = []


class _SessionContext:
    """The ACP identity a stream was minted with, reported by ``note_session``."""

    __slots__ = ("session_id", "connection_id", "store")

    def __init__(self, session_id: str, connection_id: str, store: Any) -> None:
        self.session_id = session_id
        self.connection_id = connection_id
        self.store = store


class LiveSessionRegistry:
    """chat_id -> the live ACP session behind it, so the owner's "close session"
    button in the GUI has something to act on.

    Holds a strong reference to each session's real ``SessionStore`` — both it and
    ``_ConnectionScope`` are ``__slots__`` classes with no ``__weakref__``, so a
    literal ``weakref.ref`` is not on the table. Safety instead comes from never
    outliving the session: every path a session can end — our own ``close`` below,
    and ``ChatBackedStorage.drop_history`` (which upstream calls at session close,
    idle-TTL expiry, and eviction alike) — deregisters its entry.
    """

    def __init__(self) -> None:
        self._entries: dict[str, tuple[Any, str]] = {}

    def register(self, chat_id: str, store: Any, session_id: str) -> None:
        self._entries[chat_id] = (store, session_id)

    def deregister(self, chat_id: str) -> None:
        self._entries.pop(chat_id, None)

    def live_chat_ids(self) -> frozenset[str]:
        """Every chat currently backed by a live ACP session."""
        return frozenset(self._entries)

    async def close(self, chat_id: str) -> bool:
        """Close the live session behind ``chat_id``: the client is dropped, the
        Chat and its transcript are untouched (drop_history only detaches).

        False when ``chat_id`` is not live — never was one, or the session ended
        (naturally or via a concurrent close) between the caller's check and here.
        """
        entry = self._entries.pop(chat_id, None)
        if entry is None:
            return False
        store, session_id = entry
        with contextlib.suppress(UnknownSessionError):
            await store.close(session_id)
        return True


# One process-wide registry: chat routes (a different module) read it to show
# liveness and to act on a close request.
LIVE_SESSIONS = LiveSessionRegistry()


class ChatBackedStorage:
    """``ag2.history.Storage`` that writes each session's turns to a real Chat.

    ``data_dir`` is the bound profile's own (``AG2ASSISTANT_DATA_DIR``-rooted),
    so the transcript survives a container restart and is read by the same
    ``chats.db`` the profile's Gateway serves the web UI from.
    """

    def __init__(
        self,
        *,
        paths: Paths,
        data_dir: Path,
        profile: str,
        mirror: "Callable[[str, BaseEvent], Awaitable[None]] | None" = None,
    ) -> None:
        self._peers = PeerStore(paths)
        self._store = SerialStore(SqliteKnowledgeStore(str(data_dir / "chats.db")))
        self._profile = profile
        # Best-effort push of user/final-reply events onto the gateway's own chat
        # stream (Gateway.emit_event), so the web UI replays and live-streams them.
        self._mirror = mirror
        self._streams: dict[UUID, _StreamState] = {}
        self._sessions: dict[UUID, _SessionContext] = {}

    def note_session(
        self, stream_id: UUID, *, session_id: str, connection_id: str, store: Any
    ) -> None:
        """Record the (session id, connection id, live ``SessionStore``) a stream
        was created with.

        Called by ``ChatTrackingACPAgent`` right after ``SessionStore.create``
        mints both ids — the only point they are available together.
        """
        self._sessions[stream_id] = _SessionContext(session_id, connection_id, store)

    async def save_event(self, event: BaseEvent, context: ConversationContext) -> None:
        stream_id = context.stream.id
        state = self._streams.setdefault(stream_id, _StreamState())
        state.events.append(event)

        if isinstance(event, ModelRequest):
            text = _request_text(event)
            if text:
                state.pending_user_text.append(text)
            if state.chat_id is None:
                state.chat_id = await self._birth_chat(
                    stream_id, "\n".join(state.pending_user_text)
                )
            await self._mirror_event(state.chat_id, event)
            return

        if isinstance(event, ModelResponse):
            if not _is_final_response(event) or state.chat_id is None:
                return  # mid-turn tool-call round, or a response with no recorded prompt
            user_text = "\n".join(state.pending_user_text)
            state.pending_user_text.clear()
            await self._append_reply(state.chat_id, user_text, event.content or "")
            await self._mirror_event(state.chat_id, event)

    async def get_history(self, stream_id: UUID) -> Iterable[BaseEvent]:
        state = self._streams.get(stream_id)
        return list(state.events) if state is not None else []

    async def set_history(self, stream_id: UUID, events: Iterable[BaseEvent]) -> None:
        self._streams.setdefault(stream_id, _StreamState()).events = list(events)

    async def drop_history(self, stream_id: UUID) -> None:
        """Detach: drop the live stream state, leave the Chat and transcript alone.

        Called by upstream at session close, idle-TTL expiry, and eviction alike —
        the one point every "this session is over" path passes through, so it is
        also where the live-session registry is cleared.
        """
        state = self._streams.pop(stream_id, None)
        if state is not None and state.chat_id is not None:
            LIVE_SESSIONS.deregister(state.chat_id)
        self._sessions.pop(stream_id, None)

    async def _mirror_event(self, chat_id: str | None, event: BaseEvent) -> None:
        if self._mirror is None or chat_id is None:
            return
        try:
            await self._mirror(chat_id, event)
        except Exception:
            return  # a UI mirror must never fail the served turn

    async def _birth_chat(self, stream_id: UUID, first_text: str) -> str:
        corr = self._sessions.get(stream_id)
        if corr is not None:
            connection_id, peer_chat_id = corr.connection_id, corr.session_id
        else:
            # TODO(upstream): no seam exposes the session id without note_session()
            # (e.g. Storage used outside ChatTrackingACPAgent) — key by stream id.
            connection_id, peer_chat_id = "acp:unknown", str(stream_id)
        self._peers.select_profile(
            connection_id, peer_chat_id, self._profile, platform="acp", surface="dm"
        )
        chat_id = self._peers.start_chat(connection_id, peer_chat_id, platform="acp", surface="dm")
        if corr is not None:
            LIVE_SESSIONS.register(chat_id, corr.store, corr.session_id)
        await self._write_stub(chat_id, first_text)
        return chat_id

    async def _write_stub(self, chat_id: str, user_text: str) -> None:
        path = _transcript_path(chat_id)
        if await self._store.exists(path):
            return  # first prompt fires exactly once per stream; defensive only
        doc = {
            "chat_id": chat_id,
            "messages": [{"role": "user", "text": user_text}],
            "updated": now_iso(),
            "title": None,
        }
        await self._store.write(path, json.dumps(doc))

    async def _append_reply(self, chat_id: str, user_text: str, reply_text: str) -> None:
        path = _transcript_path(chat_id)
        doc: dict[str, Any] = {"chat_id": chat_id, "messages": [], "updated": ""}
        if await self._store.exists(path):
            try:
                doc = json.loads(await self._store.read(path) or "")
            except Exception:
                pass
        doc["chat_id"] = chat_id
        msgs = doc.get("messages", [])
        if msgs and msgs[-1].get("role") == "user" and msgs[-1].get("text") == user_text:
            doc["messages"] = [*msgs, {"role": "agent", "text": reply_text}]
        else:
            doc["messages"] = [
                *msgs,
                {"role": "user", "text": user_text},
                {"role": "agent", "text": reply_text},
            ]
        doc["updated"] = now_iso()
        await self._store.write(path, json.dumps(doc))


class _CorrelatingSessionStore:
    """Forwards to a real ``SessionStore``, reporting each new session's stream id.

    The only wrapping needed: every other method (``get``, ``stream``, ``admit``,
    ``running_turn``, ``close``, ...) is used as-is via ``__getattr__``.
    """

    __slots__ = ("_inner", "_on_created")

    def __init__(self, inner: "SessionStore", on_created: Any) -> None:
        self._inner = inner
        self._on_created = on_created

    async def create(self, **context: Any) -> Any:
        session = await self._inner.create(**context)
        self._on_created(session.stream_id, session.session_id)
        return session

    def __len__(self) -> int:
        return len(self._inner)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)


class ChatTrackingACPAgent(ACPAgent):
    """``ACPAgent`` whose sessions persist as Chats via a ``ChatBackedStorage``.

    Overrides ``bind`` to swap each connection's fresh ``SessionStore`` for one
    that reports session creation back to ``chat_storage`` — see module
    docstring for why this seam, rather than ``Storage`` alone, is needed.
    """

    def __init__(
        self, *args: Any, chat_storage: ChatBackedStorage, connection_id: str, **kwargs: Any
    ) -> None:
        super().__init__(*args, **kwargs)
        self._chat_storage = chat_storage
        self._connection_id = connection_id

    def bind(self, client: "acp.Client | None") -> "_ConnectionScope":
        scope = super().bind(client)
        real_store = scope._sessions  # noqa: SLF001 — kept for the close-session registry
        # A duck-typed wrapper, not a SessionStore subclass — mypy can't see the match.
        scope._sessions = _CorrelatingSessionStore(  # type: ignore[assignment]  # noqa: SLF001
            real_store,
            lambda stream_id, session_id: self._chat_storage.note_session(
                stream_id,
                session_id=session_id,
                connection_id=self._connection_id,
                store=real_store,
            ),
        )
        return scope
