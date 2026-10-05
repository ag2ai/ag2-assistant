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


def test_nested_repeats_and_table_scopes_draw_identically_from_file_and_memory(tmp_path):
    raw = definition()
    raw["fields"]["rows"] = {"type": "array", "items": {"type": "object"}}
    raw["example"]["rows"] = [{"label": "A", "items": [{"label": "B"}]}]
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
            "columns": [{"label": "Label"}],
            "rows": {"path": "./items"},
            "cells": ["./label"],
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
