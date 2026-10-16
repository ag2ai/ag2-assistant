"""Data-table authoring examples survive validation, storage and nested expansion."""

import json
from copy import deepcopy
from pathlib import Path

import pytest
import yaml

from assistant import card_author
from assistant.a2ui import CARD_VOCABULARY
from assistant.cards import CardError, expand_card_messages, load_cards, validate_card


def table_definition():
    reference = (Path(card_author.__file__).parent / "cards" / "TABLES.md").read_text()
    return json.loads(reference.split("```json\n", 1)[1].split("```", 1)[0])


def test_documented_table_round_trips_and_preserves_nested_template_scopes(tmp_path):
    raw = table_definition()
    card = validate_card(raw, CARD_VOCABULARY)
    (tmp_path / "metrics.card.yaml").write_text(yaml.safe_dump(raw))
    stored = load_cards(tmp_path, CARD_VOCABULARY)
    assert stored[card.name] == card
    messages = [
        {
            "updateComponents": {
                "surfaceId": "dashboard",
                "components": [
                    {"id": "root", "component": "Column", "children": ["metrics"]},
                    {"id": "metrics", "component": card.name, **card.example},
                ],
            }
        }
    ]
    expanded = expand_card_messages(deepcopy(messages), stored)
    in_memory = expand_card_messages(deepcopy(messages), {card.name: card})
    assert expanded[0] == in_memory[0]
    assert {
        row["updateDataModel"]["path"]: row["updateDataModel"]["value"] for row in expanded[1:]
    } == {row["updateDataModel"]["path"]: row["updateDataModel"]["value"] for row in in_memory[1:]}
    table = next(
        node
        for node in expanded[0]["updateComponents"]["components"]
        if node["component"] == "Table"
    )
    assert table["columns"] == {"path": "/_cards/metrics/columns"}
    assert table["rows"] == {"path": "/_cards/metrics/rows"}
    assert table["columnWidth"] == {"path": "./width"}
    assert table["columnAlign"] == {"path": "./align"}
    assert table["columnOverflow"] == {"path": "./overflow"}
    assert table["rowVariant"] == {"path": "./variant"}


@pytest.mark.parametrize(
    "property,value",
    [
        ("variant", "spreadsheet"),
        ("density", "tiny"),
        ("columnWidth", "500px"),
        ("columnAlign", "right"),
        ("columnOverflow", "hidden"),
        ("rowVariant", "red"),
    ],
)
def test_data_tables_reject_unsupported_styling(property, value):
    raw = table_definition()
    table = next(node for node in raw["layout"] if node["component"] == "Table")
    table[property] = value
    with pytest.raises(CardError, match="layout 'table'"):
        validate_card(raw, CARD_VOCABULARY)
