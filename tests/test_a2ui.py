import json
import re
from pathlib import Path

import yaml
from ag2.a2ui.constants import A2UI_JSON_CLOSE_TAG, A2UI_JSON_OPEN_TAG
from ag2.a2ui.parser import A2UIResponseParser
from ag2.events import ModelMessage, ModelResponse

from assistant.a2ui import (
    CARD_VOCABULARY,
    CATALOG_ID,
    WEATHER_CONDITIONS,
    assistant_catalog,
    bundled_cards,
    catalog_rules,
    durable_surfaces_from_messages,
    expand_card_messages,
    expanded_card_surface,
    runtime,
    update_data_value,
    wrap_bare_a2ui,
)
from assistant.cards import bundled_cards_dir, expand_components, load_cards
from assistant.coding.diff import FileDiff
from assistant.coding.surface import card_fields
from assistant.events import A2UISurface
from assistant.tools.weather import condition_for


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
    assert "TaskPlan" in prompt
    # Intent → COMPONENT. Which tool gathers the data is the tool's own business, so no
    # tool name appears here (see tests/test_capability_registry.py). The imperative to
    # actually EMIT the component must survive: dropping it silently cost us the
    # MarketBoard, which the model replaced with prose.
    assert "EMIT that component" in prompt
    # The weather and the news are files now, each routed by its own description.
    assert "WeatherPanel — Use when the answer is the weather" in prompt
    assert "NewsDigest — Use when the answer is the latest news" in prompt
    # MarketBoard is a file now: its own description is what routes the model to it.
    assert "MarketBoard — Use when the answer is market prices" in prompt
    assert "Gather the real data with your tools BEFORE you render" in prompt
    assert "TaskPlan — Use when a task is being created" in prompt
    # The task board, the inbox and the agenda are files now, each routed by its own
    # description rather than by a bullet here.
    assert "TaskProgress — Use when the answer is the state of the user's existing tasks" in prompt
    # The comparison table is a file now, routed by its own description too.
    assert "DecisionMatrix — Use when the answer compares concrete alternatives" in prompt
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


def test_data_model_update_writes_into_one_repeated_row():
    """A control in a repeated row writes to its own item and leaves the list a list."""
    data = {"runs": [{"day": "Mon", "done": False}, {"day": "Wed", "done": False}]}

    updated = update_data_value(data, "/runs/1/done", True)

    assert updated["runs"] == [{"day": "Mon", "done": False}, {"day": "Wed", "done": True}]
    assert data["runs"][1]["done"] is False


def test_durable_surface_keeps_a_layout_that_repeats_one_template():
    surfaces = durable_surfaces_from_messages(
        [
            {"version": "v1.0", "createSurface": {"surfaceId": "s1", "catalogId": CATALOG_ID}},
            {
                "version": "v1.0",
                "updateComponents": {
                    "surfaceId": "s1",
                    "components": [
                        {
                            "id": "root",
                            "component": "List",
                            "children": {"componentId": "run_row", "path": "/runs"},
                        },
                        {"id": "run_row", "component": "Text", "text": {"path": "./day"}},
                    ],
                },
            },
            {
                "version": "v1.0",
                "updateDataModel": {
                    "surfaceId": "s1",
                    "path": "/runs",
                    "value": [{"day": "Mon"}, {"day": "Wed"}],
                },
            },
        ]
    )

    assert len(surfaces) == 1
    assert surfaces[0].component["children"] == {"componentId": "run_row", "path": "/runs"}
    assert surfaces[0].data["runs"] == [{"day": "Mon"}, {"day": "Wed"}]


def test_data_model_update_ignores_a_pointer_no_row_answers_to():
    data = {"runs": [{"day": "Mon"}]}

    assert update_data_value(data, "/runs/7/day", "Sun") == data
    assert update_data_value(data, "/runs/day", "Sun") == data


# --- Checklist is a file (ADR 0027/0028): offered from it, drawn from it ---


def _emit(root: dict) -> list[dict]:
    """The two messages a model writes to draw one Card as the whole surface."""
    return [
        {"version": "v1.0", "createSurface": {"surfaceId": "s1", "catalogId": CATALOG_ID}},
        {
            "version": "v1.0",
            "updateComponents": {"surfaceId": "s1", "components": [root]},
        },
    ]


def _checklist_emit() -> list[dict]:
    return _emit(
        {
            "id": "root",
            "component": "Checklist",
            "title": "Ship it",
            "items": ["Tag the release", "Run the migration"],
        }
    )


def test_the_checklist_card_is_offered_to_the_agent_from_its_file():
    card = bundled_cards()["Checklist"]
    schema = assistant_catalog()["components"]["Checklist"]

    assert schema["description"] == card.description
    assert schema["required"] == ["id", "component", "title", "items"]
    assert set(schema["properties"]) == {"id", "component", "title", "items"}

    runtime.cache_clear()
    prompt = runtime().system_prompt_section
    assert card.description in prompt
    assert '"component":"Checklist","title":"Ship the release"' in prompt


def test_the_model_emits_only_the_cards_fields_and_the_file_supplies_the_layout():
    messages = expand_card_messages(_checklist_emit(), bundled_cards())

    drawn = messages[1]["updateComponents"]["components"]
    assert [component["component"] for component in drawn] == [
        "Card",
        "Column",
        "Text",
        "List",
        "Row",
        "Icon",
        "Text",
    ]
    assert [message["updateDataModel"]["path"] for message in messages[2:]] == ["/title", "/items"]


