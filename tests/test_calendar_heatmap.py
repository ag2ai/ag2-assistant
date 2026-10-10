"""Activity calendar layouts validate and retain their bound entry contract."""

import pytest
from fastapi.testclient import TestClient

from assistant.a2ui import CARD_VOCABULARY
from assistant.cards import CardError, validate_card
from assistant.gateway.app import create_app
from assistant.profiles import ProfileRegistry
from tests.support.apps import api, make_manager
from tests.support.cards import card_definition
from tests.test_gateway_screens import instance, upload


def heatmap(**properties):
    return {
        "id": "root",
        "component": "CalendarHeatmap",
        "startDate": "2026-10-01",
        "endDate": "2026-10-31",
        "days": [],
        **properties,
    }


def test_heatmap_bound_dates_and_entries_survive_screen_restart(paths):
    pid = ProfileRegistry(paths).create_profile("Calendar", "#109e91").id
    card = instance()
    nodes = [
        {"id": "root", "component": "Card", "width": "content", "child": "calendar"},
        heatmap(
            id="calendar",
            startDate={"path": "/start"},
            endDate={"path": "/end"},
            days={"path": "/days"},
            cellSize="sm",
            locale="ru",
        ),
    ]
    card["message"]["component"] = {**nodes[0], "_components": nodes}
    card["message"]["data"] = {
        "start": "2026-10-01",
        "end": "2026-10-31",
        "days": [{"date": "2026-10-06", "count": 3}],
    }
    document = {
        "kind": "screen",
        "format_version": 1,
        "title": "Activity",
        "layout": [
            {"id": "root", "component": "Column", "children": ["calendar"]},
            {"id": "calendar", "component": "CardInstance", "path": "calendar.card-instance.yaml"},
        ],
    }
    for boot in range(2):
        with TestClient(create_app(make_manager(paths, persist=True))) as client:
            if boot == 0:
                upload(client, pid, "calendar.card-instance.yaml", card)
                upload(client, pid, "activity.screen.yaml", document)
            response = client.get(
                api(pid, "/screens/view"), params={"path": "activity.screen.yaml"}
            )
            assert response.status_code == 200, response.text
            message = response.json()["message"]
            calendar = next(
                node
                for node in message["component"]["_components"]
                if node["component"] == "CalendarHeatmap"
            )
            assert calendar["days"] == {"path": "/_cards/calendar/days"}
            assert calendar["startDate"] == {"path": "/_cards/calendar/start"}
            assert message["data"]["_cards"]["calendar"]["days"] == card["message"]["data"]["days"]


@pytest.mark.parametrize(
    "properties",
    [
        {"cellSize": "12px"},
        {"weekStartsOn": "tuesday"},
        {"width": "50%"},
        {"startDate": "2026-02-30"},
        {"endDate": "2026-09-30"},
        {"endDate": "2028-01-01"},
        {"days": [{"date": "2026-10-01", "count": -1}]},
        {"days": [{"date": "2026-10-01", "count": 0.5}]},
        {"days": [{"date": "2026-10-01", "status": "maybe"}]},
        {"days": [{"date": "2026-02-30"}]},
        {"days": [{"date": "2026-10-01"}, {"date": "2026-10-01"}]},
    ],
)
def test_heatmap_rejects_unrenderable_literal_contract(properties):
    definition = card_definition()
    definition["layout"] = [heatmap(**properties)]
    with pytest.raises(CardError):
        validate_card(definition, CARD_VOCABULARY)
