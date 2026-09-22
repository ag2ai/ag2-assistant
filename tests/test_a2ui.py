import json

from ag2.a2ui.constants import A2UI_JSON_CLOSE_TAG, A2UI_JSON_OPEN_TAG
from ag2.a2ui.parser import A2UIResponseParser
from ag2.events import ModelMessage, ModelResponse

from assistant.a2ui import (
    CARD_VOCABULARY,
    CATALOG_ID,
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
from assistant.events import A2UISurface


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
    # MarketBoard is a file now: its own description is what routes the model to it.
    assert "MarketBoard — Use when the answer is market prices" in prompt
    assert "Gather the real data with your tools BEFORE you render" in prompt
    assert "TaskPlan — Use when a task is being created" in prompt
    # The task board, the inbox and the agenda are files now, each routed by its own
    # description rather than by a bullet here.
    assert "TaskProgress — Use when the answer is the state of the user's existing tasks" in prompt
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