def test_a_replayed_checklist_renders_what_was_drawn():
    surfaces = durable_surfaces_from_messages(
        expand_card_messages(_checklist_emit(), bundled_cards())
    )

    assert len(surfaces) == 1
    surface = surfaces[0]
    assert surface.component["component"] == "Card"
    assert [c["id"] for c in surface.component["_components"]][:2] == ["root", "root__body"]
    assert surface.data["title"] == "Ship it"
    assert surface.data["items"] == ["Tag the release", "Run the migration"]
    assert surface.title == "Ship it"


def test_a_checklist_stored_before_it_was_a_file_is_redrawn_on_read():
    stored = A2UISurface(
        "s1",
        component={"id": "root", "component": "Checklist", "title": "Old list", "items": ["x"]},
        data={"title": "Old list", "items": ["x"]},
        title="Old list",
    )

    redrawn = expanded_card_surface(stored, bundled_cards())

    assert redrawn.component["component"] == "Card"
    assert redrawn.data == {"title": "Old list", "items": ["x"]}
    assert redrawn.title == "Old list"
    # A surface already drawn as primitives is left exactly as it is.
    assert expanded_card_surface(redrawn, bundled_cards()) is redrawn


def test_a_checklist_is_validated_against_the_schema_its_own_file_declares():
    runtime.cache_clear()
    parser = runtime().parser

    assert parser.validate(_checklist_emit()).is_valid

    missing = _checklist_emit()
    del missing[1]["updateComponents"]["components"][0]["items"]
    assert not parser.validate(missing).is_valid

    wrong_type = _checklist_emit()
    wrong_type[1]["updateComponents"]["components"][0]["items"] = "not a list"
    assert not parser.validate(wrong_type).is_valid


class _CollectingContext:
    """A turn context that records what the middleware publishes to the client."""

    def __init__(self) -> None:
        self.sent: list = []

    async def send(self, event) -> None:
        self.sent.append(event)


async def test_the_browser_is_asked_to_draw_primitives_not_a_card():
    runtime.cache_clear()
    reply = A2UI_JSON_OPEN_TAG + json.dumps(_checklist_emit()) + A2UI_JSON_CLOSE_TAG
    context = _CollectingContext()

    async def call_next(events, ctx):
        return ModelResponse(ModelMessage("Here is the plan. " + reply))

    middleware = runtime().middleware_factories()[0](None, context)
    response = await middleware.on_llm_call(call_next, [], context)

    published = [event.message for event in context.sent]
    drawn = published[1]["updateComponents"]["components"]
    assert all(component["component"] != "Checklist" for component in drawn)
    assert {message["updateDataModel"]["path"] for message in published[2:]} == {"/title", "/items"}
    assert response.content.strip() == "Here is the plan."


def test_a_card_that_is_not_there_is_not_offered_and_not_drawable():
    assert "Checklist" not in assistant_catalog({})["components"]
    assert "Checklist" not in catalog_rules({})
    unchanged = _checklist_emit()

    assert expand_card_messages(unchanged, {}) == unchanged


def _market_emit() -> list[dict]:
    return _emit(
        {
            "id": "root",
            "component": "MarketBoard",
            "title": "Technology",
            "currency": "USD",
            "quotes": [
                {
                    "symbol": "NVDA",
                    "name": "NVIDIA Corporation",
                    "price": 193.99,
                    "change": 1.46,
                    "changePercent": 0.76,
                    "spark": [12, 30, 22, 45, 100],
                },
                {
                    "symbol": "AAPL",
                    "name": "Apple Inc.",
                    "price": 281.51,
                    "changePercent": -0.8,
                },
            ],
        }
    )


def test_the_market_board_is_offered_to_the_agent_from_its_file():
    card = bundled_cards()["MarketBoard"]
    schema = assistant_catalog()["components"]["MarketBoard"]

    assert schema["description"] == card.description
    assert schema["required"] == ["id", "component", "title", "quotes"]
    assert set(schema["properties"]) >= {"id", "component", "title", "quotes"}

    runtime.cache_clear()
    prompt = runtime().system_prompt_section
    assert card.description in prompt
    assert '"component":"MarketBoard"' in prompt


def test_the_market_board_is_drawn_from_the_vocabulary_not_from_a_component():
    messages = expand_card_messages(_market_emit(), bundled_cards())

    drawn = messages[1]["updateComponents"]["components"]
    kinds = {component["component"] for component in drawn}
    assert "MarketBoard" not in kinds
    assert {"Sparkline", "Metric"} <= kinds
    assert [message["updateDataModel"]["path"] for message in messages[2:]] == [
        "/title",
        "/currency",
        "/quotes",
    ]


def test_a_market_board_stored_before_it_was_a_file_is_redrawn_on_read():
    stored = A2UISurface(
        "s1",
        component={
            "id": "root",
            "component": "MarketBoard",
            "title": "Old board",
            "quotes": [{"symbol": "AAPL", "name": "Apple Inc.", "price": 1.0, "changePercent": 2}],
        },
        data={
            "title": "Old board",
            "quotes": [{"symbol": "AAPL", "name": "Apple Inc.", "price": 1.0, "changePercent": 2}],
        },
        title="Old board",
    )

    redrawn = expanded_card_surface(stored, bundled_cards())

    assert redrawn.component["component"] == "Card"
    assert redrawn.data["quotes"][0]["symbol"] == "AAPL"
    assert redrawn.title == "Old board"


