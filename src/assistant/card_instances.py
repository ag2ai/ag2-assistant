"""Validated, independent message files and create-only Save receipts."""

import asyncio
import hashlib
import json
import math
import os
import stat
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

import yaml
from pydantic import ValidationError

from assistant.a2ui import CARD_VOCABULARY
from assistant.card_sources.schema import sources_from
from assistant.cards import CardError, validate_layout
from assistant.events import A2UISurface, CardInstanceSaved
from assistant.gateway.schemas.card_instance import CardInstanceMessage, CardInstanceResponse
from assistant.workspace import _MAX_WRITE_BYTES, list_files

INSTANCE_SUFFIX = ".card-instance.yaml"


class InstanceError(ValueError):
    """An invalid instance document, destination, source or Save retry."""

    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def check_json(value) -> None:
    """Bound JSON-compatible trees before encoding or validating them."""
    pending = [(value, 0)]
    size = 0
    count = 0
    while pending:
        item, depth = pending.pop()
        count += 1
        if depth > 64 or count > 100_000:
            raise InstanceError("Document is too deeply nested or has too many values")
        if isinstance(item, dict):
            if any(not isinstance(key, str) for key in item):
                raise InstanceError("JSON object keys must be strings")
            size += sum(len(key.encode()) for key in item)
            pending.extend((entry, depth + 1) for entry in item.values())
        elif isinstance(item, list):
            pending.extend((entry, depth + 1) for entry in item)
        elif isinstance(item, str):
            size += len(item.encode())
        elif item is None or isinstance(item, (bool, int)):
            size += 8
        elif isinstance(item, float) and math.isfinite(item):
            size += 8
        else:
            raise InstanceError("Document must contain only JSON-compatible values")
        if size > _MAX_WRITE_BYTES:
            raise InstanceError("File too large", 413)


def validate_message(raw: dict, instance_id: str) -> CardInstanceMessage:
    """Normalize one expanded primitive tree, without a Card catalog lookup."""
    check_json(raw)
    try:
        sources_from(raw.get("data", {}))
    except (ValueError, AttributeError) as exc:
        raise InstanceError(f"Invalid source metadata: {exc}") from exc
    allowed = {
        "surface_id",
        "version",
        "catalog_id",
        "component",
        "components",
        "data",
        "title",
        "intent",
    }
    if set(raw) - allowed:
        raise InstanceError("Message contains unsupported fields")
    component = raw.get("component")
    if not isinstance(component, dict) or not isinstance(component.get("id"), str):
        raise InstanceError("Message has no renderable root")
    nodes = raw.get("components", component.get("_components", [component]))
    if not isinstance(nodes, list):
        raise InstanceError("Components must be a list")
    nodes = [
        {key: value for key, value in node.items() if key != "_components"}
        if isinstance(node, dict)
        else node
        for node in nodes
    ]
    try:
        checked = validate_layout(nodes, CARD_VOCABULARY, root_id=component["id"])
        root = next(node for node in checked if node["id"] == component["id"])
        if root != {key: value for key, value in component.items() if key != "_components"}:
            raise InstanceError("Root does not match its component tree")
        return CardInstanceMessage.model_validate(
            {
                **{key: value for key, value in raw.items() if key != "components"},
                "surface_id": instance_id,
                "component": {**root, "_components": list(checked)},
            },
            strict=True,
        )
    except (CardError, ValidationError) as exc:
        raise InstanceError(str(exc)) from exc


