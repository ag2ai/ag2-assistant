"""SkillStateStore — install-wide Disable/Enable state + the resolution seam (ADR 0016).

Mirrors tests/test_folders.py: drive the store directly and assert observable
resolution (is a skill available?), not the on-disk byte layout.
"""

import pytest

from assistant.skills import (
    DISABLE_OWN,
    ORIGIN_BUNDLED,
    ORIGIN_GLOBAL,
    ORIGIN_PROFILE,
    SUPPRESS_SHARED,
    SkillStateStore,
    skill_origin,
)


def test_skill_origin_classifies_by_location(tmp_path):
    bundled = tmp_path / "bundled"
    (bundled / "web-research").mkdir(parents=True)
    assert skill_origin(str(bundled / "web-research" / "SKILL.md"), bundled) == ORIGIN_BUNDLED
    # Anywhere outside the bundled dir → global; a missing location → global.
    assert skill_origin(str(tmp_path / "elsewhere" / "SKILL.md"), bundled) == ORIGIN_GLOBAL
    assert skill_origin(None, bundled) == ORIGIN_GLOBAL


def _store(tmp_path):
    return SkillStateStore(path=tmp_path / "skills.json")


def test_default_on_everything_available(tmp_path):
    """Absence of a record means available — the inverse of a Folders Grant (ADR 0016)."""
    store = _store(tmp_path)
    assert store.is_available("web-research") is True
    assert store.is_disabled("web-research") is False
    assert store.disabled_names() == set()


def test_disable_then_unavailable_enable_restores(tmp_path):
    store = _store(tmp_path)
    store.set_enabled("web-research", False)
    assert store.is_disabled("web-research") is True
    assert store.is_available("web-research") is False
    assert store.disabled_names() == {"web-research"}
    # Re-enable restores availability.
    store.set_enabled("web-research", True)
    assert store.is_disabled("web-research") is False
    assert store.is_available("web-research") is True
    assert store.disabled_names() == set()


def test_disable_is_per_skill(tmp_path):
    store = _store(tmp_path)
    store.set_enabled("pdf-tools", False)
    assert store.is_available("pdf-tools") is False
    assert store.is_available("web-research") is True  # untouched skill stays on


def test_set_enabled_is_idempotent(tmp_path):
    store = _store(tmp_path)
    store.set_enabled("pdf-tools", False)
    store.set_enabled("pdf-tools", False)  # no duplicate record
    assert store.disabled_names() == {"pdf-tools"}
    store.set_enabled("pdf-tools", True)
    store.set_enabled("pdf-tools", True)  # enabling an already-enabled skill: no-op
    assert store.disabled_names() == set()


def test_set_enabled_requires_name(tmp_path):
    with pytest.raises(ValueError):
        _store(tmp_path).set_enabled("  ", False)


def test_is_available_takes_profile_but_default_on(tmp_path):
    """The seam already accepts a profile (per-profile Suppression lands later);
    for now the profile does not change the install-wide answer."""
    store = _store(tmp_path)
    assert store.is_available("web-research", profile="work") is True
    store.set_enabled("web-research", False)
    assert store.is_available("web-research", profile="work") is False
    assert store.is_available("web-research", profile="personal") is False  # install-wide


def test_persistence_and_fresh_store_reload(tmp_path):
    store = _store(tmp_path)
    store.set_enabled("email-drafting", False)
    fresh = _store(tmp_path)
    assert fresh.is_disabled("email-drafting") is True
    assert fresh.disabled_names() == {"email-drafting"}


def test_load_tolerates_non_object_json(tmp_path):
    p = tmp_path / "skills.json"
    p.write_text("[1, 2, 3]")
    store = SkillStateStore(path=p)
    assert store.disabled_names() == set()


def test_ephemeral_store_persists_nothing(tmp_path):
    store = SkillStateStore(path=None)
    store.set_enabled("web-research", False)
    assert store.is_disabled("web-research") is True
    assert not (tmp_path / "skills.json").exists()


