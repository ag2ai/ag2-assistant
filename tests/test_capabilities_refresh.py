"""Capabilities offered to real AG2 invocations through the Gateway message API."""

import asyncio
import json
import sys
import time
from contextlib import asynccontextmanager

import httpx
import pytest
from ag2 import Agent
from ag2.a2ui import A2UIMessageEvent
from ag2.events import (
    CompactionCompleted,
    ModelMessage,
    ModelMessageChunk,
    ModelResponse,
    ToolCallEvent,
    ToolCallsEvent,
    ToolErrorEvent,
    ToolResultEvent,
)
from ag2.testing import TestClient, TestConfig, TrackingConfig

from assistant.agent import (
    GOOGLE_GUIDANCE,
    agent_session,
    build_skills_plugin,
    build_skills_runtime,
    cheap_model,
    create_agent,
    model_config,
)
from assistant.cards import CARDS_DIR
from assistant.channels.base import InboundMessage
from assistant.channels.router import Reply
from assistant.codex_auth import CodexAuth
from assistant.events import TurnCancelled, TurnFailed
from assistant.gateway.tasks_service import TaskService
from assistant.llm_configs import LlmConfigStore
from assistant.memory import read_profile_sync
from assistant.pairing import PairingStore
from assistant.profiles import ProfileRegistry
from assistant.secrets import SecretStore
from assistant.settings import profile_settings
from assistant.skills import SkillStateStore
from assistant.state_store import SUPPRESS_SHARED
from tests.support.apps import make_manager, real_gateway, write_codex_session
from tests.support.cards import write_card
from tests.support.fakes import (
    ScriptedModels,
    StatefulEnvironment,
    fake_summary_factory,
)
from tests.support.http import client
from tests.support.stubs import acp_counter, mcp_counter, write_skill, write_stub


async def test_existing_chat_discovers_a_skill_on_its_next_turn(config):
    prompts = []

    class Client(TestClient):
        async def __call__(self, messages, context, **kwargs):
            prompts.append("\n".join(context.prompt))
            return await super().__call__(messages, context, **kwargs)

    class RecordingConfig(TestConfig):
        def create(self):
            return Client("done")

    def factory(cfg, **kwargs):
        plugins = (
            []
            if kwargs.get("invocation_only")
            else [build_skills_plugin(cfg, build_skills_runtime(cfg))]
        )
        return Agent("refresh-test", config=RecordingConfig(), plugins=plugins)

    gateway = real_gateway(
        config,
        memory=False,
        onboard=False,
        agent_factory=factory,
        model_factory=lambda cfg, model=None: RecordingConfig(),
    )
    await gateway.start()
    try:
        await gateway.send_message("first", chat_id="existing")
        write_skill(config)
        await gateway.send_message("second", chat_id="existing")
        assert "fresh-skill" not in prompts[0]
        assert "fresh-skill" in prompts[-1]
    finally:
        await gateway.close()


async def test_unchanged_mcp_server_keeps_state_between_turns(config, tmp_path):

    marker = tmp_path / "lifetime"
    script = mcp_counter(tmp_path / "server.py", marker)
    profile_settings(config.data_dir).upsert_mcp_server(
        {"name": "counter", "command": sys.executable, "args": [str(script), str(marker)]}
    )
    gateway = real_gateway(
        config,
        memory=False,
        onboard=False,
        model_factory=lambda cfg, model=None: TestConfig(
            ToolCallEvent(name="counter_increment", arguments={}), "done"
        ),
    )
    await gateway.start()
    results = []
    stream = await gateway.stream_for("existing")

    async def collect(event):
        if isinstance(event, (ToolResultEvent, ToolErrorEvent)):
            results.append(event.result.parts[0].content)

    stream.subscribe(collect)
    try:
        await gateway.send_message("increment", chat_id="existing")
        await gateway.send_message("increment again", chat_id="existing")
        assert results == ["1", "2"]
    finally:
        await gateway.close()


async def test_persona_and_model_are_resolved_when_a_queued_turn_starts(config):

    entered, release = asyncio.Event(), asyncio.Event()
    records = []

    class BlockingClient(TestClient):
        async def __call__(self, messages, context, **kwargs):
            if not entered.is_set():
                entered.set()
                await release.wait()
            return await super().__call__(messages, context, **kwargs)

    class BlockingConfig(TestConfig):
        def create(self):
            return BlockingClient("done")

    def model_factory(cfg, model=None):
        result = TrackingConfig(BlockingConfig())
        records.append((cfg.llm.model, result.calls))
        return result

    gateway = real_gateway(config, memory=False, onboard=False, model_factory=model_factory)
    await gateway.start()
    records.clear()
    config.agent.system_prompt = "Original persona"
    first = asyncio.create_task(gateway.send_message("first", chat_id="same"))
    await entered.wait()
    second = asyncio.create_task(gateway.send_message("second", chat_id="same"))
    config.agent.system_prompt = "Replacement persona"
    config.llm.model = "replacement-model"
    release.set()
    try:
        await asyncio.gather(first, second)
        assert records[-1][0] == "replacement-model"
        final_prompt = "\n".join(records[-1][1][-1].prompt)
        assert "Replacement persona" in final_prompt
        assert "Original persona" not in final_prompt
    finally:
        await gateway.close()


