"""The Card catalog as a Bundled Skill (ADRs 0038, 0039): the cases its Cards cover as
its description, how to draw them as its body, the rest as Resources read on demand."""

from dataclasses import replace

from ag2.context import ConversationContext
from ag2.tools.skills import MemoryRuntime, MemorySkill
from ag2.tools.skills.skill_types import Resource, Skill, SkillMetadata

from assistant.a2ui import CATALOG_ID, CardCatalog
from assistant.cards import Card
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
example.
3. Reply with one or two sentences of orientation, then the view between `<a2ui-json>` and \
`</a2ui-json>`: a JSON array of a `createSurface` (catalogId "{CATALOG_ID}") and an \
`updateComponents` for the same surfaceId whose one component is the view itself, with id \
"root" and its fields — exactly as the worked example shows.
4. The view is the answer: do not restate its contents in prose, and never mention A2UI, \
schemas or components to the user.

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


class _A2UISkill(MemorySkill):
    """The A2UI Skill over one profile's ``CardCatalog``: a body that renders the rules
    and the index per read, and one Resource per Card available right now."""

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
        """The catalog entry, described by the Cards available now, plus their detail
        resources and the protocol reference, listed per read."""
        cards = self._catalog.cards()
        entry = super().descriptor
        return replace(
            entry,
            metadata=replace(entry.metadata, description=skill_description(cards)),
            resources=(
                *(Resource(name=card_resource(name)) for name in cards),
                Resource(name=PROTOCOL_RESOURCE),
            ),
        )


class A2UISkillRuntime(MemoryRuntime):
    """The runtime owning the A2UI Skill; a Card's detail is read from the catalog,
    so a Card that is Disabled or Suppressed has none."""

    def __init__(self, catalog: CardCatalog) -> None:
        super().__init__(_A2UISkill(catalog))
        self._catalog = catalog

    async def read_resource(self, name: str, resource: str, context: ConversationContext) -> str:
        if name != A2UI_SKILL:
            return await super().read_resource(name, resource, context)
        if resource == PROTOCOL_RESOURCE:
            return self._catalog.runtime().protocol
        detail = self._catalog.runtime().card_detail(_card_named(resource))
        if detail is None:
            raise FileNotFoundError(f"resource {resource!r} not found in skill {name!r}")
        return detail
