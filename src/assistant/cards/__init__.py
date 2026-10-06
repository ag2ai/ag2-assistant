"""Cards as files: loading them from their layers, and drawing a Card instance.

A **Card** is a file declaring its name, the line the agent is offered, optionally the
``topic`` it is ready for, the fields the model fills in, the layout, and one worked example;
its identity is the ``name`` inside the file. ``load_cards`` reads one directory, ``resolve_cards`` stacks the layers a
profile is offered, and ``expand_card_messages`` replaces every Card instance with the
ordinary A2UI primitives its layout declares plus the data-model writes its fields make.
"""

import json
import logging
import os
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError, ValidationError
from referencing.exceptions import Unresolvable

from assistant.cards.layout import validate_primitive
from assistant.state_store import ORIGIN_GLOBAL, StateStore

logger = logging.getLogger(__name__)

# Place and marker both: only a file with this suffix, inside a Card directory, is a
# Card. Anything else in there is passed over in silence.
CARD_SUFFIX = ".card.yaml"

# The Card directory's name, at the install Root and inside a profile's Files space.
CARDS_DIR = "cards"

# The install-wide document recording which Cards are turned off, beside the Skill
# one at the Root: a Skill and a Card may share a name and must not share a switch.
CARDS_DOCUMENT = "cards.json"


# Where a nested Card instance's fields land in the surface's data model. A Card drawn
# as the whole surface keeps its fields at the root, where the model emitted them.
CARD_DATA_ROOT = "_cards"

# What one Card may cost the agent: the description is read on every turn, the example
# only once a Card is being drawn. A Card over budget is skipped, never truncated.
DESCRIPTION_BUDGET = 200
EXAMPLE_BUDGET = 2000

# The case a Card is ready for, named in the Skill's always-present description.
TOPIC_BUDGET = 60

# The largest a Card-suffixed file may be before it is skipped unparsed — a bound on
# what a renamed video in the user's own Card directory costs.
FILE_BUDGET = 64 * 1024

# The layout id a Card is rooted at. Its instance takes this id; every other layout id
# is namespaced under it.
LAYOUT_ROOT = "root"

# The layout ids a component names beyond its `child` and `children`, by the component
# that names them: a Table names the three templates it draws.
TEMPLATE_IDS = {"Table": ("header", "lead", "cell")}


# A Card directory with the layer it is, lowest precedence first.
CardLayers = tuple[tuple[str, Path], ...]


class CardError(ValueError):
    """A Card-suffixed file that cannot be loaded."""


class CardStateStore(StateStore):
    """Card state over the install-wide ``cards.json`` document at ``root_dir``:
    which Cards are Disabled, and which are turned off for one profile alone."""

    def __init__(self, root_dir: Path | None) -> None:
        super().__init__(Path(root_dir) / CARDS_DOCUMENT if root_dir is not None else None)


@dataclass(frozen=True)
class Card:
    """One Card: what the agent is offered, what it fills in, and how it is drawn."""

    name: str
    description: str
    fields: dict[str, Any]
    required: tuple[str, ...]
    layout: tuple[dict[str, Any], ...]
    example: dict[str, Any]
    topic: str = ""
    path: Path | None = field(default=None, compare=False)
    origin: str = field(default=ORIGIN_GLOBAL, compare=False)


def bundled_cards_dir() -> Path:
    """Directory of the first-party Cards shipped with AG2 Assistant (read-only)."""
    return Path(__file__).parent / "bundled"


@dataclass(frozen=True)
class CardProblem:
    """One file rejected by the Card loader."""

    path: Path
    origin: str
    error: str


@dataclass(frozen=True)
class CardScan:
    """Loaded Cards, rejected files, and every Card-suffixed path in one layer."""

    cards: dict[str, Card]
    problems: tuple[CardProblem, ...]
    files: tuple[Path, ...]


def scan_cards(
    directory: str | os.PathLike[str],
    components: Iterable[str] = (),
    origin: str = ORIGIN_GLOBAL,
) -> CardScan:
    """Discover a layer's Cards with the errors for every skipped file."""
    allowed = frozenset(components)
    cards: dict[str, Card] = {}
    problems = []
    files = tuple(sorted(Path(directory).glob(f"*{CARD_SUFFIX}")))
    for path in files:
        try:
            card = _read_card(path, allowed, origin)
            taken = cards.get(card.name)
            if taken is not None:
                raise CardError(f"{taken.path} already names {card.name}")
        except CardError as exc:
            logger.warning("Skipping card file %s: %s", path, exc)
            problems.append(CardProblem(path, origin, str(exc)))
            continue
        cards[card.name] = card
    return CardScan(cards, tuple(problems), files)


