"""AG2 plugins a host hands to ``ProfileManager`` reach every kind of turn."""

import asyncio

from ag2 import Context, Plugin, tool
from ag2.events import ToolCallEvent
from ag2.events.tool_events import ToolResult, ToolResultEvent
from ag2.middleware import BaseMiddleware, Middleware

from assistant.profiles import ProfileRegistry
from tests.support.apps import make_manager
from tests.support.fakes import ScriptedModels, fake_summary_factory

MARKER = "[REPLIED]"


class EndTurnOnListTasks(BaseMiddleware):
    """Turns one tool's success into the turn's final answer."""

    async def on_tool_execution(self, call_next, event, context):
        result = await call_next(event, context)
        if event.name == "list_tasks" and type(result) is ToolResultEvent:
            return ToolResultEvent.from_call(event, result=ToolResult(MARKER, final=True))
        return result


@tool
async def host_tool() -> str:
    """A tool the host adds."""
    return "from host"


def _manager(paths, plugin, script=lambda _config, _model: ["done"]):
    pid = ProfileRegistry(paths).create_profile("Work", "#109e91").id
    models = ScriptedModels(script)
    manager = make_manager(
        paths, model_factory=models, summary_factory=fake_summary_factory(), plugins=[plugin]
    )
    return manager, pid, models


async def test_a_plugin_tool_and_prompt_reach_a_chat_turn(paths):
    plugin = Plugin(prompt="Host rule.", tools=[host_tool])
    manager, pid, models = _manager(paths, plugin)
    await manager.start()
    try:
        await manager.get(pid).gateway.send_message("hi", chat_id="chat")
    finally:
        await manager.close()
    _, prompt, schemas, _, _ = models.requests[-1]
    assert "host_tool" in {schema.function.name for schema in schemas}
    assert "Host rule." in prompt


async def test_plugin_middleware_can_end_a_turn_from_a_per_turn_tool(paths):
    plugin = Plugin(middleware=[Middleware(EndTurnOnListTasks)])
    script = lambda _config, _model: [ToolCallEvent("list_tasks"), "must not be reached"]  # noqa: E731
    manager, pid, models = _manager(paths, plugin, script)
    await manager.start()
    try:
        reply = await manager.get(pid).gateway.send_message("hi", chat_id="chat")
    finally:
        await manager.close()
    assert reply == MARKER
    assert len(models.requests) == 1


async def test_a_prompt_callable_can_name_a_task_runs_origin_chat(paths):
    runtimes = []

    async def origin_chat(context: Context) -> str:
        store = runtimes[0].tasks.store
        run_id = str(context.stream.id).removeprefix("task-run:")
        run = await store.get_run(run_id)
        task = await store.get_task(run.task_id) if run is not None else None
        return f"Origin chat: {task.origin_chat}." if task is not None else ""

    manager, pid, models = _manager(paths, Plugin(prompt=origin_chat))
    await manager.start()
    runtimes.append(manager.get(pid))
    try:
        tasks = manager.get(pid).tasks
        task = await tasks.create_task(name="nightly", prompt="report", origin_chat="room-1")
        run = await tasks.start_run(task["id"])
        assert run is not None
        await asyncio.wait_for(tasks._jobs_done(), 5)
        assert (await tasks.get_run(run.id))["status"] == "completed"
    finally:
        await manager.close()
    assert "Origin chat: room-1." in models.requests[0][1]


async def test_the_acp_agent_carries_a_plugin_tool(paths):
    manager, pid, _ = _manager(paths, Plugin(tools=[host_tool]))
    await manager.start()
    try:
        agent = await manager.get(pid).gateway.acp_agent()
        assert "host_tool" in {tool.name for tool in agent.tools}
    finally:
        await manager.close()
