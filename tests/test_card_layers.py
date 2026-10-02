"""Where Cards come from: the three layers, their precedence, and their freshness."""

import logging
from pathlib import Path

import pytest

from assistant.a2ui import CARD_VOCABULARY, CardCatalog, card_layers
from assistant.cards import CARDS_DIR, resolve_cards
from assistant.config import Config
from assistant.folders import FolderStore
from assistant.permissions import PermissionManager, PermissionStore
from assistant.profiles import ProfileRegistry
from assistant.state_store import ORIGIN_BUNDLED, ORIGIN_GLOBAL, ORIGIN_PROFILE
from assistant.tools.files import write_file_impl
from tests.support.cards import a2ui_view, skill_body

CARD = """
name: RunTracker
description: {description}
fields:
  title: {{type: string}}
required: [title]
layout:
  - {{id: root, component: Card, child: head}}
  - {{id: head, component: Text, text: {{path: /title}}}}
example: {{title: This week}}
"""

OTHER = """
name: Shelf
description: Use when the user asks what is on their shelf.
fields:
  title: {type: string}
required: [title]
layout:
  - {id: root, component: Card, child: head}
  - {id: head, component: Text, text: {path: /title}}
example: {title: Paperbacks}
"""


def _write(directory: Path, filename: str, body: str) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / filename
    path.write_text(body)
    return path


def _card(description: str) -> str:
    return CARD.format(description=description)


def _catalog(config) -> CardCatalog:
    return CardCatalog(config)


@pytest.fixture
def layers(tmp_path):
    """Three Card directories with the layer each is — none of them created."""
    return (
        (ORIGIN_BUNDLED, tmp_path / "bundled"),
        (ORIGIN_GLOBAL, tmp_path / "global"),
        (ORIGIN_PROFILE, tmp_path / "profile"),
    )


def _dirs(layers) -> tuple[Path, Path, Path]:
    return tuple(directory for _origin, directory in layers)


def test_a_profile_card_wins_over_a_global_one_over_a_bundled_one(layers):
    bundled, glob, profile = _dirs(layers)
    _write(bundled, "runs.card.yaml", _card("The bundled one."))
    _write(glob, "runs.card.yaml", _card("The global one."))
    _write(profile, "runs.card.yaml", _card("The profile's own."))

    assert resolve_cards(layers)["RunTracker"].description == "The profile's own."


def test_a_global_card_wins_over_a_bundled_one(layers):
    bundled, glob, _ = _dirs(layers)
    _write(bundled, "runs.card.yaml", _card("The bundled one."))
    _write(glob, "runs.card.yaml", _card("The global one."))

    assert resolve_cards(layers)["RunTracker"].description == "The global one."


def test_every_layer_contributes_the_names_no_other_layer_claims(layers):
    bundled, _, profile = _dirs(layers)
    _write(bundled, "runs.card.yaml", _card("The bundled one."))
    _write(profile, "shelf.card.yaml", OTHER)

    assert sorted(resolve_cards(layers)) == ["RunTracker", "Shelf"]


def test_a_card_dropped_into_a_layer_is_offered_the_next_time_it_is_asked_for(layers):
    _, _, profile = _dirs(layers)
    assert resolve_cards(layers) == {}

    _write(profile, "shelf.card.yaml", OTHER)

    assert list(resolve_cards(layers)) == ["Shelf"]


def test_editing_a_card_file_changes_what_is_offered(layers):
    _, _, profile = _dirs(layers)
    path = _write(profile, "runs.card.yaml", _card("The first thing it said."))
    assert resolve_cards(layers)["RunTracker"].description == "The first thing it said."

    path.write_text(_card("Something else entirely, and longer."))

    assert resolve_cards(layers)["RunTracker"].description == "Something else entirely, and longer."


def test_deleting_a_profile_card_uncovers_the_layer_below(layers):
    bundled, _, profile = _dirs(layers)
    _write(bundled, "runs.card.yaml", _card("The bundled one."))
    path = _write(profile, "runs.card.yaml", _card("The profile's own."))
    assert resolve_cards(layers)["RunTracker"].description == "The profile's own."

    path.unlink()

    assert resolve_cards(layers)["RunTracker"].description == "The bundled one."


