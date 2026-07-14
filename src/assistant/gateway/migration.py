"""One-time legacy → multi-profile migration (§3.3).

Idempotent. On the first startup after multi-profile landed, if the install has
legacy per-profile files at the root but no ``profiles/`` tree yet, move them into
``profiles/default/`` and write a ``default`` registry entry. No dual-path support
afterwards — all code reads only the new layout (project rule: no legacy shims).
"""

import json
import shutil
from pathlib import Path
from secrets import token_hex

from assistant import profiles
from assistant.config import data_dir, default_config_path, load_config
from assistant.observability import profile_logger

# Legacy per-profile items that live at the root pre-migration and move into the
# default profile dir. Dirs and files alike. NOTE: permissions.json is deliberately
# absent — it is the install-wide global store now, and it can legitimately exist at
# the root before profiles/ does (CLI grant on a fresh install); moving it would
# orphan the global grants.
_LEGACY_ITEMS = (
    "settings.json",
    "sessions.db",
    "tasks.db",
    "inquiries.db",
    "profile.db",
    "usage.json",
    "skills",
    "debug",
)

# Platform → env vars that must ALL be present for its channel to run. Migration
# binds each platform whose token(s) are currently set to the "default" profile in
# the install-level registry, preserving current behaviour.
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

    # Strip the now-redundant onboarded key from the moved settings file.
    moved_settings = dest / "settings.json"
    if moved_settings.exists():
        _strip_onboarded_key(moved_settings)

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
    # Bind each env-enabled channel to the default profile so post-migration channel
    # startup is unchanged (registry is the only home for channel assignment now).
    _bind_channels(meta.id)
    log.info(
        "migration: registry written (profile=%s workspace=%s onboarded=%s)",
        meta.id,
        workspace,
        onboarded,
    )
    return True


def _bind_channels(pid: str) -> None:
    """Bind each platform whose token env vars are all currently set to profile ``pid``
    in the install-level registry — preserving the env-token-driven behaviour of
    existing installs (channel assignment is install-level now)."""
    import os

    for platform, envs in _CHANNEL_TOKENS.items():
        if all(os.environ.get(e) for e in envs):
            profiles.bind_channel(platform, pid)


def _entry_from_llm(name: str, provider: str, model: str, options: dict) -> dict | None:
    """Synthesise one named-config entry from a legacy flat ``(provider, model,
    options)`` triple, lifting ``base_url``/``host`` out of ``options`` into
    first-class fields and picking the right ``type``. Returns None when there's no
    model to point at (an empty/degenerate source)."""
    provider = (provider or "").lower()
    model = (model or "").strip()
    if not model:
        return None
    opts = dict(options or {})
    base_url = str(opts.pop("base_url", "") or "").strip()
    host = str(opts.pop("host", "") or "").strip()
    api = str(opts.pop("api", "") or "").lower()
    if provider == "openai":
        # A base_url (compat server) or an explicit api:"chat" → Chat Completions;
        # otherwise OpenAI's Responses API (the pre-store default for bare openai).
        ctype = "openai" if (base_url or api == "chat") else "openai_responses"
    elif provider in ("anthropic", "gemini", "ollama"):
        ctype = provider
    else:
        return None
    return {
        "name": name,
        "type": ctype,
        "model": model,
        "base_url": base_url,
        "host": host,
        "options": opts,
    }


def _dedup_key(entry: dict) -> tuple:
    """Identity of a synthesised entry for de-duplication (name is display-only)."""
    import json

    return (
        entry["type"],
        entry["model"],
        entry["base_url"],
        entry["host"],
        json.dumps(entry["options"], sort_keys=True),
    )


def _strip_profile_llm(settings_file: Path) -> None:
    """Remove the now-migrated ``llm`` / ``llm_options`` keys from a profile's
    settings.json (their home is the install-wide store now). Best-effort."""
    if not settings_file.exists():
        return
    try:
        data = json.loads(settings_file.read_text())
    except Exception:
        return
    if not isinstance(data, dict) or not ({"llm", "llm_options"} & set(data)):
        return
    data.pop("llm", None)
    data.pop("llm_options", None)
    settings_file.write_text(json.dumps(data, indent=2))


