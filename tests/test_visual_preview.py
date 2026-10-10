"""Passive visual previews are disclosed only through the Screen skill."""

import base64
import json

import pytest
from ag2.context import ConversationContext
from ag2.events import ImageInput, TextInput, ToolCallEvent, ToolErrorEvent, ToolResult
from ag2.stream import MemoryStream

from assistant.a2ui import CardCatalog
from assistant.agent import build_skills_plugin, build_skills_runtime
from assistant.card_drafts import CardDrafts
from assistant.events import A2UISurfaceDataUpdated
from assistant.screen_skill import ScreenSkillRuntime
from assistant.skills import SkillStateStore
from assistant.visual_preview import PreviewError, PreviewOptions, VisualPreview
from tests.support.apps import api
from tests.support.cards import card_definition

PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aGmsAAAAASUVORK5CYII="
)


class SnapshotPreview:
    async def render(self, component, data, **options):
        settings = PreviewOptions.model_validate(options)
        return ToolResult(
            ImageInput(data=PNG, media_type="image/png"),
            TextInput(json.dumps({"component": component, "data": data, **settings.model_dump()})),
        )


def script(skill, name, args):
    return ToolCallEvent(
        name="run_skill_script", arguments=json.dumps({"name": skill, "script": name, "args": args})
    )


def screen(instance=""):
    return {
        "kind": "screen",
        "format_version": 1,
        "title": "Preview",
        "layout": [
            {"id": "root", "component": "Grid", "columns": 2, "children": ["card"]},
            {"id": "card", "component": "CardInstance", "path": instance}
            if instance
            else {"id": "card", "component": "Column", "children": []},
        ],
    }


@pytest.mark.parametrize("snapshot", [False, True])
async def test_preview_returns_image_through_only_screens_skill(config, snapshot):
    plugin = build_skills_plugin(
        config, build_skills_runtime(config), snapshot=snapshot, preview=SnapshotPreview()
    )
    names = {tool.name for tool in plugin._tools}
    assert "preview" not in names
    run = next(tool for tool in plugin._tools if tool.name == "run_skill_script")
    context = ConversationContext(stream=MemoryStream())
    args = {"definition": card_definition(), "data": {"title": "Current"}, "width": 360}
    result = await run(script("screens", "preview", args), context)
    assert not isinstance(result, ToolErrorEvent), result
    assert len(result.result.parts) == 2
    image, text = result.result.parts
    assert image.kind == "image"
    assert image.data == PNG
    measured = json.loads(text.content)
    assert measured["data"] == {"title": "Current"}
    assert measured["component"]["component"] == "Card"
    assert measured["width"] == 360
    assert not list(config.workspace_dir.rglob("*.yaml"))
    denied = await run(script("card-author", "preview", args), context)
    assert (
        isinstance(denied, ToolErrorEvent)
        or "requires a durable Chat history" in denied.result.parts[0].content
    )


async def test_disabled_screen_skill_does_not_expose_preview(config):
    SkillStateStore(config.root_dir).set_enabled("screens", False)
    plugin = build_skills_plugin(config, build_skills_runtime(config), preview=SnapshotPreview())
    run = next(tool for tool in plugin._tools if tool.name == "run_skill_script")
    result = await run(
        script("screens", "preview", {"document": screen()}),
        ConversationContext(stream=MemoryStream()),
    )
    assert isinstance(result, ToolErrorEvent)


async def test_screen_candidate_expands_references_without_writing_or_refresh(config):
    config.workspace_dir.mkdir(parents=True, exist_ok=True)
    catalog = CardCatalog(config)
    runtime = ScreenSkillRuntime(config, catalog, preview=SnapshotPreview())
    context = ConversationContext(stream=MemoryStream())
    await runtime.execute(
        "screens",
        "save_instance",
        context,
        {
            "path": "weather.card-instance.yaml",
            "card": "WeatherPanel",
            "fields": catalog.cards()["WeatherPanel"].example,
        },
    )
    before = {path: path.read_bytes() for path in config.workspace_dir.rglob("*") if path.is_file()}
    result = await runtime.execute(
        "screens",
        "preview",
        context,
        {"document": screen("weather.card-instance.yaml"), "width": 960},
    )
    assert isinstance(result, ToolResult), result
    measured = json.loads(result.parts[1].content)
    assert measured["component"]["component"] == "Grid"
    assert any(node["id"].startswith("card/") for node in measured["component"]["_components"])
    assert measured["data"]["_cards"]["card"]
    assert before == {
        path: path.read_bytes() for path in config.workspace_dir.rglob("*") if path.is_file()
    }
    invalid = await runtime.execute(
        "screens", "preview", context, {"document": screen("../outside.card-instance.yaml")}
    )
    assert isinstance(invalid, str) and invalid.startswith("Preview unavailable:")


async def test_preview_uses_current_draft_edits_without_creating_version(config):
    events = []

    async def history(context):
        return events

    async def commit(context, event):
        events.append(event)

    drafts = CardDrafts(lambda: config, history, commit)
    context = ConversationContext(stream=MemoryStream())
    saved = await drafts.draft(context, card_definition(), {"title": "Old"})
    events.append(A2UISurfaceDataUpdated(saved["surface_id"], data={"title": "Edited"}))
    runtime = ScreenSkillRuntime(
        config, CardCatalog(config), drafts=drafts, preview=SnapshotPreview()
    )
    result = await runtime.execute("screens", "preview", context, {"draft_id": saved["draft_id"]})
    assert isinstance(result, ToolResult), result
    assert json.loads(result.parts[1].content)["data"] == {"title": "Edited"}
    assert len(await drafts.versions(context)) == 1
    missing = await runtime.execute("screens", "preview", context, {"draft_id": "unknown"})
    assert isinstance(missing, str) and "not found" in missing


@pytest.mark.parametrize(
    "values",
    [{"width": 0}, {"width": True}, {"height": 10000}, {"theme": "other"}, {"width": "960"}],
)
async def test_invalid_viewports_fail_before_launch(values):
    with pytest.raises(ValueError):
        await VisualPreview("").render(
            {"id": "root", "component": "Column", "children": []}, {}, **values
        )


async def test_unconfigured_preview_explains_explicit_setting():
    with pytest.raises(PreviewError, match="Settings.*General"):
        await VisualPreview("").render({"id": "root", "component": "Column", "children": []}, {})


def test_browser_setting_is_profile_scoped_and_persisted(profile_app):
    client, pid = profile_app
    assert client.get(api(pid, "/settings")).json()["preview_browser"] == ""
    result = client.post(
        api(pid, "/settings/preview-browser"),
        json={"preview_browser": "/applications/chromium"},
    )
    assert result.status_code == 200, result.text
    assert result.json()["preview_browser"] == "/applications/chromium"
    assert client.get(api(pid, "/settings")).json()["preview_browser"] == "/applications/chromium"
    invalid = client.post(
        api(pid, "/settings/preview-browser"), json={"preview_browser": "chromium"}
    )
    assert invalid.status_code == 400
    cleared = client.post(api(pid, "/settings/preview-browser"), json={"preview_browser": ""})
    assert cleared.json()["preview_browser"] == ""
