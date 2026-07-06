"""ProfileManager, config_factory, and legacy migration (WP3).

The autouse conftest fixture points HOME at a tmp dir, so the registry, profile
dirs, and stores resolve under disposable space. The agent factory is faked
(``create_agent``) so no runtime touches a real LLM.
"""

import json
from pathlib import Path

import pytest


class _FakeAgent:
    """Minimal deterministic agent (no LLM)."""

    def __init__(self):
        self.tools = []

    async def ask(self, *msg, stream=None, **kwargs):
        class _R:
            body = "ok"

        return _R()


@pytest.fixture(autouse=True)
def _fake_agent(monkeypatch):
    """Every runtime's gateway builds a fake agent instead of a real one."""
    import assistant.gateway.core as core_mod

    monkeypatch.setattr(core_mod, "create_agent", lambda *a, **k: _FakeAgent())


def _root() -> Path:
    from assistant.config import load_config

    return load_config().root_dir


# --- migration ---


def _seed_legacy(root: Path, *, onboarded_key=False, onboarded_marker=False, extra=None):
    """Create a legacy (pre-profile) layout at the root."""
    root.mkdir(parents=True, exist_ok=True)
    settings = {"llm": {"provider": "openai", "model": "gpt-x"}}
    if onboarded_key:
        settings["onboarded"] = True
    (root / "settings.json").write_text(json.dumps(settings))
    (root / "sessions.db").write_text("db")
    (root / "tasks.db").write_text("db")
    (root / "profile.db").write_text("db")
    (root / "skills").mkdir(exist_ok=True)
    (root / "skills" / "a.md").write_text("skill")
    if onboarded_marker:
        (root / "onboarded").write_text("")
    for name, content in (extra or {}).items():
        (root / name).write_text(content)


def test_migration_moves_files_and_writes_registry():
    from assistant import profiles
    from assistant.gateway.migration import migrate_if_needed

    root = _root()
    _seed_legacy(root, onboarded_key=True)

    assert migrate_if_needed() is True

    dest = root / "profiles" / "default"
    # files moved out of the root, into the default profile dir
    assert not (root / "sessions.db").exists()
    assert (dest / "sessions.db").exists()
    assert (dest / "tasks.db").exists()
    assert (dest / "profile.db").exists()
    assert (dest / "skills" / "a.md").exists()

    # registry written with a single "default" profile, active + onboarded carried
    reg = profiles.load_registry()
    assert reg["active_default"] == "default"
    assert reg["onboarded"] is True
    assert [p["id"] for p in reg["profiles"]] == ["default"]

    # onboarded key stripped from the moved settings file (registry is its only home)
    moved = json.loads((dest / "settings.json").read_text())
    assert "onboarded" not in moved
    assert moved["llm"]["provider"] == "openai"

    # idempotent: a second run is a no-op
    assert migrate_if_needed() is False


def test_migration_onboarded_from_marker_only():
    from assistant import profiles
    from assistant.gateway.migration import migrate_if_needed

    root = _root()
    _seed_legacy(root, onboarded_key=False, onboarded_marker=True)

    assert migrate_if_needed() is True
    assert profiles.load_registry()["onboarded"] is True
    # marker deleted (registry owns the flag now)
    assert not (root / "onboarded").exists()


def test_migration_not_onboarded_when_neither_present():
    from assistant import profiles
    from assistant.gateway.migration import migrate_if_needed

    _seed_legacy(_root(), onboarded_key=False, onboarded_marker=False)
    assert migrate_if_needed() is True
    assert profiles.load_registry()["onboarded"] is False


def test_migration_binds_channels_from_env(monkeypatch):
    for e in ("DISCORD_BOT_TOKEN", "SLACK_BOT_TOKEN", "SLACK_APP_TOKEN"):
        monkeypatch.delenv(e, raising=False)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "tok")
    from assistant import profiles
    from assistant.gateway.migration import migrate_if_needed

    root = _root()
    _seed_legacy(root)
    assert migrate_if_needed() is True
    # env-enabled channels are bound to the default profile in the registry
    bindings = profiles.channel_bindings()
    assert bindings["telegram"] == "default"
    assert bindings["discord"] is None
    assert bindings["slack"] is None