def test_a_broken_card_in_one_layer_costs_that_card_and_nothing_else(layers, caplog):
    _, glob, profile = _dirs(layers)
    _write(glob, "shelf.card.yaml", OTHER)
    broken = _write(profile, "runs.card.yaml", "name: RunTracker\nfields: [not, a, mapping]\n")

    with caplog.at_level(logging.WARNING):
        cards = resolve_cards(layers)

    assert list(cards) == ["Shelf"]
    assert str(broken) in caplog.text


def test_a_card_a_layer_cannot_draw_leaves_the_layer_below_showing(layers):
    bundled, _, profile = _dirs(layers)
    _write(bundled, "runs.card.yaml", _card("The bundled one."))
    _write(profile, "runs.card.yaml", _card("The profile's own.").replace("Text", "Kandinsky"))

    cards = resolve_cards(layers, components=CARD_VOCABULARY)

    assert cards["RunTracker"].description == "The bundled one."


def test_layers_that_are_not_there_mean_no_cards_rather_than_an_error(layers):
    assert resolve_cards(layers) == {}


async def test_the_agent_writing_a_card_creates_the_directory_it_belongs_in(config, tmp_path):
    permissions = PermissionManager(
        PermissionStore(path=tmp_path / "perm.json"),
        asker=None,
        folders=FolderStore(path=tmp_path / "folders.json"),
        profile="p1",
        workspace_dir=config.workspace_dir,
    )
    assert _catalog(config).cards().get("Shelf") is None

    await write_file_impl(f"{CARDS_DIR}/shelf.card.yaml", OTHER, permissions)

    assert (config.workspace_dir / CARDS_DIR).is_dir()
    assert "Shelf" in _catalog(config).cards()


def test_the_layers_are_bundled_then_the_root_then_the_profiles_files_space(config):
    layers = card_layers(config)
    bundled, glob, profile = _dirs(layers)

    assert [origin for origin, _ in layers] == [ORIGIN_BUNDLED, ORIGIN_GLOBAL, ORIGIN_PROFILE]
    assert bundled.is_dir() and (bundled / "checklist.card.yaml").exists()
    assert glob == config.root_dir / "cards"
    assert profile == config.workspace_dir / "cards"


def test_a_profile_card_is_offered_beside_the_bundled_ones(config):
    _write(config.workspace_dir / "cards", "shelf.card.yaml", OTHER)

    cards = _catalog(config).cards()

    assert "Shelf" in cards
    assert "Checklist" in cards


def test_one_profiles_card_is_not_another_profiles(paths):
    registry = ProfileRegistry(paths)
    base = Config.for_paths(paths)
    mine = base.with_profile(registry.create_profile("Work", "#109e91"))
    yours = base.with_profile(registry.create_profile("Personal", "#f95339"))
    _write(mine.workspace_dir / "cards", "shelf.card.yaml", OTHER)

    assert "Shelf" in _catalog(mine).cards()
    assert "Shelf" not in _catalog(yours).cards()


def test_a_global_card_reaches_every_profile(paths):
    registry = ProfileRegistry(paths)
    base = Config.for_paths(paths)
    _write(paths.root / "cards", "shelf.card.yaml", OTHER)

    for name in ("Work", "Personal"):
        profile = base.with_profile(registry.create_profile(name, "#109e91"))
        assert "Shelf" in _catalog(profile).cards()


async def test_the_agent_is_offered_an_edited_card_without_a_restart(config):
    path = _write(config.workspace_dir / "cards", "runs.card.yaml", _card("The first thing."))
    view = a2ui_view(config)
    assert "The first thing." in await skill_body(config, view)

    path.write_text(_card("Something else entirely, and longer."))

    body = await skill_body(config, view)
    assert "Something else entirely, and longer." in body
    assert "The first thing." not in body


async def test_a_card_dropped_in_is_offered_to_the_agent_without_a_restart(config):
    view = a2ui_view(config)
    assert "Shelf" not in await skill_body(config, view)

    _write(config.workspace_dir / "cards", "shelf.card.yaml", OTHER)

    assert "Shelf" in await skill_body(config, view)


def test_repeated_asks_with_nothing_changed_on_disk_rebuild_nothing(config):
    _write(config.workspace_dir / "cards", "runs.card.yaml", _card("The profile's own."))
    catalog = _catalog(config)

    assert catalog.runtime() is catalog.runtime()
