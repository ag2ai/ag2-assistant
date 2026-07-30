"""ProfileManager and config_factory (WP3).

Everything runs on the isolated ``paths`` layout, so the registry, profile dirs and
stores live under disposable space, and every manager gets a faked agent factory
(``make_manager``) so no runtime touches a real LLM.
"""

import pytest

from assistant.gateway.profile_manager import (
    ArchivedProfile,
    UnknownProfile,
    config_factory,
    resolve_active_profile,
)
from assistant.llm_configs import LlmConfigStore
from assistant.profiles import ProfileRegistry
from tests.support.apps import make_manager


@pytest.fixture
def registry(paths) -> ProfileRegistry:
    """The registry over the same isolated layout the manager resolves."""
    return ProfileRegistry(paths)


@pytest.fixture
def llm_store(paths) -> LlmConfigStore:
    """The named-LLM-configuration store over that same layout."""
    return LlmConfigStore(paths)


# --- ProfileManager start / boot ---


async def test_zero_profile_start_is_noop(paths):

    mgr = make_manager(paths)
    await mgr.start()
    assert list(mgr.runtimes()) == []
    await mgr.close()


async def test_boot_skips_archived(paths, registry):

    a = registry.create_profile("Work", "#109e91")
    b = registry.create_profile("Personal", "#f95339")
    registry.archive_profile(b.id)

    mgr = make_manager(paths)
    await mgr.start()
    running = {r.pid for r in mgr.runtimes()}
    assert running == {a.id}
    await mgr.close()


async def test_get_raises_unknown_archived_and_not_running(paths, registry):

    a = registry.create_profile("Work", "#109e91")
    b = registry.create_profile("Personal", "#f95339")

    mgr = make_manager(paths)
    await mgr.start()
    try:
        # unknown id → UnknownProfile
        with pytest.raises(UnknownProfile):
            mgr.get("nope")
        # running profile → returns runtime
        assert mgr.get(a.id).pid == a.id
        # archive b at the registry level but leave the (never-booted) runtime absent
        registry.archive_profile(b.id)
        with pytest.raises(ArchivedProfile):
            mgr.get(b.id)
        # registered + unarchived but not running → server-bug RuntimeError
        c = registry.create_profile("Third", "#2f6fe0")
        with pytest.raises(RuntimeError):
            mgr.get(c.id)
    finally:
        await mgr.close()


async def test_create_boots_live(paths, registry):

    mgr = make_manager(paths)
    await mgr.start()
    try:
        runtime = await mgr.create("Work", "#109e91")
        assert runtime.pid == "work"
        assert mgr.get("work") is runtime
        assert runtime.gateway is not None
        # the profile dir was created
        assert registry.profile_dir("work").exists()
    finally:
        await mgr.close()


# --- archive guardrails ---


async def test_archive_refuses_last_profile(paths):

    mgr = make_manager(paths)
    await mgr.start()
    try:
        await mgr.create("Only", "#109e91")
        with pytest.raises(ValueError):
            await mgr.archive("only")
    finally:
        await mgr.close()


async def test_archive_requires_new_default_when_archiving_active(paths, registry):

    mgr = make_manager(paths)
    await mgr.start()
    try:
        a = await mgr.create("Work", "#109e91")  # first → active_default
        b = await mgr.create("Personal", "#f95339")
        assert mgr.active_default == a.pid

        # archiving the active default without a replacement → ValueError
        with pytest.raises(ValueError):
            await mgr.archive(a.pid)

        # with a valid replacement it succeeds; runtime removed, default switched
        await mgr.archive(a.pid, new_default=b.pid)
        assert mgr.active_default == b.pid
        assert registry.get_profile(a.pid).archived is True
        with pytest.raises(ArchivedProfile):
            mgr.get(a.pid)
        assert {r.pid for r in mgr.runtimes()} == {b.pid}
    finally:
        await mgr.close()