def migrate_llm_configs() -> bool:
    """Fold the legacy LLM selection into the install-wide named-config store (§ named
    LLM configs). Idempotent — a no-op once ``llm_configs.json`` exists.

    Sources, in order: an explicit ``llm`` block in the root ``config.json`` (the
    install default), then each profile's ``settings.json`` ``llm`` {provider, model} +
    ``llm_options`` {provider: kwargs} (its per-profile override). Each becomes one
    entry (deduped); the active entry is the one derived from the active-default
    profile (falling back to the root default, then the first entry). Finally the
    ``llm``/``llm_options`` keys are stripped from every profile settings file.

    A genuinely fresh install carries none of these, so nothing is written and the
    store stays empty — the flat gemini defaults then apply exactly as before.
    Dev-quality: best-effort, tolerant of missing/malformed data.
    """
    store_path = data_dir() / "llm_configs.json"
    if store_path.exists() or default_config_path().exists():
        return False  # already migrated (or the install is already on the YAML layout)

    # The legacy root config lived in config.json (default_config_path() is config.yaml
    # now). Read it explicitly so the flat provider/model/options resolve as they did
    # before the YAML switch (config.json ← env, store still empty here).
    legacy_cfg_path = default_config_path().with_name("config.json")
    cfg = load_config(legacy_cfg_path)
    root_provider = cfg.llm.provider
    root_model = cfg.llm.model

    entries: list[dict] = []
    seen: set = set()

    def _add(entry: dict | None) -> tuple | None:
        if entry is None:
            return None
        key = _dedup_key(entry)
        if key not in seen:
            seen.add(key)
            entries.append(entry)
        return key

    # 1) Root config.json default — ONLY when it carries an explicit llm block (a fresh
    # install with no config.json contributes nothing, keeping the store empty).
    root_key: tuple | None = None
    if legacy_cfg_path.exists():
        try:
            has_llm = isinstance(json.loads(legacy_cfg_path.read_text()).get("llm"), dict)
        except Exception:
            has_llm = False
        if has_llm:
            root_opts = dict(cfg.llm.provider_options.get(root_provider) or {})
            root_key = _add(_entry_from_llm("Default", root_provider, root_model, root_opts))

    # 2) Each profile's settings.json llm/llm_options → an entry named after the profile.
    active_pid = profiles.load_registry().get("active_default")
    active_key: tuple | None = None
    for meta in profiles.list_profiles(include_archived=True):
        settings_file = profiles.profile_dir(meta.id) / "settings.json"
        try:
            data = json.loads(settings_file.read_text())
        except Exception:
            data = {}
        llm = data.get("llm") if isinstance(data.get("llm"), dict) else {}
        llm_options = data.get("llm_options") if isinstance(data.get("llm_options"), dict) else {}
        if not (llm or llm_options):
            if meta.id == active_pid:
                active_key = root_key  # this profile just used the root default
            continue
        provider = llm.get("provider") or root_provider
        model = llm.get("model") or root_model
        opts = dict(llm_options.get(provider) or {})
        key = _add(_entry_from_llm(meta.name, provider, model, opts))
        if meta.id == active_pid:
            active_key = key

    if not entries:
        return False  # nothing worth migrating (no legacy LLM state anywhere)

    # Persist the entries (minting ids) and mark the active one — the active-default
    # profile's entry, else the root default, else the first entry. Written straight
    # to the legacy llm_configs.json: migrate_config_files() folds it into config.yaml
    # right after this runs (the store module itself is YAML-only now).
    target = active_key or root_key or _dedup_key(entries[0])
    active_entry_id: str | None = None
    for entry in entries:
        entry["id"] = "c_" + token_hex(4)
        if active_entry_id is None and _dedup_key(entry) == target:
            active_entry_id = entry["id"]
    store_path.write_text(json.dumps({"active": active_entry_id, "configs": entries}, indent=2))

    # Strip the legacy keys from every profile settings file (the store owns them now).
    for meta in profiles.list_profiles(include_archived=True):
        _strip_profile_llm(profiles.profile_dir(meta.id) / "settings.json")

    profile_logger("default").info(
        "migration: wrote %d llm config(s), active=%s", len(entries), active_entry_id
    )
    return True