def test_a_board_of_two_quotes_and_a_board_of_twenty_are_the_same_layout():
    def drawn(count: int) -> list:
        emit = _market_emit()
        quote = emit[1]["updateComponents"]["components"][0]["quotes"][1]
        emit[1]["updateComponents"]["components"][0]["quotes"] = [
            {**quote, "symbol": f"S{index}"} for index in range(count)
        ]
        return expand_card_messages(emit, bundled_cards())[1]["updateComponents"]["components"]

    assert drawn(2) == drawn(20)


# --- The Cards whose look was a branch in the surface renderer (06) ---


def _task_plan_emit() -> list[dict]:
    return _emit(
        {
            "id": "root",
            "component": "TaskPlan",
            "objective": "Brief me on AI news every morning",
            "cadence": "Daily at 07:00",
            "deliverables": ["A five-headline digest"],
            "nextSteps": ["Confirm the time", "Pick the sources"],
        }
    )


def _places_emit(results: list[dict] | None = None) -> list[dict]:
    return _emit(
        {
            "id": "root",
            "component": "RestaurantFinder",
            "query": "Ramen near Neubau",
            "filters": ["Open now", "Walkable"],
            "results": [{"name": "Mochi", "detail": "Japanese \u00b7 4.6 \u00b7 5 min walk"}]
            if results is None
            else results,
        }
    )


def _brief_emit(sections: list[str] | None = None) -> list[dict]:
    return _emit(
        {
            "id": "root",
            "component": "AnswerBrief",
            "topic": "Rust vs Go for a CLI",
            "sections": ["Startup time", "Binary size"] if sections is None else sections,
        }
    )


def test_the_task_plan_places_and_brief_are_offered_from_their_files():
    runtime.cache_clear()
    prompt = runtime().system_prompt_section
    catalog = assistant_catalog()

    for name in ("TaskPlan", "RestaurantFinder", "AnswerBrief"):
        card = bundled_cards()[name]
        assert catalog["components"][name]["description"] == card.description
        assert catalog["components"][name]["required"] == ["id", "component", *card.required]
        assert card.description in prompt
        assert f'"component":"{name}"' in prompt


def test_the_task_plan_places_and_brief_are_drawn_from_the_vocabulary():
    for emit, fields in (
        (_task_plan_emit(), ["/objective", "/cadence", "/deliverables", "/nextSteps"]),
        (_places_emit(), ["/query", "/filters", "/results"]),
        (_brief_emit(), ["/topic", "/sections"]),
    ):
        messages = expand_card_messages(emit, bundled_cards())
        drawn = messages[1]["updateComponents"]["components"]

        assert {component["component"] for component in drawn} <= CARD_VOCABULARY
        assert [message["updateDataModel"]["path"] for message in messages[2:]] == fields


def test_a_task_plan_stored_before_it_was_a_file_is_redrawn_on_read():
    stored = A2UISurface(
        "s1",
        component={
            "id": "root",
            "component": "TaskPlan",
            "objective": "Old plan",
            "cadence": "Weekly",
            "deliverables": ["A digest"],
            "nextSteps": ["Confirm"],
        },
        data={
            "objective": "Old plan",
            "cadence": "Weekly",
            "deliverables": ["A digest"],
            "nextSteps": ["Confirm"],
        },
        title="Task setup",
    )

    redrawn = expanded_card_surface(stored, bundled_cards())

    assert redrawn.component["component"] == "Column"
    assert redrawn.data["objective"] == "Old plan"
    assert redrawn.title == "Task setup"


def test_one_place_and_many_places_are_the_same_layout():
    def drawn(count: int) -> list:
        results = [{"name": f"Place {i}", "detail": "Open now"} for i in range(count)]
        return expand_card_messages(_places_emit(results), bundled_cards())[1]["updateComponents"][
            "components"
        ]

    assert drawn(1) == drawn(12)


def test_a_brief_with_no_sections_draws_the_same_card_as_a_full_one():
    empty = expand_card_messages(_brief_emit([]), bundled_cards())
    full = expand_card_messages(_brief_emit(), bundled_cards())

    assert empty[1]["updateComponents"]["components"] == full[1]["updateComponents"]["components"]
    # The section pills are conditional on the data, so an empty brief is a topic
    # with nothing standing under it.
    sections = next(
        component
        for component in empty[1]["updateComponents"]["components"]
        if component["id"] == "root__sections"
    )
    assert sections["when"] == {"path": "/sections"}


def test_the_task_plan_places_and_brief_are_no_longer_catalog_literals():
    bare = assistant_catalog({})

    for name in ("TaskPlan", "RestaurantFinder", "AnswerBrief"):
        assert name not in bare["components"]
    rules = catalog_rules({})
    assert "render a TaskPlan" not in rules
    assert "render a RestaurantFinder" not in rules
    assert "render an AnswerBrief" not in rules


# --- The Cards that link to the app's own things (07) ---