async def test_memory_policy_changes_apply_to_the_next_turn(config):
    background = []

    class BackgroundClient(TestClient):
        async def __call__(self, messages, context, **kwargs):
            background.append("aggregate")
            return await super().__call__(messages, context, **kwargs)

    class BackgroundConfig(TestConfig):
        def create(self):
            return BackgroundClient("The user likes concise replies.")

    def model_factory(cfg, model=None):
        return BackgroundConfig() if model is not None else TestConfig("done")

    config.memory.aggregate_every_n_turns = 0
    gateway = real_gateway(config, onboard=False, model_factory=model_factory)
    await gateway.start()
    try:
        await gateway.send_message("first", chat_id="existing")
        assert background == []
        config.memory.aggregate_every_n_turns = 1
        await gateway.send_message("second", chat_id="existing")
        assert background
    finally:
        await gateway.close()


async def test_skill_metadata_switches_and_deletion_update_prompt_and_schemas(config):

    models = ScriptedModels()
    gateway = real_gateway(config, memory=False, onboard=False, model_factory=models)
    await gateway.start()
    write_skill(config)
    try:
        await gateway.send_message("first")
        assert "Freshly installed capability" in "\n".join(models.requests[-1][1])
        write_skill(config, description="Edited capability metadata")
        await gateway.send_message("second")
        assert "Edited capability metadata" in "\n".join(models.requests[-1][1])
        SkillStateStore(config.root_dir).set_enabled("fresh-skill", False)
        await gateway.send_message("third")
        assert "fresh-skill" not in "\n".join(models.requests[-1][1])
        assert "fresh-skill" not in str(
            [
                schema.function.parameters
                for schema in models.requests[-1][2]
                if getattr(schema, "function", None)
            ]
        )
        SkillStateStore(config.root_dir).set_enabled("fresh-skill", True)
        await gateway.send_message("fourth")
        assert "fresh-skill" in "\n".join(models.requests[-1][1])
        (config.skills_dir / "fresh-skill" / "SKILL.md").unlink()
        await gateway.send_message("fifth")
        assert "fresh-skill" not in "\n".join(models.requests[-1][1])
    finally:
        await gateway.close()


async def test_held_skill_stays_usable_and_its_resource_file_stays_live(config):

    write_skill(config)
    resource = config.skills_dir / "fresh-skill" / "references" / "reference.txt"
    resource.parent.mkdir()
    resource.write_text("Original content")
    entered, release = asyncio.Event(), asyncio.Event()

    async def before_call(messages, context):
        if not entered.is_set():
            entered.set()
            await release.wait()

    models = ScriptedModels(
        script=lambda cfg, model: [
            ToolCallEvent(name="load_skill", arguments=json.dumps({"name": "fresh-skill"})),
            ToolCallEvent(
                name="read_skill_resource",
                arguments=json.dumps(
                    {"name": "fresh-skill", "resource": "references/reference.txt"}
                ),
            ),
            "done",
        ],
        before_call=before_call,
    )
    gateway = real_gateway(config, memory=False, onboard=False, model_factory=models)
    await gateway.start()
    stream = await gateway.stream_for("existing")
    results = []

    async def collect(event):
        if isinstance(event, (ToolResultEvent, ToolErrorEvent)):
            results.append(event.result.parts[0].content)

    stream.subscribe(collect)
    turn = asyncio.create_task(gateway.send_message("first", chat_id="existing"))
    await entered.wait()
    SkillStateStore(config.root_dir).set_enabled("fresh-skill", False)
    resource.write_text("Updated live content")
    write_skill(config, name="installed-mid-turn")
    assert await gateway.feed_message("keep going", chat_id="existing")
    release.set()
    try:
        await turn
        assert any("Updated live content" in result for result in results), results
        assert "installed-mid-turn" not in "\n".join(models.requests[0][1])
        await gateway.send_message("next", chat_id="existing")
        assert "fresh-skill" not in "\n".join(models.requests[-1][1])
        assert "installed-mid-turn" in "\n".join(models.requests[-1][1])
    finally:
        await gateway.close()


@pytest.mark.parametrize("ending", ["complete", "failure", "cancel", "close"])
async def test_removed_mcp_stays_with_its_holder_then_closes(config, tmp_path, ending):

    marker = tmp_path / "lifetime"
    script = mcp_counter(tmp_path / "counter.py", marker)
    settings = profile_settings(config.data_dir)
    settings.upsert_mcp_server(
        {"name": "counter", "command": sys.executable, "args": [str(script), str(marker)]}
    )
    entered, release = asyncio.Event(), asyncio.Event()

    async def before_call(messages, context):
        if not entered.is_set():
            entered.set()
            await release.wait()

    config.llm.call_retries = 0
    models = ScriptedModels(before_call=before_call)
    if ending == "failure":
        models.script = lambda cfg, model: [RuntimeError("model failed")]
    gateway = real_gateway(config, memory=False, onboard=False, model_factory=models)
    await gateway.start()
    turn = asyncio.create_task(gateway.send_message("hold", chat_id="held"))
    await entered.wait()
    assert marker.read_text().splitlines() == ["started"]
    settings.delete_mcp_server("counter")
    await gateway.refresh()
    assert marker.read_text().splitlines() == ["started"]
    models.script = lambda cfg, model: ["done"]
    await gateway.send_message("new turn", chat_id="other")
    assert "counter_increment" not in str(models.requests[-1][2])
    assert marker.read_text().splitlines() == ["started"]
    try:
        if ending == "cancel":
            assert await gateway.cancel_turn("held")
            assert await turn == ""
        elif ending == "close":
            await gateway.close()
            with pytest.raises(asyncio.CancelledError):
                await turn
        elif ending == "failure":
            release.set()
            with pytest.raises(RuntimeError, match="model failed"):
                await turn
        else:
            release.set()
            await turn
        assert marker.read_text().splitlines() == ["started", "closed"]
    finally:
        await gateway.close()


