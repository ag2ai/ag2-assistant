"""The independently available Bundled Card author MemorySkill."""

import json
from collections.abc import Callable, Sequence
from dataclasses import replace
from pathlib import Path
from typing import Any

from ag2.context import ConversationContext
from ag2.tools.skills import MemoryRuntime, MemorySkill
from ag2.tools.skills.skill_types import Resource, Script, Skill, SkillMetadata

from assistant.a2ui_skill import _fields_from
from assistant.card_drafts import CardDrafts, DraftError
from assistant.cards import CardError
from assistant.cards.layout import PROPERTIES, primitive_schema

CARD_AUTHOR = "card-author"
DESCRIPTION = "Create and refine a custom interactive Card in this Chat when the user requests a custom view or no available rich view fits. Table supports compact rows and different column widths: read the current tables.md before editing tabular views, including saved Screen instances. Save its reusable definition only on explicit request."
INSTRUCTIONS = """Prefer a suitable available Card from rich-views before authoring a custom one.
Read vocabulary.md and examples.md before authoring. Use the existing primitives, styling
words, bindings and repeated templates; never add CSS, JavaScript or backend handlers.
For sizing or equal columns read layout.md. Use width="content" for a frame fitted to its
contents, width="fill" to fill its container, and Grid for equal responsive columns.
For visual inspection load screens and read its preview.md; preview is a screens script.
For analytics or publication tables read the current tables.md before creating or editing
a view, even when an earlier version of the references is already in Chat history.
Use Table for aligned columns, never independent Row components. Set variant="data",
density="compact" and columnWidth={"path":"./width"}; give each column item a width
token (narrow, regular or wide). Without that binding the columns retain equal widths.
Keep identifiers and previews in separate columns; compose numbers and toned pills.
Gather factual values from the user's context and available tools; leave missing facts
absent and explain gaps. For a requested template only, clearly label example values as
illustrative. Definition.example is a worked example, separate from the current data.

run_skill_script(name="card-author", script="draft_card", args={"definition": {...},
"data": {...}, "title": "..."}) validates and draws in this same Turn. The definition has
name, description, fields (JSON schemas), required, layout, example and optional topic.
Errors draw nothing: repair the actual definition/data and retry. No file is created.

To iterate, read drafts.md for the short index, then read_draft with draft_id and optional
version for the exact definition and current surface data. Target the intended draft from
conversation; ask when ambiguous. draft_card revises it with draft_id and expected_version.
Each draft is independent; revisions append to history and supersede only that draft.
restore_card takes draft_id, version (the earlier version), expected_version (current), and
optional schema-compatible data; it appends a new version. Input edits are instance changes.

Save ONLY on a clear user request: save_card takes draft_id, expected_version, surface_id,
name, filename (a safe basename ending .card.yaml), and request_id (unique to this request).
For ambiguous target/name ask first. A free name saves directly. On conflict ask Replace or
Save as copy: Replace resubmits with replace=true and the returned conflict_token; a copy
needs a distinct name and filename and a new request_id. Never infer overwrite permission
from an earlier save. Current instance data stays in Chat history. Saved definitions become
available on future Turns. Finish with brief orientation; do not restate the entire view.
"""


def card_author_descriptor() -> Skill:
    """The Bundled discovery entry used by Settings."""
    return Skill(metadata=SkillMetadata(name=CARD_AUTHOR, description=DESCRIPTION))


class _CardAuthorSkill(MemorySkill):
    """The author's discoverable resources and structured scripts."""

    @property
    def descriptor(self) -> Skill:
        return replace(
            super().descriptor,
            resources=tuple(
                Resource(name=name)
                for name in ("vocabulary.md", "examples.md", "tables.md", "layout.md", "drafts.md")
            ),
            scripts=tuple(
                Script(name=name)
                for name in ("draft_card", "read_draft", "restore_card", "save_card")
            ),
        )


class CardAuthorRuntime(MemoryRuntime):
    """Progressively disclose authoring references and context-scoped operations."""

    def __init__(self, drafts: CardDrafts | None, catalog) -> None:
        super().__init__(
            _CardAuthorSkill(
                name=CARD_AUTHOR,
                description=DESCRIPTION,
                instructions=INSTRUCTIONS,
            )
        )
        self.drafts = drafts
        self.catalog = catalog

    async def read(self, name: str, context: ConversationContext) -> str:
        return f'<skill_content name="{CARD_AUTHOR}">{INSTRUCTIONS}</skill_content>'

    async def read_resource(self, name: str, resource: str, context: ConversationContext) -> str:
        if resource == "vocabulary.md":
            reference = (Path(__file__).parent / "cards" / "VOCABULARY.md").read_text()
            schemas = {kind: primitive_schema(kind) for kind in PROPERTIES}
            return (
                reference
                + "\n\nValidated primitive properties:\n"
                + json.dumps(schemas, ensure_ascii=False)
            )
        if resource == "examples.md":
            return json.dumps(
                [
                    {
                        "name": card.name,
                        "description": card.description,
                        "fields": card.fields,
                        "required": card.required,
                        "layout": card.layout,
                        "example": card.example,
                    }
                    for card in self.catalog.cards().values()
                    if card.name in {"Checklist", "DecisionMatrix"}
                ],
                ensure_ascii=False,
            )
        if resource == "tables.md":
            return (Path(__file__).parent / "cards" / "TABLES.md").read_text()
        if resource == "layout.md":
            return (Path(__file__).parent / "cards" / "LAYOUT.md").read_text()
        if resource == "drafts.md" and self.drafts is not None:
            return json.dumps(await self.drafts.index(context), ensure_ascii=False)
        raise FileNotFoundError(resource)

    async def execute(
        self,
        name: str,
        script: str,
        context: ConversationContext,
        args: dict[str, Any] | Sequence[str] | None = None,
    ) -> str:
        if self.drafts is None:
            return "Card author requires a durable Chat history."
        values = _fields_from(args)
        if values is None:
            return "Not applied: pass a JSON object of named arguments."
        operations: dict[str, Callable] = {
            "draft_card": self.drafts.draft,
            "read_draft": self.drafts.read,
            "restore_card": self.drafts.restore,
        }
        if script == "save_card":
            operations[script] = self.drafts.save
        operation = operations.get(script)
        if operation is None:
            raise FileNotFoundError(script)
        try:
            result = await operation(context, **values)
            return json.dumps(result, ensure_ascii=False)
        except (CardError, DraftError, TypeError, OSError) as exc:
            return f"Not applied: {exc}"