def decode_instance(data: bytes) -> CardInstanceResponse:
    """Safely decode and validate a bounded UTF-8 instance document."""
    if len(data) > _MAX_WRITE_BYTES:
        raise InstanceError("File too large", 413)
    try:
        raw = yaml.safe_load(data.decode("utf-8"))
        check_json(raw)
        if not isinstance(raw, dict) or raw.get("kind") != "card-instance":
            raise InstanceError("This file is not a Card instance")
        if type(raw.get("format_version")) is not int or raw["format_version"] != 1:
            raise InstanceError("Unsupported Card instance format_version")
        envelope = CardInstanceResponse.model_validate(raw, strict=True)
        uuid.UUID(envelope.instance_id)
        saved_at = datetime.fromisoformat(envelope.saved_at)
        offset = saved_at.utcoffset()
        if offset is None or offset.total_seconds() != 0:
            raise InstanceError("saved_at must be a UTC timestamp")
        if not envelope.save_request_id or len(envelope.save_request_id) > 200:
            raise InstanceError("Invalid save_request_id")
        if len(envelope.request_hash) != 64 or any(
            c not in "0123456789abcdef" for c in envelope.request_hash
        ):
            raise InstanceError("Invalid request_hash")
        if envelope.message.surface_id != envelope.instance_id:
            raise InstanceError("Message identity does not match instance_id")
        envelope.message = validate_message(envelope.message.model_dump(), envelope.instance_id)
        return envelope
    except (UnicodeError, yaml.YAMLError, ValidationError, ValueError, RecursionError) as exc:
        if isinstance(exc, InstanceError):
            raise
        raise InstanceError(f"Invalid Card instance: {exc}") from exc


