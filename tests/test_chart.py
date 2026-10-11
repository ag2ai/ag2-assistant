"""Chart contracts draw through Card authoring and survive retained Screen instances."""

import json
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from assistant.a2ui import CARD_VOCABULARY, CardCatalog
from assistant.card_author import CARD_AUTHOR, CardAuthorRuntime
from assistant.cards import CardError, validate_card
from assistant.events import EphemeralCard
from assistant.gateway.app import create_app
from assistant.profiles import ProfileRegistry
from tests.support.apps import api, make_manager, real_gateway
from tests.support.cards import author, card_definition, context
from tests.support.fakes import ScriptedModels
from tests.test_gateway_screens import instance, upload


def chart(**properties):
    return {
        "id": "root",
        "component": "Chart",
        "variant": "bar",
        "labels": ["01.08", "08.08", "15.08"],
        "series": [
            {"label": "Omitted", "values": [2, None, 0]},
            {"label": "Incorrect", "values": [1, None, 3]},
        ],
        **properties,
    }


def chart_definition():
    definition = card_definition("WeeklyErrors")
    definition["layout"] = [chart(stacked=True, annotations=[{"index": 1.5, "label": "Start"}])]
    return definition


async def test_chart_authoring_draws_data_in_the_event_stream_and_survives_restart(config):
    models = ScriptedModels(
        lambda cfg, model: [
            author("draft_card", {"definition": chart_definition(), "data": {"title": "Errors"}}),
            "done",
        ]
    )
    before = []
    for boot in range(2):
        gateway = real_gateway(config, memory=False, onboard=False, model_factory=models)
        await gateway.start()
        try:
            if boot == 0:
                await gateway.send_message("Draw weekly errors", chat_id="charts")
            events = await (await gateway.stream_for("charts")).history.get_events()
            drawings = [event for event in events if isinstance(event, EphemeralCard)]
            assert len(drawings) == 1
            drawing = drawings[0]
            assert drawing.component["component"] == "Chart"
            assert drawing.component["series"][0]["values"] == [2, None, 0]
            assert drawing.component["annotations"] == [{"index": 1.5, "label": "Start"}]
            if boot == 0:
                before = [drawing.to_dict()]
            else:
                assert [drawing.to_dict()] == before
        finally:
            await gateway.close()


def test_bound_chart_data_and_markers_survive_screen_restart(paths):
    pid = ProfileRegistry(paths).create_profile("Charts", "#109e91").id
    card = instance()
    node = chart(
        labels={"path": "/labels"},
        series={"path": "/series"},
        annotations={"path": "/annotations"},
        stacked=True,
        width="fill",
        locale="ru",
    )
    card["message"]["component"] = {**node, "_components": [node]}
    card["message"]["data"] = {
        "labels": ["01.08", "08.08", "15.08"],
        "series": chart()["series"],
        "annotations": [{"index": 1.5, "label": "Start"}],
    }
    document = {
        "kind": "screen",
        "format_version": 1,
        "title": "Weekly errors",
        "layout": [
            {"id": "root", "component": "CardInstance", "path": "errors.card-instance.yaml"}
        ],
    }
    for boot in range(2):
        with TestClient(create_app(make_manager(paths, persist=True))) as client:
            if boot == 0:
                upload(client, pid, "errors.card-instance.yaml", card)
                upload(client, pid, "errors.screen.yaml", document)
            response = client.get(api(pid, "/screens/view"), params={"path": "errors.screen.yaml"})
            assert response.status_code == 200, response.text
            message = response.json()["message"]
            drawn = next(
                node for node in message["component"]["_components"] if node["component"] == "Chart"
            )
            assert drawn["labels"] == {"path": "/_cards/root/labels"}
            assert drawn["series"] == {"path": "/_cards/root/series"}
            assert drawn["annotations"] == {"path": "/_cards/root/annotations"}
            assert message["data"]["_cards"]["root"] == card["message"]["data"]


async def test_chart_guide_is_discoverable_and_its_example_is_valid(config):
    runtime = CardAuthorRuntime(None, CardCatalog(config))
    assert "charts.md" in {resource.name for resource in runtime.skills[0].resources}
    guide = await runtime.read_resource(CARD_AUTHOR, "charts.md", context())
    example = json.loads(re.search(r"```json\n(.*?)\n```", guide, re.DOTALL)[1])
    card = validate_card(example, CARD_VOCABULARY)
    assert card.name == "WeeklyErrors"
    vocabulary = (Path(__file__).parents[1] / "src/assistant/cards/VOCABULARY.md").read_text()
    assert "`Chart`" in vocabulary


@pytest.mark.parametrize(
    "properties",
    [
        {"variant": "pie"},
        {"variant": "line", "stacked": True},
        {"labels": []},
        {"labels": ["A"] * 367},
        {"series": []},
        {"series": [{"label": "A", "values": [1, 2]}]},
        {"series": [{"label": "A", "values": [1, 2, "3"]}]},
        {"series": [{"label": "A", "values": [1, 2, 1e13]}]},
        {"series": [{"label": "A", "values": [1, 2, float("nan")]}]},
        {"series": [{"label": "A", "values": [1, 2, 3], "tone": "blue"}]},
        {"series": [{"label": "A", "values": [1, 2, 3], "color": "#123456"}]},
        {"annotations": [{"index": 3, "label": "Outside"}]},
        {"annotations": [{"index": -0.5, "label": "Outside"}]},
        {"size": "300px"},
        {"width": "50%"},
    ],
)
def test_chart_rejects_unrenderable_literal_contract(properties):
    definition = card_definition()
    definition["layout"] = [chart(**properties)]
    with pytest.raises(CardError):
        validate_card(definition, CARD_VOCABULARY)
