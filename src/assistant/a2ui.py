"""A2UI configuration for AG2 Assistant's chat/task surfaces."""

import json
from functools import lru_cache
from typing import Any

from assistant.events import A2UISurface

# A2UI protocol message keys — a JSON array whose items carry any of these is an
# A2UI message batch (used to recover surfaces from models that omit the wrapper).
_A2UI_MSG_KEYS = frozenset(
    {"createSurface", "updateComponents", "updateDataModel", "deleteSurface"}
)

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
        # Keep any surface that carries a renderable payload. The frontend renders
        # a data-only surface (createSurface + updateDataModel, no component tree)
        # via its generic branch, so dropping those on `component` alone would make
        # replayed history lose UI the live turn showed. A surface with neither a
        # component nor data has nothing to render and is still dropped.
        if (state := states.get(sid)) and (state.get("component") or state.get("data"))
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
    option_array = {
        "type": "array",
        "items": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Option name, e.g. 'MacBook Air 13'."},
                "tagline": {
                    "type": "string",
                    "description": "One-line positioning, e.g. 'Lightest + longest battery'.",
                },
                "price": {
                    "type": "string",
                    "description": "Cost label if relevant, e.g. '$1,499'.",
                },
            },
            "required": ["name"],
            "additionalProperties": False,
        },
    }
    criterion_array = {
        "type": "array",
        "items": {
            "type": "object",
            "properties": {
                "label": {"type": "string", "description": "Criterion, e.g. 'Battery life'."},
                "values": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "One short value per option, in the same order as `options`.",
                },
                "best": {
                    "type": "string",
                    "description": "Name of the option that wins this criterion — only when one clearly does.",
                },
            },
            "required": ["label", "values"],
            "additionalProperties": False,
        },
    }
    thread_array = {
        "type": "array",
        "items": {
            "type": "object",
            "properties": {
                "from": {"type": "string", "description": "Sender name, e.g. 'Priya Nair'."},
                "subject": {"type": "string"},
                "when": {"type": "string", "description": "e.g. '2h ago' or 'Mon'."},
                "gist": {"type": "string", "description": "One honest line on the content."},
                "unread": {"type": "boolean"},
                "needsReply": {
                    "type": "boolean",
                    "description": "Only when the mail clearly asks the user for something.",
                },
                "url": {"type": "string", "description": "The link= URL from the tool."},
            },
            "required": ["from", "subject"],
            "additionalProperties": False,
        },
    }
    event_array = {
        "type": "array",
        "items": {
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "start": {"type": "string", "description": "e.g. '8:15 AM'. Omit for all-day."},
                "end": {"type": "string", "description": "e.g. '8:45 AM'."},
                "location": {"type": "string"},
                "allDay": {"type": "boolean"},
                "next": {
                    "type": "boolean",
                    "description": "True on the single next upcoming event.",
                },
                "url": {"type": "string", "description": "The event's link= URL from the tool."},
                "joinUrl": {"type": "string", "description": "The join= meeting URL, if any."},
            },
            "required": ["title"],
            "additionalProperties": False,
        },
    }
    task_array = {
        "type": "array",
        "items": {
            "type": "object",
            "properties": {
                "id": {
                    "type": "string",
                    "description": "The task id from list_tasks — links the row to the task page.",
                },
                "title": {"type": "string"},
                "status": {
                    "type": "string",
                    "description": "One of: active, scheduled, completed, stopped, failed.",
                },
                "schedule": {"type": "string", "description": "e.g. 'daily 07:00' or 'one-off'."},
                "nextRun": {"type": "string", "description": "e.g. 'Wed 07:00'."},
                "objective": {"type": "string"},
                "progress": {"type": "string", "description": "Latest progress message."},
                "deliverables": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "description": {"type": "string"},
                            "status": {
                                "type": "string",
                                "description": "done, pending, or failed.",
                            },
                        },
                        "required": ["description", "status"],
                        "additionalProperties": False,
                    },
                },
                "error": {"type": "string", "description": "Only when the task is failing."},
            },
            "required": ["title", "status"],
            "additionalProperties": False,
        },
    }
    quote_array = {
        "type": "array",
        "items": {
            "type": "object",
            "properties": {
                "symbol": {"type": "string", "description": "Ticker, e.g. 'AAPL' or '^AXJO'."},
                "name": {"type": "string", "description": "Instrument name, e.g. 'Apple Inc.'."},
                "price": {"type": "number"},
                "change": {"type": "number", "description": "Absolute change vs previous close."},
                "changePercent": {
                    "type": "number",
                    "description": "Percent change vs previous close.",
                },
                "currency": {"type": "string", "description": "ISO code, e.g. 'USD', 'AUD'."},
                "exchange": {"type": "string"},
                "dayLow": {"type": "number"},
                "dayHigh": {"type": "number"},
                "spark": {
                    "type": "array",
                    "items": {"type": "number"},
                    "description": "Optional normalised intraday points (0-100) for the sparkline.",
                },
                "state": {
                    "type": "string",
                    "description": "Trading state if known: 'open', 'closed', 'pre', 'after'.",
                },
                "note": {
                    "type": "string",
                    "description": "Optional one-line driver for the lead, only if genuinely known.",
                },
            },
            # First quote is rendered as the lead/featured; the rest as a ranked table.
            "required": ["symbol", "name", "price", "changePercent"],
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
            "MarketBoard": _component_schema(
                "MarketBoard",
                "Markets board for stock, index, or crypto quotes across global exchanges.",
                {
                    "title": {"type": "string", "description": "Board heading, e.g. 'Technology'."},
                    "currency": {
                        "type": "string",
                        "description": "Board currency if all quotes share one.",
                    },
                    "status": {
                        "type": "string",
                        "description": "Market state if all agree: 'open'/'closed'/'pre'/'after'.",
                    },
                    "asOf": {
                        "type": "string",
                        "description": "Timestamp of the quotes (ISO-8601).",
                    },
                    "source": {"type": "string"},
                    "quotes": quote_array,
                },
                ["title", "quotes"],
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
            "InboxBrief": _component_schema(
                "InboxBrief",
                "Email inbox digest (gather real mail via gmail_search first; most important thread first).",
                {
                    "title": {"type": "string", "description": "e.g. 'Inbox this morning'."},
                    "summary": {
                        "type": "string",
                        "description": "One honest line, e.g. '3 unread, 1 needs a reply.'",
                    },
                    "threads": thread_array,
                },
                ["title", "threads"],
            ),
            "AgendaCard": _component_schema(
                "AgendaCard",
                "Calendar agenda for one day (gather real events via calendar_list_events first).",
                {
                    "title": {"type": "string", "description": "e.g. 'Today'."},
                    "date": {"type": "string", "description": "Human date, e.g. 'Tue 8 July'."},
                    "events": event_array,
                    "note": {
                        "type": "string",
                        "description": "One honest line, e.g. 'Free after 3 PM.'",
                    },
                },
                ["title", "events"],
            ),
            "TaskProgress": _component_schema(
                "TaskProgress",
                "Status board for existing scheduled/background tasks (gather real state via list_tasks/get_task first).",
                {
                    "title": {"type": "string", "description": "Board heading, e.g. 'Your tasks'."},
                    "tasks": task_array,
                },
                ["title", "tasks"],
            ),
            "DecisionMatrix": _component_schema(
                "DecisionMatrix",
                "Side-by-side decision matrix comparing 2-4 options against criteria, with a verdict.",
                {
                    "topic": {
                        "type": "string",
                        "description": "The decision being made, e.g. 'Travel laptop'.",
                    },
                    "options": option_array,
                    "criteria": criterion_array,
                    "recommended": {
                        "type": "string",
                        "description": "Name of the recommended option; must match an option name.",
                    },
                    "verdict": {
                        "type": "string",
                        "description": (
                            "One or two sentences: why the recommendation wins, and when "
                            "to pick another option instead."
                        ),
                    },
                },
                ["topic", "options", "criteria"],
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
- Stocks, shares, ETFs, funds, indices, crypto, or market prices -> call get_quotes(symbols, title), then render a MarketBoard from the returned quotes (first quote = the lead).
- Restaurants, cafes, bars, open-now, lunch, dinner -> RestaurantFinder.
- Email, inbox, unread, "any new mail" -> call gmail_search (e.g. 'in:inbox newer_than:1d'), then InboxBrief (most important thread first; copy each link= into url; set needsReply only when the mail clearly asks for something).
- Calendar, agenda, schedule, "what's on today/tomorrow" -> call calendar_list_events with the day's time window, then AgendaCard (mark the single next upcoming event with next:true).
- Creating, scheduling, or planning a new task -> TaskPlan.
- Reviewing existing tasks ("how are my tasks going?", task status/history) -> call list_tasks (and get_task for detail), then TaskProgress.
- Multi-step operational work -> Checklist.
- Comparing concrete alternatives or recommending between options -> DecisionMatrix (2-4 options, short cell values; set `recommended` + `verdict` only when the evidence supports a pick).
- Research summaries or briefs without competing options -> AnswerBrief.

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

Inbox — user asks "Anything new in my email?" (call gmail_search first; copy link= into url):
{"version":"v1.0","createSurface":{"surfaceId":"s1","catalogId":"https://ag2.ai/assistant/a2ui/catalog.json"}}
{"version":"v1.0","updateComponents":{"surfaceId":"s1","components":[{"id":"root","component":"InboxBrief","title":"Inbox this morning","summary":"3 new since yesterday — one needs a reply.","threads":[{"from":"Priya Nair","subject":"Q3 roadmap review — your slot","when":"2h ago","gist":"Asks you to confirm Thursday 10 AM for the review.","unread":true,"needsReply":true,"url":"https://mail.google.com/mail/u/0/#all/19..."},{"from":"GitHub","subject":"PR #42 merged","when":"5h ago","gist":"Your fix landed on main.","unread":true,"url":"https://mail.google.com/mail/u/0/#all/19..."}]}]}}

Agenda — user asks "What's on today?" (call calendar_list_events first; times in the user's timezone):
{"version":"v1.0","createSurface":{"surfaceId":"s1","catalogId":"https://ag2.ai/assistant/a2ui/catalog.json"}}
{"version":"v1.0","updateComponents":{"surfaceId":"s1","components":[{"id":"root","component":"AgendaCard","title":"Today","date":"Tue 8 July","events":[{"title":"Home","allDay":true},{"title":"Sync on Merlin EKS","start":"8:15 AM","end":"8:45 AM","next":true,"url":"https://www.google.com/calendar/event?eid=...","joinUrl":"https://meet.google.com/abc-defg-hij"},{"title":"1:1 with Sam","start":"2:00 PM","end":"2:30 PM","location":"Meet"}],"note":"Free after 2:30 PM."}]}}

Task status — user asks "How are my tasks going?" (call list_tasks/get_task first, then mirror the real state):
{"version":"v1.0","createSurface":{"surfaceId":"s1","catalogId":"https://ag2.ai/assistant/a2ui/catalog.json"}}
{"version":"v1.0","updateComponents":{"surfaceId":"s1","components":[{"id":"root","component":"TaskProgress","title":"Your scheduled tasks","tasks":[{"id":"t_a1b2c3","title":"Daily AI news briefing","status":"active","schedule":"daily 07:00","nextRun":"Wed 07:00","progress":"Delivered today's briefing","deliverables":[{"description":"Morning digest","status":"done"}]},{"id":"t_d4e5f6","title":"Weekly competitor scan","status":"failed","schedule":"Mondays 09:00","error":"Search quota exhausted on last run"}]}]}}

Decision — user asks "Should I get the MacBook Air or the ThinkPad X1 for travel?"
(values align by index with options; mark `best` only where one option clearly wins):
{"version":"v1.0","createSurface":{"surfaceId":"s1","catalogId":"https://ag2.ai/assistant/a2ui/catalog.json"}}
{"version":"v1.0","updateComponents":{"surfaceId":"s1","components":[{"id":"root","component":"DecisionMatrix","topic":"Travel laptop","options":[{"name":"MacBook Air 13","tagline":"Longest battery in class","price":"$1,499"},{"name":"ThinkPad X1 Carbon","tagline":"Best keyboard + ports","price":"$1,649"}],"criteria":[{"label":"Weight","values":["1.24 kg","1.09 kg"],"best":"ThinkPad X1 Carbon"},{"label":"Battery (real-world)","values":["~15 h","~10 h"],"best":"MacBook Air 13"},{"label":"Ports","values":["2× USB-C","2× USB-C · 2× USB-A · HDMI"],"best":"ThinkPad X1 Carbon"},{"label":"Keyboard","values":["Good","Excellent"],"best":"ThinkPad X1 Carbon"}],"recommended":"MacBook Air 13","verdict":"The Air wins on battery and weight-adjusted value for travel; pick the X1 Carbon if you need USB-A/HDMI without dongles or type all day."}]}}

Markets — user asks "How are the tech stocks doing?" (call get_quotes first, then copy
its quotes straight in; the first quote is the lead. Keep each quote's `spark`, `currency`,
and numeric `price`/`change`/`changePercent` exactly as returned):
{"version":"v1.0","createSurface":{"surfaceId":"s1","catalogId":"https://ag2.ai/assistant/a2ui/catalog.json"}}
{"version":"v1.0","updateComponents":{"surfaceId":"s1","components":[{"id":"root","component":"MarketBoard","title":"Technology","currency":"USD","status":"open","asOf":"2026-06-29T18:17:30+00:00","source":"Yahoo Finance","quotes":[{"symbol":"NVDA","name":"NVIDIA Corporation","price":193.99,"change":1.46,"changePercent":0.76,"currency":"USD","exchange":"NasdaqGS","dayLow":190.1,"dayHigh":195.2,"spark":[12,30,22,45,38,60,55,72,64,80,70,88,76,92,84,100],"state":"open"},{"symbol":"AAPL","name":"Apple Inc.","price":281.51,"change":-2.27,"changePercent":-0.8,"currency":"USD","exchange":"NasdaqGS","spark":[100,82,90,60,66,48,40,20],"state":"open"}]}]}}
"""


@lru_cache(maxsize=1)
def runtime():
    """Return the configured beta A2UI runtime.

    The public A2UIServer wraps this runtime internally; for AG2 Assistant's
    existing WebSocket stream we use the same beta runtime/middleware directly
    so A2UIMessageEvent is emitted on the normal session stream.
    """

    from ag2.a2ui._runtime import _A2UIRuntime

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


def wrap_bare_a2ui(text: str) -> str | None:
    """Wrap a bare A2UI message array in the ``<a2ui-json>`` tags the parser needs.

    The A2UI runtime only extracts a surface when the model wraps its message array
    in ``<a2ui-json>…</a2ui-json>`` (see the system prompt). Some models — notably
    non-Gemini ones — inconsistently emit the raw array without the wrapper, which
    otherwise leaves the JSON stranded in the prose and renders no surface. This
    finds the first JSON array whose items look like A2UI messages and re-wraps it
    so the standard extraction path applies. Returns the rewritten text, or ``None``
    if no A2UI array is present. Callers must only invoke this when the text has no
    ``<a2ui-json>`` tag already (otherwise the normal path handles it).
    """
    if not text:
        return None
    from ag2.a2ui.constants import A2UI_JSON_CLOSE_TAG, A2UI_JSON_OPEN_TAG

    decoder = json.JSONDecoder()
    i = 0
    while True:
        start = text.find("[", i)
        if start == -1:
            return None
        try:
            value, end = decoder.raw_decode(text, start)
        except ValueError:
            i = start + 1  # not a JSON array here — keep scanning
            continue
        if isinstance(value, list) and any(
            isinstance(op, dict) and (_A2UI_MSG_KEYS & op.keys()) for op in value
        ):
            return (
                f"{text[:start]}{A2UI_JSON_OPEN_TAG}"
                f"{text[start:end]}{A2UI_JSON_CLOSE_TAG}{text[end:]}"
            )
        i = end  # a JSON array, but not A2UI — skip past it and keep scanning


def tolerant_a2ui_middleware(parser):
    """Middleware factory that recovers A2UI surfaces from an un-wrapped response.

    Complements the runtime's own extraction/validation middleware, which only
    fires when the ``<a2ui-json>`` tags are present. This one fires only when they
    are absent but a bare A2UI array is, so the two never both act on the same
    response. Reuses the runtime ``parser`` and the runtime's publish path, so the
    recovered surface travels the same out-of-band channel as a wrapped one.
    """
    from ag2.a2ui.constants import A2UI_JSON_OPEN_TAG
    from ag2.a2ui.middleware import _publish_a2ui
    from ag2.middleware.base import BaseMiddleware

    class _TolerantMiddleware(BaseMiddleware):
        async def on_llm_call(self, call_next, events, context):
            response = await call_next(events, context)
            text = response.content
            if text and A2UI_JSON_OPEN_TAG not in text:
                wrapped = wrap_bare_a2ui(text)
                if wrapped is not None:
                    await _publish_a2ui(parser.parse(wrapped), response, context)
            return response

    return lambda event, context: _TolerantMiddleware(event, context)
