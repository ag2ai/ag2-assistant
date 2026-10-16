"""Card draft operations over a Chat's durable event history."""

import asyncio
import hashlib
import json
import os
import tempfile
import uuid
from collections.abc import Callable

import yaml
from ag2.context import ConversationContext

from assistant.a2ui import (
    CARD_VOCABULARY,
    _AssistantA2UIRuntime,
    card_layers,
    durable_surfaces_from_messages,
)
from assistant.a2ui_skill import _card_messages
from assistant.cards import (
    CARD_SUFFIX,
    CARDS_DIR,
    FILE_BUDGET,
    CardError,
    expand_card_messages,
    load_cards,
    validate_card,
    validate_card_data,
)
from assistant.events import A2UISurfaceDataUpdated, CardDefinitionSaved, EphemeralCard
from assistant.state_store import ORIGIN_PROFILE
from assistant.workspace import _inside, etag_for_path, invalid_dir_name, slugify, write_text


class DraftError(ValueError):
    """A draft reference or mutation that cannot be applied."""


class CardDrafts:
    """Serialize definition mutations while deriving all draft state from history."""

    def __init__(self, config: Callable, history: Callable, commit: Callable) -> None:
        self.config = config
        self.history = history
        self.commit = commit
        self.lock = asyncio.Lock()

    async def versions(self, context: ConversationContext) -> list[EphemeralCard]:
        return [event for event in await self.history(context) if isinstance(event, EphemeralCard)]

    async def index(self, context: ConversationContext) -> list[dict]:
        current = {event.draft_id: event for event in await self.versions(context)}
        return [
            {
                "draft_id": event.draft_id,
                "version": event.draft_version,
                "surface_id": event.surface_id,
                "name": event.definition["name"],
                "title": event.title,
            }
            for event in current.values()
        ]

    async def read(self, context: ConversationContext, draft_id: str, version: int | None = None):
        matches = [event for event in await self.versions(context) if event.draft_id == draft_id]
        if version is not None:
            matches = [event for event in matches if event.draft_version == version]
        if not matches:
            raise DraftError("Draft/version not found in this Chat")
        event = matches[-1]
        result = event.to_dict()
        for update in await self.history(context):
            if isinstance(update, A2UISurfaceDataUpdated) and update.surface_id == event.surface_id:
                result["data"] = update.data
        return result

    async def current(self, context, draft_id: str, expected_version: int, surface_id=""):
        event = await self.read(context, draft_id)
        if event["draft_version"] != expected_version or (
            surface_id and event["surface_id"] != surface_id
        ):
            raise DraftError("Obsolete draft version; read the current version before retrying")
        return event

    async def draft(
        self,
        context: ConversationContext,
        definition: dict,
        data: dict,
        *,
        draft_id: str = "",
        expected_version: int | None = None,
        title: str = "",
    ) -> dict:
        card = validate_card(definition, CARD_VOCABULARY)
        if card.name in CARD_VOCABULARY:
            raise CardError("Card name must differ from a primitive name")
        validate_card_data(card.fields, card.required, data)
        runtime = _AssistantA2UIRuntime({card.name: card})
        messages = _card_messages(card, data)
        checked = runtime.parser.validate(messages)
        if not checked.is_valid:
            raise CardError("; ".join(checked.errors))
        surface = durable_surfaces_from_messages(expand_card_messages(messages, runtime.cards))[0]
        async with self.lock:
            version = 1
            if draft_id:
                if expected_version is None:
                    raise DraftError("A revision needs its expected_version")
                await self.current(context, draft_id, expected_version)
                version = expected_version + 1
            elif expected_version is not None:
                raise DraftError("expected_version needs a draft_id")
            event = EphemeralCard(
                surface.surface_id,
                component=surface.component,
                data=surface.data,
                title=title or surface.title,
                intent="card-draft",
                draft_id=draft_id or f"draft-{uuid.uuid4().hex}",
                draft_version=version,
                definition=json.loads(json.dumps(definition)),
            )
            await self.commit(context, event)
            return {"draft_id": event.draft_id, "version": version, "surface_id": event.surface_id}

    async def restore(self, context, draft_id: str, version: int, expected_version: int, data=None):
        old = await self.read(context, draft_id, version)
        return await self.draft(
            context,
            old["definition"],
            old["data"] if data is None else data,
            draft_id=draft_id,
            expected_version=expected_version,
            title=old["title"],
        )

    async def save(
        self,
        context: ConversationContext,
        draft_id: str,
        expected_version: int,
        surface_id: str,
        name: str,
        filename: str = "",
        request_id: str = "",
        replace: bool = False,
        conflict_token: str = "",
    ) -> dict:
        if not isinstance(name, str) or not name.strip():
            raise DraftError("Enter a Card name")
        name = name.strip()
        filename = filename or f"{slugify(name, default='card')}{CARD_SUFFIX}"
        if invalid_dir_name(filename) or not filename.endswith(CARD_SUFFIX):
            raise DraftError("Use a safe filename ending .card.yaml")
        if not request_id or len(request_id) > 200:
            raise DraftError(
                "Save needs a stable request_id; reuse it only when retrying that Save"
            )
        async with self.lock:
            event = await self.read(context, draft_id, expected_version)
            if event["surface_id"] != surface_id:
                raise DraftError("Surface does not match this draft version")
            definition = {**event["definition"], "name": name}
            card = validate_card(definition, CARD_VOCABULARY)
            if card.name in CARD_VOCABULARY:
                raise DraftError("Card name must differ from a primitive name")
            config = self.config()
            root = config.workspace_dir.resolve()
            rel = f"{CARDS_DIR}/{filename}"
            path = _inside(root, rel)
            if path is None or path.is_symlink():
                raise DraftError("Card destination escapes this Profile's Files space")
            receipt = hashlib.sha256(
                json.dumps(
                    [
                        str(context.stream.id),
                        draft_id,
                        expected_version,
                        request_id,
                        rel,
                        definition,
                    ],
                    sort_keys=True,
                    ensure_ascii=False,
                ).encode()
            ).hexdigest()
            content = f"# card-author-save: {receipt}\n" + yaml.safe_dump(
                definition,
                sort_keys=False,
                allow_unicode=True,
            )
            if len(content.encode()) > FILE_BUDGET:
                raise CardError(
                    f"Saved definition exceeds {FILE_BUDGET} bytes; simplify the layout"
                )
            retry = path.is_file() and path.read_bytes() == content.encode()
            if not retry:
                await self.current(context, draft_id, expected_version, surface_id)
                conflict, token, profile_match, occupied, base_token = _destination(
                    config, path, rel, name
                )
                if conflict and (not replace or conflict_token != token):
                    message = "Name or filename is occupied. Choose Replace or Save as copy."
                    if profile_match and profile_match != path:
                        message += f" To replace this Profile Card, use {profile_match.name}."
                    return _conflict(name, rel, token, message)
                if profile_match is not None and profile_match != path:
                    raise DraftError(
                        f"Replace this Profile Card at its existing filename: {profile_match.name}"
                    )
                if occupied and not path.is_file():
                    raise DraftError("The destination is not a file")
                path.parent.mkdir(parents=True, exist_ok=True)
                if occupied:
                    status, _ = write_text(root, rel, content, base_token=base_token)
                    if status == "conflict":
                        _, fresh_token, _, _, _ = _destination(config, path, rel, name)
                        return _conflict(
                            name,
                            rel,
                            fresh_token,
                            "Destination changed. Choose Replace or Save as copy again.",
                        )
                    if status != "ok":
                        raise OSError(f"Card write failed: {status}; previous file preserved")
                else:
                    try:
                        _create_atomic(path, content.encode())
                    except FileExistsError:
                        _, fresh_token, _, _, _ = _destination(config, path, rel, name)
                        return _conflict(
                            name,
                            rel,
                            fresh_token,
                            "Destination was created. Choose Replace or Save as copy.",
                        )
            recorded = False
            message = ""
            try:
                recorded = any(
                    isinstance(item, CardDefinitionSaved)
                    and item.request_id == request_id
                    and item.path == rel
                    and item.draft_id == draft_id
                    for item in await self.history(context)
                )
                if not recorded:
                    await self.commit(
                        context,
                        CardDefinitionSaved(
                            draft_id,
                            draft_version=expected_version,
                            name=name,
                            path=rel,
                            request_id=request_id,
                        ),
                    )
                    recorded = True
            except Exception as exc:
                message = f"Saved {rel}; history status could not be recorded: {exc}. Retry the same request to reconcile."
            return {
                "status": "saved",
                "name": name,
                "path": rel,
                "conflict_token": "",
                "history_recorded": recorded,
                "message": message,
            }


