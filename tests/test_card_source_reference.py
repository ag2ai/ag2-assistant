"""Source guidance is readable through skills and its examples refresh saved Cards."""

import json
import re
import sys
from functools import partial
from pathlib import Path

import pytest
import yaml
from ag2.context import ConversationContext
from ag2.events import ToolCallEvent, ToolErrorEvent
from ag2.stream import MemoryStream

from assistant.a2ui import CardCatalog
from assistant.agent import build_skills_plugin, build_skills_runtime
from assistant.card_instances import decode_instance, read_instance
from assistant.card_sources.runtime import execute_source
from assistant.card_sources.schema import bind_arguments, sources_from
from assistant.cards import validate_card_data
from assistant.config import Config
from assistant.screen_skill import ScreenSkillRuntime
from assistant.screens import decode_screen, expand_screen


async def reference(config, name, resource, snapshot=False):
    plugin = build_skills_plugin(config, build_skills_runtime(config), snapshot=snapshot)
    tool = next(tool for tool in plugin._tools if tool.name == "read_skill_resource")
    result = await tool(
        ToolCallEvent(
            name="read_skill_resource",
            arguments=json.dumps({"name": name, "resource": resource}),
        ),
        ConversationContext(stream=MemoryStream()),
    )
    assert not isinstance(result, ToolErrorEvent)
    return result.result.parts[0].content


@pytest.mark.parametrize("snapshot", [False, True])
@pytest.mark.parametrize(
    ("name", "resource"),
    [
        ("screens", "sources.md"),
        ("rich-views", "reference/sources.md"),
        ("card-author", "sources.md"),
    ],
)
async def test_source_reference_is_readable_through_skill_tools(paths, name, resource, snapshot):
    text = await reference(Config.for_paths(paths), name, resource, snapshot)

    examples = re.findall(r"```yaml\n(.*?)\n```", text, re.DOTALL)
    assert len(examples) == 2
    definition, data = (yaml.safe_load(example) for example in examples)
    assert definition["source"]["code"] == data["_sources"]["root"]["code"]
    assert definition["fields"] == data["_sources"]["root"]["fields"]


async def test_documented_source_can_be_saved_attached_and_executed(paths):
    config = Config.for_paths(paths)
    config.tools.sandbox = "local"
    config.search_path = [Path(sys.executable).parent]
    text = await reference(config, "screens", "sources.md")
    definition, data = (
        yaml.safe_load(example) for example in re.findall(r"```yaml\n(.*?)\n```", text, re.DOTALL)
    )
    cards = config.workspace_dir / "cards"
    cards.mkdir(parents=True)
    (cards / "summary.card.yaml").write_text(yaml.safe_dump(definition))
    input_path = config.workspace_dir / "summary.json"
    input_path.write_text(json.dumps({"summary": "Fresh summary from JSON."}))

    runtime = ScreenSkillRuntime(config, CardCatalog(config))
    await runtime.execute(
        "screens",
        "save_instance",
        ConversationContext(stream=MemoryStream()),
        {
            "path": "summary.card-instance.yaml",
            "card": "FileSummary",
            "fields": {
                "summary": "Initial observed summary.",
                "_parameters": {"input_path": str(input_path)},
            },
        },
    )
    saved = read_instance(config.workspace_dir, "summary.card-instance.yaml")
    assert saved.message.data["summary"] == "Initial observed summary."
    assert sources_from(saved.message.data)["root"].parameters["input_path"] == str(input_path)

    data["_sources"]["root"]["parameters"]["input_path"] = str(input_path)
    document = saved.model_dump()
    document["message"]["data"] = data
    encoded = yaml.safe_dump(document).encode()
    decode_instance(encoded)
    (config.workspace_dir / "summary.card-instance.yaml").write_bytes(encoded)
    screen_document = {
        "kind": "screen",
        "format_version": 1,
        "title": "Summary",
        "layout": [
            {"id": "root", "component": "Column", "children": ["summary_file"]},
            {
                "id": "summary_file",
                "component": "CardInstance",
                "path": "summary.card-instance.yaml",
            },
        ],
    }
    screen = expand_screen(
        decode_screen(yaml.safe_dump(screen_document).encode()),
        "summary.screen.yaml",
        partial(read_instance, config.workspace_dir),
    )
    assert screen.message.data["_cards"]["summary_file"]["summary"] == data["summary"]

    source = sources_from(data)["root"]
    output = await execute_source(
        config, source, bind_arguments(source.args, source.parameters), {}
    )
    values = json.loads(output)
    validate_card_data(source.fields, tuple(source.required), values)
    assert values == {"summary": "Fresh summary from JSON."}
