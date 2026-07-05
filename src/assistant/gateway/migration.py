"""One-time legacy → multi-profile migration (§3.3).

Idempotent. On the first startup after multi-profile landed, if the install has
legacy per-profile files at the root but no ``profiles/`` tree yet, move them into
``profiles/default/`` and write a ``default`` registry entry. No dual-path support
afterwards — all code reads only the new layout (project rule: no legacy shims).
"""

import json
import shutil
from pathlib import Path

from assistant import profiles
from assistant.config import load_config
from assistant.observability import profile_logger

# Legacy per-profile items that live at the root pre-migration and move into the
# default profile dir. Dirs and files alike.
_LEGACY_ITEMS = (
    "settings.json",
    "sessions.db",
    "tasks.db",
    "inquiries.db",
    "profile.db",
    "permissions.json",
    "usage.json",
    "skills",
    "debug",
)

# Platform → env vars that must ALL be present for its channel to run. Migration
# writes ``channels: {<platform>: {enabled: true}}`` into the default profile for
# each platform whose token(s) are currently set, preserving current behaviour.
_CHANNEL_TOKENS = {
    "telegram": ("TELEGRAM_BOT_TOKEN",),
    "discord": ("DISCORD_BOT_TOKEN",),
    "slack": ("SLACK_BOT_TOKEN", "SLACK_APP_TOKEN"),
}


def _has_legacy(root: Path) -> bool:
    """Whether any legacy per-profile item is present at the root."""
    return any((root / name).exists() for name in _LEGACY_ITEMS)


def _read_legacy_onboarded(settings_file: Path, marker: Path) -> bool:
    """Legacy install-level onboarded flag, from the settings.json key OR the marker
    file (either counts). Read BEFORE the settings file is moved. Tolerant of absence."""
    if marker.exists():
        return True
    try:
        return bool(json.loads(settings_file.read_text()).get("onboarded"))
    except Exception:
        return False


def _strip_onboarded_key(settings_file: Path) -> None:
    """Remove the ``onboarded`` key from a (moved) settings.json — the registry is its
    only home afterwards (§4.2). Best-effort; a malformed file is left alone."""
    try:
        data = json.loads(settings_file.read_text())
    except Exception:
        return
    if not isinstance(data, dict) or "onboarded" not in data:
        return
    data.pop("onboarded", None)
    settings_file.write_text(json.dumps(data, indent=2))


def migrate_if_needed(root: Path | None = None) -> bool:
    """Run the legacy migration if applicable. Returns True if migration happened.

    Condition: ``root/profiles/`` absent AND at least one legacy item present. Fresh
    installs (nothing at all) and already-migrated installs (``profiles/`` exists) are
    both no-ops.
    """
    if root is None:
        root = load_config().root_dir
    root = Path(root)

    if (root / "profiles").exists():
        return False  # already migrated (or fresh install that created profiles/)
    if not _has_legacy(root):
        return False  # fresh install — nothing to migrate

    log = profile_logger("default")
    dest = root / "profiles" / "default"
    dest.mkdir(parents=True, exist_ok=True)

    # Read the legacy onboarded flag BEFORE moving settings.json.
    legacy_settings = root / "settings.json"
    marker = root / "onboarded"
    onboarded = _read_legacy_onboarded(legacy_settings, marker)

    # Capture the current effective workspace as the default profile's workspace.
    workspace = str(load_config().workspace_dir)

    for name in _LEGACY_ITEMS:
        src = root / name
        if not src.exists():
            continue
        target = dest / name
        shutil.move(str(src), str(target))
        log.info("migration: moved %s → %s", src, target)

    # Strip the now-redundant onboarded key from the moved settings file, and record
    # which channels were env-enabled so post-migration channel startup is unchanged.
    moved_settings = dest / "settings.json"
    if moved_settings.exists():
        _strip_onboarded_key(moved_settings)
    _seed_channels(moved_settings)

    # Delete the legacy marker (the registry is the flag's only home now).
    if marker.exists():
        marker.unlink()
        log.info("migration: removed legacy onboarded marker %s", marker)

    # Write the registry: one "default" profile, active + onboarded carried over.
    meta = profiles.create_profile("Default", "teal", workspace=workspace)
    if meta.id != "default":
        # create_profile slugs "Default" → "default"; assert the assumption held.
        log.warning("migration: default profile got unexpected id %s", meta.id)
    profiles.set_active_default(meta.id)
    if onboarded:
        profiles.set_onboarded(True)
    log.info(
        "migration: registry written (profile=%s workspace=%s onboarded=%s)",
        meta.id,
        workspace,
        onboarded,
    )
    return True


def _seed_channels(settings_file: Path) -> None:
    """Write ``channels: {<platform>: {enabled: true}}`` into the default profile's
    settings for each platform whose token env vars are currently all set — preserving
    the env-token-driven behaviour of existing installs (§4.5)."""
    import os

    from assistant.settings import Settings

    settings = Settings(settings_file)
    for platform, envs in _CHANNEL_TOKENS.items():
        if all(os.environ.get(e) for e in envs):
            settings.set_channel_enabled(platform, True)