async def test_unheld_mcp_removal_closes_without_another_message(config, tmp_path):

    marker = tmp_path / "lifetime"
    script = mcp_counter(tmp_path / "counter.py", marker)
    settings = profile_settings(config.data_dir)
    settings.upsert_mcp_server(
        {"name": "counter", "command": sys.executable, "args": [str(script), str(marker)]}
    )
    gateway = real_gateway(config, memory=False, onboard=False, model_factory=ScriptedModels())
    await gateway.start()
    try:
        await gateway.send_message("first")
        settings.delete_mcp_server("counter")
        await gateway.refresh()
        assert marker.read_text().splitlines() == ["started", "closed"]
    finally:
        await gateway.close()


async def test_standalone_invocation_closes_its_mcp_process(config, tmp_path):

    marker = tmp_path / "lifetime"
    script = mcp_counter(tmp_path / "counter.py", marker)
    profile_settings(config.data_dir).upsert_mcp_server(
        {"name": "counter", "command": sys.executable, "args": [str(script), str(marker)]}
    )
    async with agent_session(
        config, memory=False, capabilities=["mcp"], model_factory=ScriptedModels()
    ) as agent:
        await agent.ask("first")
        assert marker.read_text().splitlines() == ["started"]
    assert marker.read_text().splitlines() == ["started", "closed"]


async def test_card_scripts_hold_definitions_until_the_next_turn(config):

    directory = config.data_dir / CARDS_DIR
    path = write_card(directory, "Shelf", topic="the user's shelf", example="First")
    entered, release = asyncio.Event(), asyncio.Event()

    async def before_call(messages, context):
        if not entered.is_set():
            entered.set()
            await release.wait()

    models = ScriptedModels(
        script=lambda cfg, model: [
            ToolCallEvent(
                name="run_skill_script",
                arguments=json.dumps(
                    {"name": "rich-views", "script": "Shelf", "args": {"title": "Paperbacks"}}
                ),
            ),
            "done",
        ],
        before_call=before_call,
    )
    gateway = real_gateway(config, memory=False, onboard=False, model_factory=models)
    await gateway.start()
    events = []
    stream = await gateway.stream_for("held")

    async def collect(event):
        if isinstance(event, A2UIMessageEvent):
            events.append(event.message)

    stream.subscribe(collect)
    turn = asyncio.create_task(gateway.send_message("first", chat_id="held"))
    await entered.wait()
    path.write_text(path.read_text().replace("title", "caption"))
    release.set()
    try:
        await turn
        assert any(
            message.get("updateDataModel", {}).get("value") == "Paperbacks" for message in events
        )
        events.clear()
        await gateway.send_message("second", chat_id="held")
        assert events == []
    finally:
        await gateway.close()


async def test_docker_state_is_shared_across_chats_and_models_and_replaced_safely(config, tmp_path):

    write_stub(tmp_path / "docker", stdout="Docker is available")
    config.search_path = [tmp_path]
    config.tools.sandbox = "docker"
    environments = []

    def environment_factory(**kwargs):
        environment = StatefulEnvironment(**kwargs)
        environments.append(environment)
        return environment

    entered, release = asyncio.Event(), asyncio.Event()
    holding = False

    async def before_call(messages, context):
        if holding and not entered.is_set():
            entered.set()
            await release.wait()

    models = ScriptedModels(
        script=lambda cfg, model: [
            ToolCallEvent(
                name="run_code_sandboxed",
                arguments=json.dumps({"code": "increment", "language": "python"}),
            ),
            "done",
        ],
        before_call=before_call,
    )
    gateway = real_gateway(
        config,
        memory=False,
        onboard=False,
        model_factory=models,
        environment_factory=environment_factory,
    )
    await gateway.start()
    results = []

    async def collect(event):
        if isinstance(event, ToolResultEvent):
            results.append(event.result.parts[0].content)

    for chat in ("one", "two", "held", "new"):
        (await gateway.stream_for(chat)).subscribe(collect)
    try:
        await gateway.send_message("first", chat_id="one")
        config.llm.model = "another-text-model"
        await gateway.send_message("second", chat_id="two")
        assert any("1" in result for result in results)
        assert any("2" in result for result in results)
        holding = True
        turn = asyncio.create_task(gateway.send_message("third", chat_id="held"))
        await entered.wait()
        config.tools.docker_network = "none"
        await gateway.refresh()
        assert not environments[0].closed
        await gateway.send_message("fourth", chat_id="new")
        assert environments[-1].settings["network_mode"] == "none"
        assert not environments[0].closed
        release.set()
        await turn
        assert environments[0].closed
        with pytest.raises(RuntimeError, match="disposed"):
            await environments[0].exec(["probe"])
    finally:
        await gateway.close()
    assert all(environment.closed for environment in environments)


async def test_acp_text_model_keeps_chat_session_and_retires_changed_settings(config, tmp_path):

    marker = tmp_path / "lifetime"
    script = acp_counter(tmp_path / "adapter.py")
    config.llm.provider = "codex"
    config.llm.model = "test-codex"
    config.llm.provider_options = {
        "codex": {"command": [sys.executable, str(script), str(marker)], "expose_tools": False}
    }
    gateway = real_gateway(config, memory=False, onboard=False)
    await gateway.start()
    try:
        assert await gateway.send_message("first", chat_id="existing") == "1"
        write_skill(config)
        assert await gateway.send_message("second", chat_id="existing") == "2"
        assert marker.read_text().splitlines() == ["started"]
        assert await gateway.send_message("other chat", chat_id="other") == "1"
        config.llm.provider_options["codex"]["turn_timeout"] = 90
        await gateway.refresh()
        assert marker.read_text().splitlines().count("closed") == 2
        assert await gateway.send_message("new settings", chat_id="existing") == "1"
    finally:
        await gateway.close()
    assert marker.read_text().splitlines().count("closed") == 3