def test_cross_instance_refresh_picks_up_writes(tmp_path):
    """A long-lived reader sees another writer's change on its next query (mtime
    self-refresh), the same guarantee FolderStore gives the gateway."""
    reader = _store(tmp_path)
    writer = _store(tmp_path)
    assert reader.is_available("pdf-tools") is True
    writer.set_enabled("pdf-tools", False)
    assert reader.is_available("pdf-tools") is False


# --- per-profile Suppression (ADR 0016 ticket 02) -------------------------------


def test_suppress_scopes_to_one_profile(tmp_path):
    """Suppressing a shared skill in profile A leaves B untouched — the whole point
    of Suppression (unlike an install-wide Disable, which hits everyone)."""
    store = _store(tmp_path)
    store.set_suppressed("web-research", "work", True)
    assert store.is_suppressed("web-research", "work") is True
    assert store.is_available("web-research", "work") is False
    # B is unaffected — no record for it means inherit "on".
    assert store.is_suppressed("web-research", "personal") is False
    assert store.is_available("web-research", "personal") is True
    # The install-wide answer (no profile) is likewise untouched.
    assert store.is_available("web-research") is True


def test_clearing_suppression_restores_availability(tmp_path):
    store = _store(tmp_path)
    store.set_suppressed("pdf-tools", "work", True)
    assert store.is_available("pdf-tools", "work") is False
    store.set_suppressed("pdf-tools", "work", False)
    assert store.is_suppressed("pdf-tools", "work") is False
    assert store.is_available("pdf-tools", "work") is True


def test_suppress_is_idempotent_and_per_pair(tmp_path):
    store = _store(tmp_path)
    store.set_suppressed("pdf-tools", "work", True)
    store.set_suppressed("pdf-tools", "work", True)  # no duplicate record
    assert store.suppressed_names("work") == {"pdf-tools"}
    # A different skill / profile is an independent record.
    store.set_suppressed("web-research", "work", True)
    assert store.suppressed_names("work") == {"pdf-tools", "web-research"}
    assert store.suppressed_names("personal") == set()


def test_install_wide_disable_beats_suppression_everywhere(tmp_path):
    """A skill Disabled install-wide is unavailable in every profile regardless of
    any Suppression record — the two surfaces never contradict."""
    store = _store(tmp_path)
    store.set_suppressed("web-research", "work", True)  # per-profile off in A only
    store.set_enabled("web-research", False)  # then off install-wide
    assert store.is_available("web-research", "work") is False
    assert store.is_available("web-research", "personal") is False  # off everywhere
    # Re-enabling install-wide leaves A's own Suppression standing.
    store.set_enabled("web-research", True)
    assert store.is_available("web-research", "work") is False  # still suppressed in A
    assert store.is_available("web-research", "personal") is True


def test_set_suppressed_requires_name_and_profile(tmp_path):
    store = _store(tmp_path)
    with pytest.raises(ValueError):
        store.set_suppressed("  ", "work", True)
    with pytest.raises(ValueError):
        store.set_suppressed("web-research", "  ", True)


def test_suppression_persists_and_reloads(tmp_path):
    store = _store(tmp_path)
    store.set_suppressed("email-drafting", "work", True)
    fresh = _store(tmp_path)
    assert fresh.is_suppressed("email-drafting", "work") is True
    assert fresh.suppressed_names("work") == {"email-drafting"}
    assert fresh.is_available("email-drafting", "personal") is True


# --- cascade-purge on Delete (ADR 0016 ticket 03) -------------------------------


def test_purge_clears_disabled_and_every_suppression(tmp_path):
    """Deleting a Global skill drops its install-wide Disable AND every profile's
    Suppression of it — so a later same-named re-install resolves default-on
    everywhere (no ghost). Mirrors FolderStore.delete_folder dropping grants."""
    store = _store(tmp_path)
    store.set_enabled("web-research", False)  # off install-wide
    store.set_suppressed("web-research", "work", True)  # and suppressed in one profile
    store.set_suppressed("web-research", "personal", True)  # and another
    store.set_suppressed("pdf-tools", "work", True)  # an unrelated record survives

    store.purge("web-research")

    # No lingering record for the purged skill anywhere.
    assert store.is_disabled("web-research") is False
    assert store.is_suppressed("web-research", "work") is False
    assert store.is_suppressed("web-research", "personal") is False
    # A re-install (same name, no record) is default-on for every profile.
    assert store.is_available("web-research", "work") is True
    assert store.is_available("web-research", "personal") is True
    # Another skill's records are untouched by the cascade.
    assert store.is_suppressed("pdf-tools", "work") is True


