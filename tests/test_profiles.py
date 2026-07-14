"""Profile registry (profiles.json) and Config.with_profile path derivation.

The autouse conftest fixture points HOME at a tmp dir, so ``data_dir()`` — and
therefore the registry file — resolves under disposable space.
"""

import pytest

from assistant import profiles
from assistant.config import Config, load_config


def test_create_slug_derived_workspace_and_first_becomes_default():
    meta = profiles.create_profile("My Work", "teal")
    assert meta.id == "my-work"  # lowercase, alphanumeric+dashes
    assert meta.name == "My Work"
    assert meta.palette == "teal"
    assert meta.archived is False
    # workspace is derived from the profile dir (not user-chosen), never stored.
    assert meta.workspace == str(profiles.profile_dir("my-work") / "workspace")
    assert meta.created.endswith("Z")

    reg = profiles.load_registry()
    assert reg["active_default"] == "my-work"  # first profile becomes active_default

    # second profile does not steal active_default
    second = profiles.create_profile("Personal", "coral")
    assert profiles.load_registry()["active_default"] == "my-work"
    assert second.id == "personal"


def test_slug_dedupe():
    a = profiles.create_profile("Work", "teal")
    b = profiles.create_profile("Work", "coral")
    c = profiles.create_profile("Work", "ocean")
    assert (a.id, b.id, c.id) == ("work", "work-2", "work-3")


def test_workspace_is_derived_not_stored():
    profiles.create_profile("Work", "teal")
    # The workspace is a computed property; the registry entry never persists it.
    entry = profiles.load_registry()["profiles"][0]
    assert "workspace" not in entry
    assert profiles.get_profile("work").workspace == str(
        profiles.profile_dir("work") / "workspace"
    )


def test_empty_name_rejected():
    with pytest.raises(ValueError):
        profiles.create_profile("   ", "teal")


def test_rename_keeps_id():
    meta = profiles.create_profile("Work", "teal")
    renamed = profiles.rename_profile(meta.id, "Day Job")
    assert renamed.id == "work"  # id immutable
    assert renamed.name == "Day Job"
    assert profiles.get_profile("work").name == "Day Job"


def test_rename_empty_rejected():
    profiles.create_profile("Work", "teal")
    with pytest.raises(ValueError):
        profiles.rename_profile("work", "")


def test_unknown_pid_raises():
    with pytest.raises(ValueError):
        profiles.rename_profile("nope", "X")
    with pytest.raises(ValueError):
        profiles.set_palette("nope", "teal")
    with pytest.raises(ValueError):
        profiles.set_active_default("nope")
    assert profiles.get_profile("nope") is None


def test_invalid_palette_rejected():
    with pytest.raises(ValueError):
        profiles.create_profile("Work", "rainbow")
    profiles.create_profile("Work", "teal")
    with pytest.raises(ValueError):
        profiles.set_palette("work", "rainbow")


def test_palette_uniqueness_and_relaxation_beyond_six():
    # first six must be distinct
    for name, pal in zip(("A", "B", "C", "D", "E", "F"), profiles.PALETTES, strict=True):
        profiles.create_profile(name, pal)

    # a seventh distinct palette does not exist; duplicate is now allowed (>6 rule)
    seventh = profiles.create_profile("G", "teal")
    assert seventh.palette == "teal"

    # while ≤6 unarchived, duplicates are rejected — verify with a fresh registry
    profiles.set_onboarded(False)  # touch registry; no effect on the rule


def test_palette_duplicate_rejected_within_six():
    profiles.create_profile("A", "teal")
    with pytest.raises(ValueError):
        profiles.create_profile("B", "teal")


def test_set_palette_excludes_self():
    profiles.create_profile("A", "teal")
    # setting a profile to its own current palette is a no-op, not a conflict
    meta = profiles.set_palette("a", "teal")
    assert meta.palette == "teal"
    # but taking another profile's palette is rejected
    profiles.create_profile("B", "coral")
    with pytest.raises(ValueError):
        profiles.set_palette("a", "coral")


def test_archive_flag_and_list_filtering():
    profiles.create_profile("Work", "teal")
    profiles.create_profile("Personal", "coral")
    profiles.archive_profile("personal")

    assert profiles.get_profile("personal").archived is True
    active = [m.id for m in profiles.list_profiles()]
    assert active == ["work"]  # archived hidden by default
    allp = [m.id for m in profiles.list_profiles(include_archived=True)]
    assert set(allp) == {"work", "personal"}

    # archiving frees the palette for reuse within the ≤6 rule
    reused = profiles.create_profile("New", "coral")
    assert reused.palette == "coral"


def test_set_active_default():
    profiles.create_profile("Work", "teal")
    profiles.create_profile("Personal", "coral")
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
        "channels": {"telegram": None, "discord": None, "slack": None},
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
    meta = profiles.create_profile("Work", "teal")
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
    meta = profiles.create_profile("Work", "teal")
    base = Config()
    derived = base.with_profile(meta)

    assert derived.data_dir != base.data_dir  # not the root data dir
    assert derived.skills_dir != base.skills_dir  # not the root skills dir
    assert derived.workspace_dir != base.workspace_dir  # profile's own workspace


def test_with_profile_deep_copy_isolates_nested_models():
    meta = profiles.create_profile("Work", "teal")
    base = Config()
    derived = base.with_profile(meta)
    derived.llm.model = "changed"
    assert base.llm.model != "changed"  # deep copy, not shared reference