def load_cards(
    directory: str | os.PathLike[str],
    components: Iterable[str] = (),
    origin: str = ORIGIN_GLOBAL,
) -> dict[str, Card]:
    """Cards in one layer, keyed by their declared name; rejected files are skipped."""
    return scan_cards(directory, components, origin).cards


def resolve_cards(layers: "CardLayers", components: Iterable[str] = ()) -> dict[str, Card]:
    """The Cards across ``layers``, a later layer's Card winning by name."""
    cards: dict[str, Card] = {}
    for origin, directory in layers:
        cards.update(load_cards(directory, components, origin))
    return cards


def cards_fingerprint(layers: "CardLayers") -> tuple:
    """What the Card files across ``layers`` look like right now — one
    ``(name, mtime, size)`` per file, so an untouched set fingerprints the same."""
    return tuple(_layer_fingerprint(Path(directory)) for _origin, directory in layers)


def _layer_fingerprint(directory: Path) -> tuple:
    prints = []
    for path in sorted(directory.glob(f"*{CARD_SUFFIX}")):
        try:
            info = path.stat()
        except OSError:
            continue
        prints.append((path.name, info.st_mtime_ns, info.st_size))
    return tuple(prints)


def _read_card(path: Path, components: frozenset[str], origin: str) -> Card:
    try:
        if path.resolve().parent != path.parent.resolve():
            raise CardError("file points outside its Card directory")
        if (size := path.stat().st_size) > FILE_BUDGET:
            raise CardError(f"file is {size} bytes, over {FILE_BUDGET}")
        raw = yaml.safe_load(path.read_text())
    except (OSError, UnicodeError, RuntimeError, yaml.YAMLError) as exc:
        raise CardError(f"unreadable: {exc}") from exc
    return validate_card(raw, components, path=path, origin=origin)


def validate_card(
    raw: Any,
    components: Iterable[str] = (),
    *,
    path: Path | None = None,
    origin: str = ORIGIN_GLOBAL,
) -> Card:
    """Validate a file or in-memory definition using the same Card rules."""
    try:
        size = len(json.dumps(raw, ensure_ascii=False).encode())
    except (TypeError, ValueError) as exc:
        raise CardError(f"definition must be JSON-compatible: {exc}") from exc
    if size > FILE_BUDGET:
        raise CardError(f"definition is {size} bytes, over {FILE_BUDGET}")
    if not isinstance(raw, dict):
        raise CardError("not a YAML mapping")
    for key in ("name", "description", "topic"):
        if key in raw and not isinstance(raw[key], str):
            raise CardError(f"{key} must be text")
    name = str(raw.get("name") or "").strip()
    description = str(raw.get("description") or "").strip()
    if not name:
        raise CardError("no name")
    if not description:
        raise CardError("no description")
    if len(description) > DESCRIPTION_BUDGET:
        raise CardError(f"description is {len(description)} characters, over {DESCRIPTION_BUDGET}")
    topic = " ".join(str(raw.get("topic") or "").split())
    if len(topic) > TOPIC_BUDGET:
        raise CardError(f"topic is {len(topic)} characters, over {TOPIC_BUDGET}")
    fields = raw.get("fields")
    if not isinstance(fields, dict) or not fields:
        raise CardError("no fields")
    if reserved := set(fields) & {"id", "component", "accessibility", "type"}:
        raise CardError(f"fields use reserved component metadata: {', '.join(sorted(reserved))}")
    required_raw = raw.get("required", [])
    if not isinstance(required_raw, list) or any(not isinstance(key, str) for key in required_raw):
        raise CardError("required must be a list of field names")
    required = tuple(required_raw)
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
    validate_card_data(fields, required, example, label="example")
    return Card(
        name=name,
        description=description,
        fields=dict(fields),
        required=required,
        layout=_layout(raw.get("layout"), frozenset(components)),
        example=dict(example),
        topic=topic,
        path=path,
        origin=origin,
    )


