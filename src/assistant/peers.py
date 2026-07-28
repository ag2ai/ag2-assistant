"""Peer registry persisted to ``<root>/peers.json``.

A **Peer** is one conversation on the platform side — a direct message or a group —
identified by platform plus that platform's chat id. It holds the **Profile** that
conversation talks to, so a selection survives a restart. Install-level state, a
sibling of the profile registry (ADR 0019).

Read/write style mirrors ``profiles.py``: a small read-modify-write over a JSON
file, tolerant of a missing/malformed file (treated as no peers).
"""

import json
import secrets
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path

from assistant.config import data_dir


@dataclass(frozen=True)
class Peer:
    """One platform-side conversation and what it remembers."""

    platform: str
    chat_id: str  # the platform's own chat/conversation id
    surface: str = "dm"  # "dm" | "group"
    profile: str | None = None  # the selected profile's id
    chat: str | None = None  # the Chat it is Attached to, if any
    chats: list[str] = field(default_factory=list)  # every Chat it has spoken in


def _path() -> Path:
    return data_dir() / "peers.json"


def _load() -> list[dict]:
    """Every stored peer entry (empty if the file is absent or malformed)."""
    try:
        data = json.loads(_path().read_text())
    except Exception:
        return []
    entries = data.get("peers") if isinstance(data, dict) else None
    return entries if isinstance(entries, list) else []


def _write(entries: list[dict]) -> None:
    p = _path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"peers": entries}, indent=2))


def _peer(entry: dict) -> Peer:
    chats = entry.get("chats")
    return Peer(
        platform=entry["platform"],
        chat_id=entry["chat_id"],
        surface=entry.get("surface", "dm"),
        profile=entry.get("profile"),
        chat=entry.get("chat"),
        chats=list(chats) if isinstance(chats, list) else [],
    )


def _index(entries: list[dict], platform: str, chat_id: str) -> int | None:
    """Where this conversation sits in the registry, or None when it is new."""
    for i, entry in enumerate(entries):
        if entry.get("platform") == platform and entry.get("chat_id") == chat_id:
            return i
    return None


def _save(entries: list[dict], index: int | None, peer: Peer) -> Peer:
    """Write ``peer`` at ``index``, appending when it is new."""
    if index is None:
        entries.append(asdict(peer))
    else:
        entries[index] = asdict(peer)
    _write(entries)
    return peer


def get_peer(platform: str, chat_id: str) -> Peer | None:
    """The Peer for this conversation, or None if it has never been recorded."""
    for entry in _load():
        if entry.get("platform") == platform and entry.get("chat_id") == chat_id:
            return _peer(entry)
    return None


def list_peers() -> list[Peer]:
    """Every recorded Peer, in registry order."""
    return [_peer(entry) for entry in _load()]


def attached_to(chat: str) -> Peer | None:
    """The Peer Attached to this Chat right now, or None when no Peer is in it."""
    for entry in _load():
        if entry.get("chat") == chat:
            return _peer(entry)
    return None


def peer_for_chat(chat: str) -> Peer | None:
    """The Peer this Chat belongs to, or None for one no Peer has ever been in."""
    for entry in _load():
        if chat in (entry.get("chats") or []):
            return _peer(entry)
    return None


def select_profile(platform: str, chat_id: str, pid: str, *, surface: str = "dm") -> Peer:
    """Point this conversation at profile ``pid`` and return the resulting Peer.
    Replacing a different profile detaches it; the Chat is started lazily."""
    entries = _load()
    index = _index(entries, platform, chat_id)
    current = _peer(entries[index]) if index is not None else Peer(platform, chat_id)
    switched = current.profile is not None and current.profile != pid
    return _save(
        entries,
        index,
        replace(
            current,
            surface=surface,
            profile=pid,
            chat=None if switched else current.chat,
        ),
    )


def attach(platform: str, chat_id: str, chat: str, *, surface: str = "dm") -> Peer:
    """Attach this conversation to ``chat``, creating nothing. The Chat joins the
    Peer's own, so a Task started in it still delivers back to this conversation."""
    entries = _load()
    index = _index(entries, platform, chat_id)
    current = _peer(entries[index]) if index is not None else Peer(platform, chat_id, surface)
    chats = current.chats if chat in current.chats else [*current.chats, chat]
    return _save(entries, index, replace(current, chat=chat, chats=chats))


def start_chat(platform: str, chat_id: str, *, surface: str = "dm") -> str:
    """Start a fresh Chat for this conversation, attach the Peer to it, and return
    its id — opaque and origin-prefixed, never a platform address."""
    chat = f"{platform}-{secrets.token_hex(4)}"
    attach(platform, chat_id, chat, surface=surface)
    return chat


def detach(platform: str, chat_id: str) -> None:
    """Leave the attached Chat as it is; the next message starts a fresh one."""
    entries = _load()
    index = _index(entries, platform, chat_id)
    if index is None:
        return
    current = _peer(entries[index])
    if current.chat is not None:
        _save(entries, index, replace(current, chat=None))


def forget_chat(chat: str) -> None:
    """Drop a deleted Chat from the Peer that started it."""
    entries = _load()
    for i, entry in enumerate(entries):
        current = _peer(entry)
        if chat not in current.chats:
            continue
        _save(
            entries,
            i,
            replace(
                current,
                chat=None if current.chat == chat else current.chat,
                chats=[c for c in current.chats if c != chat],
            ),
        )
        return
