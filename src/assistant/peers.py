"""Peer registry persisted to ``<root>/peers.json``: one conversation, keyed by the
Connection it arrived on plus the chat id, holding the Profile it talks to."""

import json
import secrets
from collections.abc import Callable
from dataclasses import asdict, dataclass, field, replace

from assistant.paths import Paths


@dataclass(frozen=True)
class Peer:
    """One platform-side conversation and what it remembers."""

    connection: str  # the Connection this conversation arrived on
    chat_id: str  # the platform's own chat/conversation id
    platform: str = ""  # which platform that Connection runs on
    surface: str = "dm"  # "dm" | "group"
    sender: str = ""  # the account that last spoke here; "" when none was recorded
    profile: str | None = None  # the selected profile's id
    chat: str | None = None  # the Chat it is Attached to, if any
    chats: list[str] = field(default_factory=list)  # every Chat it has spoken in


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

    def _load(self) -> list[dict]:
        """Every stored peer entry (empty if the file is absent or malformed)."""
        try:
            data = json.loads(self._path.read_text())
        except Exception:
            return []
        entries = data.get("peers") if isinstance(data, dict) else None
        return entries if isinstance(entries, list) else []

    def _write(self, entries: list[dict]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(json.dumps({"peers": entries}, indent=2))

    def _save(self, entries: list[dict], index: int | None, peer: Peer) -> Peer:
        """Write ``peer`` at ``index``, appending when it is new."""
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
        entries = self._load()
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
        Peer's own, so a Task started in it still delivers back to this conversation."""
        entries = self._load()
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

    def detach(self, connection: str, chat_id: str) -> None:
        """Leave the attached Chat as it is; the next message starts a fresh one."""
        entries = self._load()
        index = _index(entries, connection, chat_id)
        if index is None:
            return
        current = _peer(entries[index])
        if current.chat is not None:
            self._save(entries, index, replace(current, chat=None))

    def forget_chat(self, chat: str) -> None:
        """Drop a deleted Chat from the Peer that started it."""
        entries = self._load()
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
        entries = self._load()
        kept = [e for e in entries if e.get("connection") != connection]
        if len(kept) != len(entries):
            self._write(kept)

    def adopt_senders(self, is_paired: Callable[[str, str], bool]) -> int:
        """Stamp each sender-less Peer whose chat id ``is_paired`` recognises with that
        account, and return how many moved."""
        entries = self._load()
        moved = [
            e
            for e in entries
            if not e.get("sender") and is_paired(e.get("connection") or "", e["chat_id"])
        ]
        for entry in moved:
            entry["sender"] = entry["chat_id"]
        if moved:
            self._write(entries)
        return len(moved)

    def adopt_connections(self, by_platform: dict[str, str]) -> None:
        """Stamp the Connection migrated for each platform onto every Peer recorded against
        that platform, so an existing install's conversations continue in place."""
        entries = self._load()
        for entry in entries:
            connection = by_platform.get(entry.get("platform"))
            if connection and not entry.get("connection"):
                entry["connection"] = connection
        if entries:
            self._write(entries)
