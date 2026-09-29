"""The Card catalog as a Bundled Skill (ADR 0038).

The Skill's description is the only A2UI text resident in a turn; its body carries the
rules plus an index of the Cards this profile can draw; one Card's schema and worked
example are a Resource, read once the agent has decided to draw it. Body and Resources
render per read, so the index is whatever is on disk now.

Classified Bundled, so install-wide Disable and per-profile Suppression turn it off with
the same switch every other Skill answers to — and a turn it is off in is built with no
A2UI runtime and no middleware at all.
"""

from dataclasses import replace

from ag2.context import ConversationContext
from ag2.tools.skills import MemoryRuntime, MemorySkill
from ag2.tools.skills.skill_types import Resource, Skill, SkillMetadata

from assistant.a2ui import CardCatalog
from assistant.config import Config
from assistant.skills import SkillStateStore
from assistant.state_store import ORIGIN_BUNDLED

# The name users see in Settings → Skills, and the one the fetch instruction names.
A2UI_SKILL = "rich-views"

# The only text present on every turn: what a rich view is and when to reach for one.
A2UI_SKILL_DESCRIPTION = (
    "Draw the answer as a rich view — an interactive panel in the AG2 Assistant web UI "
    "— rather than prose, whenever structure would make it easier to scan: the weather, "
    "market prices, a plan, a checklist, a comparison, a list of places, stories or "
    "tasks. Users do not ask for one, so reach for it yourself; load this skill to see "
    "which views this profile can draw and how to draw them."
)

# The version the bundled skills declare, and the directory one Card's detail is
# addressed under: ``cards/<CardName>.md``.
_SKILL_VERSION = "1.0"
_RESOURCE_DIR = "cards"


def card_resource(name: str) -> str:
    """The resource path one Card's detail is read at."""
    return f"{_RESOURCE_DIR}/{name}.md"


# The lead-in to the body: what the index below is, and the one call that fetches a
# Card's own detail.
_BODY_LEAD_IN = (
    "A rich view is an A2UI surface the AG2 Assistant web UI draws. The custom "
    "components listed below are the views this profile can draw right now; each line "
    "says when to reach for one.\n"
    f'Read a view\'s own detail first: read_skill_resource(name="{A2UI_SKILL}", '
    f'resource="{card_resource("<CardName>")}") returns the exact fields it accepts '
    "and a worked example of the messages that draw it.\n\n"
)


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
            instructions=lambda: _BODY_LEAD_IN + catalog.runtime().skill_body,
            version=_SKILL_VERSION,
        )
        self._catalog = catalog

    @property
    def descriptor(self) -> Skill:
        """The catalog entry plus the Cards' detail resources, listed per read."""
        return replace(
            super().descriptor,
            resources=tuple(Resource(name=card_resource(name)) for name in self._catalog.cards()),
        )


class A2UISkillRuntime:
    """The ``SkillRuntime`` owning the A2UI Skill.

    Everything but ``read_resource`` is the ``MemoryRuntime``'s: only a Card's detail
    needs the catalog, and a Card that is Disabled or Suppressed has none.
    """

    def __init__(self, catalog: CardCatalog) -> None:
        self._catalog = catalog
        self._inner = MemoryRuntime(_A2UISkill(catalog))

    async def read_resource(self, name: str, resource: str, context: ConversationContext) -> str:
        if name != A2UI_SKILL:
            return await self._inner.read_resource(name, resource, context)
        detail = self._catalog.runtime().card_detail(_card_named(resource))
        if detail is None:
            raise FileNotFoundError(f"resource {resource!r} not found in skill {name!r}")
        return detail

    def __getattr__(self, item):
        # Delegate the rest of the SkillRuntime protocol (skills, read, execute,
        # cleanup, invalidate, ensure_storage, …) to the MemoryRuntime.
        return getattr(self._inner, item)
