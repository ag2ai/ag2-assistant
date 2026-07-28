"""Profile registry (profiles.json) and Config.with_profile path derivation.

The autouse conftest fixture points HOME at a tmp dir, so ``data_dir()`` — and
therefore the registry file — resolves under disposable space.

Accents are opaque ``#rrggbb`` hexes (ADR 0002): the backend keeps no palette
catalogue and enforces neither a closed set nor cross-profile uniqueness.
"""

import pytest

from assistant import profiles
from assistant.config import Config, load_config

TEAL = "#109e91"
CORAL = "#f95339"
OCEAN = "#2f6fe0"


def test_create_slug_derived_workspace_and_first_becomes_default():
    meta = profiles.create_profile("My Work", TEAL)
    assert meta.id == "my-work"  # lowercase, alphanumeric+dashes
    assert meta.name == "My Work"
    assert meta.accent == TEAL
    assert meta.archived is False
    # workspace is derived from the profile dir (not user-chosen), never stored.
    assert meta.workspace == str(profiles.profile_dir("my-work") / "workspace")
    assert meta.created.endswith("Z")

    reg = profiles.load_registry()
    assert reg["active_default"] == "my-work"  # first profile becomes active_default

    # second profile does not steal active_default
    second = profiles.create_profile("Personal", CORAL)
    assert profiles.load_registry()["active_default"] == "my-work"
    assert second.id == "personal"


def test_slug_dedupe():
    a = profiles.create_profile("Work", TEAL)
    b = profiles.create_profile("Work", CORAL)
    c = profiles.create_profile("Work", OCEAN)
    assert (a.id, b.id, c.id) == ("work", "work-2", "work-3")


def test_workspace_is_derived_not_stored():
    profiles.create_profile("Work", TEAL)
    # The workspace is a computed property; the registry entry never persists it.
    entry = profiles.load_registry()["profiles"][0]
    assert "workspace" not in entry
    assert profiles.get_profile("work").workspace == str(profiles.profile_dir("work") / "workspace")


def test_empty_name_rejected():
    with pytest.raises(ValueError):
        profiles.create_profile("   ", TEAL)


def test_rename_keeps_id():
    meta = profiles.create_profile("Work", TEAL)
    renamed = profiles.rename_profile(meta.id, "Day Job")
    assert renamed.id == "work"  # id immutable
    assert renamed.name == "Day Job"
    assert profiles.get_profile("work").name == "Day Job"


def test_rename_empty_rejected():
    profiles.create_profile("Work", TEAL)
    with pytest.raises(ValueError):
        profiles.rename_profile("work", "")


def test_unknown_pid_raises():
    with pytest.raises(ValueError):
        profiles.rename_profile("nope", "X")
    with pytest.raises(ValueError):
        profiles.set_accent("nope", TEAL)
    with pytest.raises(ValueError):
        profiles.set_active_default("nope")
    assert profiles.get_profile("nope") is None


def test_invalid_accent_rejected():
    # Not a hex at all.
    with pytest.raises(ValueError):
        profiles.create_profile("Work", "rainbow")
    # Wrong shape (3-digit, no hash, bad chars).
    for bad in ("#fff", "109e91", "#12345g", "#1234567"):
        with pytest.raises(ValueError):
            profiles.create_profile("Work", bad)
    profiles.create_profile("Work", TEAL)
    with pytest.raises(ValueError):
        profiles.set_accent("work", "not-a-hex")


def test_accent_normalised_to_lowercase():
    meta = profiles.create_profile("Work", "#AABBCC")
    assert meta.accent == "#aabbcc"
    assert profiles.set_accent("work", "#DDEEFF").accent == "#ddeeff"


def test_custom_accent_accepted():
    meta = profiles.create_profile("Work", "#3a7bd5")
    assert meta.accent == "#3a7bd5"


def test_duplicate_accent_allowed():
    # No uniqueness rule anymore — two profiles may share a colour (ADR 0002).
    profiles.create_profile("A", TEAL)
    b = profiles.create_profile("B", TEAL)
    assert b.accent == TEAL
    # set_accent onto a colour another profile already holds is fine too.
    profiles.create_profile("C", CORAL)
    assert profiles.set_accent("a", CORAL).accent == CORAL


def test_set_accent_updates_value():
    profiles.create_profile("A", TEAL)
    assert profiles.set_accent("a", OCEAN).accent == OCEAN
    assert profiles.get_profile("a").accent == OCEAN


def test_archive_flag_and_list_filtering():
    profiles.create_profile("Work", TEAL)
    profiles.create_profile("Personal", CORAL)
    profiles.archive_profile("personal")

    assert profiles.get_profile("personal").archived is True
    active = [m.id for m in profiles.list_profiles()]
    assert active == ["work"]  # archived hidden by default
    allp = [m.id for m in profiles.list_profiles(include_archived=True)]
    assert set(allp) == {"work", "personal"}


