"""Persist ACP sessions as ordinary Chats.

Implements ``ag2.history.Storage`` on top of the assistant's own transcript
convention (``gateway/core.py``'s ``/transcript/{chat_id}.json`` docs in the
profile's ``chats.db``), so an ACP conversation shows up in the bound profile's
chat list like any other. A Chat is born lazily, on the first prompt of a
session — never on a bare ``session/new`` — and ``drop_history`` (reached only
for an explicit close, since every listener sets ``retain_history=True``) forgets
the replay history; the Chat and its transcript are never deleted.

The same events are also written to ``/acp-history/{stream_id}.jsonl`` in that
``chats.db``, which is what ``session/load`` reads: upstream refuses any session
id whose history comes back empty, and stdio runs every turn in a fresh process,
so a memory-only slice would lose the conversation on each message.

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
from ag2.events import BaseEvent, ModelRequest, ModelResponse, TextInput, UnknownEvent
from ag2.events._serialization import import_event_class, qualified_name
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

# Replay history for `session/load`, keyed by stream id rather than chat id:
# upstream resolves a session id straight to its stream id, and never the reverse.
_HISTORY_PREFIX = "/acp-history/"

__all__ = (
    "LIVE_SESSIONS",
    "ChatBackedStorage",
    "ChatTrackingACPAgent",
    "LiveSessionRegistry",
    "purge_history_for_chat",
)


def _transcript_path(chat_id: str) -> str:
    return f"{_TRANSCRIPT_PREFIX}{quote(chat_id, safe='')}.json"


def _history_path(stream_id: UUID) -> str:
    return f"{_HISTORY_PREFIX}{stream_id.hex}.jsonl"


def _event_record(event: BaseEvent) -> str:
    """One history line, in the same tagged shape ``ag2.knowledge.log`` writes."""
    return json.dumps({"type": qualified_name(event), "data": event.to_dict()}, default=str)


def _load_event(record: dict[str, Any]) -> BaseEvent:
    """The event a history line names, or an ``UnknownEvent`` carrying its payload
    when the class is gone or its shape has moved on."""
    type_name, data = record.get("type", ""), record.get("data", {})
    cls = import_event_class(type_name)
    if cls is not None:
        with contextlib.suppress(Exception):
            return cls.from_dict(data)
    return UnknownEvent(type_name=type_name, data=data)


def _history_chat_id(raw: str) -> str | None:
    """The Chat a stored history belongs to — the last ``{"chat": ...}`` line, same
    rule ``_rehydrate`` applies."""
    chat_id: str | None = None
    for line in raw.splitlines():
        if not line.startswith('{"chat"'):
            continue
        try:
            chat_id = json.loads(line)["chat"]
        except (ValueError, KeyError):
            continue
    return chat_id


async def purge_history_for_chat(store: Any, chat_id: str) -> bool:
    """Delete the ACP replay history belonging to ``chat_id``. True if any was removed.

    Called by ``Gateway.delete_chat``, which is keyed by Chat while this history is
    keyed by stream — so the owner is found by reading each header. Skipping it would
    let a deleted Chat come back: the next ``session/load`` rehydrates its id and
    writes a fresh transcript at that path.
    """
    removed = False
    for entry in await store.list(_HISTORY_PREFIX):
        path = f"{_HISTORY_PREFIX}{entry}"
        raw = await store.read(path)
        if raw and _history_chat_id(raw) == chat_id:
            await store.delete(path)
            removed = True
    return removed


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
    plus the Chat it has been born into and the user text awaiting a reply.

    ``persisted``/``persisted_chat_id`` mark how much of that slice already reached
    the durable history, so each flush appends only what is new.
    """

    __slots__ = ("events", "chat_id", "pending_user_text", "persisted", "persisted_chat_id")

    def __init__(self) -> None:
        self.events: list[BaseEvent] = []
        self.chat_id: str | None = None
        self.pending_user_text: list[str] = []
        self.persisted: int = 0
        self.persisted_chat_id: str | None = None


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
    and ``ChatBackedStorage.detach`` (reached from ``drop_history`` on an explicit
    close, and from the connection's own teardown) — deregisters its entry.
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

        Called by ``ChatTrackingACPAgent`` right after ``SessionStore.create`` or
        ``get_or_adopt`` mints both ids — the only point they are available together.
        A stream rehydrated by ``session/load`` already knows its Chat and never
        reaches ``_birth_chat``, so this is where that Chat rejoins the registry.
        """
        self._sessions[stream_id] = _SessionContext(session_id, connection_id, store)
        state = self._streams.get(stream_id)
        if state is not None and state.chat_id is not None:
            LIVE_SESSIONS.register(state.chat_id, store, session_id)

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
            await self._flush(stream_id, state)
            return

        if isinstance(event, ModelResponse):
            if not _is_final_response(event) or state.chat_id is None:
                return  # mid-turn tool-call round, or a response with no recorded prompt
            user_text = "\n".join(state.pending_user_text)
            state.pending_user_text.clear()
            await self._append_reply(state.chat_id, user_text, event.content or "")
            await self._mirror_event(state.chat_id, event)
            await self._flush(stream_id, state)

    async def get_history(self, stream_id: UUID) -> Iterable[BaseEvent]:
        """This stream's events — from the live slice, or from storage when the
        process that served it is gone.

        The cold read is what makes ``session/load`` work at all over stdio, where
        upstream refuses any id whose history reads back empty and every turn runs
        in a fresh process.
        """
        state = self._streams.get(stream_id)
        if state is None:
            state = await self._rehydrate(stream_id)
        return list(state.events) if state is not None else []

    async def set_history(self, stream_id: UUID, events: Iterable[BaseEvent]) -> None:
        state = self._streams.setdefault(stream_id, _StreamState())
        state.events = list(events)
        state.persisted = 0
        state.persisted_chat_id = None
        await self._store.delete(_history_path(stream_id))
        await self._flush(stream_id, state)

    async def detach(self, stream_id: UUID) -> None:
        """Let go of a stream's live slice, leaving its stored history to be loaded.

        The disconnect path. ``retain_history=True`` stops upstream from calling
        ``drop_history`` when a connection ends, so this is what now clears the
        live-session registry — over stdio a disconnect is usually just the
        process being recycled between turns, not the conversation ending.
        """
        state = self._streams.pop(stream_id, None)
        if state is not None and state.chat_id is not None:
            LIVE_SESSIONS.deregister(state.chat_id)
        self._sessions.pop(stream_id, None)

    async def drop_history(self, stream_id: UUID) -> None:
        """Detach and forget the replay history; leave the Chat and transcript alone.

        With ``retain_history=True`` upstream reaches here only for an explicit
        ``SessionStore.close`` — the deliberate "this conversation is over" act.
        """
        await self.detach(stream_id)
        await self._store.delete(_history_path(stream_id))

    async def _flush(self, stream_id: UUID, state: _StreamState) -> None:
        """Append whatever of ``state`` has not reached storage yet.

        The Chat id rides the same log as a ``{"chat": ...}`` line so a cold reader
        recovers it without a second lookup; the last such line wins.
        """
        lines: list[str] = []
        if state.chat_id is not None and state.chat_id != state.persisted_chat_id:
            lines.append(json.dumps({"chat": state.chat_id}))
        lines.extend(_event_record(event) for event in state.events[state.persisted :])
        if not lines:
            return
        await self._store.append(_history_path(stream_id), "\n".join(lines) + "\n")
        state.persisted = len(state.events)
        state.persisted_chat_id = state.chat_id

    async def _rehydrate(self, stream_id: UUID) -> "_StreamState | None":
        """Rebuild a stream's slice from storage, or None when it has no history.

        A line that will not parse is skipped rather than failing the load: a
        truncated tail costs that turn, not the whole conversation.
        """
        raw = await self._store.read(_history_path(stream_id))
        if not raw:
            return None
        state = _StreamState()
        for line in raw.splitlines():
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except ValueError:
                continue
            if "chat" in record:
                state.chat_id = record["chat"]
                continue
            state.events.append(_load_event(record))
        if not state.events and state.chat_id is None:
            return None
        state.persisted = len(state.events)
        state.persisted_chat_id = state.chat_id
        self._streams[stream_id] = state
        return state

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

    Both mint points are wrapped: ``create`` for ``session/new`` and
    ``get_or_adopt`` for the ``session/load`` that revives a stream this process
    never issued. Every other method (``get``, ``stream``, ``admit``,
    ``running_turn``, ``close``, ...) is used as-is via ``__getattr__``.
    """

    __slots__ = ("_inner", "_on_created", "_on_detached", "_seen")

    def __init__(self, inner: "SessionStore", on_created: Any, on_detached: Any) -> None:
        self._inner = inner
        self._on_created = on_created
        self._on_detached = on_detached
        self._seen: set[UUID] = set()

    async def create(self, **context: Any) -> Any:
        session = await self._inner.create(**context)
        self._note(session)
        return session

    async def get_or_adopt(self, stream_id: UUID, **context: Any) -> Any:
        session, adopted = await self._inner.get_or_adopt(stream_id, **context)
        self._note(session)
        return session, adopted

    async def aclose(self) -> None:
        """Close the connection's sessions, then detach every stream it touched.

        Upstream keeps their histories (``retain_history``), so nothing else drops
        the live-session registry entries this connection registered.
        """
        streams = tuple(self._seen)
        self._seen.clear()
        try:
            await self._inner.aclose()
        finally:
            for stream_id in streams:
                await self._on_detached(stream_id)

    def _note(self, session: Any) -> None:
        self._seen.add(session.stream_id)
        self._on_created(session.stream_id, session.session_id)

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
            self._chat_storage.detach,
        )
        return scope
