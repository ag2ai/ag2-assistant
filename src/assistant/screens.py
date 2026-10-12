"""Screens compose independent instance files into one primitive surface."""

import hashlib
import os
from collections.abc import Callable
from pathlib import Path

import yaml
from pydantic import ValidationError

from assistant.a2ui import CARD_VOCABULARY, CATALOG_ID
from assistant.card_instances import InstanceError, check_json, read_document
from assistant.card_sources.schema import sources_from
from assistant.cards import CardError, namespace_components, validate_layout
from assistant.gateway.schemas.card_instance import CardInstanceMessage, CardInstanceResponse
from assistant.gateway.schemas.screen import (
    ScreenDocument,
    ScreenListResponse,
    ScreenResponse,
    ScreenRow,
    ScreenSource,
)
from assistant.workspace import _MAX_WRITE_BYTES, list_files, write_text

SCREEN_SUFFIX = ".screen.yaml"


def decode_screen(data: bytes) -> ScreenDocument:
    """Validate a bounded Screen layout with file references as leaf components."""
    if len(data) > _MAX_WRITE_BYTES:
        raise InstanceError("Screen is too large", 413)
    try:
        raw = yaml.safe_load(data.decode("utf-8"))
        check_json(raw)
        if not isinstance(raw, dict) or type(raw.get("format_version")) is not int:
            raise InstanceError("Screen format_version must be the integer 1")
        document = ScreenDocument.model_validate(raw, strict=True)
        if not document.title.strip() or len(document.title) > 200:
            raise InstanceError("Screen needs a title of at most 200 characters")
        layout = []
        references = 0
        for node in document.layout:
            if not isinstance(node.get("id"), str) or not isinstance(node.get("component"), str):
                raise InstanceError("Screen component ids and names must be strings")
            if node.get("component") == "CardInstance":
                references += 1
                if set(node) != {"id", "component", "path"} or not isinstance(node["path"], str):
                    raise InstanceError("CardInstance needs only id, component and path")
                layout.append({"id": node["id"], "component": "Column", "children": []})
            else:
                layout.append(node)
        if references > 32:
            raise InstanceError("A Screen may reference at most 32 instances")
        checked = validate_layout(layout, CARD_VOCABULARY)
        document.layout = [
            {**original, "id": normalized["id"], "component": original["component"].strip()}
            for original, normalized in zip(document.layout, checked, strict=True)
        ]
        return document
    except (UnicodeError, yaml.YAMLError, ValidationError, CardError, RecursionError) as exc:
        raise InstanceError(f"Invalid Screen: {exc}") from exc


def list_screens(root: str | os.PathLike[str]) -> ScreenListResponse:
    """Discover Screens in Files, including malformed documents for repair."""
    rows = []
    for entry in list_files(root):
        path = entry["path"]
        if not path.endswith(SCREEN_SUFFIX):
            continue
        try:
            document = decode_screen(read_document(root, path, SCREEN_SUFFIX))
            rows.append(ScreenRow(path=path, title=document.title, error=""))
        except (InstanceError, OSError, ValueError) as exc:
            rows.append(ScreenRow(path=path, title=Path(path).name, error=str(exc)))
    return ScreenListResponse(screens=rows)


def load_screen(root: str | os.PathLike[str], path: str, instances) -> ScreenResponse:
    """Expand references from the current Profile without catalog or Chat dependencies."""
    document = decode_screen(read_document(root, path, SCREEN_SUFFIX))
    return expand_screen(document, path, instances.file_instance)


def rename_screen(root: str | os.PathLike[str], path: str, title: str) -> ScreenRow:
    """Persist a Screen title while preserving its layout and referenced files."""
    title = title.strip()
    if not title or len(title) > 200:
        raise InstanceError("Screen needs a title of at most 200 characters")
    original = read_document(root, path, SCREEN_SUFFIX)
    decode_screen(original)
    document = yaml.safe_load(original)
    document["title"] = title
    content = yaml.safe_dump(document, allow_unicode=True, sort_keys=False)
    decode_screen(content.encode("utf-8"))
    status, _ = write_text(root, path, content, base_token=hashlib.sha256(original).hexdigest())
    if status != "ok":
        errors = {
            "conflict": ("Screen changed; try renaming it again", 409),
            "not_found": ("Screen no longer exists", 404),
            "too_large": ("Screen is too large", 413),
            "invalid": ("Screen could not be renamed", 400),
        }
        message, code = errors[status]
        raise InstanceError(message, code)
    return ScreenRow(path=path, title=title, error="")


def expand_screen(
    document: ScreenDocument, path: str, read: Callable[[str], CardInstanceResponse]
) -> ScreenResponse:
    """Compose one validated document with the retained instance messages it names."""
    nodes = []
    data: dict = {"_cards": {}}
    sources = {}
    envelopes = {}
    for node in document.layout:
        if node["component"] != "CardInstance":
            nodes.append(node)
            continue
        slot, target = node["id"], node["path"]
        if target not in envelopes:
            envelopes[target] = read(target)
        envelope = envelopes[target]
        message = envelope.message
        original = message.component.get("_components", [message.component])
        original = [
            {key: value for key, value in item.items() if key != "_components"} for item in original
        ]
        ids = {item["id"]: f"{slot}/{item['id']}" for item in original}
        ids[message.component["id"]] = slot
        prefix = "/_cards/" + slot.replace("~", "~0").replace("/", "~1")
        nodes.extend(namespace_components(original, ids, prefix))
        data["_cards"][slot] = message.data
        for source_id, source in sources_from(message.data).items():
            if source_id not in ids:
                raise InstanceError(f"Source {source_id!r} has no component in {target!r}")
            sources[ids[source_id]] = ScreenSource(
                slot=slot,
                path=target,
                surface_id=message.surface_id,
                source_id=source_id,
                source=source.model_dump(),
            )
    try:
        checked = validate_layout(nodes, CARD_VOCABULARY)
        check_json({"layout": nodes, "data": data})
    except CardError as exc:
        raise InstanceError(f"Invalid expanded Screen: {exc}") from exc
    component = next(node for node in checked if node["id"] == "root")
    return ScreenResponse(
        path=path,
        title=document.title,
        sources=sources,
        message=CardInstanceMessage(
            surface_id=path,
            version="v1.0",
            catalog_id=CATALOG_ID,
            component={**component, "_components": list(checked)},
            data=data,
            title=document.title,
            intent="",
        ),
    )
