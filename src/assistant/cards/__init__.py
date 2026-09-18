"""Cards as files: loading the Bundled layer, and drawing a Card instance.

A **Card** is a file declaring its name, the line the agent is offered, the fields the
model fills in, the layout, and one worked example; its identity is the ``name`` inside
the file. ``expand_card_messages`` replaces every Card instance with the ordinary A2UI
primitives its layout declares plus the data-model writes its fields make.
"""

import json
import logging
import os
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)

# Place and marker both: only a file with this suffix, inside a Card directory, is a
# Card. Anything else in there is passed over in silence.
CARD_SUFFIX = ".card.yaml"

# Where a nested Card instance's fields land in the surface's data model. A Card drawn
# as the whole surface keeps its fields at the root, where the model emitted them.
CARD_DATA_ROOT = "_cards"

# What one Card may cost the agent: the description is read on every turn, the example
# only once a Card is being drawn. A Card over budget is skipped, never truncated.
DESCRIPTION_BUDGET = 200
EXAMPLE_BUDGET = 2000

# The layout id a Card is rooted at. Its instance takes this id; every other layout id
# is namespaced under it.
LAYOUT_ROOT = "root"


class CardError(ValueError):
    """A Card-suffixed file that cannot be loaded."""


@dataclass(frozen=True)
class Card:
    """One Card: what the agent is offered, what it fills in, and how it is drawn."""

    name: str
    description: str
    fields: dict[str, Any]
    required: tuple[str, ...]
    layout: tuple[dict[str, Any], ...]
    example: dict[str, Any]
    path: Path | None = field(default=None, compare=False)


def bundled_cards_dir() -> Path:
    """Directory of the first-party Cards shipped with AG2 Assistant (read-only)."""
    return Path(__file__).parent / "bundled"


def load_cards(
    directory: str | os.PathLike[str], components: Iterable[str] = ()
) -> dict[str, Card]:
    """The Cards in ``directory``, keyed by the name inside each file.

    ``components`` is the primitive vocabulary a layout may draw from; empty accepts
    any. A missing directory means no Cards; a Card-suffixed file that fails to load
    is skipped with a warning and the rest of the catalog still loads.
    """
    allowed = frozenset(components)
    cards: dict[str, Card] = {}
    for path in sorted(Path(directory).glob(f"*{CARD_SUFFIX}")):
        try:
            card = _read_card(path, allowed)
        except CardError as exc:
            logger.warning("Skipping card file %s: %s", path, exc)
            continue
        cards[card.name] = card
    return cards


def _read_card(path: Path, components: frozenset[str]) -> Card:
    try:
        raw = yaml.safe_load(path.read_text())
    except (OSError, yaml.YAMLError) as exc:
        raise CardError(f"unreadable: {exc}") from exc
    if not isinstance(raw, dict):
        raise CardError("not a YAML mapping")
    name = str(raw.get("name") or "").strip()
    description = str(raw.get("description") or "").strip()
    if not name:
        raise CardError("no name")
    if not description:
        raise CardError("no description")
    if len(description) > DESCRIPTION_BUDGET:
        raise CardError(f"description is {len(description)} characters, over {DESCRIPTION_BUDGET}")
    fields = raw.get("fields")
    if not isinstance(fields, dict) or not fields:
        raise CardError("no fields")
    required = tuple(str(key) for key in raw.get("required") or ())
    if unknown := [key for key in required if key not in fields]:
        raise CardError(f"required names fields the Card does not declare: {', '.join(unknown)}")
    example = raw.get("example")
    if not isinstance(example, dict) or not example:
        raise CardError("no example")
    if stray := [key for key in example if key not in fields]:
        raise CardError(f"example sets fields the Card does not declare: {', '.join(stray)}")
    if missing := [key for key in required if key not in example]:
        raise CardError(f"example omits required fields: {', '.join(missing)}")
    if (size := len(json.dumps(example, separators=(",", ":")))) > EXAMPLE_BUDGET:
        raise CardError(f"example is {size} characters, over {EXAMPLE_BUDGET}")
    return Card(
        name=name,
        description=description,
        fields=dict(fields),
        required=required,
        layout=_layout(raw.get("layout"), components),
        example=dict(example),
        path=path,
    )


def _layout(raw: Any, components: frozenset[str]) -> tuple[dict[str, Any], ...]:
    """The layout's components, checked for unique ids and resolvable references."""
    if not isinstance(raw, list) or not raw:
        raise CardError("no layout")
    nodes: list[dict[str, Any]] = []
    for entry in raw:
        if not isinstance(entry, dict):
            raise CardError("a layout entry is not a mapping")
        node_id = str(entry.get("id") or "").strip()
        kind = str(entry.get("component") or "").strip()
        if not node_id or not kind:
            raise CardError("a layout entry needs an id and a component")
        if components and kind not in components:
            raise CardError(f"layout draws {kind!r}, which is not a primitive")
        nodes.append({**entry, "id": node_id, "component": kind})
    ids = [node["id"] for node in nodes]
    if duplicates := sorted({node_id for node_id in ids if ids.count(node_id) > 1}):
        raise CardError(f"duplicate layout ids: {', '.join(duplicates)}")
    if LAYOUT_ROOT not in ids:
        raise CardError(f"layout has no {LAYOUT_ROOT!r} component to be rooted at")
    known = set(ids)
    for node in nodes:
        for reference in _references(node):
            if reference not in known:
                raise CardError(f"layout references unknown id {reference!r}")
    return tuple(nodes)