async def test_restart_after_archive_stays_gone(paths, registry, monkeypatch):
    """§6.6: archive B → close manager → new ProfileManager.start() → B not booted
    (get raises ArchivedProfile), absent from list_profiles(), folder intact on disk."""

    mgr = make_manager(paths)
    await mgr.start()
    a = await mgr.create("Work", "#109e91")  # first → active_default
    b = await mgr.create("Personal", "#f95339")
    await mgr.archive(b.pid)  # archiving a non-default needs no replacement
    b_dir = registry.profile_dir(b.pid)
    assert b_dir.exists()
    await mgr.close()

    # a fresh manager reads the same on-disk registry
    mgr2 = make_manager(paths)
    await mgr2.start()
    try:
        assert {r.pid for r in mgr2.runtimes()} == {a.pid}  # B not booted
        with pytest.raises(ArchivedProfile):
            mgr2.get(b.pid)
        # absent from the API-facing list; present only with include_archived
        assert [m.id for m in registry.list_profiles()] == [a.pid]
        assert b.pid in {m.id for m in registry.list_profiles(include_archived=True)}
        assert b_dir.exists()  # folder intact on disk
    finally:
        await mgr2.close()


async def test_archive_bad_new_default_rejected(paths):

    mgr = make_manager(paths)
    await mgr.start()
    try:
        a = await mgr.create("Work", "#109e91")
        await mgr.create("Personal", "#f95339")
        with pytest.raises(ValueError):
            await mgr.archive(a.pid, new_default="does-not-exist")
    finally:
        await mgr.close()


# --- restore (unarchive + boot) ---


async def test_restore_boots_live(paths, registry):

    mgr = make_manager(paths)
    await mgr.start()
    try:
        a = await mgr.create("Work", "#109e91")
        b = await mgr.create("Personal", "#f95339")
        await mgr.archive(b.pid)  # non-default, no replacement needed
        assert {r.pid for r in mgr.runtimes()} == {a.pid}

        runtime = await mgr.restore(b.pid)
        assert runtime.pid == b.pid
        assert registry.get_profile(b.pid).archived is False
        assert mgr.get(b.pid) is runtime  # booted + resolvable
        assert {r.pid for r in mgr.runtimes()} == {a.pid, b.pid}
    finally:
        await mgr.close()


async def test_restore_unknown_raises(paths):

    mgr = make_manager(paths)
    await mgr.start()
    try:
        with pytest.raises(UnknownProfile):
            await mgr.restore("ghost")
    finally:
        await mgr.close()


async def test_restore_non_archived_rejected(paths):

    mgr = make_manager(paths)
    await mgr.start()
    try:
        a = await mgr.create("Work", "#109e91")
        await mgr.create("Personal", "#f95339")
        # a is live, not archived → restoring it is a no-op error, not a boot
        with pytest.raises(ValueError):
            await mgr.restore(a.pid)
    finally:
        await mgr.close()


async def test_restore_rolls_back_on_boot_failure(paths, registry, monkeypatch):
    """§4.9 (Q9): if boot fails, the profile stays cleanly archived — never left in the
    unarchived-but-not-running limbo the manager treats as a server bug."""

    mgr = make_manager(paths)
    await mgr.start()
    try:
        a = await mgr.create("Work", "#109e91")
        b = await mgr.create("Personal", "#f95339")
        await mgr.archive(b.pid)

        async def _boom(meta):
            raise RuntimeError("boot exploded")

        monkeypatch.setattr(mgr, "_boot", _boom)
        with pytest.raises(RuntimeError):
            await mgr.restore(b.pid)

        # rolled back: still archived, not running, still resolvable-as-archived
        assert registry.get_profile(b.pid).archived is True
        assert {r.pid for r in mgr.runtimes()} == {a.pid}
        with pytest.raises(ArchivedProfile):
            mgr.get(b.pid)
    finally:
        await mgr.close()


# --- purge (hard delete, archive-first) ---


