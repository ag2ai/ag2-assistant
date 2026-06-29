"""A2UI configuration for AG2 Assistant's chat/task surfaces."""

from functools import lru_cache
from typing import Any

from assistant.events import A2UISurface

CATALOG_ID = "https://ag2.ai/assistant/a2ui/catalog.json"

# The dominant weather conditions a WeatherPanel can declare. Single source of truth:
# the catalog schema's `condition` enum AND the get_weather tool's mapping both use this,
# so the tool can never emit a value the schema rejects. (Mirrored in the Svelte
# A2USurface `WEATHER_CONDITIONS` validator.)
WEATHER_CONDITIONS = [
    "sunny",
    "partly-cloudy",
    "cloudy",
    "foggy",
    "rainy",
    "thunderstorm",
    "snow",
    "windy",
]


def _message_dict(message: Any) -> dict:
    if hasattr(message, "to_dict"):
        return message.to_dict()
    if hasattr(message, "model_dump"):
        return message.model_dump(mode="json")
    return message if isinstance(message, dict) else {}


def _component_data(component: dict, existing: dict | None = None) -> dict:
    data = dict(existing or {})
    for key, value in component.items():
        if key not in {"id", "component", "type", "accessibility"}:
            data[key] = value
    return data


def _surface_title(component: dict, data: dict) -> str:
    kind = str(component.get("component") or component.get("type") or "").lower()
    if kind == "weatherpanel":
        return "Weather view"
    if kind == "newsdigest":
        return "News digest"
    if kind == "restaurantfinder":
        return "Open places"
    if kind == "taskplan":
        return "Task setup"
    if kind == "checklist":
        return data.get("title") or "Checklist"
    if kind in {"column", "row", "list", "card", "text"}:
        return "Briefing"
    return "Structured answer"


def durable_surfaces_from_messages(messages: list[Any]) -> list[A2UISurface]:
    """Project transient beta A2UI messages into durable app-level surface events.

    ``A2UIMessageEvent`` is intentionally transient in AG2 beta. The live event is
    still the right source of truth for validation; this projection stores the
    final surface state so chat URLs can replay rendered UI from history.
    """

    states: dict[str, dict] = {}
    order: list[str] = []
    for raw in messages:
        message = _message_dict(raw)
        if not message:
            continue
        version = message.get("version") or "v1.0"
        if create := message.get("createSurface"):
            surface_id = create.get("surfaceId")
            if not surface_id:
                continue
            if surface_id not in states:
                states[surface_id] = {
                    "surface_id": surface_id,
                    "catalog_id": create.get("catalogId") or CATALOG_ID,
                    "version": version,
                    "component": {},
                    "data": {},
                }
                order.append(surface_id)
            else:
                states[surface_id]["catalog_id"] = (
                    create.get("catalogId") or states[surface_id]["catalog_id"]
                )
                states[surface_id]["version"] = version
        elif update := message.get("updateComponents"):
            surface_id = update.get("surfaceId")
            if not surface_id:
                continue
            if surface_id not in states:
                states[surface_id] = {
                    "surface_id": surface_id,
                    "catalog_id": CATALOG_ID,
                    "version": version,
                    "component": {},
                    "data": {},
                }
                order.append(surface_id)
            components = update.get("components") or []
            root = next(
                (c for c in components if c.get("id") == "root"),
                components[0] if components else {},
            )
            if root:
                states[surface_id]["component"] = {**root, "_components": components}
                states[surface_id]["components"] = components
                states[surface_id]["data"] = _component_data(root, states[surface_id].get("data"))
                states[surface_id]["title"] = _surface_title(root, states[surface_id]["data"])
            states[surface_id]["version"] = version
        elif update := message.get("updateDataModel"):
            surface_id = update.get("surfaceId")
            if not surface_id:
                continue
            state = states.setdefault(
                surface_id,
                {
                    "surface_id": surface_id,
                    "catalog_id": CATALOG_ID,
                    "version": version,
                    "component": {},
                    "data": {},
                },
            )
            if surface_id not in order:
                order.append(surface_id)
            path = (update.get("path") or "/").lstrip("/")
            value = update.get("value")
            if not path:
                state["data"] = value if isinstance(value, dict) else {"value": value}
            else:
                state["data"][path] = value
        elif delete := message.get("deleteSurface"):
            surface_id = delete.get("surfaceId")
            if surface_id in states:
                del states[surface_id]
                order = [sid for sid in order if sid != surface_id]

    return [
        A2UISurface(
            state["surface_id"],
            catalog_id=state.get("catalog_id") or CATALOG_ID,
            version=state.get("version") or "v1.0",
            component=state.get("component") or {},
            data=state.get("data") or {},
            title=state.get("title") or "A2UI",
            intent="generated-ui",
        )
        for sid in order
        if (state := states.get(sid)) and state.get("component")
    ]