@contextmanager
def document_location(
    root: str | os.PathLike[str], rel: str, suffix: str
) -> Iterator[tuple[int, str]]:
    """Open an existing Files Directory without following links or escaping its root."""
    parts = rel.split("/")
    if (
        not rel.endswith(suffix)
        or any(part in {"", ".", ".."} for part in parts)
        or any("\\" in part or "\x00" in part for part in parts)
    ):
        raise InstanceError(f"Use a Files relative path ending {suffix}")
    fd = os.open(Path(root).resolve(), os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in parts[:-1]:
            try:
                child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            except OSError as exc:
                raise InstanceError("Destination Directory is missing or is a link", 400) from exc
            os.close(fd)
            fd = child
        yield fd, parts[-1]
    finally:
        os.close(fd)


@contextmanager
def instance_location(root: str | os.PathLike[str], rel: str) -> Iterator[tuple[int, str]]:
    """Open the Directory of an instance within this Profile's Files space."""
    with document_location(root, rel, INSTANCE_SUFFIX) as location:
        yield location


def _read_at(directory: int, filename: str) -> bytes:
    """Read a regular file with a hard byte cap and no followed links."""
    try:
        fd = os.open(filename, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
    except FileNotFoundError as exc:
        raise InstanceError("File not found", 404) from exc
    except OSError as exc:
        raise InstanceError("Instance path is not a readable regular file") from exc
    if not stat.S_ISREG(os.fstat(fd).st_mode):
        os.close(fd)
        raise InstanceError("Instance path is not a regular file")
    with os.fdopen(fd, "rb") as source:
        data = source.read(_MAX_WRITE_BYTES + 1)
    if len(data) > _MAX_WRITE_BYTES:
        raise InstanceError("File too large", 413)
    return data


def read_instance(root: str | os.PathLike[str], rel: str) -> CardInstanceResponse:
    """Read a portable instance from this Profile's Files space."""
    with instance_location(root, rel) as (directory, filename):
        return decode_instance(_read_at(directory, filename))


def read_document(root: str | os.PathLike[str], rel: str, suffix: str) -> bytes:
    """Read a bounded regular document without following any path links."""
    with document_location(root, rel, suffix) as (directory, filename):
        return _read_at(directory, filename)


def _create_at(directory: int, filename: str, data: bytes) -> None:
    """Publish complete bytes atomically without replacing an occupied destination."""
    temporary = f".instance-{uuid.uuid4().hex}"
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600, dir_fd=directory)
    try:
        with os.fdopen(fd, "wb") as target:
            target.write(data)
            target.flush()
            os.fsync(target.fileno())
        os.link(
            temporary, filename, src_dir_fd=directory, dst_dir_fd=directory, follow_symlinks=False
        )
    finally:
        os.unlink(temporary, dir_fd=directory)


def create_document(root: str | os.PathLike[str], path: str, suffix: str, data: bytes) -> None:
    """Create a complete document within Files without overwriting an existing file."""
    if len(data) > _MAX_WRITE_BYTES:
        raise InstanceError("File too large", 413)
    with document_location(root, path, suffix) as (directory, filename):
        try:
            _create_at(directory, filename, data)
        except FileExistsError as exc:
            raise InstanceError("Destination already exists", 409) from exc


class CardInstances:
    """Serialize explicit Save operations with receipts retained in files and Chat history."""

    def __init__(self, config: Callable, history: Callable, commit: Callable):
        self.config = config
        self.history = history
        self.commit = commit
        self.lock = asyncio.Lock()

    async def save(
        self, context, surface_id: str, message: dict, path: str, request_id: str
    ) -> dict:
        check_json(message)
        request_hash = hashlib.sha256(
            json.dumps(
                [str(context.stream.id), surface_id, message, path], sort_keys=True, allow_nan=False
            ).encode()
        ).hexdigest()
        normalized = validate_message(message, str(uuid.uuid4()))
        async with self.lock:
            history = await self.history(context)
            if not any(
                isinstance(event, A2UISurface)
                and event.surface_id == surface_id
                and event.component
                for event in history
            ):
                raise InstanceError("Rendered source not found in this Chat", 404)
            notices = [
                event
                for event in history
                if isinstance(event, CardInstanceSaved) and event.request_id == request_id
            ]
            if any(event.request_hash != request_hash for event in notices):
                raise InstanceError("Save request_id was already used for different input", 409)
            root = self.config().workspace_dir
            try:
                envelope = read_instance(root, path)
            except InstanceError:
                envelope = None
            if envelope is not None:
                if envelope.save_request_id != request_id:
                    envelope = None
                elif envelope.request_hash != request_hash:
                    raise InstanceError("Save request_id was already used for different input", 409)
            for row in list_files(root) if envelope is None else []:
                if not row["path"].endswith(INSTANCE_SUFFIX):
                    continue
                try:
                    candidate = read_instance(root, row["path"])
                except InstanceError:
                    continue
                if candidate.save_request_id == request_id:
                    if candidate.request_hash != request_hash or row["path"] != path:
                        raise InstanceError(
                            "Save request_id was already used for another copy", 409
                        )
                    envelope = candidate
                    break
            with instance_location(root, path) as (directory, filename):
                if envelope is None:
                    if notices:
                        raise InstanceError(
                            "The saved copy was moved, deleted or damaged; use a new Save", 409
                        )
                    saved_at = datetime.now(UTC).isoformat()
                    envelope = CardInstanceResponse(
                        kind="card-instance",
                        format_version=1,
                        instance_id=normalized.surface_id,
                        saved_at=saved_at,
                        save_request_id=request_id,
                        request_hash=request_hash,
                        message=normalized,
                    )
                    content = yaml.safe_dump(
                        envelope.model_dump(), sort_keys=False, allow_unicode=True
                    ).encode()
                    if len(content) > _MAX_WRITE_BYTES:
                        raise InstanceError("File too large", 413)
                    try:
                        _create_at(directory, filename, content)
                    except FileExistsError as exc:
                        raise InstanceError(
                            "Destination is occupied. Choose a different filename.", 409
                        ) from exc
            recorded = bool(notices)
            feedback = f"Saved to {path}"
            if not recorded:
                try:
                    await self.commit(
                        context,
                        CardInstanceSaved(
                            surface_id,
                            path=path,
                            instance_id=envelope.instance_id,
                            saved_at=envelope.saved_at,
                            request_id=request_id,
                            request_hash=request_hash,
                        ),
                    )
                    recorded = True
                except Exception:
                    feedback += (
                        "; Chat feedback could not be recorded. Retry this Save to record it."
                    )
            return {
                "status": "saved",
                "path": path,
                "instance_id": envelope.instance_id,
                "saved_at": envelope.saved_at,
                "history_recorded": recorded,
                "message": feedback,
            }
