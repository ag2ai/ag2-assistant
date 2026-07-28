"""Peer registry persisted to ``<root>/peers.json``.

A **Peer** is one conversation on the platform side — a direct message or a group —
identified by platform plus that platform's chat id. It holds the **Profile** that
conversation talks to, so a selection survives a restart. Install-level state, a
sibling of the profile registry (ADR 0019).

Read/write style mirrors ``profiles.py``: a small read-modify-write over a JSON
file, tolerant of a missing/malformed file (treated as no peers).
"""

import json
from dataclasses import asdict, dataclass
from pathlib import Path

from assistant.config import data_dir

# Separates a Chat id's platform address from its switch count:
# "telegram:42#2". ``chat_address`` recovers the address a push is delivered to.
_CHAT_SEP = "#"


@dataclass(frozen=True)
class Peer:
    """One platform-side conversation and what it remembers."""

    platform: str
    chat_id: str  # the platform's own chat/conversation id
    surface: str = "dm"  # "dm" | "group"
    profile: str | None = None  # the selected profile's id
    chat_seq: int = 0  # bumped on every Profile switch (see _CHAT_SEP)

    def chat(self) -> str:
        """The gateway Chat id this Peer's turns run on."""
        address = f"{self.platform}:{self.chat_id}"
        return address if self.chat_seq == 0 else f"{address}{_CHAT_SEP}{self.chat_seq}"


def chat_address(chat_id: str) -> str:
    """Strip a Chat id's switch discriminator, leaving the platform address a push
    is delivered to. Already-plain ids pass through unchanged."""
    return chat_id.partition(_CHAT_SEP)[0]


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
    return Peer(
        platform=entry["platform"],
        chat_id=entry["chat_id"],
        surface=entry.get("surface", "dm"),
        profile=entry.get("profile"),
        chat_seq=int(entry.get("chat_seq", 0)),
    )


def get_peer(platform: str, chat_id: str) -> Peer | None:
    """The Peer for this conversation, or None if it has never been recorded."""
    for entry in _load():
        if entry.get("platform") == platform and entry.get("chat_id") == chat_id:
            return _peer(entry)
    return None


def list_peers() -> list[Peer]:
    """Every recorded Peer, in registry order."""
    return [_peer(entry) for entry in _load()]


def select_profile(platform: str, chat_id: str, pid: str, *, surface: str = "dm") -> Peer:
    """Point this conversation at profile ``pid`` and return the resulting Peer.
    Replacing a different profile also moves the Peer to a fresh Chat; re-selecting
    the one it already holds leaves its Chat alone. The Chat is minted lazily by the
    first message, not here."""
    entries = _load()
    for entry in entries:
        if entry.get("platform") == platform and entry.get("chat_id") == chat_id:
            current = _peer(entry)
            switched = current.profile is not None and current.profile != pid
            peer = Peer(
                platform=platform,
                chat_id=chat_id,
                surface=surface,
                profile=pid,
                chat_seq=current.chat_seq + 1 if switched else current.chat_seq,
            )
            entries[entries.index(entry)] = asdict(peer)
            _write(entries)
            return peer

    peer = Peer(platform=platform, chat_id=chat_id, surface=surface, profile=pid)
    entries.append(asdict(peer))
    _write(entries)
    return peer
