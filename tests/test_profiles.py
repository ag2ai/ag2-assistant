"""Profile registry (profiles.json) and Config.with_profile path derivation.

Each test gets its own ``ProfileRegistry`` over the isolated ``paths`` fixture, so
the registry file lives in disposable space and two registries never share state.

Accents are opaque ``#rrggbb`` hexes (ADR 0002): the backend keeps no palette
catalogue and enforces neither a closed set nor cross-profile uniqueness.
"""

import pytest

from assistant.config import Config
from assistant.profiles import ProfileRegistry


@pytest.fixture
def registry(paths) -> ProfileRegistry:
    """The profile registry over an isolated layout."""
    return ProfileRegistry(paths)


TEAL = "#109e91"
CORAL = "#f95339"
OCEAN = "#2f6fe0"


def test_create_slug_derived_workspace_and_first_becomes_default(paths, registry):
    meta = registry.create_profile("My Work", TEAL)
    assert meta.id == "my-work"  # lowercase, alphanumeric+dashes
    assert meta.name == "My Work"
    assert meta.accent == TEAL
    assert meta.archived is False
    # workspace is derived from the profile dir (not user-chosen), never stored.
    assert (
        registry.profile_dir("my-work") / "workspace"
        == paths.root / "profiles" / "my-work" / "workspace"
    )
    assert meta.created.endswith("Z")

    reg = registry.load_registry()
    assert reg["active_default"] == "my-work"  # first profile becomes active_default

    # second profile does not steal active_default
    second = registry.create_profile("Personal", CORAL)
    assert registry.load_registry()["active_default"] == "my-work"
    assert second.id == "personal"


def test_slug_dedupe(registry):
    a = registry.create_profile("Work", TEAL)
    b = registry.create_profile("Work", CORAL)
    c = registry.create_profile("Work", OCEAN)
    assert (a.id, b.id, c.id) == ("work", "work-2", "work-3")


def test_workspace_is_derived_not_stored(registry, paths):
    registry.create_profile("Work", TEAL)
    # The workspace is derived from the layout; the registry entry never persists it,
    # and ProfileMeta — pure data — does not know the layout either.
    entry = registry.load_registry()["profiles"][0]
    assert "workspace" not in entry
    assert not hasattr(registry.get_profile("work"), "workspace")
    assert registry.profile_dir("work") / "workspace" == (
        paths.root / "profiles" / "work" / "workspace"
    )


def test_empty_name_rejected(registry):
    with pytest.raises(ValueError):
        registry.create_profile("   ", TEAL)


def test_rename_keeps_id(registry):
    meta = registry.create_profile("Work", TEAL)
    renamed = registry.rename_profile(meta.id, "Day Job")
    assert renamed.id == "work"  # id immutable
    assert renamed.name == "Day Job"
    assert registry.get_profile("work").name == "Day Job"


def test_rename_empty_rejected(registry):
    registry.create_profile("Work", TEAL)
    with pytest.raises(ValueError):
        registry.rename_profile("work", "")


def test_unknown_pid_raises(registry):
    with pytest.raises(ValueError):
        registry.rename_profile("nope", "X")
    with pytest.raises(ValueError):
        registry.set_accent("nope", TEAL)
    with pytest.raises(ValueError):
        registry.set_active_default("nope")
    assert registry.get_profile("nope") is None


def test_invalid_accent_rejected(registry):
    # Not a hex at all.
    with pytest.raises(ValueError):
        registry.create_profile("Work", "rainbow")
    # Wrong shape (3-digit, no hash, bad chars).
    for bad in ("#fff", "109e91", "#12345g", "#1234567"):
        with pytest.raises(ValueError):
            registry.create_profile("Work", bad)
    registry.create_profile("Work", TEAL)
    with pytest.raises(ValueError):
        registry.set_accent("work", "not-a-hex")


def test_accent_normalised_to_lowercase(registry):
    meta = registry.create_profile("Work", "#AABBCC")
    assert meta.accent == "#aabbcc"
    assert registry.set_accent("work", "#DDEEFF").accent == "#ddeeff"


def test_custom_accent_accepted(registry):
    meta = registry.create_profile("Work", "#3a7bd5")
    assert meta.accent == "#3a7bd5"


def test_duplicate_accent_allowed(registry):
    # No uniqueness rule anymore — two profiles may share a colour (ADR 0002).
    registry.create_profile("A", TEAL)
    b = registry.create_profile("B", TEAL)
    assert b.accent == TEAL
    # set_accent onto a colour another profile already holds is fine too.
    registry.create_profile("C", CORAL)
    assert registry.set_accent("a", CORAL).accent == CORAL