def _references(node: dict[str, Any]) -> list[str]:
    """The layout ids one component names: its child, its children, or its template."""
    children = node.get("children")
    if isinstance(children, list):
        named = [child for child in children if isinstance(child, str)]
    elif isinstance(children, dict) and isinstance(children.get("componentId"), str):
        named = [children["componentId"]]
    else:
        named = []
    child = node.get("child")
    return [*named, child] if isinstance(child, str) else named


# --- drawing a Card ---------------------------------------------------------


def expand_card_messages(messages: list[Any], cards: dict[str, Card]) -> list[Any]:
    """A2UI messages with every Card instance drawn as primitives, each followed by
    the data-model writes its fields make."""
    if not cards:
        return list(messages)
    drawn: list[Any] = []
    for message in messages:
        update = message.get("updateComponents") if isinstance(message, dict) else None
        components = update.get("components") if isinstance(update, dict) else None
        if not isinstance(components, list):
            drawn.append(message)
            continue
        expanded, writes = expand_components(components, cards)
        if not writes and expanded == components:
            drawn.append(message)
            continue
        version = message.get("version") or "v1.0"
        surface_id = update.get("surfaceId")
        drawn.append({**message, "updateComponents": {**update, "components": expanded}})
        drawn.extend(
            {
                "version": version,
                "updateDataModel": {"surfaceId": surface_id, "path": path, "value": value},
            }
            for path, value in writes
        )
    return drawn


def expand_components(
    components: list[Any], cards: dict[str, Card]
) -> tuple[list[Any], list[tuple[str, Any]]]:
    """The component list with every Card instance replaced by the primitives its
    layout declares, plus the ``(pointer, value)`` writes the instances' fields make."""
    if not cards:
        return list(components), []
    root = _root_id(components)
    expanded: list[Any] = []
    writes: list[tuple[str, Any]] = []
    for component in components:
        card = cards.get(_kind(component))
        if card is None:
            expanded.append(component)
            continue
        nodes, fields = _draw(component, card, root=component.get("id") == root)
        expanded.extend(nodes)
        writes.extend(fields)
    return expanded, writes


def _kind(component: Any) -> str:
    return str(component.get("component") or "") if isinstance(component, dict) else ""


def _root_id(components: list[Any]) -> str | None:
    """The component the surface is rooted at — the same one every renderer picks."""
    ids = [component.get("id") for component in components if isinstance(component, dict)]
    return "root" if "root" in ids else (ids[0] if ids else None)


def _draw(
    component: dict[str, Any], card: Card, root: bool
) -> tuple[list[dict[str, Any]], list[tuple[str, Any]]]:
    """One Card instance as its layout's primitives, plus its fields' data writes.

    A nested instance namespaces both by its own id, so two instances never collide.
    """
    instance = str(component.get("id") or "")
    prefix = "" if root else f"/{CARD_DATA_ROOT}/{_escaped(instance)}"
    ids = {
        node["id"]: instance if node["id"] == LAYOUT_ROOT else f"{instance}__{node['id']}"
        for node in card.layout
    }
    nodes = [_rewritten(node, ids, prefix) for node in card.layout]
    writes = [
        (f"{prefix}/{_escaped(name)}", component[name]) for name in card.fields if name in component
    ]
    return nodes, writes


def _escaped(part: str) -> str:
    """One JSON Pointer path segment (RFC 6901)."""
    return part.replace("~", "~0").replace("/", "~1")


def _rewritten(node: dict[str, Any], ids: dict[str, str], prefix: str) -> dict[str, Any]:
    """One layout component with its ids namespaced and its bindings re-rooted."""
    drawn: dict[str, Any] = {}
    for key, value in node.items():
        if key == "id" or (key == "child" and isinstance(value, str)):
            drawn[key] = ids.get(value, value)
        elif key == "children" and isinstance(value, list):
            drawn[key] = [ids.get(item, item) if isinstance(item, str) else item for item in value]
        else:
            drawn[key] = _rerooted(value, ids, prefix)
    return drawn


def _rerooted(value: Any, ids: dict[str, str], prefix: str) -> Any:
    """A layout value with every absolute binding re-rooted at ``prefix``. A path
    opening with ``.`` reads the repeated item it is drawn in and is left alone."""
    if isinstance(value, list):
        return [_rerooted(item, ids, prefix) for item in value]
    if not isinstance(value, dict):
        return value
    drawn: dict[str, Any] = {}
    for key, item in value.items():
        if key == "path" and isinstance(item, str):
            drawn[key] = item if item.startswith(".") else f"{prefix}{item}"
        elif key == "componentId" and isinstance(item, str):
            drawn[key] = ids.get(item, item)
        else:
            drawn[key] = _rerooted(item, ids, prefix)
    return drawn
