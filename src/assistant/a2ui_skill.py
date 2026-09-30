"""The Card catalog as a Bundled Skill (ADRs 0038-0040): the cases its Cards cover as its
description, how to draw them as its body, one Resource and one drawing Script per Card."""

import json
import uuid
from collections.abc import Sequence
from dataclasses import replace
from typing import Any

from ag2.a2ui import A2UIMessageEvent
from ag2.a2ui._types import CreateSurfaceMessage, ServerToClientMessage, UpdateComponentsMessage
from ag2.context import ConversationContext
from ag2.tools.skills import MemoryRuntime, MemorySkill
from ag2.tools.skills.skill_types import Resource, Script, Skill, SkillMetadata

from assistant.a2ui import CATALOG_ID, CardCatalog
from assistant.cards import Card, expand_card_messages
from assistant.config import Config
from assistant.skills import SkillStateStore
from assistant.state_store import ORIGIN_BUNDLED

# The name users see in Settings → Skills, and the one the fetch instruction names.
A2UI_SKILL = "rich-views"

# What a rich view is, as Settings shows it and as the description opens.
A2UI_SKILL_DESCRIPTION = (
    "Answer with a rich view — a panel the AG2 Assistant web UI draws — instead of prose "
    "whenever one of this profile's views fits the question."
)

# The longest description a Skill may carry (the Agent Skills format's own cap).
SKILL_DESCRIPTION_LIMIT = 1024

# The version the bundled skills declare, and the directory one Card's detail is
# addressed under: ``cards/<CardName>.md``.
_SKILL_VERSION = "1.0"
_RESOURCE_DIR = "cards"

# The A2UI protocol at large: composing views and drawing from the basic components.
PROTOCOL_RESOURCE = "reference/protocol.md"


def card_resource(name: str) -> str:
    """The resource path one Card's detail is read at."""
    return f"{_RESOURCE_DIR}/{name}.md"


def skill_description(cards: dict[str, Card]) -> str:
    """The resident line: what a rich view is, and every case a Card is ready for,
    kept within the description cap."""
    closing = " Users never ask for one: load this skill before you answer such a question."
    lead = f"{A2UI_SKILL_DESCRIPTION[:-1]}. Views are ready for: "
    room = SKILL_DESCRIPTION_LIMIT - len(lead) - len(closing) - len(", and more.")
    topics: list[str] = []
    for topic in (card.topic for card in cards.values() if card.topic):
        if len(", ".join([*topics, topic])) > room:
            return f"{lead}{', '.join(topics)}, and more.{closing}"
        topics.append(topic)
    if not topics:
        return A2UI_SKILL_DESCRIPTION
    return f"{lead}{', '.join(topics)}.{closing}"


def skill_body(cards: dict[str, Card]) -> str:
    """The Skill's body: the views this profile can draw, and how to draw one."""
    index = "\n".join(f"- **{card.name}**: {card.description}" for card in cards.values())
    return f"""A rich view is a panel the AG2 Assistant web UI draws from a view's fields. When \
the user's question matches one of the views below, the view IS the answer — do not settle \
for prose. Users never ask for one.

## Views this profile can draw

{index}

## Drawing a view

1. Gather the real data with your tools first. Never fill a field from memory or invent a \
value; leave out what you do not have.
2. Read the view's detail: read_skill_resource(name="{A2UI_SKILL}", \
resource="{card_resource("<CardName>")}") returns the exact fields it accepts and a worked \
example of the call that draws it.
3. Draw it: every view is a script of the same name — run_skill_script(name="{A2UI_SKILL}", \
script="<CardName>", args={{…fields}}). The view is drawn in the chat, or the call says which \
fields to fix.
4. Reply with one or two sentences of orientation. The view is the answer: do not restate \
its contents in prose, and never mention A2UI, schemas or components to the user.

To put several views on one surface, or to build one from the basic components when no view \
above fits, read_skill_resource(name="{A2UI_SKILL}", resource="{PROTOCOL_RESOURCE}") first.
"""


def _card_named(resource: str) -> str:
    """The Card a resource path names; empty for a path that is not a Card's."""
    directory, _, leaf = resource.partition("/")
    return leaf[: -len(".md")] if directory == _RESOURCE_DIR and leaf.endswith(".md") else ""


def a2ui_skill_descriptor() -> Skill:
    """The Skill's catalog entry — name and description, reading no Card at all, so
    the Settings list can show the row without resolving a catalog."""
    return Skill(
        metadata=SkillMetadata(
            name=A2UI_SKILL, description=A2UI_SKILL_DESCRIPTION, version=_SKILL_VERSION
        )
    )


def a2ui_available(config: Config) -> bool:
    """Whether this profile draws rich views at all: the Skill's own switch, read
    fresh so a toggle lands on the next message with no restart."""
    return SkillStateStore(config.root_dir).is_available(
        A2UI_SKILL, config.data_dir.name, origin=ORIGIN_BUNDLED
    )


def card_fields_schema(card: Card) -> dict[str, Any]:
    """The arguments of the script that draws ``card``: the Card's own fields."""
    return {"type": "object", "properties": card.fields, "required": list(card.required)}


