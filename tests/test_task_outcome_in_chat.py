"""A task created in a plain Chat reports its outcome back into that Chat.

The outcome used to go nowhere: `_origin` recorded (None, None) for a Chat no
Connection started, and `_deliver` had no address. Now the Chat itself is the
origin (CHAT_ORIGIN) and the runtime pushes the outcome into it as a turn of the
chat's own agent, which answers the owner there."""

import asyncio

from assistant.profiles import ProfileRegistry
from assistant.system_tools import CHAT_ORIGIN
from tests.support.apps import make_manager
from tests.support.fakes import ScriptedModels, fake_summary_factory


async def test_a_completed_task_lands_in_the_chat_it_was_created_from(paths):
    pid = ProfileRegistry(paths).create_profile("Work", "#109e91").id
    models = ScriptedModels()
    manager = make_manager(
        paths, model_factory=models, summary_factory=fake_summary_factory(summary="78°F")
    )
    await manager.start()
    try:
        tasks = manager.get(pid).tasks
        task = await tasks.create_task(
            name="weather", prompt="report", origin_channel=CHAT_ORIGIN, origin_chat="room-1"
        )
        run = await tasks.start_run(task["id"])
        assert run is not None
        await asyncio.wait_for(tasks._jobs_done(), 5)
        assert (await tasks.get_run(run.id))["status"] == "completed"
        # the chat's agent was handed the outcome as a turn on room-1 and answered
        for _ in range(40):
            if any("✅ weather: 78°F" in str(r) for r in models.requests):
                break
            await asyncio.sleep(0.05)
        assert any("✅ weather: 78°F" in str(r) for r in models.requests), [
            str(r)[:200] for r in models.requests
        ]
    finally:
        await manager.close()


async def test_a_chat_outcome_is_pushed_through_the_runtime_not_the_install_notifier(paths):
    pid = ProfileRegistry(paths).create_profile("Work", "#109e91").id
    manager = make_manager(paths, model_factory=ScriptedModels())
    await manager.start()
    try:
        runtime = manager.get(pid)
        sent = []

        async def send_message(text, chat_id="default", asker=None, **kw):
            sent.append((text, chat_id, asker is not None))
            return "noted"

        runtime.gateway.send_message = send_message
        await runtime.notify_channel(CHAT_ORIGIN, "room-1", "✅ nightly: done")
        assert sent == [("✅ nightly: done", "room-1", True)]
    finally:
        await manager.close()
