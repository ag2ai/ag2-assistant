"""A2UI configuration for AG2 Assistant's chat/task surfaces."""

import json
from functools import lru_cache
from typing import TYPE_CHECKING, Any

from ag2.a2ui import a2ui_action
from ag2.a2ui.actions import collect_action_declarations, collect_server_actions
from ag2.a2ui.middleware import A2UIValidationMiddleware, _A2UIValidationMiddleware
from ag2.a2ui.parser import A2UIResponseParser

from assistant.cards import (
    CARDS_DIR,
    Card,
    CardLayers,
    CardStateStore,
    bundled_cards_dir,
    cards_fingerprint,
    expand_card_messages,
    expand_components,
    load_cards,
    resolve_cards,
    root_component,
)
from assistant.config import Config
from assistant.events import A2UISurface
from assistant.state_store import ORIGIN_BUNDLED, ORIGIN_GLOBAL, ORIGIN_PROFILE

if TYPE_CHECKING:
    from ag2.a2ui.schema_manager import A2UISchemaManager

# A2UI protocol message keys — a JSON array whose items carry any of these is an
# A2UI message batch (used to recover surfaces from models that omit the wrapper).
_A2UI_MSG_KEYS = frozenset(
    {"createSurface", "updateComponents", "updateDataModel", "deleteSurface"}
)

CATALOG_ID = "https://ag2.ai/assistant/a2ui/catalog.json"

# AG2's schema manager merges the whole Basic Catalog into every custom catalog.
# Keep this app's contract honest: only advertise and validate what the web renderer
# implements, plus the assistant-specific components below.
SUPPORTED_BASIC_COMPONENTS = frozenset(
    {
        "Button",
        "Card",
        "CheckBox",
        "ChoicePicker",
        "Column",
        "DateTimeInput",
        "Divider",
        "Icon",
        "Image",
        "List",
        "Row",
        "Slider",
        "Text",
        "TextField",
        "Video",
    }
)

# Primitives this renderer draws beyond the Basic Catalog: the visual atoms a Card
# needs. A Card layout may draw them; the model never emits one directly.
CARD_PRIMITIVES = frozenset(
    {"Diff", "Figure", "Link", "Metric", "Sparkline", "Table", "WeatherGlyph"}
)

# The conditions the WeatherGlyph primitive draws — its vocabulary, not any Card's.
# The weather tool reports in it; mirrored in web/src/lib/weather/conditions.ts.
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

# The vocabulary a Card's layout is composed from.
CARD_VOCABULARY = SUPPORTED_BASIC_COMPONENTS | CARD_PRIMITIVES


@lru_cache(maxsize=1)
def bundled_cards() -> dict[str, Card]:
    """The Cards shipped with the app, read once from their files."""
    return load_cards(bundled_cards_dir(), CARD_VOCABULARY, ORIGIN_BUNDLED)


def card_layers(config: Config) -> CardLayers:
    """The three Card directories with the layer each one is, lowest precedence
    first: Bundled, Global at the Root, and the profile's own in its Files space."""
    return (
        (ORIGIN_BUNDLED, bundled_cards_dir()),
        (ORIGIN_GLOBAL, config.paths.cards_dir),
        (ORIGIN_PROFILE, config.workspace_dir / CARDS_DIR),
    )


def _build_schema_manager(*args: Any, **kwargs: Any) -> "A2UISchemaManager":
    """AG2's schema manager, filtering its merged Basic Catalog to this renderer's
    supported subset."""
    from ag2.a2ui.schema_manager import A2UISchemaManager

    class FilteredSchemaManager(A2UISchemaManager):
        def _ensure_loaded(self):
            super()._ensure_loaded()
            if getattr(self, "_assistant_catalog_filtered", False):
                return
            basic = dict(self._basic_catalog)
            raw_components = basic.get("components")
            basic_components = raw_components if isinstance(raw_components, dict) else {}
            components = {
                name: schema
                for name, schema in basic_components.items()
                if name in SUPPORTED_BASIC_COMPONENTS
            }
            raw_defs = basic.get("$defs")
            definitions = dict(raw_defs) if isinstance(raw_defs, dict) else {}
            definitions["anyComponent"] = {
                "oneOf": [{"$ref": f"#/components/{name}"} for name in components],
                "discriminator": {"propertyName": "component"},
            }
            basic["components"] = components
            basic["$defs"] = definitions
            self._basic_catalog = basic
            self._catalog_rules = (
                "Only use these Basic Catalog components: "
                + ", ".join(sorted(SUPPORTED_BASIC_COMPONENTS))
                + ". A canvas is an A2UI surface, not a component: compose it with "
                "Card, Column, Row, and List; never emit a Canvas component. Image "
                "requires exactly its url plus optional description, fit, and variant."
            )
            self._assistant_catalog_filtered = True

    return FilteredSchemaManager(*args, **kwargs)


