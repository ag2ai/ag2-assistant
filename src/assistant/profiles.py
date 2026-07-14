"""Profile registry persisted to ``<root>/profiles.json``.

A profile is a named, colour-coded runtime; on disk it is a directory under
``<root>/profiles/<id>``. This module owns the registry file only — booting the
runtimes and guardrails live elsewhere (ProfileManager).

Read/write style mirrors ``settings.py``: a small read-modify-write over a JSON
file, tolerant of a missing/malformed file (treated as empty registry).
"""

import json
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from assistant.config import data_dir

# The 6 design-system palettes (web/src/design/tokens/palettes.css).
PALETTES = ("teal", "coral", "ocean", "violet", "sage", "sunset")

# The canonical messaging platforms a channel can bind to. This is the single
# source of truth for platform names.
CHANNEL_PLATFORMS = ("telegram", "discord", "slack")

# Platform → env vars that must ALL be present for its channel to run. Lives here
# (dependency-light) so both ProfileManager and the secrets store can import it
# without a circular dependency. Slack needs BOTH a bot token and an app token.
CHANNEL_TOKEN_ENVS = {
    "telegram": ("TELEGRAM_BOT_TOKEN",),
    "discord": ("DISCORD_BOT_TOKEN",),
    "slack": ("SLACK_BOT_TOKEN", "SLACK_APP_TOKEN"),
}
assert set(CHANNEL_TOKEN_ENVS) == set(CHANNEL_PLATFORMS)

# Every channel env var name, flattened — the closed set the secrets store accepts.
CHANNEL_TOKEN_ENV_NAMES = frozenset(e for envs in CHANNEL_TOKEN_ENVS.values() for e in envs)

_SLUG_RE = re.compile(r"[^a-z0-9]+")


@dataclass(frozen=True)
class ProfileMeta:
    """A profile's registry entry. ``id`` is immutable once created."""

    id: str
    name: str
    palette: str
    created: str
    archived: bool = field(default=False)

    @property
    def workspace(self) -> str:
        """The agent's working file folder — always ``<profile dir>/workspace``. Derived,
        never stored: the user cannot pick it (every profile lives under the install root)."""
        return str(profile_dir(self.id) / "workspace")


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _slug(name: str) -> str:
    """Lowercase, alphanumeric-plus-dashes slug (dashes collapsed and trimmed)."""
    return _SLUG_RE.sub("-", name.strip().lower()).strip("-")


def _path() -> Path:
    return data_dir() / "profiles.json"


def profile_dir(pid: str) -> Path:
    """The on-disk directory for a profile (does NOT create it)."""
    return data_dir() / "profiles" / pid


def _empty_registry() -> dict:
    return {
        "active_default": None,
        "onboarded": False,
        "profiles": [],
        "channels": {p: None for p in CHANNEL_PLATFORMS},
    }


def load_registry() -> dict:
    """Read the registry (empty, well-formed default if the file is absent/broken)."""
    try:
        data = json.loads(_path().read_text())
    except Exception:
        return _empty_registry()
    if not isinstance(data, dict):
        return _empty_registry()
    data.setdefault("active_default", None)
    data.setdefault("onboarded", False)
    if not isinstance(data.get("profiles"), list):
        data["profiles"] = []
    # channels is a top-level {platform: owning-pid|null} map; absent platforms
    # read as null (unbound). Malformed → treated as all-unbound.
    chans = data.get("channels")
    if not isinstance(chans, dict):
        chans = {}
    data["channels"] = {p: chans.get(p) for p in CHANNEL_PLATFORMS}
    return data


def _write(data: dict) -> None:
    p = _path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, indent=2))


def _meta(entry: dict) -> ProfileMeta:
    return ProfileMeta(
        id=entry["id"],
        name=entry["name"],
        palette=entry["palette"],
        created=entry["created"],
        archived=bool(entry.get("archived", False)),
    )


def list_profiles(include_archived: bool = False) -> list[ProfileMeta]:
    """Registered profiles in registry order (archived hidden unless asked)."""
    out = []
    for entry in load_registry()["profiles"]:
        meta = _meta(entry)
        if meta.archived and not include_archived:
            continue
        out.append(meta)
    return out


def get_profile(pid: str) -> ProfileMeta | None:
    """The profile with this id, or None if absent (archived included)."""
    for entry in load_registry()["profiles"]:
        if entry["id"] == pid:
            return _meta(entry)
    return None