def _component_schema(
    name: str, description: str, properties: dict, required: list[str] | None = None
) -> dict:
    props = {
        "id": {"type": "string"},
        "component": {"const": name},
        **properties,
    }
    return {
        "type": "object",
        "description": description,
        "properties": props,
        "required": ["id", "component", *(required or [])],
        "additionalProperties": False,
    }


def assistant_catalog() -> dict:
    """Custom A2UI catalog rendered by the Svelte chat/task UI."""

    string_array = {"type": "array", "items": {"type": "string"}}
    row_array = {
        "type": "array",
        "items": {
            "type": "object",
            "properties": {
                "label": {"type": "string"},
                "value": {"type": "string"},
            },
            "required": ["label", "value"],
            "additionalProperties": False,
        },
    }
    story_array = {
        "type": "array",
        "items": {
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "source": {"type": "string", "description": "Publisher, e.g. 'Reuters'."},
                "published": {"type": "string", "description": "Recency, e.g. '2h ago' or a date."},
                "category": {
                    "type": "string",
                    "description": "Short tag, e.g. 'Breaking', 'Markets'.",
                },
                "summary": {"type": "string", "description": "One or two sentences of detail."},
                "why": {
                    "type": "string",
                    "description": "Why it matters — used for the lead story.",
                },
                "image": {
                    "type": "string",
                    "description": "Article image URL; omit if you don't have one.",
                },
                "url": {"type": "string"},
            },
            # First story is rendered as the lead; the rest as a ranked list.
            "required": ["title", "source"],
            "additionalProperties": False,
        },
    }
    result_array = {
        "type": "array",
        "items": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "detail": {"type": "string"},
                "url": {"type": "string"},
            },
            "required": ["name", "detail"],
            "additionalProperties": False,
        },
    }

    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": CATALOG_ID,
        "title": "AG2 Assistant Generative UI Catalog",
        "description": "Purpose-built A2UI components for AG2 Assistant chat and task answers.",
        "catalogId": CATALOG_ID,
        "instructions": (
            "Use one focused custom component as the root component when the answer "
            "benefits from structure: weather, latest news, restaurants, tasks, "
            "checklists, comparisons, or compact research briefs. For mixed answers, "
            "compose multiple components with the basic A2UI layout components."
        ),
        "components": {
            "WeatherPanel": _component_schema(
                "WeatherPanel",
                "Weather forecast panel with a location, a dominant weather condition, and labeled condition rows.",
                {
                    "location": {"type": "string"},
                    "condition": {
                        "type": "string",
                        "enum": list(WEATHER_CONDITIONS),
                        "description": "Dominant weather condition; selects the animated WeatherPanel banner.",
                    },
                    "rows": row_array,
                },
                ["location", "condition", "rows"],
            ),
            "NewsDigest": _component_schema(
                "NewsDigest",
                "Source-oriented news digest for latest headlines or recent developments.",
                {
                    "topic": {"type": "string"},
                    "stories": story_array,
                },
                ["topic", "stories"],
            ),
            "RestaurantFinder": _component_schema(
                "RestaurantFinder",
                "Restaurant, cafe, or bar finder with active filters and result rows.",
                {
                    "query": {"type": "string"},
                    "filters": string_array,
                    "results": result_array,
                },
                ["query", "filters", "results"],
            ),
            "TaskPlan": _component_schema(
                "TaskPlan",
                "Task planning panel with objective, cadence, deliverables, and next steps.",
                {
                    "objective": {"type": "string"},
                    "cadence": {"type": "string"},
                    "deliverables": string_array,
                    "nextSteps": string_array,
                },
                ["objective", "cadence", "deliverables", "nextSteps"],
            ),
            "Checklist": _component_schema(
                "Checklist",
                "Compact action checklist for multi-step operational work.",
                {
                    "title": {"type": "string"},
                    "items": string_array,
                },
                ["title", "items"],
            ),
            "AnswerBrief": _component_schema(
                "AnswerBrief",
                "Structured brief for comparisons, recommendations, tradeoffs, or research summaries.",
                {
                    "topic": {"type": "string"},
                    "sections": string_array,
                },
                ["topic", "sections"],
            ),
        },
    }