def _card_messages(card: Card, fields: dict[str, Any]) -> list[ServerToClientMessage]:
    """The two messages that draw ``card`` as a new surface with ``fields``."""
    surface = f"{card.name.lower()}-{uuid.uuid4().hex[:8]}"
    root = {**fields, "id": "root", "component": card.name}
    return [
        CreateSurfaceMessage(
            version="v1.0", createSurface={"surfaceId": surface, "catalogId": CATALOG_ID}
        ),
        UpdateComponentsMessage(
            version="v1.0", updateComponents={"surfaceId": surface, "components": [root]}
        ),
    ]


def _card_detail(card: Card) -> str:
    """One Card's detail: the fields its script takes, and the call that draws it."""
    schema = json.dumps(card_fields_schema(card), indent=2, ensure_ascii=False)
    example = json.dumps(card.example, ensure_ascii=False, separators=(",", ":"))
    return (
        f"# {card.name}\n\n{card.description}\n\nPass exactly these fields; a value you do "
        f"not have is left out, never invented.\n\n```json\n{schema}\n```\n\nDraw it:\n"
        f'run_skill_script(name="{A2UI_SKILL}", script="{card.name}", args={example})\n'
    )


def _fields_from(args: dict[str, Any] | Sequence[str] | None) -> dict[str, Any] | None:
    """The fields a script call carries: an object, one JSON object in a list, or
    ``--name value`` pairs — every shape models send (ag2ai/ag2#3327)."""
    if isinstance(args, dict):
        return dict(args)
    if args is None or isinstance(args, str):
        return None
    items = list(args)
    if len(items) == 1:
        try:
            value = json.loads(items[0])
        except (TypeError, ValueError):
            return None
        return value if isinstance(value, dict) else None
    fields: dict[str, Any] = {}
    for key, raw in zip(items[::2], items[1::2]):
        if not key.startswith("--"):
            return None
        try:
            fields[key[2:]] = json.loads(raw)
        except (TypeError, ValueError):
            fields[key[2:]] = raw
    return fields


class _A2UISkill(MemorySkill):
    """The A2UI Skill over one profile's ``CardCatalog``: the body, one detail Resource
    and one drawing Script per Card available right now, all listed per read."""

    def __init__(self, catalog: CardCatalog) -> None:
        super().__init__(
            name=A2UI_SKILL,
            description=A2UI_SKILL_DESCRIPTION,
            instructions=lambda: skill_body(catalog.cards()),
            version=_SKILL_VERSION,
        )
        self._catalog = catalog

    @property
    def descriptor(self) -> Skill:
        """The catalog entry, described by the Cards available now, with their detail
        resources, the protocol reference and one script per Card."""
        cards = self._catalog.cards()
        entry = super().descriptor
        return replace(
            entry,
            metadata=replace(entry.metadata, description=skill_description(cards)),
            resources=(
                *(Resource(name=card_resource(name)) for name in cards),
                Resource(name=PROTOCOL_RESOURCE),
            ),
            scripts=tuple(Script(name=name) for name in cards),
        )


class A2UISkillRuntime(MemoryRuntime):
    """The runtime owning the A2UI Skill: a Card's detail and its drawing script come
    from the catalog, so a Card that is Disabled or Suppressed has neither."""

    def __init__(self, catalog: CardCatalog) -> None:
        super().__init__(_A2UISkill(catalog))
        self._catalog = catalog

    async def read(self, name: str, context: ConversationContext) -> str:
        if name != A2UI_SKILL:
            return await super().read(name, context)
        body = skill_body(self._catalog.cards())
        return f'<skill_content name="{A2UI_SKILL}">\n{body.strip()}\n</skill_content>'

    async def read_resource(self, name: str, resource: str, context: ConversationContext) -> str:
        if name != A2UI_SKILL:
            return await super().read_resource(name, resource, context)
        if resource == PROTOCOL_RESOURCE:
            return self._catalog.runtime().protocol
        card = self._catalog.cards().get(_card_named(resource))
        if card is None:
            raise FileNotFoundError(f"resource {resource!r} not found in skill {name!r}")
        return _card_detail(card)

    async def execute(
        self,
        name: str,
        script: str,
        context: ConversationContext,
        args: dict[str, Any] | Sequence[str] | None = None,
    ) -> str:
        if name != A2UI_SKILL:
            return await super().execute(name, script, context, args)
        runtime = self._catalog.runtime()
        card = runtime.cards.get(script)
        if card is None:
            raise FileNotFoundError(f"script {script!r} not found in skill {name!r}")
        fields = _fields_from(args)
        if fields is None:
            return f"Not drawn: pass the {card.name} fields as an object of named arguments."
        messages = _card_messages(card, fields)
        checked = runtime.parser.validate(messages)
        if not checked.is_valid:
            schema = json.dumps(card_fields_schema(card), ensure_ascii=False)
            return f"Not drawn: {'; '.join(checked.errors)}. Its fields: {schema}"
        for message in expand_card_messages(messages, runtime.cards):
            await context.send(A2UIMessageEvent(message))
        return f"The {card.name} is drawn in the chat. Reply in one or two sentences; do not restate it."