def _message_dict(message: Any) -> dict:
    if hasattr(message, "to_dict"):
        return message.to_dict()
    if hasattr(message, "model_dump"):
        return message.model_dump(mode="json")
    return message if isinstance(message, dict) else {}


def _component_data(component: dict, existing: dict | None = None) -> dict:
    """A Card instance's fields as the surface's data model. A primitive root draws
    layout rather than holding data, so it contributes none."""
    data = dict(existing or {})
    if component.get("component") in CARD_VOCABULARY:
        return data
    for key, value in component.items():
        if key not in {"id", "component", "type", "accessibility"}:
            data[key] = value
    return data


def _copy_of(value: Any) -> dict | list:
    """A copy of the container a pointer step walks into; a list stays a list."""
    if isinstance(value, list):
        return list(value)
    return dict(value) if isinstance(value, dict) else {}


def _key_for(container: dict | list, part: str) -> str | int | None:
    """The key ``part`` names in ``container``; None when it names nothing writable —
    a list answers only to an index it already holds."""
    if not isinstance(container, list):
        return part
    return int(part) if part.isdigit() and int(part) < len(container) else None


def update_data_value(data: dict, path: str, value: Any) -> dict:
    """Return a copy of ``data`` with a JSON Pointer value updated."""
    if path in {"", "/"}:
        return value if isinstance(value, dict) else {"value": value}
    parts = [part.replace("~1", "/").replace("~0", "~") for part in path.lstrip("/").split("/")]
    result = dict(data)
    current: dict | list = result
    for part in parts[:-1]:
        key = _key_for(current, part)
        if isinstance(current, list) and isinstance(key, int):
            branch = _copy_of(current[key])
            current[key] = branch
        elif isinstance(current, dict) and isinstance(key, str):
            branch = _copy_of(current.get(key))
            current[key] = branch
        else:
            return result
        current = branch
    last = _key_for(current, parts[-1])
    if isinstance(current, list) and isinstance(last, int):
        current[last] = value
    elif isinstance(current, dict) and isinstance(last, str):
        current[last] = value
    return result


def _surface_title(data: dict) -> str:
    """A surface is named by its own data model, whatever its layout draws."""
    title = data.get("title")
    return title if isinstance(title, str) and title else "Interactive view"


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
            root = root_component(components)
            if root:
                states[surface_id]["component"] = {**root, "_components": components}
                states[surface_id]["components"] = components
                states[surface_id]["data"] = _component_data(root, states[surface_id].get("data"))
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
            state["data"] = update_data_value(
                state.get("data") or {}, update.get("path") or "/", update.get("value")
            )
        elif delete := message.get("deleteSurface"):
            surface_id = delete.get("surfaceId")
            if surface_id in states:
                del states[surface_id]
                order = [sid for sid in order if sid != surface_id]

    return [
        A2UISurface(
            final["surface_id"],
            catalog_id=final.get("catalog_id") or CATALOG_ID,
            version=final.get("version") or "v1.0",
            component=final.get("component") or {},
            data=final.get("data") or {},
            title=_surface_title(final.get("data") or {}),
            intent="generated-ui",
        )
        for sid in order
        # A record with data and no tree carries a later turn's write to a surface an
        # earlier one drew. Neither a component nor data is nothing to keep.
        if (final := states.get(sid)) and (final.get("component") or final.get("data"))
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


def assistant_catalog(cards: dict[str, Card] | None = None) -> dict:
    """Custom A2UI catalog rendered by the Svelte chat/task UI.

    Every Card in ``cards`` is advertised — and validated — under the schema its own
    file declares.
    """
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": CATALOG_ID,
        "title": "AG2 Assistant Generative UI Catalog",
        "description": "Purpose-built A2UI components for AG2 Assistant chat and task answers.",
        "catalogId": CATALOG_ID,
        "instructions": (
            "Use one focused custom component as the root component when the answer "
            "benefits from structure. Each component below describes what it is for — "
            "choose by the shape of the answer, not by a fixed list of topics. For "
            "mixed answers, compose several with the basic A2UI layout components."
        ),
        "components": {
            **{
                card.name: _component_schema(
                    card.name, card.description, card.fields, list(card.required)
                )
                for card in (bundled_cards() if cards is None else cards).values()
            },
        },
    }