CATALOG_RULES = """
Prefer an A2UI surface when it would make the answer easier to scan; users do not need to ask for A2UI explicitly.
Lead with a brief 1-2 sentence prose orientation, then make the A2UI surface the canonical structured view. The surface IS the answer — do NOT also restate its contents in prose (don't list the stories, rows, items, or details in text as well); that duplication is unwanted.
Every component is fully defined by the schema and the worked examples below — gather the real data the user asked for (e.g. the actual weather), then populate the matching component and emit it directly.

Intent mapping:
- Weather or forecast -> call get_weather(location), then render a WeatherPanel using the returned condition + rows.
- Latest news, headlines, or recent developments -> NewsDigest.
- Restaurants, cafes, bars, open-now, lunch, dinner -> RestaurantFinder.
- Creating, scheduling, tracking, or planning tasks -> TaskPlan.
- Multi-step operational work -> Checklist.
- Comparisons, recommendations, tradeoffs, or research summaries -> AnswerBrief.

For mixed requests, compose multiple components with basic layout components: root component="Column" or "Row", with children referencing component ids from the same updateComponents.components array. Use Divider for section separation when useful.
Always emit createSurface followed by updateComponents for the same surfaceId. Use catalog id https://ag2.ai/assistant/a2ui/catalog.json and root id "root".
Do not call tools to discover A2UI components or catalog contracts; use the schema and rules already provided in this prompt.
Do not describe or print "corrected A2UI components"; emit valid A2UI messages directly.
User-facing prose must describe the answer, not A2UI mechanics; never mention schemas, validation, properties, components, or corrected/updated UI.
Keep surfaces concise, factual, and consistent with the prose. Put fuller NewsDigest story text in optional summary when available.

Worked examples (gather real data first, then emit exactly this shape):

Weather — user asks "What's the weather in Vienna?":
{"version":"v1.0","createSurface":{"surfaceId":"s1","catalogId":"https://ag2.ai/assistant/a2ui/catalog.json"}}
{"version":"v1.0","updateComponents":{"surfaceId":"s1","components":[{"id":"root","component":"WeatherPanel","location":"Vienna, Austria","condition":"sunny","rows":[{"label":"Temperature","value":"24°C (feels 22°C)"},{"label":"Wind","value":"12 km/h NW"},{"label":"Humidity","value":"45%"}]}]}}

News — user asks "Latest F1 news" (the first story is the lead: give it a category, a
summary, and a one-line `why`; later stories just need title/source/published/summary):
{"version":"v1.0","createSurface":{"surfaceId":"s1","catalogId":"https://ag2.ai/assistant/a2ui/catalog.json"}}
{"version":"v1.0","updateComponents":{"surfaceId":"s1","components":[{"id":"root","component":"NewsDigest","topic":"Formula 1","stories":[{"title":"Lead headline","source":"Reuters","published":"2h ago","category":"Breaking","summary":"One or two sentences of detail.","why":"Why this is the most important story right now.","url":"https://www.reuters.com/sport/formula1/the-article"},{"title":"Second headline","source":"BBC Sport","published":"4h ago","category":"Teams","summary":"One sentence of detail.","url":"https://www.bbc.com/sport/formula1/the-article"},{"title":"Third headline","source":"Autosport","published":"6h ago","category":"Results","summary":"One sentence of detail.","url":"https://www.autosport.com/f1/news/the-article"}]}]}}
Always include each story's `url` (the article link) so readers can click through — never put the source links only in your prose.
"""


@lru_cache(maxsize=1)
def runtime():
    """Return the configured beta A2UI runtime.

    The public A2UIServer wraps this runtime internally; for AG2 Assistant's
    existing WebSocket stream we use the same beta runtime/middleware directly
    so A2UIMessageEvent is emitted on the normal session stream.
    """

    from autogen.beta.a2ui._runtime import _A2UIRuntime

    return _A2UIRuntime(
        protocol_version="v1.0",
        custom_catalog=assistant_catalog(),
        custom_catalog_rules=CATALOG_RULES,
        include_schema_in_prompt=True,
        include_rules_in_prompt=True,
        validate_responses=True,
        validation_retries=1,
        system_message=(
            "You can generate rich A2UI interfaces for the AG2 Assistant web UI. "
            "Prefer using A2UI components whenever they make answers easier to scan, "
            "especially for weather, news, restaurants, tasks, comparisons, and multi-step work. "
            "Users do not need to mention A2UI for you to use it."
        ),
    )