def _find(data: dict, pid: str) -> dict:
    for entry in data["profiles"]:
        if entry["id"] == pid:
            return entry
    raise ValueError(f"unknown profile: {pid}")


def _check_palette(palette: str, data: dict, *, exclude: str | None = None) -> None:
    """Validate a palette value and enforce per-profile uniqueness while ≤6 unarchived
    profiles exist (the palette is the visual identity); reuse is allowed beyond 6."""
    if palette not in PALETTES:
        raise ValueError(f"invalid palette: {palette} (choose from {', '.join(PALETTES)})")
    unarchived = [e for e in data["profiles"] if not e.get("archived")]
    # Uniqueness holds only while ≤6 unarchived profiles exist; once all 6 palettes
    # are spoken for, further profiles may reuse one (spec §3.2).
    if len(unarchived) >= len(PALETTES):
        return
    for entry in unarchived:
        if entry["id"] == exclude:
            continue
        if entry["palette"] == palette:
            raise ValueError(f"palette already in use: {palette}")


def create_profile(name: str, palette: str) -> ProfileMeta:
    """Create a profile. Slug id derived from name (deduped -2/-3…); first profile
    becomes ``active_default``. The workspace is derived from the profile dir (not a
    user choice) — see ``ProfileMeta.workspace``."""
    name = name.strip()
    if not name:
        raise ValueError("profile name is required")
    data = load_registry()
    _check_palette(palette, data)

    base = _slug(name) or "profile"
    existing = {e["id"] for e in data["profiles"]}
    pid = base
    n = 2
    while pid in existing:
        pid = f"{base}-{n}"
        n += 1

    meta = ProfileMeta(
        id=pid,
        name=name,
        palette=palette,
        created=_now(),
    )
    data["profiles"].append(asdict(meta))
    if data["active_default"] is None:
        data["active_default"] = pid
    _write(data)
    return meta


def rename_profile(pid: str, name: str) -> ProfileMeta:
    """Change a profile's display name (id is immutable)."""
    name = name.strip()
    if not name:
        raise ValueError("profile name is required")
    data = load_registry()
    entry = _find(data, pid)
    entry["name"] = name
    _write(data)
    return _meta(entry)


def set_palette(pid: str, palette: str) -> ProfileMeta:
    """Change a profile's palette (validated + uniqueness-checked, self excluded)."""
    data = load_registry()
    entry = _find(data, pid)
    _check_palette(palette, data, exclude=pid)
    entry["palette"] = palette
    _write(data)
    return _meta(entry)


def archive_profile(pid: str) -> ProfileMeta:
    """Mark a profile archived and clear any channel bindings pointing at it
    (registry-level only; runtime guardrails live in ProfileManager)."""
    data = load_registry()
    entry = _find(data, pid)
    entry["archived"] = True
    for platform, owner in data["channels"].items():
        if owner == pid:
            data["channels"][platform] = None
    _write(data)
    return _meta(entry)


def set_active_default(pid: str) -> None:
    """Set the server-side fallback profile."""
    data = load_registry()
    _find(data, pid)
    data["active_default"] = pid
    _write(data)


def is_onboarded() -> bool:
    """The install-level onboarding flag (§4.2)."""
    return bool(load_registry().get("onboarded"))


def set_onboarded(value: bool = True) -> None:
    """Set the install-level onboarding flag."""
    data = load_registry()
    data["onboarded"] = bool(value)
    _write(data)


# --- channel bindings (install-level: a platform binds to one profile or is off) ---


def channel_bindings() -> dict[str, str | None]:
    """The install-level channel→profile map. Every canonical platform is present;
    an unbound platform reads as ``None``."""
    return dict(load_registry()["channels"])


def bind_channel(platform: str, pid: str | None) -> None:
    """Bind ``platform`` to profile ``pid`` (or ``None`` to disable it).

    Validates the platform against the canonical list and, when ``pid`` is given,
    that the profile exists and is not archived. Two profiles enabling the same
    channel is structurally impossible — a platform maps to exactly one pid."""
    if platform not in CHANNEL_PLATFORMS:
        raise ValueError(
            f"unknown channel platform: {platform} (choose from {', '.join(CHANNEL_PLATFORMS)})"
        )
    data = load_registry()
    if pid is not None:
        entry = next((e for e in data["profiles"] if e["id"] == pid), None)
        if entry is None:
            raise ValueError(f"unknown profile: {pid}")
        if entry.get("archived"):
            raise ValueError(f"profile archived: {pid}")
    data["channels"][platform] = pid
    _write(data)