def _task_board_emit(tasks: list[dict] | None = None) -> list[dict]:
    return _emit(
        {
            "id": "root",
            "component": "TaskProgress",
            "title": "Your scheduled tasks",
            "tasks": [
                {
                    "id": "t_a1b2c3",
                    "title": "Daily AI news briefing",
                    "status": "active",
                    "schedule": "daily 07:00",
                    "deliverables": [{"description": "Morning digest", "status": "done"}],
                },
                {"id": "t_d4e5f6", "title": "Weekly scan", "status": "failed", "error": "Quota"},
            ]
            if tasks is None
            else tasks,
        }
    )


def _inbox_emit() -> list[dict]:
    return _emit(
        {
            "id": "root",
            "component": "InboxBrief",
            "title": "Inbox this morning",
            "summary": "3 new since yesterday.",
            "threads": [
                {
                    "from": "Priya Nair",
                    "subject": "Q3 roadmap review",
                    "unread": True,
                    "needsReply": True,
                    "url": "https://mail.google.com/mail/u/0/#all/19",
                }
            ],
        }
    )


def _agenda_emit(events: list[dict] | None = None) -> list[dict]:
    return _emit(
        {
            "id": "root",
            "component": "AgendaCard",
            "title": "Today",
            "date": "Tue 8 July",
            "events": [
                {"title": "Home", "allDay": True},
                {
                    "title": "Sync on Merlin EKS",
                    "start": "8:15 AM",
                    "next": True,
                    "url": "https://www.google.com/calendar/event?eid=abc",
                },
            ]
            if events is None
            else events,
            "note": "Free after 2:30 PM.",
        }
    )


def test_the_board_the_inbox_and_the_agenda_are_offered_from_their_files():
    runtime.cache_clear()
    prompt = runtime().system_prompt_section
    catalog = assistant_catalog()

    for name in ("TaskProgress", "InboxBrief", "AgendaCard"):
        card = bundled_cards()[name]
        assert catalog["components"][name]["description"] == card.description
        assert catalog["components"][name]["required"] == ["id", "component", *card.required]
        assert card.description in prompt
        assert f'"component":"{name}"' in prompt


def test_the_board_the_inbox_and_the_agenda_are_drawn_from_the_vocabulary():
    for emit, fields in (
        (_task_board_emit(), ["/title", "/tasks"]),
        (_inbox_emit(), ["/title", "/summary", "/threads"]),
        (_agenda_emit(), ["/title", "/date", "/events", "/note"]),
    ):
        messages = expand_card_messages(emit, bundled_cards())
        drawn = messages[1]["updateComponents"]["components"]

        assert {component["component"] for component in drawn} <= CARD_VOCABULARY
        assert [message["updateDataModel"]["path"] for message in messages[2:]] == fields


def test_a_row_points_at_the_task_it_describes():
    drawn = expand_card_messages(_task_board_emit(), bundled_cards())[1]["updateComponents"][
        "components"
    ]

    link = next(component for component in drawn if component["component"] == "Link")
    # The id is read off the row the link is drawn in, so one written row serves
    # every task on the board.
    assert link["task"] == {"path": "./id"}


def test_a_card_links_to_a_mail_thread_and_a_meeting_by_its_url():
    inbox = expand_card_messages(_inbox_emit(), bundled_cards())[1]["updateComponents"][
        "components"
    ]
    agenda = expand_card_messages(_agenda_emit(), bundled_cards())[1]["updateComponents"][
        "components"
    ]

    assert {"path": "./url"} in [component.get("url") for component in inbox]
    assert {"path": "./url"} in [component.get("url") for component in agenda]
    # A meeting link stands only over an event that has one; the subject line is
    # drawn whether or not the mail carried a URL.
    join = next(component for component in agenda if component.get("url") == {"path": "./joinUrl"})
    assert join["when"] == {"path": "./joinUrl"}


def test_a_task_board_stored_before_it_was_a_file_is_redrawn_on_read():
    stored = A2UISurface(
        "s1",
        component={
            "id": "root",
            "component": "TaskProgress",
            "title": "Old board",
            "tasks": [{"id": "t_1", "title": "A task", "status": "active"}],
        },
        data={
            "title": "Old board",
            "tasks": [{"id": "t_1", "title": "A task", "status": "active"}],
        },
        title="Task status",
    )

    redrawn = expanded_card_surface(stored, bundled_cards())

    assert redrawn.component["component"] == "Card"
    assert redrawn.data["tasks"][0]["id"] == "t_1"
    assert redrawn.title == "Task status"


def test_a_day_of_one_event_and_a_day_of_twenty_are_the_same_layout():
    def drawn(count: int) -> list:
        events = [{"title": f"Event {index}", "start": "9:00 AM"} for index in range(count)]
        return expand_card_messages(_agenda_emit(events), bundled_cards())[1]["updateComponents"][
            "components"
        ]

    assert drawn(1) == drawn(20)


def test_a_status_mark_takes_its_colour_from_the_field_it_prints():
    drawn = expand_card_messages(_task_board_emit(), bundled_cards())[1]["updateComponents"][
        "components"
    ]

    badge = next(component for component in drawn if component.get("variant") == "badge")
    # The Card names tones, never colours: the status field picks which one.
    assert badge["tone"]["path"] == "./status"
    assert set(badge["tone"]["map"].values()) <= {
        "neutral",
        "muted",
        "accent",
        "positive",
        "negative",
    }