def test_set_accent_updates_value(registry):
    registry.create_profile("A", TEAL)
    assert registry.set_accent("a", OCEAN).accent == OCEAN
    assert registry.get_profile("a").accent == OCEAN


def test_archive_flag_and_list_filtering(registry):
    registry.create_profile("Work", TEAL)
    registry.create_profile("Personal", CORAL)
    registry.archive_profile("personal")

    assert registry.get_profile("personal").archived is True
    active = [m.id for m in registry.list_profiles()]
    assert active == ["work"]  # archived hidden by default
    allp = [m.id for m in registry.list_profiles(include_archived=True)]
    assert set(allp) == {"work", "personal"}


def test_restore_profile_clears_flag_and_keeps_accent(registry):
    registry.create_profile("Work", TEAL)
    registry.create_profile("Personal", CORAL)
    registry.archive_profile("personal")
    assert registry.get_profile("personal").archived is True

    restored = registry.restore_profile("personal")
    assert restored.archived is False
    assert restored.accent == CORAL  # keeps its stored accent
    assert registry.get_profile("personal").archived is False
    # it reappears in the default (unarchived-only) listing
    assert "personal" in [m.id for m in registry.list_profiles()]


def test_restore_unknown_raises(registry):
    with pytest.raises(ValueError):
        registry.restore_profile("nope")


def test_delete_profile_removes_entry(registry):
    registry.create_profile("Work", TEAL)
    registry.create_profile("Personal", CORAL)
    registry.archive_profile("personal")

    removed = registry.delete_profile("personal")
    assert removed.id == "personal"
    # gone from the registry entirely, even with include_archived
    assert registry.get_profile("personal") is None
    assert "personal" not in [m.id for m in registry.list_profiles(include_archived=True)]


def test_delete_unknown_raises(registry):
    with pytest.raises(ValueError):
        registry.delete_profile("nope")


def test_set_active_default(registry):
    registry.create_profile("Work", TEAL)
    registry.create_profile("Personal", CORAL)
    registry.set_active_default("personal")
    assert registry.load_registry()["active_default"] == "personal"


def test_onboarded_get_set(registry):
    assert registry.is_onboarded() is False  # empty registry default
    registry.set_onboarded()
    assert registry.is_onboarded() is True
    registry.set_onboarded(False)
    assert registry.is_onboarded() is False


def test_load_registry_missing_file_is_empty(registry):
    reg = registry.load_registry()
    assert reg == {
        "active_default": None,
        "onboarded": False,
        "profiles": [],
        "channels": {"telegram": None, "discord": None, "slack": None},
    }


def test_profile_dir_does_not_create(paths, registry):
    d = registry.profile_dir("work")
    assert d == paths.root / "profiles" / "work"
    assert not d.exists()


def test_load_registry_does_not_create_tree(paths, registry):
    # load_registry must not mkdir the profiles/ tree as a side effect
    registry.load_registry()
    assert not (paths.root / "profiles").exists()
    assert not (paths.root / "profiles.json").exists()


def test_with_profile_path_derivation(paths, registry):
    meta = registry.create_profile("Work", TEAL)
    base = Config.for_paths(paths)
    derived = base.with_profile(meta)

    root = base.root_dir
    assert derived.root_dir == root  # root preserved
    assert derived.data_dir == root / "profiles" / "work"
    assert derived.skills_dir == root / "profiles" / "work" / "skills"
    # workspace is a subfolder of the profile dir, derived (not user-chosen)
    assert derived.workspace_dir == root / "profiles" / "work" / "workspace"

    # derived is an independent copy; the base config is untouched
    assert base.data_dir == root
    assert base.skills_dir == root / "skills"


def test_with_profile_paths_differ_from_legacy_root_locations(paths, registry):
    """Every overridable path field on the derived config must differ from its
    legacy root-level location (§3.4) — else installed state leaks across profiles."""
    meta = registry.create_profile("Work", TEAL)
    base = Config.for_paths(paths)
    derived = base.with_profile(meta)

    assert derived.data_dir != base.data_dir  # not the root data dir
    assert derived.skills_dir != base.skills_dir  # not the root skills dir
    assert derived.workspace_dir != base.workspace_dir  # profile's own workspace


def test_with_profile_deep_copy_isolates_nested_models(paths, registry):
    meta = registry.create_profile("Work", TEAL)
    base = Config.for_paths(paths)
    derived = base.with_profile(meta)
    derived.llm.model = "changed"
    assert base.llm.model != "changed"  # deep copy, not shared reference
