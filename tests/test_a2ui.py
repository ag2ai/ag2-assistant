from ag2.a2ui.constants import A2UI_JSON_CLOSE_TAG, A2UI_JSON_OPEN_TAG
from ag2.a2ui.parser import A2UIResponseParser

from assistant.a2ui import (
    CATALOG_ID,
    assistant_catalog,
    durable_surfaces_from_messages,
    runtime,
    wrap_bare_a2ui,
)


def test_assistant_catalog_declares_custom_components():
    catalog = assistant_catalog()

    assert catalog["$id"] == CATALOG_ID
    assert set(catalog["components"]) >= {
        "WeatherPanel",
        "NewsDigest",
        "RestaurantFinder",
        "TaskPlan",
        "Checklist",
        "AnswerBrief",
    }
    assert "LowdownPanel" not in catalog["components"]
    assert catalog["components"]["WeatherPanel"]["required"] == [
        "id",
        "component",
        "location",
        "condition",
        "rows",
    ]
    assert (
        "thunderstorm" in catalog["components"]["WeatherPanel"]["properties"]["condition"]["enum"]
    )
    story_schema = catalog["components"]["NewsDigest"]["properties"]["stories"]["items"]
    assert "summary" in story_schema["properties"]
    assert catalog["components"]["TaskPlan"]["required"] == [
        "id",
        "component",
        "objective",
        "cadence",
        "deliverables",
        "nextSteps",
    ]


def test_a2ui_runtime_prompt_exposes_schema_and_custom_contracts():
    runtime.cache_clear()
    rt = runtime()
    prompt = rt.system_prompt_section

    assert rt.catalog_id == CATALOG_ID
    assert "## A2UI Message Schema (v1.0)" in prompt
    assert "## Available Components" in prompt
    assert "**Custom components:**" in prompt
    assert "WeatherPanel" in prompt
    assert "LowdownPanel" not in prompt
    assert 'root component="Column"' in prompt
    assert "users do not need to ask for A2UI explicitly" in prompt
    assert "Prefer an A2UI component" in prompt
    assert "optional summary" in prompt
    assert "TaskPlan" in prompt
    # Intent → COMPONENT. Which tool gathers the data is the tool's own business, so no
    # tool name appears here (see tests/test_capability_registry.py). The imperative to
    # actually EMIT the component must survive: dropping it silently cost us the
    # MarketBoard, which the model replaced with prose.
    assert "EMIT that component" in prompt
    assert "Weather or forecast -> render a WeatherPanel" in prompt
    assert "market prices -> render a MarketBoard" in prompt
    assert "Gather the real data with your tools BEFORE you render" in prompt
    # The worked examples are English-only shape illustrations; without this directive
    # the model imitates their language and cards come out English-labelled even
    # mid-conversation in another language.
    assert "in the language of the conversation" in prompt
    assert "only to illustrate the SHAPE" in prompt
    assert "Creating, scheduling, or planning a new task -> render a TaskPlan" in prompt
    assert "task status/history) -> render a TaskProgress" in prompt
    assert "DecisionMatrix" in prompt
    assert "recommending between options -> render a DecisionMatrix" in prompt
    assert "Use Divider for section separation when useful" in prompt
    assert "A canvas is an A2UI surface, not a component" in prompt
    assert "place that exact value in an Image component's required `url`" in prompt
    assert '"component":"DateTimeInput"' in prompt
    assert "Do not call tools to discover A2UI components" in prompt
    assert 'Do not describe or print "corrected A2UI components"' in prompt
    assert (
        "never mention schemas, validation, properties, components, or corrected/updated UI"
        in prompt
    )
    assert '"createSurface"' in prompt
    assert '"updateComponents"' in prompt
    assert CATALOG_ID in prompt


def test_durable_surfaces_project_transient_a2ui_messages():
    surfaces = durable_surfaces_from_messages(
        [
            {
                "version": "v1.0",
                "createSurface": {"surfaceId": "s1", "catalogId": CATALOG_ID},
            },
            {
                "version": "v1.0",
                "updateComponents": {
                    "surfaceId": "s1",
                    "components": [
                        {
                            "id": "root",
                            "component": "WeatherPanel",
                            "location": "Sydney tomorrow",
                            "rows": [{"label": "Rain", "value": "Low"}],
                        }
                    ],
                },
            },
        ]
    )

    assert len(surfaces) == 1
    surface = surfaces[0]
    assert surface.surface_id == "s1"
    assert surface.catalog_id == CATALOG_ID
    assert surface.component["component"] == "WeatherPanel"
    assert surface.data == {
        "location": "Sydney tomorrow",
        "rows": [{"label": "Rain", "value": "Low"}],
    }


def test_durable_surfaces_skip_create_only_messages():
    assert (
        durable_surfaces_from_messages(
            [{"version": "v1.0", "createSurface": {"surfaceId": "s1", "catalogId": CATALOG_ID}}]
        )
        == []
    )


def test_durable_surfaces_preserve_composed_component_tree():
    surfaces = durable_surfaces_from_messages(
        [
            {"version": "v1.0", "createSurface": {"surfaceId": "s1", "catalogId": CATALOG_ID}},
            {
                "version": "v1.0",
                "updateComponents": {
                    "surfaceId": "s1",
                    "components": [
                        {"id": "root", "component": "Column", "children": ["weather", "news"]},
                        {
                            "id": "weather",
                            "component": "WeatherPanel",
                            "location": "Sydney",
                            "rows": [{"label": "Rain", "value": "Low"}],
                        },
                        {
                            "id": "news",
                            "component": "NewsDigest",
                            "topic": "Sydney",
                            "stories": [{"title": "Story", "meta": "Source"}],
                        },
                    ],
                },
            },
        ]
    )

    assert len(surfaces) == 1
    root = surfaces[0].component
    assert root["component"] == "Column"
    assert root["children"] == ["weather", "news"]
    assert [c["id"] for c in root["_components"]] == ["root", "weather", "news"]


# --- tolerant recovery of un-wrapped A2UI (models that omit <a2ui-json>) ---

_BARE_ARRAY = (
    '[{"version":"v1.0","createSurface":{"surfaceId":"s1","catalogId":"c"}},'
    '{"version":"v1.0","updateComponents":{"surfaceId":"s1","components":'
    '[{"id":"root","component":"MarketBoard","quotes":[{"symbol":"AAPL","price":313}]}]}}]'
)


def test_wrap_bare_a2ui_makes_untagged_array_parseable():

    text = "Here are the quotes. " + _BARE_ARRAY
    # Baseline: the raw response is NOT recognised by the parser (the live bug).
    assert A2UIResponseParser("v1.0").parse(text).has_a2ui is False

    wrapped = wrap_bare_a2ui(text)
    assert wrapped is not None
    assert A2UI_JSON_OPEN_TAG in wrapped and A2UI_JSON_CLOSE_TAG in wrapped

    result = A2UIResponseParser("v1.0").parse(wrapped)
    assert result.has_a2ui is True
    assert len(result.operations) == 2
    assert result.text.strip() == "Here are the quotes."  # prose preserved, JSON removed


def test_wrap_bare_a2ui_ignores_non_a2ui_and_prose():
    assert wrap_bare_a2ui("just prose, no json here") is None
    assert wrap_bare_a2ui("a plain list [1, 2, 3] is not A2UI") is None
    assert wrap_bare_a2ui("") is None
