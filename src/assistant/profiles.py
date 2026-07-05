"""Profile registry persisted to ``<root>/profiles.json``.

A profile is a named, colour-coded runtime; on disk it is a directory under
``<root>/profiles/<id>``. This module owns the registry file only — booting the
runtimes, migration, and guardrails live elsewhere (ProfileManager, later).

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

_SLUG_RE = re.compile(r"[^a-z0-9]+")


@dataclass(frozen=True)
class ProfileMeta:
    """A profile's registry entry. ``id`` is immutable once created."""

    id: str
    name: str
    palette: str
    workspace: str
    created: str
    archived: bool = field(default=False)


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _slug(name: str) -> str:
    """Lowercase, alphanumeric-plus-dashes slug (dashes collapsed and trimmed)."""
    return _SLUG_RE.sub("-", name.strip().lower()).strip("-")


def _default_workspace(name: str) -> str:
    return str(Path.home() / "Documents" / "AG2 Assistant" / name.strip())


def _path() -> Path:
    return data_dir() / "profiles.json"


def profile_dir(pid: str) -> Path:
    """The on-disk directory for a profile (does NOT create it)."""
    return data_dir() / "profiles" / pid


def load_registry() -> dict:
    """Read the registry (empty, well-formed default if the file is absent/broken)."""
    try:
        data = json.loads(_path().read_text())
    except Exception:
        return {"active_default": None, "onboarded": False, "profiles": []}
    if not isinstance(data, dict):
        return {"active_default": None, "onboarded": False, "profiles": []}
    data.setdefault("active_default", None)
    data.setdefault("onboarded", False)
    if not isinstance(data.get("profiles"), list):
        data["profiles"] = []
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
        workspace=entry["workspace"],
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


def create_profile(name: str, palette: str, workspace: str | None = None) -> ProfileMeta:
    """Create a profile. Slug id derived from name (deduped -2/-3…); first profile
    becomes ``active_default``; workspace defaults to ~/Documents/AG2 Assistant/<Name>."""
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
        workspace=workspace or _default_workspace(name),
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


def set_workspace(pid: str, workspace: str) -> ProfileMeta:
    """Change a profile's workspace folder (registry-level; runtime reload is elsewhere)."""
    data = load_registry()
    entry = _find(data, pid)
    entry["workspace"] = workspace
    _write(data)
    return _meta(entry)


def archive_profile(pid: str) -> ProfileMeta:
    """Mark a profile archived (registry-level only; guardrails live in ProfileManager)."""
    data = load_registry()
    entry = _find(data, pid)
    entry["archived"] = True
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
