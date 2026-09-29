"""A Card can be turned off: its own state document, and the one resolution seam."""

import json
from pathlib import Path

import pytest

from assistant.a2ui import CATALOG_ID, CardCatalog
from assistant.cards import CARDS_DIR, CARDS_DOCUMENT, CardStateStore
from assistant.config import Config
from assistant.profiles import ProfileRegistry
from assistant.skills import SkillStateStore
from assistant.state_store import DISABLE_OWN, SUPPRESS_SHARED
from tests.support.cards import skill_body

SHELF = """
name: Shelf
description: {description}
fields:
  title: {{type: string}}
required: [title]
layout:
  - {{id: root, component: Card, child: head}}
  - {{id: head, component: Text, text: {{path: /title}}}}
example: {{title: Paperbacks}}
"""


def _write(directory: Path, body: str) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "shelf.card.yaml"
    path.write_text(body)
    return path


def _shared(paths, description: str | None = None) -> Path:
    """A Global Card — a shared layer, so install-wide Disable reaches it."""
    return _write(paths.root / CARDS_DIR, _shelf(description) if description else _shelf())


def _own(config, description: str | None = None) -> Path:
    """A Card in one profile's own Files space."""
    return _write(
        config.workspace_dir / CARDS_DIR, _shelf(description) if description else _shelf()
    )


def _shelf(description: str = "Use when the user asks what is on their shelf.") -> str:
    return SHELF.format(description=description)


def _emit(component: str) -> list[dict]:
    """The two messages a model writes to draw one Card as the whole surface."""
    return [
        {"version": "v1.0", "createSurface": {"surfaceId": "s1", "catalogId": CATALOG_ID}},
        {
            "version": "v1.0",
            "updateComponents": {
                "surfaceId": "s1",
                "components": [{"id": "root", "component": component, "title": "Paperbacks"}],
            },
        },
    ]


@pytest.fixture
def profiles(paths):
    """Two real profiles on one install, each with its own Files space."""
    registry = ProfileRegistry(paths)
    base = Config.for_paths(paths)
    return (
        base.with_profile(registry.create_profile("Work", "#109e91")),
        base.with_profile(registry.create_profile("Personal", "#f95339")),
    )


def test_everything_defaults_to_on(config):
    """An install upgrading to this has every Card available, and reading takes
    no document to disk."""
    _own(config)

    cards = CardCatalog(config).cards()

    assert "Shelf" in cards and "Checklist" in cards
    assert not (config.root_dir / CARDS_DOCUMENT).exists()


def test_a_card_disabled_install_wide_is_offered_in_no_profile(profiles, paths):
    work, personal = profiles
    _shared(paths)
    assert "Shelf" in CardCatalog(work).cards()

    CardStateStore(paths.root).set_enabled("Shelf", False)

    assert "Shelf" not in CardCatalog(work).cards()
    assert "Shelf" not in CardCatalog(personal).cards()


def test_a_card_suppressed_by_one_profile_stays_available_to_the_others(profiles, paths):
    work, personal = profiles
    _shared(paths)

    CardStateStore(paths.root).set_suppressed(
        "Shelf", work.data_dir.name, True, kind=SUPPRESS_SHARED
    )

    assert "Shelf" not in CardCatalog(work).cards()
    assert "Shelf" in CardCatalog(personal).cards()


def test_a_profiles_own_card_turns_off_without_touching_a_same_named_shared_one(profiles, paths):
    work, personal = profiles
    _shared(paths, "The Global one.")
    _own(work, "Work's own.")

    CardStateStore(paths.root).set_suppressed("Shelf", work.data_dir.name, True, kind=DISABLE_OWN)

    assert "Shelf" not in CardCatalog(work).cards()
    assert CardCatalog(personal).cards()["Shelf"].description == "The Global one."


def test_disabling_an_own_copy_does_not_reach_a_shared_card_of_the_same_name(profiles, paths):
    """The two off-records are separate switches: turning off Work's own copy says
    nothing about the Global Card of that name, which Work has no copy of at all."""
    work, _ = profiles
    _shared(paths, "The Global one.")

    CardStateStore(paths.root).set_suppressed("Shelf", work.data_dir.name, True, kind=DISABLE_OWN)

    assert "Shelf" in CardCatalog(work).cards()


def test_changing_which_kind_of_record_turns_a_card_off_reaches_the_agent(profiles, paths):
    """The two records name the same (profile, Card): the catalog must notice the
    switch moving from one to the other, not just the name being listed."""
    work, _ = profiles
    _shared(paths, "The Global one.")
    catalog = CardCatalog(work)
    store = CardStateStore(paths.root)
    store.set_suppressed("Shelf", work.data_dir.name, True, kind=DISABLE_OWN)
    assert "Shelf" in catalog.cards()

    store.set_suppressed("Shelf", work.data_dir.name, True, kind=SUPPRESS_SHARED)

    assert "Shelf" not in catalog.cards()


def test_deleting_a_shared_card_clears_every_profiles_suppression_of_it(profiles, paths):
    work, personal = profiles
    _shared(paths)
    store = CardStateStore(paths.root)
    store.set_enabled("Shelf", False)
    store.set_suppressed("Shelf", work.data_dir.name, True, kind=SUPPRESS_SHARED)
    store.set_suppressed("Shelf", personal.data_dir.name, True, kind=SUPPRESS_SHARED)

    (paths.root / CARDS_DIR / "shelf.card.yaml").unlink()
    store.purge("Shelf")
    _shared(paths, "A later Card of the same name.")

    assert "Shelf" in CardCatalog(work).cards()
    assert "Shelf" in CardCatalog(personal).cards()


def test_a_skill_and_a_card_sharing_a_name_have_independent_state(config, paths):
    _own(config)

    SkillStateStore(paths.root).set_enabled("Shelf", False)

    assert "Shelf" in CardCatalog(config).cards()
    assert CardStateStore(paths.root).disabled_names() == set()


def test_card_state_is_its_own_document(paths):
    CardStateStore(paths.root).set_enabled("Shelf", False)

    assert json.loads((paths.root / CARDS_DOCUMENT).read_text())["disabled"] == ["Shelf"]
    assert not (paths.root / "skills.json").exists()


async def test_a_disabled_card_is_neither_offered_nor_valid_to_emit(config, paths):
    _shared(paths)
    catalog = CardCatalog(config)
    assert catalog.runtime().parser.validate(_emit("Shelf")).is_valid

    CardStateStore(paths.root).set_enabled("Shelf", False)

    assert "Shelf" not in await skill_body(config)
    assert not catalog.runtime().parser.validate(_emit("Shelf")).is_valid


def test_turning_a_card_off_and_back_on_reaches_the_agent_without_a_restart(config, paths):
    _shared(paths)
    catalog = CardCatalog(config)
    store = CardStateStore(paths.root)
    assert "Shelf" in catalog.cards()

    store.set_enabled("Shelf", False)
    assert "Shelf" not in catalog.cards()

    store.set_enabled("Shelf", True)
    assert "Shelf" in catalog.cards()


def test_a_card_instance_already_drawn_still_draws_after_the_card_is_turned_off(config, paths):
    _shared(paths)
    catalog = CardCatalog(config)

    CardStateStore(paths.root).set_enabled("Shelf", False)

    assert "Shelf" not in catalog.cards()
    assert "Shelf" in catalog.drawable()