def validate_card_data(fields: dict, required: tuple[str, ...], data: Any, *, label="data") -> None:
    """Validate instance values or a worked example against the declared fields."""
    schema = {
        "type": "object",
        "properties": fields,
        "required": list(required),
        "additionalProperties": False,
    }
    for keyword in ("$ref", "$dynamicRef"):
        for ref in _values_for(fields, keyword):
            if not isinstance(ref, str) or not ref.startswith("#"):
                raise CardError("fields may reference only local schemas")
    try:
        Draft202012Validator.check_schema(schema)
        Draft202012Validator(schema).validate(data)
    except (SchemaError, ValidationError) as exc:
        at = "/".join(str(part) for part in exc.absolute_path)
        raise CardError(f"{label} {at}: {exc.message}") from exc
    except Unresolvable as exc:
        raise CardError(f"invalid fields reference: {exc}") from exc


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
        try:
            validate_primitive(node)
        except ValueError as exc:
            raise CardError(str(exc)) from exc
        for reference in _references(node):
            if reference not in known:
                raise CardError(f"layout references unknown id {reference!r}")
    _binding_scopes({node["id"]: node for node in nodes})
    return tuple(nodes)


def _values_for(value: Any, key: str):
    """Yield values of a named property throughout a JSON tree."""
    if isinstance(value, dict):
        for name, item in value.items():
            if name == key:
                yield item
            yield from _values_for(item, key)
    elif isinstance(value, list):
        for item in value:
            yield from _values_for(item, key)


def _binding_scopes(nodes: dict[str, dict]) -> None:
    """Check bindings in their inherited repeated-item or Table-template scope."""
    visited: set[tuple[str, bool]] = set()

    def visit(node_id: str, scoped: bool, ancestors: frozenset[str]) -> None:
        if len(ancestors) >= 24:
            raise CardError("layout exceeds the renderer's maximum depth of 24")
        if node_id in ancestors:
            raise CardError(f"cyclic layout reference at {node_id!r}")
        if (node_id, scoped) in visited:
            return
        visited.add((node_id, scoped))
        node = nodes[node_id]
        unscoped_properties = {
            key: value
            for key, value in node.items()
            if not (node["component"] == "Table" and key in {"cells", "key", "win"})
        }
        if not scoped and any(
            isinstance(path, str) and path.startswith(".")
            for path in _values_for(unscoped_properties, "path")
        ):
            raise CardError(f"relative binding outside a template scope in {node_id!r}")
        children = node.get("children")
        repeated = children.get("componentId") if isinstance(children, dict) else None
        templates = {node.get(key) for key in TEMPLATE_IDS.get(node["component"], ())}
        for ref in _references(node):
            visit(ref, scoped or ref == repeated or ref in templates, ancestors | {node_id})

    visit(LAYOUT_ROOT, False, frozenset())
    for node_id in nodes:
        if not any(key[0] == node_id for key in visited):
            visit(node_id, False, frozenset())


def _references(node: dict[str, Any]) -> list[str]:
    """The layout ids one component names: its child, its children, or its templates."""
    children = node.get("children")
    if isinstance(children, list):
        named = [child for child in children if isinstance(child, str)]
    elif isinstance(children, dict) and isinstance(children.get("componentId"), str):
        named = [children["componentId"]]
    else:
        named = []
    return [*named, *(node[key] for key in _id_keys(node) if isinstance(node.get(key), str))]


def _id_keys(node: dict[str, Any]) -> tuple[str, ...]:
    """The keys of one component whose value is a layout id rather than a value."""
    return ("child", *TEMPLATE_IDS.get(str(node.get("component") or ""), ()))


# --- drawing a Card ---------------------------------------------------------


def expand_card_messages(messages: list[Any], cards: dict[str, Card]) -> list[Any]:
    """A2UI messages with every Card instance drawn as primitives, each followed by
    the data-model writes its fields make."""
    if not cards:
        return list(messages)
    drawn: list[Any] = []
    for message in messages:
        update = message.get("updateComponents") if isinstance(message, dict) else None
        if not isinstance(update, dict):
            drawn.append(message)
            continue
        components = update.get("components")
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
    root = root_component(components).get("id")
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


def root_component(components: list[Any]) -> dict[str, Any]:
    """The component a surface is rooted at — the same one every renderer picks."""
    nodes = [component for component in components if isinstance(component, dict)]
    rooted = next((node for node in nodes if node.get("id") == LAYOUT_ROOT), None)
    return rooted or (nodes[0] if nodes else {})


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
    named = _id_keys(node)
    drawn: dict[str, Any] = {}
    for key, value in node.items():
        if key == "id" or (key in named and isinstance(value, str)):
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
