"""Sizing and responsive Grid contracts survive Screen expansion."""

import json

import pytest
from fastapi.testclient import TestClient

from assistant.gateway.app import create_app
from assistant.profiles import ProfileRegistry
from assistant.screens import decode_screen
from tests.support.apps import api, make_manager
from tests.test_gateway_screens import instance, upload


def test_grid_screen_preserves_equal_columns_and_instance_sizing(paths):
    pid = ProfileRegistry(paths).create_profile("Layout", "#109e91").id
    card = instance()
    card["message"]["component"] = {
        "id": "root",
        "component": "Card",
        "width": "content",
        "child": "text",
        "_components": [
            {"id": "root", "component": "Card", "width": "content", "child": "text"},
            {"id": "text", "component": "Text", "text": {"path": "/summary"}},
        ],
    }
    card["message"]["data"].pop("_sources")
    document = {
        "kind": "screen",
        "format_version": 1,
        "title": "Equal halves",
        "layout": [
            {
                "id": "root",
                "component": "Grid",
                "columns": 2,
                "minColumnWidth": "sm",
                "gap": "sm",
                "width": "fill",
                "children": ["calendar"],
            },
            {"id": "calendar", "component": "CardInstance", "path": "calendar.card-instance.yaml"},
        ],
    }
    for _ in range(2):
        with TestClient(create_app(make_manager(paths, persist=True))) as client:
            if _ == 0:
                upload(client, pid, "calendar.card-instance.yaml", card)
                upload(client, pid, "layout.screen.yaml", document)
            response = client.get(api(pid, "/screens/view"), params={"path": "layout.screen.yaml"})
            assert response.status_code == 200, response.text
            nodes = response.json()["message"]["component"]["_components"]
            assert nodes[0] == document["layout"][0]
            frame = next(node for node in nodes if node["id"] == "calendar")
            assert frame["component"] == "Card" and frame["width"] == "content"


@pytest.mark.parametrize(
    "properties",
    [
        {"columns": 0},
        {"columns": 7},
        {"columns": 1.5},
        {"columns": "2"},
        {"columns": 2, "width": "50%"},
        {"columns": 2, "minColumnWidth": "100px"},
    ],
)
def test_grid_rejects_unrenderable_sizing(properties):
    with pytest.raises(ValueError):
        decode_screen(
            json.dumps(
                {
                    "kind": "screen",
                    "format_version": 1,
                    "title": "Bad",
                    "layout": [{"id": "root", "component": "Grid", "children": [], **properties}],
                }
            ).encode()
        )
