"""Peer registry persisted to ``<root>/peers.json``: one conversation, keyed by the
Connection it arrived on plus the chat id, holding the Profile it talks to.

The file is install-level, so several processes write it at once — the gateway, a
stdio ``ag2-assistant acp`` child, one per ACP client. Every mutation therefore runs
inside :meth:`PeerStore._transaction` (an exclusive ``flock`` on a sidecar lock file,
held across the whole read-modify-write) and lands through :meth:`PeerStore._write`
(temp file + ``os.replace``). Readers take no lock: the rename is atomic, so a reader
sees either the previous file or the next one, never a half-written one.
"""

import json
import os
import secrets
import tempfile
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field, replace

from assistant.paths import Paths

try:
    import fcntl
except ImportError:  # pragma: no cover - POSIX-only; the atomic write still holds
    fcntl = None  # type: ignore[assignment]


@dataclass(frozen=True)
class Peer:
    """One platform-side conversation and what it remembers."""

    connection: str  # the Connection this conversation arrived on
    chat_id: str  # the platform's own chat/conversation id
    platform: str = ""  # which platform that Connection runs on
    surface: str = "dm"  # "dm" | "group"
    sender: str = ""  # the account last served here; "" when none was recorded
    profile: str | None = None  # the selected profile's id
    chat: str | None = None  # the Chat it is Attached to, if any
    chats: list[str] = field(default_factory=list)  # every Chat it has spoken in
    # The Pending override (ADR 0025): a Text model chosen while Attached to nothing,
    # which the next message's Chat is born on. Dropped on detach, attach, or a switch.
    pending_model: str | None = None


def _peer(entry: dict) -> Peer:
    chats = entry.get("chats")
    return Peer(
        connection=entry.get("connection") or "",
        chat_id=entry["chat_id"],
        platform=entry.get("platform") or "",
        surface=entry.get("surface", "dm"),
        sender=entry.get("sender") or "",
        profile=entry.get("profile"),
        chat=entry.get("chat"),
        chats=list(chats) if isinstance(chats, list) else [],
        # Absent on an entry written before the Pending override existed, and absent
        # reads as holding nothing.
        pending_model=entry.get("pending_model") or None,
    )


def _index(entries: list[dict], connection: str, chat_id: str) -> int | None:
    """Where this conversation sits in the registry, or None when it is new."""
    for i, entry in enumerate(entries):
        if entry.get("connection") == connection and entry.get("chat_id") == chat_id:
            return i
    return None


