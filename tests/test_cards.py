"""Cards as files: what the loader offers, and what the expansion draws."""

import logging
from pathlib import Path

from assistant.cards import expand_card_messages, expand_components, load_cards

CARD = """
name: RunTracker
description: Use when the user asks about their runs or weekly mileage.
fields:
  title: {type: string}
  runs:
    type: array
    items: {type: object}
required: [title]
layout:
  - {id: root, component: Card, child: body}
  - {id: body, component: Column, children: [head, rows]}
  - {id: head, component: Text, text: {path: /title}}
  - {id: rows, component: List, children: {componentId: row, path: /runs}}
  - {id: row, component: Text, text: {path: ./day}}
example: {title: This week, runs: [{day: Mon}]}
"""


def _write(directory: Path, filename: str, body: str) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / filename
    path.write_text(body)
    return path


def _instance(**fields) -> dict:
    return {"id": "root", "component": "RunTracker", **fields}


def test_a_card_is_known_by_the_name_inside_it_not_by_its_filename(tmp_path):
    _write(tmp_path, "whatever-i-called-it.card.yaml", CARD)

    cards = load_cards(tmp_path)

    assert list(cards) == ["RunTracker"]
    assert cards["RunTracker"].description.startswith("Use when")


def test_renaming_a_card_file_changes_nothing_about_the_card(tmp_path):
    first = _write(tmp_path, "runs.card.yaml", CARD)
    before = load_cards(tmp_path)["RunTracker"]

    first.rename(tmp_path / "tracker.card.yaml")

    assert load_cards(tmp_path)["RunTracker"] == before


def test_a_file_without_the_card_suffix_is_passed_over_without_a_word(tmp_path, caplog):
    _write(tmp_path, "notes.yaml", CARD)
    _write(tmp_path, "readme.md", "not a card")

    with caplog.at_level(logging.WARNING):
        assert load_cards(tmp_path) == {}

    assert caplog.text == ""


def test_a_missing_card_directory_means_no_cards(tmp_path):
    assert load_cards(tmp_path / "nothing-here") == {}


def test_a_broken_card_costs_only_itself(tmp_path):
    _write(tmp_path, "runs.card.yaml", CARD)
    _write(tmp_path, "broken.card.yaml", "name: Broken\n  bad: [indentation")

    assert list(load_cards(tmp_path)) == ["RunTracker"]


def test_a_card_whose_layout_names_an_id_it_does_not_declare_is_skipped(tmp_path):
    _write(tmp_path, "typo.card.yaml", CARD.replace("children: [head, rows]", "children: [hed]"))

    assert load_cards(tmp_path) == {}


def test_a_card_whose_layout_draws_something_that_is_not_a_primitive_is_skipped(tmp_path):
    _write(tmp_path, "runs.card.yaml", CARD)

    assert load_cards(tmp_path, components={"Card", "Column", "Text"}) == {}
    assert list(load_cards(tmp_path, components={"Card", "Column", "Text", "List"})) == [
        "RunTracker"
    ]


def test_a_card_whose_example_omits_a_required_field_is_skipped(tmp_path):
    _write(tmp_path, "runs.card.yaml", CARD.replace("example: {title: This week, ", "example: {"))

    assert load_cards(tmp_path) == {}


def test_a_card_instance_is_drawn_as_primitives_and_its_fields_become_data(tmp_path):
    _write(tmp_path, "runs.card.yaml", CARD)
    cards = load_cards(tmp_path)

    components, writes = expand_components(
        [_instance(title="This week", runs=[{"day": "Mon"}])], cards
    )

    assert [component["component"] for component in components] == [
        "Card",
        "Column",
        "Text",
        "List",
        "Text",
    ]
    assert components[0]["id"] == "root"
    assert writes == [("/title", "This week"), ("/runs", [{"day": "Mon"}])]


def test_a_layouts_bindings_survive_the_drawing(tmp_path):
    _write(tmp_path, "runs.card.yaml", CARD)
    cards = load_cards(tmp_path)

    components, _ = expand_components([_instance(title="This week")], cards)
    by_id = {component["id"]: component for component in components}

    assert by_id["root__head"]["text"] == {"path": "/title"}
    assert by_id["root__rows"]["children"] == {"componentId": "root__row", "path": "/runs"}
    # A path opening with `.` reads the repeated item and is never re-rooted.
    assert by_id["root__row"]["text"] == {"path": "./day"}


def test_a_nested_card_keeps_its_ids_and_its_data_to_itself(tmp_path):
    _write(tmp_path, "runs.card.yaml", CARD)
    cards = load_cards(tmp_path)

    components, writes = expand_components(
        [
            {"id": "root", "component": "Column", "children": ["one", "two"]},
            {"id": "one", "component": "RunTracker", "title": "Mine"},
            {"id": "two", "component": "RunTracker", "title": "Yours"},
        ],
        cards,
    )
    by_id = {component["id"]: component for component in components}

    assert len(by_id) == len(components)  # every drawn id is its own
    assert by_id["one"]["component"] == "Card"
    assert by_id["one__head"]["text"] == {"path": "/_cards/one/title"}
    assert by_id["two__head"]["text"] == {"path": "/_cards/two/title"}
    assert writes == [("/_cards/one/title", "Mine"), ("/_cards/two/title", "Yours")]