async def test_purge_deletes_dir_and_registry_entry(paths, registry):

    mgr = make_manager(paths)
    await mgr.start()
    try:
        await mgr.create("Work", "#109e91")
        b = await mgr.create("Personal", "#f95339")
        await mgr.archive(b.pid)
        b_dir = registry.profile_dir(b.pid)
        assert b_dir.exists()

        await mgr.purge(b.pid)

        assert not b_dir.exists()  # folder erased from disk
        assert registry.get_profile(b.pid) is None  # registry entry gone
        assert b.pid not in {m.id for m in registry.list_profiles(include_archived=True)}
    finally:
        await mgr.close()


async def test_purge_unknown_raises(paths):

    mgr = make_manager(paths)
    await mgr.start()
    try:
        with pytest.raises(UnknownProfile):
            await mgr.purge("ghost")
    finally:
        await mgr.close()


async def test_purge_refuses_unarchived_profile(paths, registry):
    """Archive-first (Q4/ADR 0003): a live profile cannot be hard-deleted directly."""

    mgr = make_manager(paths)
    await mgr.start()
    try:
        a = await mgr.create("Work", "#109e91")
        await mgr.create("Personal", "#f95339")
        with pytest.raises(ValueError):
            await mgr.purge(a.pid)  # not archived
        # untouched: still live, dir intact, still in registry
        assert registry.profile_dir(a.pid).exists()
        assert registry.get_profile(a.pid) is not None
    finally:
        await mgr.close()


# --- config_factory ---


def test_config_factory_derives_workspace_under_profile_dir(paths, registry):

    meta = registry.create_profile("Work", "#109e91")
    factory = config_factory(meta.id, paths)
    # workspace is always <profile dir>/workspace — derived, not user-chosen.
    assert factory().workspace_dir == registry.profile_dir(meta.id) / "workspace"


def test_config_factory_derives_active_llm_config(paths, llm_store, registry):
    """The LLM is install-wide now: config_factory doesn't overlay per-profile
    settings — it just carries whatever the install resolve derived from the active
    named LLM config (common across every profile)."""

    meta = registry.create_profile("Work", "#109e91")
    registry.profile_dir(meta.id).mkdir(parents=True, exist_ok=True)

    # No store yet → flat gemini defaults.
    assert config_factory(meta.id, paths)().llm.provider == "gemini"

    # Activate an install-wide anthropic config → every profile's factory sees it.
    entry = llm_store.save_config({"name": "Claude", "type": "anthropic", "model": "claude-x"})
    llm_store.set_active(entry["id"])
    cfg = config_factory(meta.id, paths)()
    assert cfg.llm.provider == "anthropic"
    assert cfg.llm.model == "claude-x"


def test_config_factory_env_wins_over_active_config(paths, llm_store, registry):

    meta = registry.create_profile("Work", "#109e91")
    registry.profile_dir(meta.id).mkdir(parents=True, exist_ok=True)
    entry = llm_store.save_config({"name": "Claude", "type": "anthropic", "model": "claude-x"})
    llm_store.set_active(entry["id"])

    # model has no env override → still taken from the active config
    cfg = config_factory(meta.id, paths, {"AG2ASSISTANT_LLM_PROVIDER": "gemini"})()
    assert cfg.llm.provider == "gemini"  # env wins
    assert cfg.llm.model == "claude-x"  # active config still applied


def test_config_factory_unknown_profile_raises(paths):

    with pytest.raises(UnknownProfile):
        config_factory("ghost", paths)()


# --- resolve_active_profile (CLI chat path) ---


def test_resolve_active_profile_zero_profiles_raises(paths):

    with pytest.raises(UnknownProfile):
        resolve_active_profile(paths=paths)


def test_resolve_active_profile_defaults_to_active(paths, registry):

    meta = registry.create_profile("Work", "#109e91")
    pid, cfg, factory = resolve_active_profile(paths=paths)
    assert pid == meta.id
    assert cfg.workspace_dir == registry.profile_dir(meta.id) / "workspace"
    assert callable(factory)