def test_migration_noop_on_fresh_install():
    from assistant.gateway.migration import migrate_if_needed

    # nothing at the root → no-op
    assert migrate_if_needed() is False
    assert not (_root() / "profiles").exists()


# --- ProfileManager start / boot ---


async def test_zero_profile_start_is_noop():
    from assistant.gateway.profile_manager import ProfileManager

    mgr = ProfileManager(memory=False, persist=False)
    await mgr.start()
    assert list(mgr.runtimes()) == []
    await mgr.close()


async def test_boot_skips_archived():
    from assistant import profiles
    from assistant.gateway.profile_manager import ProfileManager

    a = profiles.create_profile("Work", "teal")
    b = profiles.create_profile("Personal", "coral")
    profiles.archive_profile(b.id)

    mgr = ProfileManager(memory=False, persist=False)
    await mgr.start()
    running = {r.pid for r in mgr.runtimes()}
    assert running == {a.id}
    await mgr.close()


async def test_get_raises_unknown_archived_and_not_running():
    from assistant import profiles
    from assistant.gateway.profile_manager import (
        ArchivedProfile,
        ProfileManager,
        UnknownProfile,
    )

    a = profiles.create_profile("Work", "teal")
    b = profiles.create_profile("Personal", "coral")

    mgr = ProfileManager(memory=False, persist=False)
    await mgr.start()
    try:
        # unknown id → UnknownProfile
        with pytest.raises(UnknownProfile):
            mgr.get("nope")
        # running profile → returns runtime
        assert mgr.get(a.id).pid == a.id
        # archive b at the registry level but leave the (never-booted) runtime absent
        profiles.archive_profile(b.id)
        with pytest.raises(ArchivedProfile):
            mgr.get(b.id)
        # registered + unarchived but not running → server-bug RuntimeError
        c = profiles.create_profile("Third", "ocean")
        with pytest.raises(RuntimeError):
            mgr.get(c.id)
    finally:
        await mgr.close()


async def test_create_boots_live():
    from assistant.gateway.profile_manager import ProfileManager

    mgr = ProfileManager(memory=False, persist=False)
    await mgr.start()
    try:
        runtime = await mgr.create("Work", "teal")
        assert runtime.pid == "work"
        assert mgr.get("work") is runtime
        assert runtime.gateway is not None
        # the profile dir was created
        from assistant import profiles

        assert profiles.profile_dir("work").exists()
    finally:
        await mgr.close()


# --- archive guardrails ---


async def test_archive_refuses_last_profile():
    from assistant.gateway.profile_manager import ProfileManager

    mgr = ProfileManager(memory=False, persist=False)
    await mgr.start()
    try:
        await mgr.create("Only", "teal")
        with pytest.raises(ValueError):
            await mgr.archive("only")
    finally:
        await mgr.close()


async def test_archive_requires_new_default_when_archiving_active():
    from assistant import profiles
    from assistant.gateway.profile_manager import ProfileManager

    mgr = ProfileManager(memory=False, persist=False)
    await mgr.start()
    try:
        a = await mgr.create("Work", "teal")  # first → active_default
        b = await mgr.create("Personal", "coral")
        assert mgr.active_default == a.pid

        # archiving the active default without a replacement → ValueError
        with pytest.raises(ValueError):
            await mgr.archive(a.pid)

        # with a valid replacement it succeeds; runtime removed, default switched
        await mgr.archive(a.pid, new_default=b.pid)
        assert mgr.active_default == b.pid
        assert profiles.get_profile(a.pid).archived is True
        from assistant.gateway.profile_manager import ArchivedProfile

        with pytest.raises(ArchivedProfile):
            mgr.get(a.pid)
        assert {r.pid for r in mgr.runtimes()} == {b.pid}
    finally:
        await mgr.close()