def test_a_card_is_drawn_the_same_whole_or_nested(tmp_path):
    _write(tmp_path, "runs.card.yaml", CARD)
    cards = load_cards(tmp_path)

    whole, whole_writes = expand_components([_instance(title="Mine")], cards)
    nested, nested_writes = expand_components(
        [
            {"id": "root", "component": "Column", "children": ["card"]},
            {"id": "card", "component": "RunTracker", "title": "Mine"},
        ],
        cards,
    )

    def normalised(value, instance, prefix):
        """The drawing with its instance's ids and data prefix taken back out."""
        if isinstance(value, list | tuple):
            return [normalised(item, instance, prefix) for item in value]
        if isinstance(value, dict):
            return {k: normalised(v, instance, prefix) for k, v in value.items()}
        if isinstance(value, str):
            if value == instance:
                return "@"
            return value.removeprefix(f"{instance}__").replace(prefix, "", 1)
        return value

    drawn = [component for component in nested if component["id"] != "root"]
    assert normalised(whole, "root", "") == normalised(drawn, "card", "/_cards/card")
    assert normalised(whole_writes, "root", "") == normalised(nested_writes, "card", "/_cards/card")


def test_a_drawn_card_is_followed_by_the_writes_its_fields_make(tmp_path):
    _write(tmp_path, "runs.card.yaml", CARD)
    cards = load_cards(tmp_path)

    messages = expand_card_messages(
        [
            {"version": "v1.0", "createSurface": {"surfaceId": "s1", "catalogId": "c"}},
            {
                "version": "v1.0",
                "updateComponents": {"surfaceId": "s1", "components": [_instance(title="Mine")]},
            },
        ],
        cards,
    )

    assert "createSurface" in messages[0]
    drawn = messages[1]["updateComponents"]["components"]
    assert all(component["component"] != "RunTracker" for component in drawn)
    assert messages[2] == {
        "version": "v1.0",
        "updateDataModel": {"surfaceId": "s1", "path": "/title", "value": "Mine"},
    }


def test_messages_that_draw_no_card_are_left_alone(tmp_path):
    _write(tmp_path, "runs.card.yaml", CARD)
    original = [
        {
            "version": "v1.0",
            "updateComponents": {
                "surfaceId": "s1",
                "components": [{"id": "root", "component": "Text", "text": "hello"}],
            },
        }
    ]

    assert expand_card_messages(original, load_cards(tmp_path)) == original


def test_a_card_whose_layout_has_no_root_is_skipped(tmp_path):
    _write(
        tmp_path,
        "rootless.card.yaml",
        CARD.replace("id: root,", "id: top,"),
    )

    assert load_cards(tmp_path) == {}


def test_the_layouts_root_is_named_not_positional(tmp_path):
    reordered = CARD.replace(
        "  - {id: root, component: Card, child: body}\n  - {id: body, component: Column, children: [head, rows]}\n",
        "  - {id: body, component: Column, children: [head, rows]}\n  - {id: root, component: Card, child: body}\n",
    )
    _write(tmp_path, "runs.card.yaml", reordered)

    components, _ = expand_components([_instance(title="Mine")], load_cards(tmp_path))
    by_id = {component["id"]: component for component in components}

    # The instance takes the id of the layout's `root`, wherever the file lists it.
    assert by_id["root"]["component"] == "Card"
    assert "root__body" in by_id


def test_a_card_whose_description_busts_its_budget_is_skipped_not_truncated(tmp_path, caplog):
    path = _write(
        tmp_path,
        "wordy.card.yaml",
        CARD.replace("Use when the user asks about their runs or weekly mileage.", "x" * 201),
    )

    with caplog.at_level(logging.WARNING):
        assert load_cards(tmp_path) == {}

    assert str(path) in caplog.text


def test_a_card_whose_example_busts_its_budget_is_skipped(tmp_path):
    fat = "example: {title: This week, runs: [" + ", ".join(["{day: Monday}"] * 200) + "]}"
    _write(
        tmp_path,
        "fat.card.yaml",
        CARD.replace("example: {title: This week, runs: [{day: Mon}]}", fat),
    )

    assert load_cards(tmp_path) == {}


def test_a_card_suffixed_file_too_big_to_be_a_card_is_never_parsed(tmp_path, caplog):
    path = _write(tmp_path, "video.card.yaml", CARD + "# " + "x" * 64 * 1024)

    with caplog.at_level(logging.WARNING):
        assert load_cards(tmp_path) == {}

    assert str(path) in caplog.text


def test_a_second_file_claiming_a_name_already_taken_is_skipped_with_a_warning(tmp_path, caplog):
    _write(tmp_path, "a.card.yaml", CARD)
    twin = _write(tmp_path, "b.card.yaml", CARD.replace("weekly mileage", "their shoes"))

    with caplog.at_level(logging.WARNING):
        cards = load_cards(tmp_path)

    assert cards["RunTracker"].description.endswith("weekly mileage.")
    assert str(twin) in caplog.text
