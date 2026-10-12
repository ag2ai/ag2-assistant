"""Screens instructions load through the agent's progressively disclosed skill tools."""

import pytest
from ag2.context import ConversationContext
from ag2.events import ToolCallEvent, ToolErrorEvent
from ag2.stream import MemoryStream

from assistant.agent import build_skills_plugin, build_skills_runtime
from assistant.config import Config


@pytest.mark.parametrize("snapshot", [False, True])
async def test_screen_skill_loads_instructions_through_plugin(paths, snapshot):
    config = Config.for_paths(paths)
    plugin = build_skills_plugin(config, build_skills_runtime(config), snapshot=snapshot)
    load = next(tool for tool in plugin._tools if tool.name == "load_skill")

    result = await load(
        ToolCallEvent(name="load_skill", arguments='{"name":"screens"}'),
        ConversationContext(stream=MemoryStream()),
    )

    assert not isinstance(result, ToolErrorEvent)
    body = result.result.parts[0].content
    assert '<skill_content name="screens">' in body
    assert 'script="save_instance"' in body
    assert 'script="save_screen"' in body
    assert "list_screens and read_screen" in body
    assert "show_instance" in body
    assert "</skill_content>" in body