_CATALOG_RULES_TEMPLATE = """
Lead with a brief 1-2 sentence prose orientation, then make the A2UI surface the canonical structured view; do not restate its contents in prose.
Gather the real data with your tools BEFORE you render. Never populate a component from memory, and never invent a value to fill a field: leave it out instead.

For mixed requests, compose multiple components with basic layout components: root component="Column" or "Row", with children referencing component ids from the same updateComponents.components array. Use Divider for section separation when useful.
A Column, Row or List can repeat one written component instead of listing every child: `"children":{"componentId":"run_row","path":"/runs"}` draws `run_row` once per item of the array at `/runs`. Inside that component a path opening with `./` reads the item (`{"path":"./day"}`, or `{"path":"."}` for the whole item) and an absolute path still reads the whole data model, so one written row serves three items or thirty.
An interactive canvas is an A2UI surface, not a `Canvas` component. Build it from Card, Column, Row, and List. To show an image `generate_image` made, place the exact URL it says the image is served at in an Image component's required `url`; do not substitute the workspace path or invent image properties.
For basic controls, use these exact property shapes: TextField `{"component":"TextField","label":"Name","value":{"path":"/name"}}`; ChoicePicker `{"component":"ChoicePicker","options":[{"label":"One","value":"one"}],"value":{"path":"/choice"},"variant":"mutuallyExclusive"}`; CheckBox `{"component":"CheckBox","label":"Enable","value":{"path":"/enabled"}}`; Slider `{"component":"Slider","label":"Level","max":100,"value":{"path":"/level"}}`; DateTimeInput `{"component":"DateTimeInput","label":"When","value":{"path":"/when"},"enableDate":true,"enableTime":true}`. Do not add undocumented properties. A Button needs a Text child and an action event.
Interactive inputs (CheckBox, ChoicePicker, TextField, Slider, DateTimeInput) only update the local surface data model. If the user expects the assistant to use, submit, reveal, save, search, or otherwise act on those values, include a separate Button in the same layout. Its action must be an `event` with a specific verb-like name and a context object containing every required input as JSON Pointer bindings. For example: `{"id":"submit","component":"Button","child":"submit_text","variant":"primary","action":{"event":{"name":"apply_preferences","context":{"colours":{"path":"/selectedColours"}}}}}` followed by `{"id":"submit_text","component":"Text","text":"Apply"}`. Do not imply that choosing an option alone sends it to the assistant.
Always emit createSurface followed by updateComponents for the same surfaceId. Give each new surface a surfaceId not used earlier in this chat; reuse an earlier surfaceId only to replace that surface. Use catalog id __CATALOG_ID__ and root id "root".
Read a custom component's own detail resource before you draw it — it carries the exact fields that component accepts and a worked example. Never emit those fields from memory.
Do not describe or print "corrected A2UI components"; emit valid A2UI messages directly.
User-facing prose must describe the answer, not A2UI mechanics; never mention schemas, validation, properties, components, or corrected/updated UI.
Keep surfaces concise, factual, and consistent with the prose.
"""

# The catalog id is a constant, not a literal repeated through the prompt: keep the
# rules and every worked example pointing at whatever CATALOG_ID currently is.
CATALOG_RULES = _CATALOG_RULES_TEMPLATE.replace("__CATALOG_ID__", CATALOG_ID)


class _CardValidationMiddleware(_A2UIValidationMiddleware):
    """The per-turn instance: AG2's validation, then the Card instances drawn."""

    def __init__(self, event, context, *, parser, max_retries, cards) -> None:
        super().__init__(event, context, parser=parser, max_retries=max_retries)
        self._cards = cards

    def _validate(self, response_text: str):
        parse_result, errors = super()._validate(response_text)
        if errors is None:
            parse_result.operations[:] = expand_card_messages(parse_result.operations, self._cards)
        return parse_result, errors


class CardValidationMiddleware(A2UIValidationMiddleware):
    """Validate the model's fields against the Card's own schema — a bad emit is
    retried, as any invalid surface is — then publish the primitives its layout
    draws, so the browser is never told that Cards exist."""

    def __init__(self, parser, cards: dict[str, Card], max_retries: int = 1) -> None:
        super().__init__(parser, max_retries)
        self._cards = cards

    def __call__(self, event, context):
        return _CardValidationMiddleware(
            event,
            context,
            parser=self._parser,
            max_retries=self._max_retries,
            cards=self._cards,
        )


def expanded_card_surface(surface: A2UISurface, cards: dict[str, Card]) -> A2UISurface:
    """A durable surface with any Card instance redrawn as primitives. A surface
    already drawn as primitives comes back unchanged."""
    nested = surface.component.get("_components")
    components = (
        list(nested)
        if isinstance(nested, list)
        else ([surface.component] if surface.component else [])
    )
    expanded, writes = expand_components(components, cards)
    if not writes and expanded == components:
        return surface
    data = surface.data
    for path, value in writes:
        data = update_data_value(data, path, value)
    root = root_component(expanded)
    redrawn = A2UISurface(
        surface.surface_id,
        catalog_id=surface.catalog_id,
        version=surface.version,
        component={**root, "_components": expanded},
        data=data,
        title=surface.title,
        intent=surface.intent,
    )
    redrawn.created_at = surface.created_at
    return redrawn