def test_the_board_the_inbox_and_the_agenda_are_no_longer_catalog_literals():
    bare = assistant_catalog({})

    for name in ("TaskProgress", "InboxBrief", "AgendaCard"):
        assert name not in bare["components"]
    rules = catalog_rules({})
    assert "render a TaskProgress" not in rules
    assert "render an InboxBrief" not in rules
    assert "render an AgendaCard" not in rules


# --- The Cards with the bespoke artwork (08) ---


def _weather_emit(rows: list[dict] | None = None) -> list[dict]:
    return _emit(
        {
            "id": "root",
            "component": "WeatherPanel",
            "location": "Vienna, Austria",
            "condition": "sunny",
            "temperature": "24°",
            "summary": "Clear and mild all day.",
            "rows": [
                {"label": "Temperature", "value": "24°C (feels 22°C)"},
                {"label": "Wind", "value": "12 km/h NW"},
            ]
            if rows is None
            else rows,
        }
    )


def _news_emit(stories: list[dict] | None = None) -> list[dict]:
    return _emit(
        {
            "id": "root",
            "component": "NewsDigest",
            "topic": "Formula 1",
            "stories": [
                {
                    "title": "Lead headline",
                    "source": "Reuters",
                    "published": "2h ago",
                    "category": "Breaking",
                    "summary": "One or two sentences of detail.",
                    "why": "Why this is the most important story right now.",
                    "image": "https://example.com/lead.jpg",
                    "url": "https://www.reuters.com/sport/formula1/the-article",
                },
                {"title": "Second headline", "source": "BBC Sport", "published": "4h ago"},
            ]
            if stories is None
            else stories,
        }
    )


def test_the_weather_and_the_news_are_offered_from_their_files():
    runtime.cache_clear()
    prompt = runtime().system_prompt_section
    catalog = assistant_catalog()

    for name in ("WeatherPanel", "NewsDigest"):
        card = bundled_cards()[name]
        assert catalog["components"][name]["description"] == card.description
        assert catalog["components"][name]["required"] == ["id", "component", *card.required]
        assert card.description in prompt
        assert f'"component":"{name}"' in prompt


def test_the_weather_and_the_news_are_drawn_from_the_vocabulary():
    for emit, fields in (
        (_weather_emit(), ["/location", "/condition", "/temperature", "/summary", "/rows"]),
        (_news_emit(), ["/topic", "/stories"]),
    ):
        messages = expand_card_messages(emit, bundled_cards())
        drawn = messages[1]["updateComponents"]["components"]

        assert {component["component"] for component in drawn} <= CARD_VOCABULARY
        assert [message["updateDataModel"]["path"] for message in messages[2:]] == fields


def test_the_weather_glyph_is_a_primitive_any_card_can_draw():
    drawn = expand_card_messages(_weather_emit(), bundled_cards())[1]["updateComponents"][
        "components"
    ]

    glyph = next(component for component in drawn if component["component"] == "WeatherGlyph")
    # The Card names no artwork: it binds the condition and the primitive draws it.
    assert glyph["condition"] == {"path": "/condition"}
    assert "WeatherGlyph" in CARD_VOCABULARY


def test_the_condition_vocabulary_belongs_to_the_glyph_not_to_the_card(tmp_path):
    # The bundled Card mirrors the glyph's vocabulary so a model typo is caught.
    assert bundled_cards()["WeatherPanel"].fields["condition"]["enum"] == WEATHER_CONDITIONS

    # A profile's own copy names whatever it likes — and the tool, which reads the
    # primitive's vocabulary, still maps into the eight the glyph can draw.
    mine = yaml.safe_load((bundled_cards_dir() / "weatherpanel.card.yaml").read_text())
    mine["fields"]["condition"]["enum"] = ["hail"]
    (tmp_path / "weatherpanel.card.yaml").write_text(yaml.safe_dump(mine))
    assert load_cards(tmp_path)["WeatherPanel"].fields["condition"]["enum"] == ["hail"]

    assert condition_for(113) in WEATHER_CONDITIONS
    assert condition_for("nonsense") in WEATHER_CONDITIONS


def test_the_two_renderers_draw_the_same_eight_conditions():
    # The comment on each list promises the other mirrors it; this is the promise.
    source = (Path(__file__).parents[1] / "web/src/lib/weather/conditions.ts").read_text()
    declared = source.split("export const WEATHER_CONDITIONS = [", 1)[1].split("]", 1)[0]

    assert re.findall(r"'([a-z-]+)'", declared) == WEATHER_CONDITIONS


def test_the_news_lead_keeps_its_media_and_the_rest_keep_their_rank():
    drawn = expand_card_messages(_news_emit(), bundled_cards())[1]["updateComponents"]["components"]

    figure = next(component for component in drawn if component["component"] == "Figure")
    assert figure["url"] == {"path": "/stories/0/image"}
    # The lead is drawn on its own, so the ranked list starts at the second story.
    ranked = next(
        component
        for component in drawn
        if component["component"] == "List" and component.get("variant") == "ranked"
    )
    assert ranked["children"] == {
        # The instance namespaces the layout id it repeats.
        "componentId": "root__story",
        "path": "/stories",
        "start": 1,
    }


