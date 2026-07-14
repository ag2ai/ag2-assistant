"""ProfileManager and config_factory (WP3).

The autouse conftest fixture points HOME at a tmp dir, so the registry, profile
dirs, and stores resolve under disposable space. The agent factory is faked
(``create_agent``) so no runtime touches a real LLM.
"""

from pathlib import Path

import pytest

from tests.conftest import FakeRunMixin


class _FakeAgent(FakeRunMixin):
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


def test_config_factory_derives_workspace_under_profile_dir():
    from assistant import profiles
    from assistant.gateway.profile_manager import config_factory

    meta = profiles.create_profile("Work", "teal")
    factory = config_factory(meta.id)
    # workspace is always <profile dir>/workspace — derived, not user-chosen.
    assert factory().workspace_dir == profiles.profile_dir(meta.id) / "workspace"


def test_config_factory_derives_active_llm_config(monkeypatch):
    """The LLM is install-wide now: config_factory doesn't overlay per-profile
    settings — it just carries whatever load_config() derived from the active named
    LLM config (common across every profile)."""
    from assistant import llm_configs, profiles
    from assistant.gateway.profile_manager import config_factory

    monkeypatch.delenv("AG2ASSISTANT_LLM_PROVIDER", raising=False)
    monkeypatch.delenv("AG2ASSISTANT_MODEL", raising=False)
    meta = profiles.create_profile("Work", "teal")
    profiles.profile_dir(meta.id).mkdir(parents=True, exist_ok=True)

    # No store yet → flat gemini defaults.
    assert config_factory(meta.id)().llm.provider == "gemini"

    # Activate an install-wide anthropic config → every profile's factory sees it.
    entry = llm_configs.save_config({"name": "Claude", "type": "anthropic", "model": "claude-x"})
    llm_configs.set_active(entry["id"])
    cfg = config_factory(meta.id)()
    assert cfg.llm.provider == "anthropic"
    assert cfg.llm.model == "claude-x"


def test_config_factory_env_wins_over_active_config(monkeypatch):
    from assistant import llm_configs, profiles
    from assistant.gateway.profile_manager import config_factory

    meta = profiles.create_profile("Work", "teal")
    profiles.profile_dir(meta.id).mkdir(parents=True, exist_ok=True)
    entry = llm_configs.save_config({"name": "Claude", "type": "anthropic", "model": "claude-x"})
    llm_configs.set_active(entry["id"])

    monkeypatch.setenv("AG2ASSISTANT_LLM_PROVIDER", "gemini")
    # model has no env override → still taken from the active config
    cfg = config_factory(meta.id)()
    assert cfg.llm.provider == "gemini"  # env wins
    assert cfg.llm.model == "claude-x"  # active config still applied


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

    meta = profiles.create_profile("Work", "teal")
    pid, cfg, factory = resolve_active_profile()
    assert pid == meta.id
    assert cfg.workspace_dir == profiles.profile_dir(meta.id) / "workspace"
    assert callable(factory)
