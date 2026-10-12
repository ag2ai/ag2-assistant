"""File and in-memory Card definitions obey the same validation rules."""

import pytest
import yaml

from assistant.a2ui import CARD_VOCABULARY
from assistant.cards import (
    DESCRIPTION_BUDGET,
    EXAMPLE_BUDGET,
    FILE_BUDGET,
    TOPIC_BUDGET,
    CardError,
    expand_card_messages,
    load_cards,
    validate_card,
)
from tests.support.cards import card_definition as definition


def test_invalid_example_is_rejected_in_memory_and_from_file(tmp_path):
    raw = definition()
    raw["example"] = {"title": 17}
    with pytest.raises(CardError, match="example.*title"):
        validate_card(raw, CARD_VOCABULARY)
    (tmp_path / "shelf.card.yaml").write_text(yaml.safe_dump(raw, sort_keys=False))
    assert load_cards(tmp_path, CARD_VOCABULARY) == {}


def test_relative_binding_requires_a_template_scope():
    raw = definition()
    raw["layout"][1]["text"] = {"path": "./title"}
    with pytest.raises(CardError, match="relative binding"):
        validate_card(raw, CARD_VOCABULARY)


@pytest.mark.parametrize("properties", [{"tone": "purple"}, {"color": "red"}, {"text": 17}])
def test_invalid_primitive_properties_are_rejected(properties):
    raw = definition()
    raw["layout"][1].update(properties)
    with pytest.raises(CardError, match="head"):
        validate_card(raw, CARD_VOCABULARY)


@pytest.mark.parametrize(
    "component",
    [
        {"component": "Video", "url": "clip.mp4", "posterUrl": {"path": "/title"}},
        {
            "component": "ChoicePicker",
            "options": [{"label": "Yes", "value": "yes"}],
            "displayStyle": "chips",
        },
        {
            "component": "DateTimeInput",
            "enableDate": True,
            "min": "2026-01-01",
            "max": {"path": "/title"},
        },
    ],
)
def test_existing_media_and_input_properties_remain_loadable(component, tmp_path):
    raw = definition()
    raw["layout"][1] = {"id": "head", **component}
    card = validate_card(raw, CARD_VOCABULARY)
    (tmp_path / "shelf.card.yaml").write_text(yaml.safe_dump(raw))
    assert load_cards(tmp_path, CARD_VOCABULARY)[card.name] == card


@pytest.mark.parametrize(
    "change, error",
    [
        ({"required": 1}, "required must"),
        ({"fields": {"title": {"type": "invalid"}}}, "example"),
        ({"description": "x" * (DESCRIPTION_BUDGET + 1)}, "description.*over"),
        ({"topic": "x" * (TOPIC_BUDGET + 1)}, "topic.*over"),
        ({"example": {"title": "x" * EXAMPLE_BUDGET}}, "example.*over"),
        ({"extra": "x" * FILE_BUDGET}, "definition.*over"),
        ({"layout": [{"id": "other", "component": "Text", "text": "X"}]}, "no 'root'"),
        ({"layout": [{"id": "root", "component": "Card", "child": "absent"}]}, "unknown id"),
        ({"layout": [{"id": "root", "component": "Unknown"}]}, "not a primitive"),
    ],
)
def test_invalid_definitions_are_actionable(change, error):
    with pytest.raises(CardError, match=error):
        validate_card({**definition(), **change}, CARD_VOCABULARY)


@pytest.mark.parametrize("key", ["name", "description", "topic"])
def test_metadata_must_match_the_wire_contract(key):
    with pytest.raises(CardError, match=f"{key} must be text"):
        validate_card({**definition(), key: 17}, CARD_VOCABULARY)


@pytest.mark.parametrize("keyword", ["$ref", "$dynamicRef"])
def test_fields_cannot_trigger_remote_schema_resolution(keyword):
    raw = definition()
    raw["fields"]["title"] = {keyword: "https://example.com/remote-schema"}
    with pytest.raises(CardError, match="only local schemas"):
        validate_card(raw, CARD_VOCABULARY)


@pytest.mark.parametrize("axis", ["columns", "rows", "cells", "key"])
def test_table_axes_and_column_key_require_data_bindings(axis):
    raw = definition()
    raw["layout"] = [
        {
            "id": "root",
            "component": "Table",
            "columns": {"path": "/columns"},
            "rows": {"path": "/rows"},
            "cells": {"path": "./cells"},
            "cell": "cell",
        },
        {"id": "cell", "component": "Text", "text": {"path": "."}},
    ]
    raw["layout"][0][axis] = [{"label": "Literal axis"}]
    with pytest.raises(CardError, match="root"):
        validate_card(raw, CARD_VOCABULARY)


def test_nested_repeats_and_table_scopes_draw_identically_from_file_and_memory(tmp_path):
    raw = definition()
    raw["fields"]["rows"] = {"type": "array", "items": {"type": "object"}}
    raw["fields"]["columns"] = {"type": "array", "items": {"type": "object"}}
    raw["example"]["columns"] = [{"label": "Label"}]
    raw["example"]["rows"] = [{"label": "A", "items": [{"label": "B", "cells": ["Cell"]}]}]
    raw["layout"] = [
        {"id": "root", "component": "Column", "children": {"componentId": "row", "path": "/rows"}},
        {"id": "row", "component": "Column", "children": ["label", "nested", "table"]},
        {"id": "label", "component": "Text", "text": {"path": "./label"}},
        {
            "id": "nested",
            "component": "List",
            "children": {"componentId": "item", "path": "./items"},
        },
        {"id": "item", "component": "Text", "text": {"path": "./label"}},
        {
            "id": "table",
            "component": "Table",
            "columns": {"path": "/columns"},
            "rows": {"path": "./items"},
            "cells": {"path": "./cells"},
            "header": "header",
            "cell": "cell",
        },
        {"id": "header", "component": "Column", "children": ["header_text"]},
        {"id": "header_text", "component": "Text", "text": {"path": "./label"}},
        {"id": "cell", "component": "Text", "text": {"path": "."}},
    ]
    card = validate_card(raw, CARD_VOCABULARY)
    (tmp_path / "shelf.card.yaml").write_text(yaml.safe_dump(raw, sort_keys=False))
    files = load_cards(tmp_path, CARD_VOCABULARY)
    assert files[card.name] == card
    messages = [
        {
            "updateComponents": {
                "surfaceId": "whole",
                "components": [
                    {"id": "whole", "component": card.name, **card.example},
                ],
            }
        }
    ]
    assert expand_card_messages(messages, files) == expand_card_messages(
        messages, {card.name: card}
    )
    nested = [
        {
            "updateComponents": {
                "surfaceId": "nested",
                "components": [
                    {"id": "root", "component": "Column", "children": ["inside"]},
                    {"id": "inside", "component": card.name, **card.example},
                ],
            }
        }
    ]
    assert expand_card_messages(nested, files) == expand_card_messages(nested, {card.name: card})
