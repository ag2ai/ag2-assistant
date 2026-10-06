"""Profile-owned source refresh and application-managed execution consent."""

import asyncio
import copy
import hashlib
import inspect
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import yaml
from ag2.context import ConversationContext
from ag2.stream import MemoryStream

from assistant.card_instances import InstanceError, check_json, read_instance
from assistant.card_sources.runtime import execute_source
from assistant.card_sources.schema import (
    MAX_OUTPUT,
    SOURCE_KEY,
    TIMEOUT,
    bind_arguments,
    sources_from,
)
from assistant.cards import validate_card_data
from assistant.events import A2UISurface, A2UISurfaceDataUpdated, CardSourceUpdated
from assistant.secrets import SecretStore
from assistant.tools.finance import get_quotes
from assistant.tools.weather import get_weather
from assistant.workspace import etag_for_path, write_text


async def weather(**args):
    return await asyncio.to_thread(get_weather.model.call, **args)


async def quotes(**args):
    return await asyncio.to_thread(get_quotes.model.call, **args)


class CardSources:
    """Refresh each authorized object independently, coalescing overlapping source calls."""

    def __init__(self, config: Callable, gateway):
        self.config = config
        self.gateway = gateway
        self.tools: dict[str, Callable] = {"get_weather": weather, "get_quotes": quotes}
        self.executor: Callable | None = None
        self.pending: dict[tuple, asyncio.Task] = {}
        self.overlays: dict[str, tuple[str, dict]] = {}

    def _consent_path(self) -> Path:
        return self.config().data_dir / "card-source-consent.json"

    def _consent(self) -> dict:
        try:
            return json.loads(self._consent_path().read_text())
        except FileNotFoundError:
            return {}

    def _file_revision(self, path: str) -> str:
        target = Path(self.config().workspace_dir) / path
        stat = target.stat()
        return f"{stat.st_dev}:{stat.st_ino}:{stat.st_mtime_ns}:{etag_for_path(target)}"

    def file_instance(self, path: str):
        envelope = read_instance(self.config().workspace_dir, path)
        cached = self.overlays.get(path)
        if cached and cached[0] == self._file_revision(path):
            envelope.message.data = copy.deepcopy(cached[1])
        return envelope

    async def _target(self, *, path="", chat_id="", surface_id="") -> tuple[dict, str, str]:
        if bool(path) == bool(chat_id and surface_id) or (path and (chat_id or surface_id)):
            raise InstanceError("Select one file or Chat surface")
        if path:
            envelope = self.file_instance(path)
            return envelope.message.data, self._file_revision(path), envelope.message.surface_id
        if not any(row["chat_id"] == chat_id for row in await self.gateway.list_chats()):
            raise InstanceError("Chat not found", 404)
        stream = await self.gateway.stream_for(chat_id)
        data = None
        authored = None
        for event in await stream.history.get_events():
            if isinstance(event, A2UISurface) and event.surface_id == surface_id:
                data = copy.deepcopy(event.data)
                authored = event.to_dict()
            elif isinstance(event, A2UISurfaceDataUpdated) and event.surface_id == surface_id:
                data = copy.deepcopy(event.data)
                authored = event.to_dict()
            elif (
                isinstance(event, CardSourceUpdated)
                and not event.path
                and event.surface_id == surface_id
                and event.status in {"updated", "configured"}
            ):
                data = copy.deepcopy(event.data)
                if event.status == "configured":
                    authored = event.to_dict()
        if data is None:
            raise InstanceError("Card instance not found in this Chat", 404)
        revision = hashlib.sha256(json.dumps(authored, sort_keys=True).encode()).hexdigest()
        return data, revision, surface_id

    async def _publish(self, event, chat_id, emit) -> dict:
        if chat_id and any(row["chat_id"] == chat_id for row in await self.gateway.list_chats()):
            await emit(chat_id, event)
        else:
            await ConversationContext(stream=MemoryStream()).send(event)
        return {
            "status": event.status,
            "events": [
                {"type": f"assistant.events.{type(event).__name__}", "data": event.to_dict()}
            ],
        }

    async def refresh(self, source_id: str, **target) -> dict:
        data, revision, surface = await self._target(**target)
        source = sources_from(data).get(source_id)
        if source is None:
            raise InstanceError("Source not found on this instance", 404)
        key = (target.get("path", ""), target.get("chat_id", ""), surface, source_id, revision)
        if key not in self.pending:
            self.pending[key] = asyncio.create_task(
                self._refresh(source_id, source, data, revision, surface, target)
            )
            self.pending[key].add_done_callback(lambda done: self.pending.pop(key, None))
        task = self.pending[key]
        try:
            return await asyncio.shield(task)
        finally:
            if task.done() and self.pending.get(key) is task:
                self.pending.pop(key)

    async def _refresh(self, source_id, source, data, revision, surface, target):
        status = "updated"
        error = ""
        result_data = data
        keys = {}
        try:
            arguments = bind_arguments(source.args, source.parameters)
            async with asyncio.timeout(TIMEOUT):
                if source.tool:
                    runner = self.tools.get(source.tool)
                    if runner is None:
                        raise ValueError("Source tool is unavailable")
                    output: Any = runner(**arguments)
                    if inspect.isawaitable(output):
                        output = await output
                else:
                    consent = self._consent().get(source.code_version)
                    if consent is None:
                        status = "approval_required"
                        raise ValueError("Approve this code version before execution")
                    for name in source.secret_names:
                        identity = source.secrets.get(name)
                        if not identity or identity not in consent.get("secrets", {}).get(name, []):
                            raise ValueError(f"Select and authorize Secret {name} for this Profile")
                        value = SecretStore(self.config().paths).secret_value(identity)
                        if not value:
                            raise ValueError(f"Selected Secret {name} is missing")
                        keys[name] = value
                    output = await (self.executor or execute_source)(
                        self.config(), source, arguments, keys
                    )
                if not isinstance(output, str) or len(output.encode()) > MAX_OUTPUT:
                    raise ValueError("Source output exceeds its JSON size limit")
                parsed = json.loads(output)
                check_json(parsed)
                parsed = redact(parsed, keys)
                validate_card_data(
                    source.fields, tuple(source.required), parsed, label="source result"
                )
        except Exception as exc:
            if status != "approval_required":
                status = "error"
            error = (
                "Source execution timed out"
                if isinstance(exc, TimeoutError)
                else redact(str(exc), keys)[:500]
            )
        async with self.gateway.event_transaction() as emit:
            try:
                latest, latest_revision, _ = await self._target(**target)
            except (InstanceError, OSError, ValueError):
                latest_revision = None
            if revision != latest_revision:
                status, error, result_data = "discarded", "Instance changed while refreshing", data
            elif status == "updated":
                if source.code and source.code_version not in self._consent():
                    status, error = "discarded", "Source approval was withdrawn"
                else:
                    result_data = apply_result(latest, source, parsed)
                    if target.get("path"):
                        if len(self.overlays) >= 128:
                            self.overlays.pop(next(iter(self.overlays)))
                        self.overlays[target["path"]] = (revision, copy.deepcopy(result_data))
            elif latest_revision is not None:
                result_data = latest
            event = CardSourceUpdated(
                surface,
                source_id=source_id,
                path=target.get("path", ""),
                status=status,
                error=error,
                data=result_data,
                code_version=source.code_version if source.code else "",
            )
            return await self._publish(event, target.get("chat_id", ""), emit)

    async def approve(self, source_id, code_version, approved, secrets, **target):
        async with self.gateway.event_transaction() as emit:
            data, revision, surface = await self._target(**target)
            source = sources_from(data).get(source_id)
            if source is None or not source.code:
                raise InstanceError("Custom source not found", 404)
            if code_version != source.code_version:
                raise InstanceError("Code version changed; review it again", 409)
            if approved and set(secrets) - set(source.secret_names):
                raise InstanceError("Secret binding is not declared by this source")
            store = SecretStore(self.config().paths)
            if approved and any(not store.secret_value(identity) for identity in secrets.values()):
                raise InstanceError("Selected Secret is missing", 400)
            consent = self._consent()
            if approved:
                record = consent.setdefault(code_version, {"secrets": {}})
                for name, identity in secrets.items():
                    authorized = record["secrets"].setdefault(name, [])
                    if identity not in authorized:
                        authorized.append(identity)
                data = copy.deepcopy(data)
                data[SOURCE_KEY][source_id]["secrets"] = secrets
                if target.get("path"):
                    path = target["path"]
                    envelope = read_instance(self.config().workspace_dir, path)
                    envelope.message.data = data
                    root = self.config().workspace_dir
                    status, _ = write_text(
                        root,
                        path,
                        yaml.safe_dump(envelope.model_dump(), sort_keys=False),
                        base_token=etag_for_path(Path(root) / path),
                    )
                    if status != "ok":
                        raise InstanceError("Instance changed before approval was saved", 409)
                    self.overlays.pop(path, None)
            else:
                consent.pop(code_version, None)
            location = self._consent_path()
            location.parent.mkdir(parents=True, exist_ok=True)
            temporary = location.with_suffix(".tmp")
            temporary.write_text(json.dumps(consent))
            temporary.replace(location)
            return await self._publish(
                CardSourceUpdated(
                    surface,
                    source_id=source_id,
                    path=target.get("path", ""),
                    status="configured",
                    data=data,
                    code_version=code_version,
                ),
                target.get("chat_id", ""),
                emit,
            )

    async def close(self):
        tasks = list(self.pending.values())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self.pending.clear()


def apply_result(data, source, values):
    """Replace source-owned fields while retaining other sources and authored values."""
    updated = copy.deepcopy(data)
    target = updated
    if source.path:
        for part in source.path.lstrip("/").split("/"):
            name = part.replace("~1", "/").replace("~0", "~")
            target = target.setdefault(name, {})
            if not isinstance(target, dict):
                raise InstanceError("Source data target is not an object")
    for name in source.fields:
        target.pop(name, None)
    target.update(values)
    return updated


def redact(value: Any, keys: dict) -> Any:
    """Remove selected Secret values from decoded JSON and diagnostic strings."""
    if isinstance(value, str):
        for secret in keys.values():
            for encoded in (
                secret,
                json.dumps(secret)[1:-1],
                json.dumps(secret, ensure_ascii=False)[1:-1],
                repr(secret)[1:-1],
            ):
                value = value.replace(encoded, "[redacted]")
        return value
    if isinstance(value, list):
        return [redact(item, keys) for item in value]
    if isinstance(value, dict):
        return {redact(name, keys): redact(item, keys) for name, item in value.items()}
    return value