def test_a_weather_panel_stored_before_it_was_a_file_is_redrawn_on_read():
    stored = A2UISurface(
        "s1",
        component={
            "id": "root",
            "component": "WeatherPanel",
            "location": "Sydney",
            "condition": "rainy",
            "rows": [{"label": "Rain", "value": "Low"}],
        },
        data={
            "location": "Sydney",
            "condition": "rainy",
            "rows": [{"label": "Rain", "value": "Low"}],
        },
        title="Weather view",
    )

    redrawn = expanded_card_surface(stored, bundled_cards())

    assert redrawn.component["component"] == "Card"
    assert redrawn.data["condition"] == "rainy"
    assert redrawn.title == "Weather view"


def test_a_digest_of_one_story_and_a_digest_of_twenty_are_the_same_layout():
    def drawn(count: int) -> list:
        stories = [{"title": f"Story {index}", "source": "Reuters"} for index in range(count)]
        return expand_card_messages(_news_emit(stories), bundled_cards())[1]["updateComponents"][
            "components"
        ]

    assert drawn(1) == drawn(20)


def test_the_weather_and_the_news_are_no_longer_catalog_literals():
    bare = assistant_catalog({})

    for name in ("WeatherPanel", "NewsDigest"):
        assert name not in bare["components"]
    rules = catalog_rules({})
    assert "render a WeatherPanel" not in rules
    assert "render a NewsDigest" not in rules


def test_a_card_nested_in_a_layout_draws_what_it_draws_on_its_own():
    weather = _weather_emit()[1]["updateComponents"]["components"][0]
    news = _news_emit()[1]["updateComponents"]["components"][0]
    alone = {
        name: [
            component["component"]
            for component in expand_card_messages(_emit(card), bundled_cards())[1][
                "updateComponents"
            ]["components"]
        ]
        for name, card in (("WeatherPanel", weather), ("NewsDigest", news))
    }

    composed = expand_card_messages(
        [
            {"version": "v1.0", "createSurface": {"surfaceId": "s1", "catalogId": CATALOG_ID}},
            {
                "version": "v1.0",
                "updateComponents": {
                    "surfaceId": "s1",
                    "components": [
                        {"id": "root", "component": "Column", "children": ["wx", "wire"]},
                        {**weather, "id": "wx"},
                        {**news, "id": "wire"},
                    ],
                },
            },
        ],
        bundled_cards(),
    )
    drawn = [
        component["component"]
        for component in composed[1]["updateComponents"]["components"]
        if component["id"] != "root"
    ]

    # One layout each, whether the Card is the surface or a block inside one.
    assert drawn == alone["WeatherPanel"] + alone["NewsDigest"]
    # Nested, each instance's fields land under its own id rather than at the root.
    assert [message["updateDataModel"]["path"] for message in composed[2:]][0].startswith(
        "/_cards/wx/"
    )


def test_a_digest_stored_before_the_byline_was_split_still_reads():
    stored = A2UISurface(
        "s1",
        component={
            "id": "root",
            "component": "NewsDigest",
            "topic": "Tech",
            "stories": [{"title": "Old headline", "meta": "Reuters · 2h ago"}],
        },
        data={"topic": "Tech", "stories": [{"title": "Old headline", "meta": "Reuters · 2h ago"}]},
        title="News digest",
    )

    redrawn = expanded_card_surface(stored, bundled_cards())
    drawn = redrawn.component["_components"]

    # The whole byline lived in one field before source and published were split.
    assert {"path": "/stories/0/meta"} in [component.get("text") for component in drawn]
    assert redrawn.data["stories"][0]["meta"] == "Reuters · 2h ago"


# --- The comparison table (09) ---


def _decision_emit(
    options: list[dict] | None = None, criteria: list[dict] | None = None
) -> list[dict]:
    return _emit(
        {
            "id": "root",
            "component": "DecisionMatrix",
            "topic": "Travel laptop",
            "options": [{"name": "MacBook Air 13"}, {"name": "ThinkPad X1 Carbon"}]
            if options is None
            else options,
            "criteria": [
                {"label": "Weight", "values": ["1.24 kg", "1.09 kg"], "best": "ThinkPad X1 Carbon"},
                {"label": "Keyboard", "values": ["Good", "Excellent"]},
            ]
            if criteria is None
            else criteria,
        }
    )


def _decision_drawn(
    options: list[dict] | None = None, criteria: list[dict] | None = None
) -> list[dict]:
    return expand_card_messages(_decision_emit(options, criteria), bundled_cards())[1][
        "updateComponents"
    ]["components"]


def test_the_decision_matrix_is_offered_to_the_agent_from_its_file():
    runtime.cache_clear()
    prompt = runtime().system_prompt_section
    card = bundled_cards()["DecisionMatrix"]

    assert assistant_catalog()["components"]["DecisionMatrix"]["description"] == card.description
    assert "DecisionMatrix — Use when the answer compares" in prompt
    assert '"component":"DecisionMatrix"' in prompt


def test_the_decision_matrix_is_drawn_from_the_vocabulary_not_from_a_component():
    messages = expand_card_messages(_decision_emit(), bundled_cards())
    drawn = messages[1]["updateComponents"]["components"]

    assert {component["component"] for component in drawn} <= CARD_VOCABULARY
    assert [message["updateDataModel"]["path"] for message in messages[2:]] == [
        "/topic",
        "/options",
        "/criteria",
    ]


