"""Progressively disclosed Screen creation over the current Card catalog."""

import uuid
from collections.abc import Sequence
from dataclasses import replace
from datetime import UTC, datetime
from functools import partial
from typing import Any

import yaml
from ag2.context import ConversationContext
from ag2.tools.skills import MemoryRuntime, MemorySkill
from ag2.tools.skills.skill_types import Script, Skill, SkillMetadata

from assistant.a2ui import CATALOG_ID
from assistant.a2ui_skill import _fields_from
from assistant.card_instances import (
    INSTANCE_SUFFIX,
    create_document,
    decode_instance,
    read_document,
    read_instance,
)
from assistant.cards import expand_components, generation_contract, validate_card_data
from assistant.events import CardInstanceReference
from assistant.screens import SCREEN_SUFFIX, decode_screen, expand_screen, list_screens

DESCRIPTION = "Create or rearrange Screens: dashboards of saved Cards outside chats."
INSTRUCTIONS = """Screens are pages in the Screens tab, composed from saved Card instance files.
Read rich-views Card details first. Fetch real initial fields; never invent values.
run_skill_script(name="screens", script="save_instance", args={"path":"weather.card-instance.yaml",
"card":"WeatherPanel", "fields":{...}}) creates an independent source-backed instance.
Choose its _parameters once; later refresh uses its retained source without another Turn.
For existing instances, reuse their Files paths instead of copying or regenerating them.

run_skill_script(name="screens", script="save_screen", args={"path":"morning.screen.yaml",
"document":{"kind":"screen","format_version":1,"title":"Morning","layout":[
{"id":"root","component":"Column","children":["weather"]},
{"id":"weather","component":"CardInstance","path":"weather.card-instance.yaml"}]}})
creates one Screen. Each CardInstance is a reference, never inline Card data or a catalog name.
Layouts use the Card vocabulary (Column, Row, Text, etc.); relative ./ bindings are permitted
only within repeated templates. Read card-author's vocabulary when choosing primitives.
When editing table density or column widths, read card-author's current tables.md. Keep a
Table with explicit columnWidth bindings; independent Rows do not share aligned columns.
Paths are relative to the Profile's Files root, including references in nested directories.
To show the SAME file-backed instance in Chat, use show_instance with its path. This is an
explicit reference; ordinary drawn Chat Cards and Save instance copies remain independent.
Create parent directories with file tools first. Save scripts never overwrite existing files.
Use list_screens and read_screen to discover current Screens. Rearrange an existing Screen
by editing only its layout through the ordinary Files tools; keep its instance references.
Renaming an instance requires updating its references. Missing or invalid references report
an error; repair the file rather than substituting another Card. Buttons in Screens are passive
except source Refresh and approval controls. Drag/write-back boards are a separate feature.
"""


class _ScreenSkill(MemorySkill):
    @property
    def descriptor(self) -> Skill:
        return replace(
            super().descriptor,
            scripts=tuple(
                Script(name=name)
                for name in (
                    "save_instance",
                    "save_screen",
                    "list_screens",
                    "read_screen",
                    "show_instance",
                )
            ),
        )


def screen_skill_descriptor() -> Skill:
    """The Bundled Screen Skill entry in Settings."""
    return Skill(metadata=SkillMetadata(name="screens", description=DESCRIPTION))


class ScreenSkillRuntime(MemoryRuntime):
    """Create independent instance files and Screen layouts through structured scripts."""

    def __init__(self, config, catalog):
        super().__init__(
            _ScreenSkill(name="screens", description=DESCRIPTION, instructions=INSTRUCTIONS)
        )
        self.root = config.workspace_dir
        self.catalog = catalog

    async def read(self, name: str, context: ConversationContext) -> str:
        if name != "screens":
            return await super().read(name, context)
        return f'<skill_content name="screens">\n{INSTRUCTIONS.strip()}\n</skill_content>'

    async def execute(
        self,
        name: str,
        script: str,
        context: ConversationContext,
        args: dict[str, Any] | Sequence[str] | None = None,
    ) -> str:
        if name != "screens":
            return await super().execute(name, script, context, args)
        values = _fields_from(args)
        if values is None:
            raise ValueError("Pass named script arguments as an object")
        if script == "list_screens":
            return list_screens(self.root).model_dump_json()
        if script == "read_screen":
            return read_document(self.root, values["path"], SCREEN_SUFFIX).decode("utf-8")
        if script == "show_instance":
            read_instance(self.root, values["path"])
            await context.send(
                CardInstanceReference("file:" + values["path"], file_path=values["path"])
            )
            return f"Shown a reference to {values['path']} in Chat."
        if script == "save_screen":
            encoded = yaml.safe_dump(values["document"], sort_keys=False).encode()
            document = decode_screen(encoded)
            expand_screen(document, values["path"], partial(read_instance, self.root))
            create_document(self.root, values["path"], SCREEN_SUFFIX, encoded)
            return f"Saved Screen {values['path']}. Open it from Screens."
        if script == "save_instance":
            card = self.catalog.cards().get(values["card"])
            if card is None:
                raise ValueError("Card is unavailable in this Profile")
            fields = values["fields"]
            contract, required = generation_contract(card)
            validate_card_data(contract, tuple(required), fields)
            nodes, writes = expand_components(
                [{**fields, "id": "root", "component": card.name}],
                {card.name: card},
                capture_sources=True,
            )
            data: dict[str, Any] = {}
            for pointer, value in writes:
                parts = [
                    part.replace("~1", "/").replace("~0", "~") for part in pointer[1:].split("/")
                ]
                parent = data
                for part in parts[:-1]:
                    parent = parent.setdefault(part, {})
                parent[parts[-1]] = value
            identity = str(uuid.uuid4())
            envelope = {
                "kind": "card-instance",
                "format_version": 1,
                "instance_id": identity,
                "saved_at": datetime.now(UTC).isoformat(),
                "save_request_id": identity,
                "request_hash": "0" * 64,
                "message": {
                    "surface_id": identity,
                    "version": "v1.0",
                    "catalog_id": CATALOG_ID,
                    "title": card.name,
                    "intent": "",
                    "data": data,
                    "component": {
                        **next(node for node in nodes if node["id"] == "root"),
                        "_components": nodes,
                    },
                },
            }
            encoded = yaml.safe_dump(envelope, sort_keys=False).encode()
            decode_instance(encoded)
            create_document(self.root, values["path"], INSTANCE_SUFFIX, encoded)
            return f"Saved instance {values['path']}. Reference this file in the Screen."
        raise FileNotFoundError(f"Unknown Screen script {script!r}")