def test_restore_profile_clears_flag_and_keeps_accent():
    profiles.create_profile("Work", TEAL)
    profiles.create_profile("Personal", CORAL)
    profiles.archive_profile("personal")
    assert profiles.get_profile("personal").archived is True

    restored = profiles.restore_profile("personal")
    assert restored.archived is False
    assert restored.accent == CORAL  # keeps its stored accent
    assert profiles.get_profile("personal").archived is False
    # it reappears in the default (unarchived-only) listing
    assert "personal" in [m.id for m in profiles.list_profiles()]


def test_restore_unknown_raises():
    with pytest.raises(ValueError):
        profiles.restore_profile("nope")


def test_delete_profile_removes_entry():
    profiles.create_profile("Work", TEAL)
    profiles.create_profile("Personal", CORAL)
    profiles.archive_profile("personal")

    removed = profiles.delete_profile("personal")
    assert removed.id == "personal"
    # gone from the registry entirely, even with include_archived
    assert profiles.get_profile("personal") is None
    assert "personal" not in [m.id for m in profiles.list_profiles(include_archived=True)]


def test_delete_unknown_raises():
    with pytest.raises(ValueError):
        profiles.delete_profile("nope")


# --- channel exposure (default-allow; a record only ever withdraws) ---


def test_a_new_profile_is_reachable_from_every_surface():
    meta = profiles.create_profile("Work", TEAL)
    assert meta.withdrawn == []
    assert profiles.exposure("work") == dict.fromkeys(profiles.CHANNEL_SURFACES, True)


def test_withdrawing_records_only_that_surface():
    profiles.create_profile("Work", TEAL)
    profiles.set_exposure("work", "telegram:group", False)

    assert profiles.get_profile("work").withdrawn == ["telegram:group"]
    assert profiles.exposure("work")["telegram:group"] is False
    # telegram's direct messages are a surface of their own and stay reachable
    assert profiles.exposure("work")["telegram:dm"] is True
    assert profiles.withdrawn_from("telegram:group") == {"work"}
    assert profiles.withdrawn_from("telegram:dm") == set()


def test_exposing_drops_the_record_rather_than_storing_an_allow():
    profiles.create_profile("Work", TEAL)
    profiles.set_exposure("work", "discord", False)
    profiles.set_exposure("work", "discord", True)
    assert profiles.get_profile("work").withdrawn == []


def test_withdrawing_twice_is_recorded_once():
    profiles.create_profile("Work", TEAL)
    profiles.set_exposure("work", "slack", False)
    profiles.set_exposure("work", "slack", False)
    assert profiles.get_profile("work").withdrawn == ["slack"]


def test_unknown_surface_and_unknown_profile_raise():
    profiles.create_profile("Work", TEAL)
    with pytest.raises(ValueError):
        profiles.set_exposure("work", "telegram", False)  # not a surface: dm/group split
    with pytest.raises(ValueError):
        profiles.set_exposure("nope", "slack", False)
    with pytest.raises(ValueError):
        profiles.exposure("nope")


def test_set_active_default():
    profiles.create_profile("Work", TEAL)
    profiles.create_profile("Personal", CORAL)
    profiles.set_active_default("personal")
    assert profiles.load_registry()["active_default"] == "personal"


def test_onboarded_get_set():
    assert profiles.is_onboarded() is False  # empty registry default
    profiles.set_onboarded()
    assert profiles.is_onboarded() is True
    profiles.set_onboarded(False)
    assert profiles.is_onboarded() is False


def test_load_registry_missing_file_is_empty():
    reg = profiles.load_registry()
    assert reg == {
        "active_default": None,
        "onboarded": False,
        "profiles": [],
        "channel_defaults": {"telegram": None, "discord": None, "slack": None},
    }


def test_profile_dir_does_not_create():
    d = profiles.profile_dir("work")
    assert d == profiles.data_dir() / "profiles" / "work"
    assert not d.exists()


def test_load_registry_does_not_create_tree(tmp_path, monkeypatch):
    # load_registry must not mkdir the profiles/ tree as a side effect
    profiles.load_registry()
    assert not (profiles.data_dir() / "profiles").exists()
    assert not (profiles.data_dir() / "profiles.json").exists()


def test_with_profile_path_derivation():
    meta = profiles.create_profile("Work", TEAL)
    base = load_config()
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


def test_with_profile_paths_differ_from_legacy_root_locations():
    """Every overridable path field on the derived config must differ from its
    legacy root-level location (§3.4) — else installed state leaks across profiles."""
    meta = profiles.create_profile("Work", TEAL)
    base = Config()
    derived = base.with_profile(meta)

    assert derived.data_dir != base.data_dir  # not the root data dir
    assert derived.skills_dir != base.skills_dir  # not the root skills dir
    assert derived.workspace_dir != base.workspace_dir  # profile's own workspace


def test_with_profile_deep_copy_isolates_nested_models():
    meta = profiles.create_profile("Work", TEAL)
    base = Config()
    derived = base.with_profile(meta)
    derived.llm.model = "changed"
    assert base.llm.model != "changed"  # deep copy, not shared reference