async def test_google_tools_and_guidance_follow_current_readiness(config):

    class Google:
        connected = False

        def google_ready(self):
            return self.connected

    google = Google()
    models = ScriptedModels()
    gateway = real_gateway(config, memory=False, onboard=False, model_factory=models, google=google)
    await gateway.start()
    try:
        for connected in (False, True, False):
            google.connected = connected
            await gateway.send_message("current connections", chat_id="existing")
            prompt, tools = models.requests[-1][1:3]
            assert (GOOGLE_GUIDANCE in prompt) == connected
            assert ("gmail_search" in str(tools)) == connected
            assert ("drive_search" in str(tools)) == connected
    finally:
        await gateway.close()


async def test_saved_model_definition_credentials_and_native_tools_refresh(config):

    secrets = SecretStore(config.paths)
    key = secrets.create_secret(name="model key", value="first-key", provider="anthropic")
    store = LlmConfigStore(config.paths)
    entry = store.save_config(
        {
            "name": "chosen",
            "type": "anthropic",
            "model": "original",
            "secret_id": key["id"],
            "options": {"base_url": "https://first.invalid"},
        }
    )
    models = ScriptedModels()
    gateway = real_gateway(config, memory=False, onboard=False, model_factory=models)
    await gateway.start()
    try:
        await gateway.send_message("first", chat_id="existing", chat_model=entry["id"])
        first = models.requests[-1]
        secrets.update_secret(
            sid=key["id"], name="model key", value="second-key", provider="anthropic"
        )
        entry.update(
            model="updated",
            options={"base_url": "https://second.invalid"},
            builtin_tools={"web_search": {}},
        )
        store.save_config(entry)
        await gateway.send_message("second", chat_id="existing")
        second = models.requests[-1]
        assert first[0].llm.model == "original"
        assert first[0].llm.provider_options["anthropic"]["api_key"] == "first-key"
        assert second[0].llm.model == "updated"
        assert second[0].llm.provider_options["anthropic"]["api_key"] == "second-key"
        assert second[0].llm.provider_options["anthropic"]["base_url"] == "https://second.invalid"
        assert "duckduckgo_search" in str(first[2])
        assert "duckduckgo_search" not in str(second[2])
        assert any(type(schema).__name__ == "WebSearchToolSchema" for schema in second[2])
    finally:
        await gateway.close()


async def test_subscription_override_refreshes_before_model_creation(config):

    write_codex_session(config.paths, access_token="expired-token")
    tokens = json.loads(config.paths.codex_tokens.read_text())
    tokens["expires_at"] = time.time() - 1
    config.paths.codex_tokens.write_text(json.dumps(tokens))

    def refresh(request):
        assert b"grant_type=refresh_token" in request.content
        return httpx.Response(
            200, json={"access_token": "rotated-token", "refresh_token": "next", "expires_in": 3600}
        )

    auth = CodexAuth(config.paths, client=client(refresh))
    entry = LlmConfigStore(config.paths).save_config(
        {"name": "subscription override", "type": "openai_subscription", "model": "gpt-test"}
    )
    models = ScriptedModels()
    resolved = []

    def factory(cfg, model=None):
        if cfg.llm.auth_mode == "subscription":
            resolved.append(model_config(cfg, model).api_key)
        return models(cfg, model)

    gateway = real_gateway(
        config, memory=False, onboard=False, model_factory=factory, subscription_auth=auth
    )
    await gateway.start()
    try:
        await gateway.send_message("override", llm_config_id=entry["id"])
        assert resolved == ["rotated-token"]
        assert models.requests[-1][0].llm.auth_mode == "subscription"
        assert config.llm.auth_mode != "subscription"
    finally:
        await gateway.close()


async def test_rich_views_off_removes_runtime_without_removing_drawn_history(config):

    write_card(config.data_dir / CARDS_DIR, "Shelf", topic="books", example="Books")
    models = ScriptedModels(
        script=lambda cfg, model: [
            ToolCallEvent(
                name="run_skill_script",
                arguments=json.dumps(
                    {"name": "rich-views", "script": "Shelf", "args": {"title": "Books"}}
                ),
            ),
            "done",
        ]
    )
    gateway = real_gateway(config, memory=False, onboard=False, model_factory=models)
    await gateway.start()
    events = []

    async def collect(event):
        events.append(event)

    (await gateway.stream_for("existing")).subscribe(collect)
    try:
        await gateway.send_message("draw", chat_id="existing")
        drawn = [event for event in events if isinstance(event, A2UIMessageEvent)]
        assert drawn
        SkillStateStore(config.paths.root).set_enabled("rich-views", False)
        models.script = lambda cfg, model: [
            '<a2ui-json>{"beginRendering":{"surfaceId":"new","root":"root"}}</a2ui-json>'
        ]
        await gateway.send_message("second", chat_id="existing")
        assert [event for event in events if isinstance(event, A2UIMessageEvent)] == drawn
        assert "<name>rich-views</name>" not in "\n".join(models.requests[-1][1])
        assert "rich-views" not in str(models.requests[-1][2])
    finally:
        await gateway.close()