def test_purge_survives_reload_and_is_idempotent(tmp_path):
    store = _store(tmp_path)
    store.set_suppressed("email-drafting", "work", True)
    store.purge("email-drafting")
    store.purge("email-drafting")  # unknown/already-clean name: no error
    fresh = _store(tmp_path)
    assert fresh.suppressed_names("work") == set()
    assert fresh.is_available("email-drafting", "work") is True


# --- own-disable vs shared-suppress: the two off-records don't collide (findings 4 & 5) ---


def test_global_purge_leaves_same_named_profile_own_disable_intact(tmp_path):
    """A profile's OWN 'foo' Disabled + an unrelated Global 'foo' deleted install-wide:
    the Global purge must NOT clear the profile's own off-state (finding 4), else the
    still-on-disk own 'foo' silently flips back to available."""
    store = _store(tmp_path)
    store.set_suppressed("foo", "work", True, kind=DISABLE_OWN)  # P's own foo, Disabled
    assert store.is_available("foo", "work", origin=ORIGIN_PROFILE) is False

    store.purge("foo")  # delete the unrelated Global "foo" install-wide

    # The own off-record survives — P's own foo stays Disabled across a reload.
    assert store.is_suppressed("foo", "work") is True
    assert _store(tmp_path).is_available("foo", "work", origin=ORIGIN_PROFILE) is False


def test_global_purge_clears_shared_suppression_but_not_own(tmp_path):
    """A shared Suppression (kind=SHARED) is cleared by a Global purge; a same-named
    OWN disable in another profile is not — the two records coexist and each answers to
    its own Delete."""
    store = _store(tmp_path)
    store.set_suppressed("foo", "work", True, kind=SUPPRESS_SHARED)  # shared, suppressed in work
    store.set_suppressed("foo", "home", True, kind=DISABLE_OWN)  # home's OWN foo, disabled

    store.purge("foo")

    assert store.is_suppressed("foo", "work") is False  # shared suppression gone
    assert store.is_suppressed("foo", "home") is True  # own disable survives


def test_profile_copy_delete_clears_own_but_keeps_shadowed_shared_suppression(tmp_path):
    """Profile P suppressed a Global 'foo', THEN installed its own 'foo' (shadowing the
    Global) and disabled it. Deleting the own copy clears only the OWN record — the
    shared Suppression stays, so the revealed Global 'foo' is still off, not available
    (finding 5)."""
    store = _store(tmp_path)
    store.set_suppressed("foo", "work", True, kind=SUPPRESS_SHARED)  # suppressed the Global
    store.set_suppressed("foo", "work", True, kind=DISABLE_OWN)  # own copy, disabled

    # Deleting the own copy clears just the OWN off-record (what delete_profile_skill does).
    store.set_suppressed("foo", "work", False, kind=DISABLE_OWN)

    # The shared Suppression remains → the shadowed Global stays suppressed for P.
    assert store.is_suppressed("foo", "work") is True
    assert store.is_available("foo", "work") is False


def test_set_suppressed_rejects_unknown_kind(tmp_path):
    with pytest.raises(ValueError):
        _store(tmp_path).set_suppressed("foo", "work", True, kind="bogus")


def test_pre_kind_records_load_as_shared(tmp_path):
    """A suppressed record written before the kind tag (no 'kind' field) loads as a
    SHARED suppression — the original semantics — so a Global purge still clears it."""
    p = tmp_path / "skills.json"
    p.write_text('{"disabled": [], "suppressed": [{"profile": "work", "name": "foo"}]}')
    store = SkillStateStore(path=p)
    assert store.is_suppressed("foo", "work") is True
    store.purge("foo")
    assert store.is_suppressed("foo", "work") is False  # cleared as a shared record
