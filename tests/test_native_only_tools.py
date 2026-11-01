"""With ``tools.disable_native`` on, native-only tools are gone from what the model receives."""

import asyncio

from assistant.config import Config
from assistant.gateway.core import build_gateway
from assistant.profiles import ProfileRegistry
from tests.support.apps import make_manager
from tests.support.fakes import ScriptedModels, fake_summary_factory

NATIVE_ONLY = [
    "ask_user",
    "answer_question",
    "list_open_questions",
    "describe_settings",
    "describe_integrations",
    "list_coding_agents",
    "install_skill",
    "remove_skill",
    "search_skills",
    "list_folders",
]
OPEN_QUESTIONS = "list/answer open questions"


def _gateway(paths, disable_native=False):
    config = Config.for_paths(paths)
    config.tools.disable_native = disable_native
    models = ScriptedModels()
    gateway, tasks = build_gateway(
        config,
        memory=False,
        persist=False,
        config_factory=lambda: config,
        model_factory=models,
        summary_factory=fake_summary_factory(),
    )
    return gateway, tasks, models


def _sent(models):
    """The tool names and prompt text of the model's latest request."""
    _, prompt, schemas, _, _ = models.requests[-1]
    return {schema.function.name for schema in schemas}, "\n".join(prompt)


async def test_every_native_only_tool_is_on_by_default(paths):
    gateway, _, models = _gateway(paths)
    await gateway.start()
    try:
        await gateway.send_message("hi", chat_id="chat")
    finally:
        await gateway.close()
    names, prompt = _sent(models)
    assert set(NATIVE_ONLY) <= names
    assert OPEN_QUESTIONS in prompt


async def test_a_chat_turn_never_sends_a_native_only_tool(paths):
    gateway, _, models = _gateway(paths, disable_native=True)
    await gateway.start()
    try:
        await gateway.send_message("hi", chat_id="chat")
    finally:
        await gateway.close()
    names, prompt = _sent(models)
    assert not names & set(NATIVE_ONLY)
    assert {"read_file", "list_tasks", "web_fetch", "load_skill"} <= names
    assert OPEN_QUESTIONS not in prompt
    assert "list/read past conversations." in prompt


async def test_a_task_run_never_sends_a_native_only_tool(paths):
    pid = ProfileRegistry(paths).create_profile("Work", "#109e91").id
    models = ScriptedModels()
    manager = make_manager(
        paths,
        env={"AG2ASSISTANT_DISABLE_NATIVE_TOOLS": "true"},
        model_factory=models,
        summary_factory=fake_summary_factory(),
    )
    await manager.start()
    try:
        tasks = manager.get(pid).tasks
        task = await tasks.create_task(name="nightly", prompt="report")
        run = await tasks.start_run(task["id"])
        assert run is not None
        await asyncio.wait_for(tasks._jobs_done(), 5)
        assert (await tasks.get_run(run.id))["status"] == "completed"
    finally:
        await manager.close()
    names, prompt = _sent(models)
    assert not names & set(NATIVE_ONLY)
    assert "list_tasks" in names
    assert OPEN_QUESTIONS not in prompt


async def test_the_acp_agent_never_holds_a_native_only_tool(paths):
    gateway, _, _ = _gateway(paths, disable_native=True)
    await gateway.start()
    try:
        agent = await gateway.acp_agent()
        names = {tool.name for tool in agent.tools}
    finally:
        await gateway.close()
    assert not names & set(NATIVE_ONLY)
    assert "list_tasks" in names