async def test_failed_preparation_releases_holds_and_preserves_failure(config, tmp_path):

    marker = tmp_path / "lifetime"
    script = mcp_counter(tmp_path / "server.py", marker)
    settings = profile_settings(config.data_dir)
    settings.upsert_mcp_server(
        {"name": "counter", "command": sys.executable, "args": [str(script), str(marker)]}
    )
    models = ScriptedModels()
    fail = False

    def factory(cfg, model=None):
        if fail:
            raise ValueError("cannot prepare model")
        return models(cfg, model)

    gateway = real_gateway(config, memory=False, onboard=False, model_factory=factory)
    await gateway.start()
    emitted = []

    async def collect(event):
        emitted.append(event)

    (await gateway.stream_for("existing")).subscribe(collect)
    try:
        await gateway.send_message("first", chat_id="existing")
        fail = True
        with pytest.raises(ValueError, match="cannot prepare"):
            await gateway.send_message("failed", chat_id="existing")
        assert any(isinstance(event, TurnFailed) for event in emitted)
        assert any(message["text"] == "failed" for message in await gateway.transcript("existing"))
        settings.delete_mcp_server("counter")
        await gateway.refresh()
        assert marker.read_text().splitlines() == ["started", "closed"]
    finally:
        await gateway.close()


async def test_mcp_replacement_waits_for_every_holder(config, tmp_path):

    old_marker, new_marker = tmp_path / "old", tmp_path / "new"
    script = mcp_counter(tmp_path / "server.py", old_marker)
    settings = profile_settings(config.data_dir)
    settings.upsert_mcp_server(
        {"name": "counter", "command": sys.executable, "args": [str(script), str(old_marker)]}
    )
    entered = [asyncio.Event(), asyncio.Event()]
    release = [asyncio.Event(), asyncio.Event()]
    calls = 0

    async def before_call(messages, context):
        nonlocal calls
        index, calls = calls, calls + 1
        if index < 2:
            entered[index].set()
            await release[index].wait()

    gateway = real_gateway(
        config, memory=False, onboard=False, model_factory=ScriptedModels(before_call=before_call)
    )
    await gateway.start()
    first = asyncio.create_task(gateway.send_message("one", chat_id="one"))
    await entered[0].wait()
    second = asyncio.create_task(gateway.send_message("two", chat_id="two"))
    await entered[1].wait()
    try:
        settings.upsert_mcp_server(
            {"name": "counter", "command": sys.executable, "args": [str(script), str(new_marker)]}
        )
        await gateway.refresh()
        await gateway.send_message("new generation", chat_id="three")
        assert old_marker.read_text().splitlines() == ["started"]
        assert new_marker.read_text().splitlines() == ["started"]
        release[0].set()
        await first
        assert old_marker.read_text().splitlines() == ["started"]
        release[1].set()
        await second
        assert old_marker.read_text().splitlines() == ["started", "closed"]
    finally:
        await gateway.close()
    assert new_marker.read_text().splitlines() == ["started", "closed"]


async def test_docker_and_acp_resources_are_isolated_between_profiles(config, tmp_path):

    profiles = [
        ProfileRegistry(config.paths).create_profile(name, "#109e91") for name in ("One", "Two")
    ]
    write_stub(tmp_path / "docker", stdout="available")
    environments = []

    def environment_factory(**kwargs):
        environment = StatefulEnvironment(**kwargs)
        environments.append(environment)
        return environment

    gateways = []
    for profile in profiles:
        cfg = config.with_profile(profile)
        cfg.search_path = [tmp_path]
        cfg.tools.sandbox = "docker"
        gateways.append(
            real_gateway(
                cfg,
                memory=False,
                onboard=False,
                model_factory=ScriptedModels(),
                environment_factory=environment_factory,
            )
        )
    for gateway in gateways:
        await gateway.start()
        await gateway.send_message("start environment")
    try:
        await environments[0].exec(["increment"])
        assert (await environments[0].exec(["increment"])).output == "2"
        assert (await environments[1].exec(["increment"])).output == "1"
        await gateways[0].close()
        assert environments[0].closed and not environments[1].closed
        assert (await environments[1].exec(["increment"])).output == "2"
    finally:
        for gateway in gateways:
            await gateway.close()

    marker = tmp_path / "acp-lifetime"
    script = acp_counter(tmp_path / "adapter.py")
    gateways = []
    for profile in profiles:
        cfg = config.with_profile(profile)
        cfg.llm.provider = "codex"
        cfg.llm.provider_options = {
            "codex": {"command": [sys.executable, str(script), str(marker)], "expose_tools": False}
        }
        gateway = real_gateway(cfg, memory=False, onboard=False)
        await gateway.start()
        gateways.append(gateway)
    try:
        assert await gateways[0].send_message("first", chat_id="same") == "1"
        assert await gateways[0].send_message("second", chat_id="same") == "2"
        assert await gateways[1].send_message("first", chat_id="same") == "1"
        await gateways[0].close()
        assert await gateways[1].send_message("second", chat_id="same") == "2"
    finally:
        for gateway in gateways:
            await gateway.close()
    assert marker.read_text().splitlines().count("started") == 2
    assert marker.read_text().splitlines().count("closed") == 2


