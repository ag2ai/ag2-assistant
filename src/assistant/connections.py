"""Connection registry persisted to ``<root>/connections.json``: one Connection is one
configured instance of a platform, keyed by id, with ``platform`` naming its adapter."""

import json
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from secrets import token_hex

from assistant.pairing import PairingStore
from assistant.paths import Paths
from assistant.peers import PeerStore
from assistant.profiles import CHANNEL_PLATFORMS, CHANNEL_TOKEN_ENVS, ProfileRegistry
from assistant.secrets import SecretStore

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


class ConnectionStore:
    """One install's Connections (``connections.json``) and everything hung off them.

    A Connection is install-level and never owned by a profile (ADR 0022). The stores
    it coordinates — tokens, profile exposure, paired accounts, Peers — are built from
    the same layout, so a Connection deleted here leaves nothing behind.
    """

    def __init__(self, paths: Paths, env: Mapping[str, str] | None = None) -> None:
        self._paths = paths
        self._path = paths.root / "connections.json"
        # The environment a first Connection's tokens are seeded from. Nothing else
        # reads it: a registered Connection holds its own tokens.
        self._env = env or {}
        self._profiles = ProfileRegistry(paths)
        self._secrets = SecretStore(paths)
        self._pairing = PairingStore(paths)
        self._peers = PeerStore(paths)

    # ---- storage --------------------------------------------------------------

    def _read_file(self) -> tuple[list[dict], bool] | None:
        """The stored entries and whether their adoption finished, or None when the file
        does not exist. A malformed file reads as no Connections, already adopted."""
        if not self._path.exists():
            return None
        try:
            data = json.loads(self._path.read_text())
        except Exception:
            return [], True
        if not isinstance(data, dict):
            return [], True
        entries = data.get("connections")
        if not isinstance(entries, list):
            entries = []
        kept = [e for e in entries if isinstance(e, dict) and e.get("id") and e.get("platform")]
        return kept, bool(data.get("adopted"))

    def _write(self, entries: list[dict], adopted: bool = True) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(json.dumps({"connections": entries, "adopted": adopted}, indent=2))

    def _load(self) -> list[dict]:
        """Every stored entry, migrating a token-seeded install when no file exists and
        finishing an adoption that a crash interrupted."""
        read = self._read_file()
        if read is None:
            return self._migrate()
        entries, adopted = read
        if not adopted:
            self._adopt(entries)
            self._write(entries)
        return entries

    # ---- the Connections ------------------------------------------------------

    def list_connections(self) -> list[Connection]:
        """Every Connection, in the order they were created."""
        return [_connection(e) for e in self._load()]

    def get_connection(self, cid: str) -> Connection | None:
        """The Connection with this id, or None when there is none."""
        return next((c for c in self.list_connections() if c.id == cid), None)

    def connections_for(self, platform: str) -> list[Connection]:
        """Every Connection of one platform, in creation order."""
        return [c for c in self.list_connections() if c.platform == platform]

    def first_by_platform(self) -> dict[str, str]:
        """Each platform's first Connection id — the id a leftover platform key moves
        onto."""
        by: dict[str, str] = {}
        for connection in self.list_connections():
            by.setdefault(connection.platform, connection.id)
        return by

    def default_name(self, platform: str, entries: list[dict] | None = None) -> str:
        """``Telegram``, then ``Telegram 2`` — the name a Connection gets when the user
        does not choose one."""
        entries = self._load() if entries is None else entries
        title = PLATFORM_TITLES.get(platform, platform)
        taken = {(e.get("name") or "").strip() for e in entries}
        if title not in taken:
            return title
        n = 2
        while f"{title} {n}" in taken:
            n += 1
        return f"{title} {n}"

    def create_connection(
        self, platform: str, name: str = "", tokens: Mapping[str, str] | None = None
    ) -> Connection:
        """Register a Connection for ``platform`` with the token(s) it will run on and
        return it; an empty name is defaulted. Unknown platform → ValueError."""
        if platform not in CHANNEL_PLATFORMS:
            raise ValueError(
                f"unknown channel platform: {platform} (choose from {', '.join(CHANNEL_PLATFORMS)})"
            )
        entries = self._load()
        connection = _new(platform, (name or "").strip() or self.default_name(platform, entries))
        self._secrets.set_connection_tokens(connection.id, tokens or {})
        entries.append(asdict(connection))
        self._write(entries)
        return connection

    def rename_connection(self, cid: str, name: str) -> Connection:
        """Change a Connection's display name (its id is immutable). Blank name or
        unknown id → ValueError."""
        name = (name or "").strip()
        if not name:
            raise ValueError("connection name is required")
        entries = self._load()
        for entry in entries:
            if entry.get("id") == cid:
                entry["name"] = name
                self._write(entries)
                return _connection(entry)
        raise ValueError(f"unknown connection: {cid}")

    def delete_connection(self, cid: str) -> None:
        """Forget a Connection and everything hung off it — its token(s), exposure
        records, default Profile, paired accounts, live pairing code and Peers. Unknown
        id → ValueError."""
        entries = self._load()
        kept = [e for e in entries if e.get("id") != cid]
        if len(kept) == len(entries):
            raise ValueError(f"unknown connection: {cid}")
        self._write(kept)
        self.clear_exposure(cid)
        self._profiles.set_connection_default(cid, None)
        self._secrets.clear_connection_tokens(cid)
        self._pairing.forget_connection(cid)
        self._peers.forget_connection(cid)

    # ---- tokens ---------------------------------------------------------------

    def set_tokens(self, cid: str, tokens: Mapping[str, str]) -> None:
        """Store token value(s) on a Connection, keyed by env-var name; an empty value
        clears one. Unknown id → ValueError."""
        if self.get_connection(cid) is None:
            raise ValueError(f"unknown connection: {cid}")
        self._secrets.set_connection_tokens(cid, tokens)

    def tokens_for(self, cid: str) -> dict:
        """A Connection's raw token(s) by env-var name — for adapter construction only,
        never for an API response."""
        return self._secrets.connection_tokens(cid)

    def token_status(self, cid: str) -> dict:
        """Per-token ``{set, hint}`` for every token this Connection's platform needs."""
        connection = self.get_connection(cid)
        envs = CHANNEL_TOKEN_ENVS[connection.platform] if connection else ()
        return self._secrets.connection_token_status(cid, envs)

    # ---- Profile exposure (per Connection, default-allow; ADR 0022) ------------

    def exposure(self, cid: str) -> dict[str, dict[str, bool]]:
        """Every unarchived profile's reachability on each of this Connection's surfaces.
        Default-allow: a profile with no withdrawal recorded reads True everywhere.
        Unknown id → ValueError."""
        connection = self.get_connection(cid)
        if connection is None:
            raise ValueError(f"unknown connection: {cid}")
        keys = surfaces(connection).values()
        return {
            meta.id: {s: s not in meta.withdrawn for s in keys}
            for meta in self._profiles.list_profiles()
        }

    def reachable(self, cid: str, pid: str) -> bool:
        """Whether this profile is still reachable on any surface of this Connection."""
        return any(self.exposure(cid).get(pid, {}).values())

    def set_exposure(self, cid: str, pid: str, surface: str, exposed: bool) -> None:
        """Expose or withdraw one profile on one surface of this Connection; a withdrawal
        that takes its last surface clears it as the default. Unknown id or surface →
        ValueError."""
        connection = self.get_connection(cid)
        if connection is None:
            raise ValueError(f"unknown connection: {cid}")
        keys = surfaces(connection)
        if surface not in keys.values():
            raise ValueError(
                f"unknown surface for {connection.name}: {surface} "
                f"(choose from {', '.join(keys.values())})"
            )
        self._profiles.set_exposure(pid, surface, exposed)
        if self._profiles.connection_defaults().get(cid) == pid and not self.reachable(cid, pid):
            self._profiles.set_connection_default(cid, None)

    def clear_exposure(self, cid: str) -> None:
        """Drop every withdrawal recorded against this Connection's surfaces, on every
        profile — archived ones included, so nothing outlives the Connection."""
        for meta in self._profiles.list_profiles(include_archived=True):
            for surface in meta.withdrawn:
                if surface == cid or surface.startswith(f"{cid}:"):
                    self._profiles.set_exposure(meta.id, surface, True)

    def set_default_profile(self, cid: str, pid: str | None) -> None:
        """Set (or clear, with ``None``) the profile this Connection's conversations land
        in by default. A profile withdrawn from every surface of the Connection is
        refused — the Connection cannot default to somewhere it cannot reach."""
        meta = self._profiles.get_profile(pid) if pid is not None else None
        if meta is not None and not meta.archived and not self.reachable(cid, pid):
            raise ValueError(f"profile not reachable from this connection: {pid}")
        self._profiles.set_connection_default(cid, pid)

    # ---- migration from the platform-keyed era --------------------------------

    def _seeded_platforms(self) -> list[str]:
        """Platforms this install already has every token for, from the secrets store
        or the environment it was wired with."""
        return [
            p
            for p in CHANNEL_PLATFORMS
            if all(self._secrets.channel_token(e, self._env) for e in CHANNEL_TOKEN_ENVS[p])
        ]

    def _adopt(self, entries: list[dict]) -> None:
        """Seed each entry's token(s) and move that platform's default Profile, exposure,
        Peers and paired accounts onto it. Re-runnable."""
        by_platform: dict[str, str] = {}
        for entry in entries:
            cid, platform = entry["id"], entry["platform"]
            by_platform.setdefault(platform, cid)
            if not self._secrets.connection_tokens(cid):
                self._secrets.set_connection_tokens(
                    cid,
                    {
                        e: self._secrets.channel_token(e, self._env)
                        for e in CHANNEL_TOKEN_ENVS[platform]
                    },
                )
        if not by_platform:
            return
        self._profiles.adopt_channel_defaults(by_platform)
        self._profiles.adopt_exposure(by_platform)
        self._peers.adopt_connections(by_platform)
        self._pairing.adopt_connections(by_platform)
        self.adopt_peer_senders()

    def adopt_peer_senders(self) -> int:
        """Stamp the account onto every Peer recorded before senders were, against this
        install's paired accounts. Idempotent: a stamped Peer is left alone."""
        return self._peers.adopt_senders(self._pairing.is_paired)

    def _migrate(self) -> list[dict]:
        """One Connection per platform that already has its token(s), its ids persisted
        before anything is stamped with them. Seeding nothing writes no file, so a
        reader without the install's environment cannot lock migration out."""
        entries = [asdict(_new(p, PLATFORM_TITLES[p])) for p in self._seeded_platforms()]
        if not entries:
            return []
        self._write(entries, adopted=False)
        self._adopt(entries)
        self._write(entries)
        return entries