async def test_restart_after_archive_stays_gone(monkeypatch):
    """§6.6: archive B → close manager → new ProfileManager.start() → B not booted
    (get raises ArchivedProfile), absent from list_profiles(), folder intact on disk."""
    from assistant import profiles
    from assistant.gateway.profile_manager import ArchivedProfile, ProfileManager

    mgr = ProfileManager(memory=False, persist=False)
    await mgr.start()
    a = await mgr.create("Work", "teal")  # first → active_default
    b = await mgr.create("Personal", "coral")
    await mgr.archive(b.pid)  # archiving a non-default needs no replacement
    b_dir = profiles.profile_dir(b.pid)
    assert b_dir.exists()
    await mgr.close()

    # a fresh manager reads the same on-disk registry
    mgr2 = ProfileManager(memory=False, persist=False)
    await mgr2.start()
    try:
        assert {r.pid for r in mgr2.runtimes()} == {a.pid}  # B not booted
        with pytest.raises(ArchivedProfile):
            mgr2.get(b.pid)
        # absent from the API-facing list; present only with include_archived
        assert [m.id for m in profiles.list_profiles()] == [a.pid]
        assert b.pid in {m.id for m in profiles.list_profiles(include_archived=True)}
        assert b_dir.exists()  # folder intact on disk
    finally:
        await mgr2.close()


async def test_archive_bad_new_default_rejected():
    from assistant.gateway.profile_manager import ProfileManager

    mgr = ProfileManager(memory=False, persist=False)
    await mgr.start()
    try:
        a = await mgr.create("Work", "teal")
        await mgr.create("Personal", "coral")
        with pytest.raises(ValueError):
            await mgr.archive(a.pid, new_default="does-not-exist")
    finally:
        await mgr.close()


# --- config_factory ---


def test_config_factory_picks_up_workspace_edit():
    from assistant import profiles
    from assistant.gateway.profile_manager import config_factory

    meta = profiles.create_profile("Work", "teal", workspace="/tmp/ws-one")
    factory = config_factory(meta.id)
    assert str(factory().workspace_dir) == "/tmp/ws-one"

    # edit the registry; the factory (which re-reads meta each call) sees it
    profiles.set_workspace(meta.id, "/tmp/ws-two")
    assert str(factory().workspace_dir) == "/tmp/ws-two"


def test_config_factory_overlays_settings_llm():
    from assistant import profiles
    from assistant.gateway.profile_manager import config_factory
    from assistant.settings import Settings

    meta = profiles.create_profile("Work", "teal")
    data_dir = meta_dir = profiles.profile_dir(meta.id)
    meta_dir.mkdir(parents=True, exist_ok=True)
    Settings(data_dir / "settings.json").set_llm(provider="anthropic", model="claude-x")

    cfg = config_factory(meta.id)()
    assert cfg.llm.provider == "anthropic"
    assert cfg.llm.model == "claude-x"


def test_config_factory_env_wins_over_settings(monkeypatch):
    from assistant import profiles
    from assistant.gateway.profile_manager import config_factory
    from assistant.settings import Settings

    meta = profiles.create_profile("Work", "teal")
    d = profiles.profile_dir(meta.id)
    d.mkdir(parents=True, exist_ok=True)
    Settings(d / "settings.json").set_llm(provider="anthropic", model="claude-x")

    monkeypatch.setenv("AG2ASSISTANT_LLM_PROVIDER", "gemini")
    # model has no env override → still taken from settings
    cfg = config_factory(meta.id)()
    assert cfg.llm.provider == "gemini"  # env wins
    assert cfg.llm.model == "claude-x"  # settings still applied


def test_config_factory_unknown_profile_raises():
    from assistant.gateway.profile_manager import UnknownProfile, config_factory

    with pytest.raises(UnknownProfile):
        config_factory("ghost")()


# --- resolve_active_profile (CLI chat path) ---


def test_resolve_active_profile_zero_profiles_raises():
    from assistant.gateway.profile_manager import UnknownProfile, resolve_active_profile

    with pytest.raises(UnknownProfile):
        resolve_active_profile()


def test_resolve_active_profile_defaults_to_active():
    from assistant import profiles
    from assistant.gateway.profile_manager import resolve_active_profile

    meta = profiles.create_profile("Work", "teal", workspace="/tmp/ws")
    pid, cfg, factory = resolve_active_profile()
    assert pid == meta.id
    assert str(cfg.workspace_dir) == "/tmp/ws"
    assert callable(factory)