async def test_task_runs_and_followups_refresh_with_model_precedence(config):

    store = LlmConfigStore(config.paths)
    task_model = store.save_config({"name": "Task", "type": "gemini", "model": "task-model"})
    chat_model = store.save_config({"name": "Chat", "type": "gemini", "model": "chat-model"})
    models = ScriptedModels()
    tasks = TaskService(config, summary_factory=fake_summary_factory())
    gateway = real_gateway(
        config, memory=False, onboard=False, model_factory=models, task_service=tasks
    )
    tasks.set_gateway(gateway)
    await tasks.start(scheduler=False)
    await gateway.start()
    try:
        task = await tasks.create_task(
            name="Refresh", prompt="current tools", model=task_model["id"]
        )
        first = await tasks.start_run(task["id"])
        await asyncio.wait_for(tasks._jobs_done(), 5)
        assert (await tasks.get_run(first.id))["status"] == "completed"
        assert models.requests[-1][0].llm.model == "task-model"
        write_skill(config)
        second = await tasks.start_run(task["id"])
        await asyncio.wait_for(tasks._jobs_done(), 5)
        assert "fresh-skill" in "\n".join(models.requests[-1][1])
        assert models.requests[-1][0].llm.model == "task-model"
        await gateway.send_message("followup", chat_id=second.stream_id)
        assert models.requests[-1][0].llm.model == "task-model"
        await gateway.update_chat(second.stream_id, model=chat_model["id"])
        await gateway.send_message("override", chat_id=second.stream_id)
        assert models.requests[-1][0].llm.model == "chat-model"
        await gateway.send_message(
            "explicit", chat_id=second.stream_id, llm_config_id=task_model["id"]
        )
        assert models.requests[-1][0].llm.model == "task-model"
        store.delete_config(chat_model["id"])
        await gateway.send_message("dangling", chat_id=second.stream_id)
        assert models.requests[-1][0].llm.model == "task-model"
        config.llm.env_pinned = True
        config.llm.model = "deployed-model"
        await gateway.send_message(
            "pinned", chat_id=second.stream_id, llm_config_id=task_model["id"]
        )
        assert models.requests[-1][0].llm.model == "deployed-model"
    finally:
        await tasks.close()
        await gateway.close()


async def test_channel_delivery_refreshes_existing_chat(paths):

    registry = ProfileRegistry(paths)
    profile = registry.create_profile("Channel", "#109e91")
    registry.set_connection_default("telegram", profile.id)
    PairingStore(paths).add_account("telegram", "1001", platform="telegram")
    models = ScriptedModels()
    manager = make_manager(paths, persist=True, model_factory=models)
    await manager.start()
    message = InboundMessage(
        text="first",
        sender_id="1001",
        chat_id="chat",
        platform="telegram",
        connection="telegram",
        is_direct=True,
    )
    try:
        assert await manager.router.handle(message) == Reply("done")
        write_skill(manager.get(profile.id).require_config())
        assert await manager.router.handle(message) == Reply("done")
        assert "fresh-skill" not in "\n".join(models.requests[0][1])
        assert "fresh-skill" in "\n".join(models.requests[-1][1])
    finally:
        await manager.close()


async def test_voice_delegate_refreshes_during_one_connection(config):
    config.secret_env = {**config.secret_env, "GEMINI_API_KEY": "test"}
    models = ScriptedModels()
    tasks = TaskService(config, summary_factory=fake_summary_factory())
    await tasks.start(scheduler=False)
    gateway = real_gateway(
        config, memory=False, onboard=False, model_factory=models, task_service=tasks
    )
    await gateway.start()
    instructions = []

    class RealtimeConfig:
        @asynccontextmanager
        async def session(self, context, **kwargs):
            instructions.extend(kwargs["instructions"])
            yield

    try:
        voice = await gateway.build_voice_agent(voice_id="call", origin_chat="existing")
        async with voice.run(config=RealtimeConfig()) as context:
            assert "write_file" in "\n".join(instructions)
            for message in ("first", "second"):
                finished = asyncio.Event()

                async def completed(event):
                    if isinstance(event, ToolResultEvent) and event.name == "ask_assistant":
                        finished.set()

                handle = context.stream.subscribe(completed)
                await context.send(
                    ToolCallsEvent(
                        calls=[
                            ToolCallEvent(
                                name="ask_assistant", arguments=json.dumps({"request": message})
                            )
                        ]
                    )
                )
                await asyncio.wait_for(finished.wait(), 5)
                context.stream.unsubscribe(handle)
                write_skill(config)
            assert "fresh-skill" not in "\n".join(models.requests[0][1])
            assert "fresh-skill" in "\n".join(models.requests[-1][1])
    finally:
        await gateway.close()
        await tasks.close()


async def test_constructor_replacement_keeps_memory_history_and_the_running_agent(config):

    entered, release = asyncio.Event(), asyncio.Event()
    first = True

    async def before_call(messages, context):
        nonlocal first
        if first:
            first = False
            entered.set()
            await release.wait()

    models = ScriptedModels(
        script=lambda cfg, model: [
            ToolCallEvent(
                name="remember",
                arguments=json.dumps({"note": "Prefer concise replies", "category": "how"}),
            ),
            "done",
        ],
        before_call=before_call,
    )
    config.memory.aggregate_every_n_turns = 0
    gateway = real_gateway(config, onboard=False, model_factory=models)
    await gateway.start()
    held = asyncio.create_task(gateway.send_message("first preference", chat_id="existing"))
    await entered.wait()
    try:
        config.memory.compact_max_tokens += 1000
        await gateway.send_message("new policy", chat_id="other")
        assert "Prefer concise replies" in read_profile_sync(config.data_dir / "profile.db")
        release.set()
        await held
        await gateway.send_message("continue", chat_id="existing")
        assert "Prefer concise replies" in "\n".join(models.requests[-1][1])
        assert "first preference" in str(models.requests[-1][3])
        assert any(
            message["text"] == "first preference"
            for message in await gateway.transcript("existing")
        )
    finally:
        await gateway.close()