def _destination(config, path, rel: str, name: str) -> tuple:
    """Fingerprint name and file collisions in all ordinary Card layers."""
    collisions = []
    profile_match = None
    for origin, directory in card_layers(config):
        for other in load_cards(directory, CARD_VOCABULARY, origin).values():
            if other.name == name and other.path is not None:
                collisions.append([str(other.path), etag_for_path(other.path)])
                if origin == ORIGIN_PROFILE:
                    profile_match = other.path.resolve()
    occupied = path.exists()
    base_token = etag_for_path(path)
    token = hashlib.sha256(
        json.dumps([rel, name, collisions, occupied, base_token], sort_keys=True).encode()
    ).hexdigest()
    return bool(collisions or occupied), token, profile_match, occupied, base_token


def _conflict(name: str, rel: str, token: str, message: str) -> dict:
    """A collision decision bound to the currently observed destination."""
    return {
        "status": "conflict",
        "name": name,
        "path": rel,
        "conflict_token": token,
        "history_recorded": False,
        "message": message,
    }


def _create_atomic(path, data: bytes) -> None:
    """Create a complete file without replacing a destination created concurrently."""
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=".card-")
    try:
        with os.fdopen(fd, "wb") as output:
            output.write(data)
            output.flush()
            os.fsync(output.fileno())
        os.link(temporary, path)
    finally:
        os.unlink(temporary)