class PeerStore:
    """One install's Peers (``peers.json``). Install-level by design (ADR 0022): a
    conversation belongs to the Connection it arrived on, not to a profile."""

    def __init__(self, paths: Paths) -> None:
        self._path = paths.root / "peers.json"
        # Sidecar, never renamed: a lock taken on peers.json itself would be held on
        # an inode the next _write replaces, so the following writer would lock a
        # different file and both would proceed.
        self._lock_path = paths.root / "peers.json.lock"

    @contextmanager
    def _transaction(self) -> Iterator[list[dict]]:
        """Hold the registry exclusively for one read-modify-write, yielding the
        entries read under the lock.

        Every mutating method runs its whole load-mutate-write inside this, so a
        second process cannot read the same entries and write back over the first
        one's append.
        """
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with open(self._lock_path, "w") as lock:
            if fcntl is not None:
                fcntl.flock(lock, fcntl.LOCK_EX)
            yield self._load()

    def _load(self) -> list[dict]:
        """Every stored peer entry (empty if the file is absent or malformed)."""
        try:
            data = json.loads(self._path.read_text())
        except Exception:
            return []
        entries = data.get("peers") if isinstance(data, dict) else None
        return entries if isinstance(entries, list) else []

    def _write(self, entries: list[dict]) -> None:
        """Replace the registry atomically — a reader never observes a partial file."""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps({"peers": entries}, indent=2)
        fd, tmp = tempfile.mkstemp(dir=self._path.parent, prefix=".peers-", suffix=".json")
        try:
            with os.fdopen(fd, "w") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp, self._path)
        except BaseException:
            os.unlink(tmp)
            raise

    def _save(self, entries: list[dict], index: int | None, peer: Peer) -> Peer:
        """Write ``peer`` at ``index``, appending when it is new. Called from inside
        a ``_transaction``, which is what makes the append safe against a second
        process."""
        if index is None:
            entries.append(asdict(peer))
        else:
            entries[index] = asdict(peer)
        self._write(entries)
        return peer

    def get_peer(self, connection: str, chat_id: str) -> Peer | None:
        """The Peer for this conversation, or None if it has never been recorded."""
        entries = self._load()
        index = _index(entries, connection, chat_id)
        return _peer(entries[index]) if index is not None else None

    def list_peers(self) -> list[Peer]:
        """Every recorded Peer, in registry order."""
        return [_peer(entry) for entry in self._load()]

    def attached_to(self, chat: str) -> Peer | None:
        """The Peer Attached to this Chat right now, or None when no Peer is in it."""
        for entry in self._load():
            if entry.get("chat") == chat:
                return _peer(entry)
        return None

    def peer_for_chat(self, chat: str) -> Peer | None:
        """The Peer this Chat belongs to, or None for one no Peer has ever been in."""
        for entry in self._load():
            if chat in (entry.get("chats") or []):
                return _peer(entry)
        return None

    def select_profile(
        self,
        connection: str,
        chat_id: str,
        pid: str,
        *,
        platform: str = "",
        surface: str = "dm",
        sender: str = "",
    ) -> Peer:
        """Point this conversation at profile ``pid`` and return the resulting Peer.
        Replacing a different profile detaches it; the Chat is started lazily."""
        with self._transaction() as entries:
            index = _index(entries, connection, chat_id)
            current = _peer(entries[index]) if index is not None else Peer(connection, chat_id)
            switched = current.profile is not None and current.profile != pid
            return self._save(
                entries,
                index,
                replace(
                    current,
                    surface=surface,
                    sender=sender or current.sender,
                    profile=pid,
                    platform=platform or current.platform,
                    chat=None if switched else current.chat,
                    # A model held for a Chat that was never started belongs to the
                    # Profile it was chosen in; leaving that Profile leaves it behind too.
                    pending_model=None if switched else current.pending_model,
                ),
            )

    def attach(
        self,
        connection: str,
        chat_id: str,
        chat: str,
        *,
        platform: str = "",
        surface: str = "dm",
        sender: str = "",
    ) -> Peer:
        """Attach this conversation to ``chat``, creating nothing. The Chat joins the
        Peer's own, so a Task started in it still delivers back to this conversation.

        Attaching drops any Pending override: a caller handing it to the Chat it is
        attaching must take it first."""
        with self._transaction() as entries:
            index = _index(entries, connection, chat_id)
            current = (
                _peer(entries[index])
                if index is not None
                else Peer(connection, chat_id, surface=surface)
            )
            chats = current.chats if chat in current.chats else [*current.chats, chat]
            return self._save(
                entries,
                index,
                replace(
                    current,
                    chat=chat,
                    chats=chats,
                    sender=sender or current.sender,
                    platform=platform or current.platform,
                    pending_model=None,
                ),
            )

    def start_chat(
        self,
        connection: str,
        chat_id: str,
        *,
        platform: str = "",
        surface: str = "dm",
        sender: str = "",
    ) -> str:
        """Start a fresh Chat for this conversation, attach the Peer to it, and return
        its id — opaque and origin-prefixed, never a platform address."""
        stored = self.get_peer(connection, chat_id)
        origin = platform or (stored.platform if stored is not None else "")
        chat = f"{origin}-{secrets.token_hex(4)}"
        self.attach(connection, chat_id, chat, platform=platform, surface=surface, sender=sender)
        return chat

    def set_pending_model(self, connection: str, chat_id: str, model: str) -> Peer:
        """Hold ``model`` for the Chat this conversation's next message starts, the empty
        string dropping what it held. Recording it makes the conversation known."""
        with self._transaction() as entries:
            index = _index(entries, connection, chat_id)
            current = _peer(entries[index]) if index is not None else Peer(connection, chat_id)
            return self._save(entries, index, replace(current, pending_model=model.strip() or None))

    def take_pending_model(self, connection: str, chat_id: str) -> str:
        """The model this conversation was holding, cleared as it is handed over — the
        Chat it starts owns it from here, so no later Chat inherits it. "" for none."""
        with self._transaction() as entries:
            index = _index(entries, connection, chat_id)
            if index is None:
                return ""
            current = _peer(entries[index])
            if current.pending_model is None:
                return ""
            self._save(entries, index, replace(current, pending_model=None))
            return current.pending_model

    def detach(self, connection: str, chat_id: str) -> None:
        """Leave the attached Chat as it is; the next message starts a fresh one. Starting
        over is a clean start, so a model held for that next Chat goes with it."""
        with self._transaction() as entries:
            index = _index(entries, connection, chat_id)
            if index is None:
                return
            current = _peer(entries[index])
            if current.chat is not None or current.pending_model is not None:
                self._save(entries, index, replace(current, chat=None, pending_model=None))

    def forget_chat(self, chat: str) -> None:
        """Drop a deleted Chat from the Peer that started it."""
        with self._transaction() as entries:
            for i, entry in enumerate(entries):
                current = _peer(entry)
                if chat not in current.chats:
                    continue
                self._save(
                    entries,
                    i,
                    replace(
                        current,
                        chat=None if current.chat == chat else current.chat,
                        chats=[c for c in current.chats if c != chat],
                    ),
                )
                return

    def forget_connection(self, connection: str) -> None:
        """Drop every Peer recorded against one Connection — its conversations end with it."""
        with self._transaction() as entries:
            kept = [e for e in entries if e.get("connection") != connection]
            if len(kept) != len(entries):
                self._write(kept)

    def adopt_senders(self, is_paired: Callable[[str, str], bool]) -> int:
        """Stamp each sender-less direct Peer with the account its chat id names — the chat
        id is offered to ``is_paired(connection, account)`` — and return how many moved."""
        with self._transaction() as entries:
            moved = [
                e
                for e in entries
                if not e.get("sender")
                and e.get("surface") == "dm"
                and is_paired(e.get("connection") or "", e["chat_id"])
            ]
            for entry in moved:
                entry["sender"] = entry["chat_id"]
            if moved:
                self._write(entries)
            return len(moved)

    def adopt_connections(self, by_platform: dict[str, str]) -> None:
        """Stamp the Connection migrated for each platform onto every Peer recorded against
        that platform, so an existing install's conversations continue in place."""
        with self._transaction() as entries:
            for entry in entries:
                connection = by_platform.get(entry.get("platform") or "")
                if connection and not entry.get("connection"):
                    entry["connection"] = connection
            if entries:
                self._write(entries)