async def test_invocation_guidance_preserves_base_dynamic_hook_and_one_catalog(config):

    models = ScriptedModels()

    def factory(cfg, **kwargs):
        agent = create_agent(cfg, **kwargs)

        @agent.prompt
        def dynamic():
            return "A base dynamic hook is still present."

        return agent

    gateway = real_gateway(
        config, memory=False, onboard=False, model_factory=models, agent_factory=factory
    )
    await gateway.start()
    try:
        for persona in ("First persona", "Next persona"):
            config.agent.system_prompt = persona
            await gateway.send_message("request", surface="Current surface details")
            prompt = "\n".join(models.requests[-1][1])
            assert prompt.count(persona) == 1
            assert prompt.count("</available_skills>") == 1
            assert prompt.count("A base dynamic hook is still present.") == 1
            assert "Current surface details" in prompt
    finally:
        await gateway.close()


async def test_profile_skill_shadowing_and_shared_suppression_do_not_leak(config):

    registry = ProfileRegistry(config.paths)
    configs = [
        config.with_profile(registry.create_profile(name, "#109e91")) for name in ("Own", "Shared")
    ]
    write_skill(config, name="same-name", description="Shared implementation")
    write_skill(configs[0], name="same-name", description="Profile implementation")
    models = [ScriptedModels(), ScriptedModels()]
    gateways = [
        real_gateway(cfg, memory=False, onboard=False, model_factory=model)
        for cfg, model in zip(configs, models, strict=True)
    ]
    for gateway in gateways:
        await gateway.start()
    try:
        await asyncio.gather(*(gateway.send_message("first") for gateway in gateways))
        assert "Profile implementation" in "\n".join(models[0].requests[-1][1])
        assert "Shared implementation" not in "\n".join(models[0].requests[-1][1])
        assert "Shared implementation" in "\n".join(models[1].requests[-1][1])
        state = SkillStateStore(config.root_dir)
        state.set_suppressed("same-name", configs[1].data_dir.name, True, kind=SUPPRESS_SHARED)
        await asyncio.gather(*(gateway.send_message("second") for gateway in gateways))
        assert "same-name" in str(models[0].requests[-1][2])
        assert "same-name" not in str(models[1].requests[-1][2])
        state.set_enabled("same-name", False)
        await gateways[0].send_message("own stays available")
        assert "Profile implementation" in "\n".join(models[0].requests[-1][1])
    finally:
        for gateway in gateways:
            await gateway.close()


async def test_acp_replacement_retains_a_running_session_until_cancellation(config, tmp_path):
    marker = tmp_path / "lifetime"
    script = acp_counter(tmp_path / "adapter.py")
    config.llm.provider = "codex"
    config.llm.provider_options = {
        "codex": {"command": [sys.executable, str(script), str(marker)], "expose_tools": False}
    }
    gateway = real_gateway(config, memory=False, onboard=False)
    await gateway.start()
    entered = asyncio.Event()

    async def collect(event):
        if isinstance(event, ModelMessageChunk):
            entered.set()

    (await gateway.stream_for("held")).subscribe(collect)
    turn = asyncio.create_task(gateway.send_message("BLOCK_ACP", chat_id="held"))
    await asyncio.wait_for(entered.wait(), 5)
    try:
        config.llm.provider_options["codex"]["turn_timeout"] = 90
        await gateway.refresh()
        assert marker.read_text().splitlines() == ["started"]
        assert await gateway.send_message("new generation", chat_id="other") == "1"
        assert marker.read_text().splitlines().count("closed") == 0
        assert await gateway.cancel_turn("held")
        assert await turn == ""
        assert marker.read_text().splitlines().count("closed") == 1
        assert any(message["text"] == "BLOCK_ACP" for message in await gateway.transcript("held"))
    finally:
        await gateway.close()
    assert marker.read_text().splitlines().count("closed") == 2


async def test_scoped_standalone_agent_keeps_its_capability_limits(config):
    models = ScriptedModels()
    async with agent_session(
        config, memory=False, capabilities=["files"], model_factory=models
    ) as agent:
        await agent.ask("files only")
    tools = str(models.requests[-1][2])
    assert "write_file" in tools
    for excluded in ("load_skill", "duckduckgo_search", "run_code", "gmail_search", "ask_user"):
        assert excluded not in tools


async def test_acp_wire_prompt_receives_current_persona_catalog_and_guidance(config, tmp_path):
    marker = tmp_path / "lifetime"
    script = acp_counter(tmp_path / "adapter.py")
    config.llm.provider = "codex"
    config.llm.provider_options = {
        "codex": {"command": [sys.executable, str(script), str(marker)], "expose_tools": False}
    }
    gateway = real_gateway(config, memory=False, onboard=False)
    await gateway.start()
    try:
        config.agent.system_prompt = "ACP first persona"
        assert (
            await gateway.send_message(
                "first request", chat_id="existing", surface="channel context"
            )
            == "1"
        )
        write_skill(config)
        config.agent.system_prompt = "ACP next persona"
        assert (
            await gateway.send_message("next request", chat_id="existing", surface="voice context")
            == "2"
        )
        prompts = [
            json.loads(line) for line in marker.with_suffix(".prompts").read_text().splitlines()
        ]
        first, second = ("\n".join(part["text"] for part in prompt) for prompt in prompts)
        assert "ACP first persona" in first and "ACP next persona" not in first
        assert "ACP next persona" in second and "ACP first persona" not in second
        assert "fresh-skill" not in first and "fresh-skill" in second
        assert first.count("</available_skills>") == second.count("</available_skills>") == 1
        assert "channel context" in first and "voice context" in second
        assert "first request" in first and "next request" in second
        transcript = await gateway.transcript("existing")
        assert [message["text"] for message in transcript if message["role"] == "user"] == [
            "first request",
            "next request",
        ]
    finally:
        await gateway.close()