def test_the_table_is_a_primitive_any_card_can_draw():
    drawn = _decision_drawn()

    table = next(component for component in drawn if component["component"] == "Table")
    # The Card names its two axes and the cells a row carries; nothing else aligns them.
    assert table["columns"] == {"path": "/options"}
    assert table["rows"] == {"path": "/criteria"}
    assert table["cells"] == {"path": "./values"}
    # A row names its winner, and a column says what it is called, so the table can
    # mark the cell where the two meet.
    assert table["key"] == {"path": "./name"}
    assert table["win"] == {"path": "./best"}
    assert table["pick"] == {"path": "/recommended"}
    # Its three templates are layout ids, namespaced by the instance like any child.
    assert {table["header"], table["lead"], table["cell"]} <= {
        component["id"] for component in drawn
    }
    assert "Table" in CARD_VOCABULARY


def test_a_table_of_two_options_and_a_table_of_four_are_the_same_layout():
    def drawn(count: int) -> list:
        return _decision_drawn(
            [{"name": f"Option {index}"} for index in range(count)],
            [{"label": "Price", "values": [f"${index}" for index in range(count)]}],
        )

    assert drawn(2) == drawn(3) == drawn(4)


def test_the_recommendation_and_the_verdict_are_drawn_only_when_they_are_there():
    drawn = _decision_drawn()

    verdict = next(component for component in drawn if component["id"].endswith("verdict_block"))
    assert verdict["when"] == [{"path": "/recommended"}, {"path": "/verdict"}]


def test_a_nested_table_reads_the_instances_own_options():
    drawn = expand_card_messages(
        [
            {"version": "v1.0", "createSurface": {"surfaceId": "s1", "catalogId": CATALOG_ID}},
            {
                "version": "v1.0",
                "updateComponents": {
                    "surfaceId": "s1",
                    "components": [
                        {"id": "root", "component": "Column", "children": ["pick"]},
                        {**_decision_emit()[1]["updateComponents"]["components"][0], "id": "pick"},
                    ],
                },
            },
        ],
        bundled_cards(),
    )[1]["updateComponents"]["components"]

    table = next(component for component in drawn if component["component"] == "Table")
    assert table["columns"] == {"path": "/_cards/pick/options"}
    assert table["pick"] == {"path": "/_cards/pick/recommended"}
    # A cell's path reads the row the table is drawing, wherever the instance sits.
    assert table["cells"] == {"path": "./values"}


def test_a_decision_matrix_stored_before_it_was_a_file_is_redrawn_on_read():
    fields = {
        "topic": "Travel laptop",
        "options": [{"name": "MacBook Air 13", "price": "$1,499"}],
        "criteria": [{"label": "Weight", "values": ["1.24 kg"], "best": "MacBook Air 13"}],
        "recommended": "MacBook Air 13",
        "verdict": "The Air wins on battery.",
    }
    stored = A2UISurface(
        "s1",
        component={"id": "root", "component": "DecisionMatrix", **fields},
        data=dict(fields),
        title="Decision",
    )

    redrawn = expanded_card_surface(stored, bundled_cards())

    assert redrawn.component["component"] == "Card"
    assert redrawn.data["recommended"] == "MacBook Air 13"
    assert redrawn.title == "Decision"


def test_the_decision_matrix_is_no_longer_a_catalog_literal():
    assert "DecisionMatrix" not in assistant_catalog({})["components"]
    assert "render a DecisionMatrix" not in catalog_rules({})


# --- The coding session is a Card like any other (10) ---


def _coding_emit(**over) -> list[dict]:
    """The Card instance the coding tool fills, as it reaches the expansion path."""
    state = {
        "agent_label": "Claude Code",
        "directory": "/repo",
        "task": "Add a /health endpoint",
        "status": "done",
        "files": [FileDiff("app.py", "modified", "@@ -1 +1 @@\n-a\n+b\n", 1, 1)],
        **over,
    }
    return _emit({"id": "root", "component": "CodingSession", **card_fields(**state)})


def _coding_drawn(**over) -> list[dict]:
    return expand_card_messages(_coding_emit(**over), bundled_cards())[1]["updateComponents"][
        "components"
    ]


def test_the_coding_session_is_a_card_in_the_catalog():
    runtime.cache_clear()
    card = bundled_cards()["CodingSession"]

    assert assistant_catalog()["components"]["CodingSession"]["description"] == card.description
    assert "CodingSession — Use when the answer is a coding agent's run" in (
        runtime().system_prompt_section
    )


def test_the_coding_session_is_drawn_from_the_vocabulary_not_from_a_component():
    drawn = _coding_drawn()

    assert {component["component"] for component in drawn} <= CARD_VOCABULARY
    assert "CodingSession" not in {component["component"] for component in drawn}


def test_the_diff_is_a_primitive_any_card_can_draw():
    drawn = _coding_drawn()

    diff = next(component for component in drawn if component["component"] == "Diff")
    # The hunks are read from the file the repeated row is drawing, not from a path
    # the Card had to know in advance.
    assert diff["hunks"] == {"path": "./hunks"}
    assert "Diff" in CARD_VOCABULARY


def test_a_changed_file_and_the_folder_are_opened_through_the_link_primitive():
    drawn = _coding_drawn()
    links = [component for component in drawn if component["component"] == "Link"]

    assert {"path": "/directory"} in [link.get("folder") for link in links]
    assert {"path": "./full"} in [link.get("file") for link in links]