@a2ui_action(
    name="save_surface",
    description="Persist the current values in an interactive A2UI surface.",
    example_context={"surface_id": "<surface id>", "data": {}},
)
def save_surface(surface_id: str, data: dict) -> dict:
    """Store a complete interactive surface data model."""
    return {"updateDataModel": {"surfaceId": surface_id, "path": "/", "value": data}}


A2UI_ACTIONS = (save_surface,)
A2UI_SERVER_ACTIONS = collect_server_actions(A2UI_ACTIONS)


class _AssistantA2UIRuntime:
    """AG2 runtime using the assistant's filtered Basic Catalog."""

    def __init__(self, cards: dict[str, Card]) -> None:
        self.cards = cards
        self.schema_manager = _build_schema_manager(
            protocol_version="v1.0",
            custom_catalog=assistant_catalog(self.cards),
            custom_catalog_rules=CATALOG_RULES,
        )
        self.catalog_id = self.schema_manager.catalog_id
        self.parser = A2UIResponseParser(
            version_string=self.schema_manager.version_string,
            server_to_client_schema=self.schema_manager.server_to_client_schema,
            schema_registry=self.schema_manager.build_schema_registry(),
            component_schemas=self.schema_manager.get_component_schemas(),
            catalog_id=self.schema_manager.catalog_id,
        )
        self.actions = collect_action_declarations(A2UI_ACTIONS)
        # The protocol reference: the message format, composition rules and actions —
        # no component schema and no worked example, which are one Card's detail each.
        self.protocol = self.schema_manager.generate_prompt_section(
            include_schema=False,
            include_rules=True,
            actions=list(self.actions),
        )
        self._middleware = CardValidationMiddleware(self.parser, self.cards, 1)

    @property
    def version_string(self) -> str:
        return self.schema_manager.version_string

    def middleware_factories(self):
        return [self._middleware]

    def capabilities_prompt(self, caps):
        from ag2.a2ui.capabilities import capabilities_to_prompt

        return capabilities_to_prompt(caps, catalog_id=self.schema_manager.catalog_id)


class CardCatalog:
    """One profile's Cards and the A2UI runtime built from them — re-read when a
    Card file changes, or when the state document turns one off or back on."""

    def __init__(self, config: Config) -> None:
        self._layers = card_layers(config)
        self._state = CardStateStore(config.root_dir)
        self._profile = config.data_dir.name
        self._fingerprint: tuple | None = None
        self._drawable: dict[str, Card] = {}
        self._cards: dict[str, Card] = {}
        self._runtime: _AssistantA2UIRuntime | None = None

    def cards(self) -> dict[str, Card]:
        """The Cards the agent is offered — Profile over Global over Bundled, minus
        whatever is Disabled install-wide or Suppressed for this profile."""
        self._resolve()
        return dict(self._cards)

    def drawable(self) -> dict[str, Card]:
        """Every Card on disk, whatever its state — a Card instance already in a
        Thread goes on drawing however its Card is switched now."""
        self._resolve()
        return dict(self._drawable)

    def _resolve(self) -> None:
        fingerprint = (cards_fingerprint(self._layers), self._state.revision())
        if fingerprint == self._fingerprint:
            return
        self._fingerprint = fingerprint
        self._drawable = resolve_cards(self._layers, components=CARD_VOCABULARY)
        self._cards = {
            name: card
            for name, card in self._drawable.items()
            if self._state.is_available(name, self._profile, origin=card.origin)
        }
        self._runtime = None

    def runtime(self) -> "_AssistantA2UIRuntime":
        """The configured beta A2UI runtime over those Cards.

        The public A2UIServer wraps this runtime internally; for AG2 Assistant's
        existing WebSocket stream we use the same beta runtime/middleware directly
        so A2UIMessageEvent is emitted on the normal chat stream.
        """
        cards = self.cards()
        if self._runtime is None:
            self._runtime = _AssistantA2UIRuntime(cards)
        return self._runtime


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


def tolerant_a2ui_middleware(parser, cards: dict[str, Card]):
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
                    recovered = parser.parse(wrapped)
                    if not parser.validate(recovered.operations).is_valid:
                        return response  # held to the same schema as a wrapped emit
                    recovered.operations[:] = expand_card_messages(recovered.operations, cards)
                    await _publish_a2ui(recovered, response, context)
            return response

    return lambda event, context: _TolerantMiddleware(event, context)