@pytest.mark.parametrize("boundary", ["refresh", "close"])
async def test_cancellation_during_unheld_resource_cleanup_finishes_disposal(
    config, tmp_path, boundary
):
    write_stub(tmp_path / "docker", stdout="available")
    config.search_path = [tmp_path]
    config.tools.sandbox = "docker"
    entered, release = asyncio.Event(), asyncio.Event()
    environments = []

    class Environment(StatefulEnvironment):
        async def aclose(self):
            entered.set()
            await release.wait()
            await super().aclose()

    def factory(**kwargs):
        environment = Environment(**kwargs)
        environments.append(environment)
        return environment

    gateway = real_gateway(
        config,
        memory=False,
        onboard=False,
        model_factory=ScriptedModels(),
        environment_factory=factory,
    )
    await gateway.start()
    await gateway.send_message("create sandbox")
    config.tools.docker_network = "none"
    cleanup = asyncio.create_task(gateway.refresh() if boundary == "refresh" else gateway.close())
    await entered.wait()
    cleanup.cancel()
    release.set()
    try:
        with pytest.raises(asyncio.CancelledError):
            await cleanup
        with pytest.raises(RuntimeError, match="disposed"):
            await environments[0].exec(["increment"])
    finally:
        await gateway.close()


async def test_user_stop_during_preparation_releases_resources_and_preserves_queued_turn(
    config, tmp_path
):
    write_stub(tmp_path / "docker", stdout="available")
    config.search_path = [tmp_path]
    config.tools.sandbox = "docker"
    entered, release = asyncio.Event(), asyncio.Event()
    environments = []

    class Environment(StatefulEnvironment):
        async def aclose(self):
            entered.set()
            await release.wait()
            await super().aclose()

    def factory(**kwargs):
        environment = Environment(**kwargs)
        environments.append(environment)
        return environment

    models = ScriptedModels()
    gateway = real_gateway(
        config, memory=False, onboard=False, model_factory=models, environment_factory=factory
    )
    await gateway.start()
    try:
        await gateway.send_message("create sandbox", chat_id="existing")
        config.tools.docker_network = "none"
        preparing = asyncio.create_task(gateway.send_message("stop this", chat_id="existing"))
        await asyncio.wait_for(entered.wait(), timeout=2)
        assert not await gateway.feed_message("keep this", chat_id="existing")
        queued = asyncio.create_task(gateway.send_message("keep this", chat_id="existing"))
        assert gateway.is_running("existing")
        assert await gateway.cancel_turn("existing")
        release.set()
        assert await asyncio.wait_for(preparing, timeout=2) == ""
        assert await asyncio.wait_for(queued, timeout=2) == "done"
        assert len(models.requests) == 2
        with pytest.raises(RuntimeError, match="disposed"):
            await environments[0].exec(["increment"])
        events = await (await gateway.stream_for("existing")).history.get_events()
        assert any(isinstance(event, TurnCancelled) for event in events)
        assert any(
            message["text"] == "stop this" for message in await gateway.transcript("existing")
        )
        assert not await gateway.cancel_turn("existing")
    finally:
        release.set()
        await gateway.close()


async def test_cancelled_turn_keeps_work_when_another_gateway_changes_the_chat_log(config):
    entered = asyncio.Event()

    async def before_call(messages, context):
        await context.send(ModelResponse(message=ModelMessage(content="first Turn work")))
        entered.set()
        await asyncio.Event().wait()

    first = real_gateway(
        config, memory=False, onboard=False, model_factory=ScriptedModels(before_call=before_call)
    )
    second = real_gateway(config, memory=False, onboard=False, model_factory=ScriptedModels())
    fresh = real_gateway(config, memory=False, onboard=False, model_factory=ScriptedModels())
    await first.start()
    await second.start()
    try:
        turn = asyncio.create_task(first.send_message("work", chat_id="shared"))
        await asyncio.wait_for(entered.wait(), timeout=2)
        await second.emit_event(
            "shared", ModelResponse(message=ModelMessage(content="another process work"))
        )
        assert await first.cancel_turn("shared")
        assert await asyncio.wait_for(turn, timeout=2) == ""
        await fresh.start()
        events = await (await fresh.stream_for("shared")).history.get_events()
        assert any(
            isinstance(event, ModelResponse) and event.message.content == "first Turn work"
            for event in events
        )
        assert isinstance(events[-1], TurnCancelled)
    finally:
        await first.close()
        await second.close()
        await fresh.close()


async def test_changed_compaction_threshold_emits_compaction_and_uses_profile_cheap_model(config):
    models = ScriptedModels()
    config.memory.aggregate_every_n_turns = 0
    config.memory.compact_max_tokens = 0
    gateway = real_gateway(config, onboard=False, model_factory=models)
    await gateway.start()
    events = []

    async def collect(event):
        if isinstance(event, CompactionCompleted):
            events.append(event)

    (await gateway.stream_for("existing")).subscribe(collect)
    try:
        for index in range(32):
            await gateway.send_message(f"history turn {index}", chat_id="existing")
        assert events == []
        config.memory.compact_max_tokens = 1
        await gateway.send_message("apply new threshold", chat_id="existing")
        assert events and events[-1].events_before > events[-1].events_after
        assert any(request[4] == cheap_model(config) for request in models.requests)
        assert any(
            message["text"] == "history turn 0" for message in await gateway.transcript("existing")
        )
    finally:
        await gateway.close()
