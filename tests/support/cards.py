"""Reading the A2UI Skill the way the agent does, and writing a Card file to disk.

``skill_body`` and ``card_detail`` go through the same filtered view the agent's
plugin is built from, so what a test reads is what a turn would be handed.
"""

import json
from pathlib import Path

import yaml
from ag2.a2ui import A2UIMessageEvent
from ag2.context import ConversationContext
from ag2.events import ToolCallEvent
from ag2.stream import MemoryStream

from assistant.a2ui_skill import A2UI_SKILL, PROTOCOL_RESOURCE, card_resource
from assistant.agent import resolve_a2ui_skill
from assistant.config import Config
from assistant.skills import FilteredSkillRuntime
from tests.support.apps import api

# One minimal Card: a title drawn in a Card, which is the smallest file that loads.
CARD = """
name: {name}
description: {description}{topic}
fields:
  title: {{type: string}}
required: [title]
layout:
  - {{id: root, component: Card, child: head}}
  - {{id: head, component: Text, text: {{path: /title}}}}
example: {{title: {example}}}
"""


def card_definition(name: str = "Shelf") -> dict:
    """A minimal reusable definition with an independently filled title."""
    return yaml.safe_load(CARD.format(name=name, description="Books", topic="", example="Books"))


def write_card(
    directory: Path,
    name: str,
    description: str = "Use when the user asks what is on their shelf.",
    example: str = "Paperbacks",
    topic: str | None = None,
) -> Path:
    """Write one Card file into ``directory``, creating the directory if needed."""
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{name.lower()}.card.yaml"
    line = "" if topic is None else f"\ntopic: {topic}"
    path.write_text(CARD.format(name=name, description=description, example=example, topic=line))
    return path


def a2ui_view(config: Config) -> FilteredSkillRuntime:
    """The A2UI Skill view one profile's agent reads through."""
    return resolve_a2ui_skill(config)


def context() -> ConversationContext:
    """A conversation context to read a skill with — a skill read is context-aware."""
    return ConversationContext(stream=MemoryStream(id="cards"))


async def skill_body(config: Config, view: FilteredSkillRuntime | None = None) -> str:
    """The A2UI Skill's body: the rules plus the index of the available Cards."""
    return await (view or a2ui_view(config)).read(A2UI_SKILL, context())


async def card_detail(config: Config, name: str, view: FilteredSkillRuntime | None = None) -> str:
    """One Card's own detail resource: its schema and its worked example."""
    return await (view or a2ui_view(config)).read_resource(
        A2UI_SKILL, card_resource(name), context()
    )


async def protocol(config: Config, view: FilteredSkillRuntime | None = None) -> str:
    """The A2UI protocol reference: composing views and drawing from the basic components."""
    return await (view or a2ui_view(config)).read_resource(A2UI_SKILL, PROTOCOL_RESOURCE, context())


async def draw(
    config: Config, card: str, args, view: FilteredSkillRuntime | None = None
) -> tuple[str, list[dict]]:
    """Run a Card's drawing script the way the agent does; its reply and the A2UI
    messages it published."""
    ctx = context()
    published: list[dict] = []

    async def collect(event) -> None:
        if isinstance(event, A2UIMessageEvent):
            published.append(event.message)

    ctx.stream.subscribe(collect)
    reply = await (view or a2ui_view(config)).execute(A2UI_SKILL, card, ctx, args)
    return reply, published


def author(script, args):
    return ToolCallEvent(
        name="run_skill_script",
        arguments=json.dumps(
            {
                "name": "card-author",
                "script": script,
                "args": args,
            }
        ),
    )


def replay(client, pid, chat="drafts"):
    events = []
    with client.websocket_connect(api(pid, f"/stream?chat={chat}")) as ws:
        while True:
            frame = ws.receive_json()
            if frame.get("type") == "ready":
                return events
            if "event" in frame:
                events.append(frame["event"])


def send(client, pid, text="Draw", chat="drafts"):
    result = client.post(api(pid, "/message"), json={"text": text, "chat_id": chat})
    assert result.status_code == 200, result.text


def snapshot_message(source: dict) -> dict:
    """The rendering fields of a replayed message, with draft metadata omitted."""
    return {
        key: source[key]
        for key in ("version", "catalog_id", "component", "data", "title", "intent")
    }