def test_a_run_of_one_file_and_a_run_of_thirty_are_the_same_layout():
    def drawn(count: int) -> list:
        return _coding_drawn(
            files=[FileDiff(f"f{index}.py", "added", "@@\n+x\n", 1, 0) for index in range(count)]
        )

    assert drawn(1) == drawn(30)


def test_a_coding_session_stored_before_it_was_a_file_is_redrawn_on_read():
    fields = {
        "agent": "Claude Code",
        "directory": "/repo",
        "task": "add hello",
        "status": "done",
        "plan": [{"content": "write hello", "status": "completed"}],
        "files": [{"path": "hello.py", "status": "added", "added": 1, "removed": 0, "hunks": ""}],
    }
    stored = A2UISurface(
        "cs1",
        component={"id": "root", "component": "CodingSession", **fields},
        data=dict(fields),
        title="Coding session",
    )

    redrawn = expanded_card_surface(stored, bundled_cards())

    assert redrawn.component["component"] == "Card"
    assert redrawn.data["status"] == "done"
    assert redrawn.title == "Coding session"


# --- The renderer forgets Card types (11) ---


def test_the_front_end_knows_no_card_by_name():
    # Every Card is a file the server draws into primitives, so no Card's name is on
    # the wire and the renderer has nothing to branch on.
    web = Path(__file__).parents[1] / "web/src"
    sources = [
        path for path in web.rglob("*") if path.is_file() and not path.name.endswith(".test.ts")
    ]
    named = {
        name: sorted(
            str(path.relative_to(web))
            for path in sources
            if re.search(rf"\b{name}\b", path.read_text(errors="ignore"))
        )
        for name in bundled_cards()
    }

    assert {name: hits for name, hits in named.items() if hits} == {}


def test_every_bundled_card_draws_only_primitives_whole_or_nested():
    # What reaches the browser is the vocabulary and nothing else — the property the
    # renderer's ignorance of Cards rests on.
    for name, card in bundled_cards().items():
        instance = {"id": "root", "component": name, **card.example}

        alone, _ = expand_components([instance], bundled_cards())
        nested, _ = expand_components(
            [
                {"id": "root", "component": "Column", "children": ["one"]},
                {**instance, "id": "one"},
            ],
            bundled_cards(),
        )

        for drawn in (alone, nested):
            kinds = {component["component"] for component in drawn}
            assert kinds <= CARD_VOCABULARY, (name, kinds - CARD_VOCABULARY)


def test_a_drawn_card_persists_its_fields_and_none_of_its_layout():
    # The data model is what the Card's author filled in. A layout property — the
    # primitive's variant, the id of its child — is structure and must not land there.
    cards = bundled_cards()
    for name, card in cards.items():
        drawn = expand_card_messages(
            _emit({"id": "root", "component": name, **card.example}), cards
        )
        data = durable_surfaces_from_messages(drawn)[0].data

        assert data == card.example, name


def test_a_plain_layout_root_contributes_no_data():
    surface = durable_surfaces_from_messages(
        _emit({"id": "root", "component": "Column", "children": ["one"]})
    )[0]

    assert surface.data == {}


def test_a_surface_is_titled_by_its_data_model_not_by_what_it_draws():
    titled = durable_surfaces_from_messages(
        _emit({"id": "root", "component": "Checklist", "title": "Ship it", "items": ["Tag it"]})
    )
    plain = durable_surfaces_from_messages(
        _emit({"id": "root", "component": "Column", "children": ["one"]})
    )

    assert titled[0].title == "Ship it"
    assert plain[0].title == "Interactive view"


def test_the_renderer_draws_every_primitive_a_card_may_name():
    # What a Card file is allowed to draw and what the browser can draw are one list.
    # A Card nobody has seen before renders because of this, not because of its name.
    source = (
        Path(__file__).parents[1] / "web/src/components/items/BasicA2UIComponent.svelte"
    ).read_text()
    drawn = set(re.findall(r"""type === ['"]([a-z]+)['"]""", source))

    assert drawn == {name.lower() for name in CARD_VOCABULARY}


def test_a_card_nobody_has_seen_before_is_drawn_from_its_layout(tmp_path):
    (tmp_path / "sighting.card.yaml").write_text(
        """
name: Sighting
description: A bird nobody has logged before.
fields:
  bird: {type: string}
  notes: {type: array, items: {type: string}}
required: [bird]
layout:
  - {id: root, component: Column, children: [name, notes]}
  - {id: name, component: Text, text: {path: /bird}, variant: h4}
  - {id: notes, component: List, children: {componentId: note, path: /notes}}
  - {id: note, component: Text, text: {path: .}}
example:
  bird: Superb fairywren
"""
    )
    cards = load_cards(tmp_path, components=CARD_VOCABULARY)

    drawn = expand_card_messages(
        _emit({"id": "root", "component": "Sighting", "bird": "Kea", "notes": ["Alpine"]}), cards
    )
    components = drawn[1]["updateComponents"]["components"]

    assert {component["component"] for component in components} <= CARD_VOCABULARY
    assert [message["updateDataModel"]["value"] for message in drawn[2:]] == ["Kea", ["Alpine"]]
