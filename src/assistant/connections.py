"""Connection registry persisted to ``<root>/connections.json``.

A **Connection** is one configured instance of a messaging platform, with its own
identity. A platform can be connected as many times as the user wants — two
Telegram bots are two Connections — so the Connection id, not the platform string,
is what the rest of the install keys by. The platform survives as a *field*,
telling the system which adapter to construct and which surfaces exist.

Install-level state, a sibling of the profile and Peer registries (ADR 0019): a
Connection is never owned by a Profile.

Read/write style mirrors ``peers.py`` / ``profiles.py``: a small read-modify-write
over a JSON file, tolerant of a missing/malformed file. Reading migrates an install
that already has bot tokens — see :func:`_migrate`.
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

# Platforms whose direct messages and groups are exposed independently.
SPLIT_PLATFORMS = ("telegram",)


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


def _read_file() -> tuple[list[dict], bool] | None:
    """The stored entries and whether their adoption finished, or None when the file
    does not exist. A malformed file reads as no Connections, already adopted."""
    path = _path()
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text())
    except Exception:
        return [], True
    if not isinstance(data, dict):
        return [], True
    entries = data.get("connections")
    if not isinstance(entries, list):
        entries = []
    kept = [e for e in entries if isinstance(e, dict) and e.get("id") and e.get("platform")]
    return kept, bool(data.get("adopted"))


def _write(entries: list[dict], adopted: bool = True) -> None:
    p = _path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"connections": entries, "adopted": adopted}, indent=2))


def _load() -> list[dict]:
    """Every stored entry, migrating a token-seeded install when no file exists and
    finishing an adoption that a crash interrupted."""
    read = _read_file()
    if read is None:
        return _migrate()
    entries, adopted = read
    if not adopted:
        _adopt(entries)
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


def delete_connection(cid: str) -> None:
    """Forget a Connection and everything hung off it — its token(s), exposure records,
    default Profile, paired accounts, live pairing code and Peers. Unknown id → ValueError."""
    entries = _load()
    kept = [e for e in entries if e.get("id") != cid]
    if len(kept) == len(entries):
        raise ValueError(f"unknown connection: {cid}")
    _write(kept)
    clear_exposure(cid)
    profiles.set_connection_default(cid, None)
    secrets.clear_connection_tokens(cid)
    pairing.forget_connection(cid)
    peers.forget_connection(cid)


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


# --- Profile exposure (per Connection, default-allow; ADR 0019) ---


def surface_key(cid: str, platform: str, surface: str) -> str:
    """The exposure surface a conversation sits on: ``<cid>:dm`` / ``<cid>:group`` on a
    platform whose two are independent, the Connection's own id on the rest."""
    return f"{cid}:{surface}" if platform in SPLIT_PLATFORMS else cid


def surfaces(connection: Connection) -> dict[str, str]:
    """This Connection's exposure surfaces by kind — ``dm`` and ``group`` where the two
    are switched independently, a single ``all`` where they are not."""
    if connection.platform in SPLIT_PLATFORMS:
        return {kind: f"{connection.id}:{kind}" for kind in ("dm", "group")}
    return {"all": connection.id}


def exposure(cid: str) -> dict[str, dict[str, bool]]:
    """Every unarchived profile's reachability on each of this Connection's surfaces.
    Default-allow: a profile with no withdrawal recorded reads True everywhere.
    Unknown id → ValueError."""
    connection = get_connection(cid)
    if connection is None:
        raise ValueError(f"unknown connection: {cid}")
    keys = surfaces(connection).values()
    return {
        meta.id: {s: s not in meta.withdrawn for s in keys} for meta in profiles.list_profiles()
    }


def reachable(cid: str, pid: str) -> bool:
    """Whether this profile is still reachable on any surface of this Connection."""
    return any(exposure(cid).get(pid, {}).values())


def set_exposure(cid: str, pid: str, surface: str, exposed: bool) -> None:
    """Expose or withdraw one profile on one surface of this Connection; a withdrawal that
    takes its last surface clears it as the default. Unknown id or surface → ValueError."""
    connection = get_connection(cid)
    if connection is None:
        raise ValueError(f"unknown connection: {cid}")
    keys = surfaces(connection)
    if surface not in keys.values():
        raise ValueError(
            f"unknown surface for {connection.name}: {surface} "
            f"(choose from {', '.join(keys.values())})"
        )
    profiles.set_exposure(pid, surface, exposed)
    if profiles.connection_defaults().get(cid) == pid and not reachable(cid, pid):
        profiles.set_connection_default(cid, None)


def clear_exposure(cid: str) -> None:
    """Drop every withdrawal recorded against this Connection's surfaces, on every
    profile — archived ones included, so nothing outlives the Connection."""
    for meta in profiles.list_profiles(include_archived=True):
        for surface in meta.withdrawn:
            if surface == cid or surface.startswith(f"{cid}:"):
                profiles.set_exposure(meta.id, surface, True)


def set_default_profile(cid: str, pid: str | None) -> None:
    """Set (or clear, with ``None``) the profile this Connection's conversations land in
    by default. A profile withdrawn from every surface of the Connection is refused —
    the Connection cannot default to somewhere it cannot reach."""
    meta = profiles.get_profile(pid) if pid is not None else None
    if meta is not None and not meta.archived and not reachable(cid, pid):
        raise ValueError(f"profile not reachable from this connection: {pid}")
    profiles.set_connection_default(cid, pid)


def _seeded_platforms() -> list[str]:
    """Platforms this install already has every token for, from the secrets store
    or the process env."""
    present = secrets.channel_token_status()
    return [p for p in CHANNEL_PLATFORMS if all(present.get(e) for e in CHANNEL_TOKEN_ENVS[p])]


def _adopt(entries: list[dict]) -> None:
    """Seed each entry's token(s) from the env and move that platform's default Profile,
    exposure, Peers and paired accounts onto it. Re-runnable."""
    by_platform: dict[str, str] = {}
    for entry in entries:
        cid, platform = entry["id"], entry["platform"]
        by_platform.setdefault(platform, cid)
        if not secrets.connection_tokens(cid):
            secrets.set_connection_tokens(
                cid, {e: secrets.channel_token(e) for e in CHANNEL_TOKEN_ENVS[platform]}
            )
    if not by_platform:
        return
    profiles.adopt_channel_defaults(by_platform)
    profiles.adopt_exposure(by_platform)
    peers.adopt_connections(by_platform)
    pairing.adopt_connections(by_platform)


def _migrate() -> list[dict]:
    """One Connection per platform that already has its token(s). The ids are persisted
    before anything is stamped with them; adoption re-runs until it is marked done."""
    entries = [asdict(_new(p, PLATFORM_TITLES[p])) for p in _seeded_platforms()]
    _write(entries, adopted=False)
    _adopt(entries)
    _write(entries)
    return entries
