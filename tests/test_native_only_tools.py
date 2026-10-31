"""Tools named in ``tools.native_only`` are gone from what the model receives."""

import asyncio
import logging

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


def _gateway(paths, native_only=()):
    config = Config.for_paths(paths)
    config.tools.native_only = list(native_only)
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


async def test_every_listed_tool_exists_without_the_setting(paths):
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
    gateway, _, models = _gateway(paths, NATIVE_ONLY)
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
        env={"AG2ASSISTANT_NATIVE_ONLY_TOOLS": ",".join(NATIVE_ONLY)},
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
    gateway, _, _ = _gateway(paths, NATIVE_ONLY)
    await gateway.start()
    try:
        agent = await gateway.acp_agent()
        names = {tool.name for tool in agent.tools}
    finally:
        await gateway.close()
    assert not names & set(NATIVE_ONLY)
    assert "list_tasks" in names


async def test_the_prompt_drops_open_questions_when_either_tool_goes(paths):
    gateway, _, models = _gateway(paths, ["answer_question"])
    await gateway.start()
    try:
        await gateway.send_message("hi", chat_id="chat")
    finally:
        await gateway.close()
    names, prompt = _sent(models)
    assert "list_open_questions" in names
    assert OPEN_QUESTIONS not in prompt


async def test_an_unmatched_name_warns_once_and_changes_nothing_else(paths, caplog):
    gateway, _, models = _gateway(paths, ["no_such_tool"])
    await gateway.start()
    try:
        with caplog.at_level(logging.WARNING, logger="ag2assistant.gateway"):
            await gateway.send_message("one", chat_id="chat")
            await gateway.send_message("two", chat_id="chat")
    finally:
        await gateway.close()
    warned = [r for r in caplog.records if "no_such_tool" in r.getMessage()]
    assert len(warned) == 1
    assert set(NATIVE_ONLY) <= _sent(models)[0]
