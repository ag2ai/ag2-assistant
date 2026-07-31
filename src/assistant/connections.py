"""Connection registry persisted to ``<root>/connections.json``.

A **Connection** is one configured instance of a messaging platform, with its own
identity. A platform can be connected as many times as the user wants — two
Telegram bots are two Connections — so the Connection id, not the platform string,
is what the rest of the install keys by. The platform survives as a *field*,
telling the system which adapter to construct and which surfaces exist.

Install-level state, a sibling of the profile and Peer registries (ADR 0019): a
Connection is never owned by a Profile.

Read/write style mirrors ``peers.py`` / ``profiles.py``: a small read-modify-write
over a JSON file, tolerant of a missing/malformed file. Reading also performs the
one-shot migration of an install that already has bot tokens — see :func:`_migrate`.
"""

import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from secrets import token_hex

from assistant import pairing, peers, profiles, secrets
from assistant.config import data_dir
from assistant.profiles import CHANNEL_PLATFORMS, CHANNEL_TOKEN_ENVS

# What a Connection of each platform is called when the user does not name it.
PLATFORM_TITLES = {"telegram": "Telegram", "discord": "Discord", "slack": "Slack"}


@dataclass(frozen=True)
class Connection:
    """One configured instance of a platform. ``id`` is opaque and immutable."""

    id: str
    platform: str
    name: str
    created_at: str


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _path() -> Path:
    return data_dir() / "connections.json"


def _read_file() -> list[dict] | None:
    """The stored entries, or None when there is no readable registry file — which
    is what triggers the one-shot migration."""
    try:
        data = json.loads(_path().read_text())
    except Exception:
        return None
    if not isinstance(data, dict):
        return None
    entries = data.get("connections")
    if not isinstance(entries, list):
        return []
    return [e for e in entries if isinstance(e, dict) and e.get("id") and e.get("platform")]


def _write(entries: list[dict]) -> None:
    p = _path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"connections": entries}, indent=2))


def _load() -> list[dict]:
    """Every stored entry, migrating a token-seeded install on first read. The
    registry file is the done-marker, so migration happens exactly once."""
    entries = _read_file()
    if entries is not None:
        return entries
    entries = _migrate()
    _write(entries)
    return entries


def _connection(entry: dict) -> Connection:
    platform = entry["platform"]
    return Connection(
        id=entry["id"],
        platform=platform,
        name=entry.get("name") or PLATFORM_TITLES.get(platform, platform),
        created_at=entry.get("created_at", ""),
    )


def _new(platform: str, name: str) -> Connection:
    return Connection(id="cn_" + token_hex(4), platform=platform, name=name, created_at=_now())


def list_connections() -> list[Connection]:
    """Every Connection, in the order they were created."""
    return [_connection(e) for e in _load()]


def get_connection(cid: str) -> Connection | None:
    """The Connection with this id, or None when there is none."""
    return next((c for c in list_connections() if c.id == cid), None)


def connections_for(platform: str) -> list[Connection]:
    """Every Connection of one platform, in creation order."""
    return [c for c in list_connections() if c.platform == platform]


def default_name(platform: str, entries: list[dict] | None = None) -> str:
    """``Telegram``, then ``Telegram 2`` — the name a Connection gets when the user
    does not choose one."""
    entries = _load() if entries is None else entries
    title = PLATFORM_TITLES.get(platform, platform)
    taken = {(e.get("name") or "").strip() for e in entries}
    if title not in taken:
        return title
    n = 2
    while f"{title} {n}" in taken:
        n += 1
    return f"{title} {n}"


def create_connection(platform: str, name: str = "", tokens: dict | None = None) -> Connection:
    """Register a Connection for ``platform`` with the token(s) it will run on and
    return it; an empty name is defaulted. Unknown platform → ValueError."""
    if platform not in CHANNEL_PLATFORMS:
        raise ValueError(
            f"unknown channel platform: {platform} (choose from {', '.join(CHANNEL_PLATFORMS)})"
        )
    entries = _load()
    connection = _new(platform, (name or "").strip() or default_name(platform, entries))
    secrets.set_connection_tokens(connection.id, tokens or {})
    entries.append(asdict(connection))
    _write(entries)
    return connection


def rename_connection(cid: str, name: str) -> Connection:
    """Change a Connection's display name (its id is immutable). Blank name or
    unknown id → ValueError."""
    name = (name or "").strip()
    if not name:
        raise ValueError("connection name is required")
    entries = _load()
    for entry in entries:
        if entry.get("id") == cid:
            entry["name"] = name
            _write(entries)
            return _connection(entry)
    raise ValueError(f"unknown connection: {cid}")


def set_tokens(cid: str, tokens: dict) -> None:
    """Store token value(s) on a Connection, keyed by env-var name; an empty value
    clears one. Unknown id → ValueError."""
    if get_connection(cid) is None:
        raise ValueError(f"unknown connection: {cid}")
    secrets.set_connection_tokens(cid, tokens)


def tokens_for(cid: str) -> dict:
    """A Connection's raw token(s) by env-var name — for adapter construction only,
    never for an API response."""
    return secrets.connection_tokens(cid)


def token_status(cid: str) -> dict:
    """Per-token ``{set, hint}`` for every token this Connection's platform needs."""
    connection = get_connection(cid)
    envs = CHANNEL_TOKEN_ENVS[connection.platform] if connection else ()
    return secrets.connection_token_status(cid, envs)


def _seeded_platforms() -> list[str]:
    """Platforms this install already has every token for, from the secrets store
    or the process env."""
    present = secrets.channel_token_status()
    return [p for p in CHANNEL_PLATFORMS if all(present.get(e) for e in CHANNEL_TOKEN_ENVS[p])]


def _migrate() -> list[dict]:
    """One Connection per platform that already has its token(s), named after the
    platform and holding the token(s) it was seeded from. Each inherits that platform's
    default Profile, paired accounts, live pairing code and every Peer recorded against
    it, so an existing install's conversations continue in place and nobody who could
    reach it loses access. The entries are written by the caller in one go —
    and the tokens land first — so a half-migrated registry is not a state anyone can
    observe."""
    entries = []
    adopted = {}
    for platform in _seeded_platforms():
        connection = _new(platform, PLATFORM_TITLES[platform])
        secrets.set_connection_tokens(
            connection.id, {e: secrets.channel_token(e) for e in CHANNEL_TOKEN_ENVS[platform]}
        )
        adopted[platform] = connection.id
        entries.append(asdict(connection))
    if adopted:
        profiles.adopt_channel_defaults(adopted)
        peers.adopt_connections(adopted)
        pairing.adopt_connections(adopted)
    return entries
