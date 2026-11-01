"""A host's ``task_outcome`` handler sees each run's outcome before the default delivery."""

import asyncio

from assistant.profiles import ProfileRegistry
from assistant.system_tools import CHAT_ORIGIN
from tests.support.apps import make_manager
from tests.support.fakes import ScriptedModels, fake_summary_factory


class Handler:
    """Records every outcome and answers with ``delivered``."""

    def __init__(self, delivered=True):
        self.delivered = delivered
        self.calls = []

    async def __call__(self, profile, task, text, *, failed, run_id):
        self.calls.append((profile, task.origin_chat, text, failed, run_id))
        if isinstance(self.delivered, Exception):
            raise self.delivered
        return self.delivered


async def _one_run(paths, handler, script=lambda _config, _model: ["the result"], notifier=None):
    """Run one chat-origin task. → (profile id, its Run, the model's requests)."""
    pid = ProfileRegistry(paths).create_profile("Work", "#109e91").id
    models = ScriptedModels(script)
    manager = make_manager(
        paths,
        model_factory=models,
        summary_factory=fake_summary_factory(summary="short"),
        task_outcome=handler,
    )
    await manager.start()
    try:
        tasks = manager.get(pid).tasks
        if notifier is not None:
            tasks.set_notifier(notifier)
        task = await tasks.create_task(
            name="nightly", prompt="report", origin_channel=CHAT_ORIGIN, origin_chat="room-1"
        )
        run = await tasks.start_run(task["id"])
        assert run is not None
        await asyncio.wait_for(tasks._jobs_done(), 5)
        record = await tasks.store.get_run(run.id)
    finally:
        await manager.close()
    return pid, record, models.requests


async def test_a_handled_outcome_starts_no_chat_turn(paths):
    handler = Handler()
    pid, run, requests = await _one_run(paths, handler)
    assert run.status == "completed"
    assert handler.calls == [(pid, "room-1", "the result", False, run.id)]
    assert run.delivery_error is None
    assert len(requests) == 1


async def test_a_declined_outcome_falls_through_to_the_chat(paths):
    handler = Handler(delivered=False)
    _, run, requests = await _one_run(paths, handler)
    assert run.status == "completed"
    assert len(handler.calls) == 1
    assert len(requests) == 2


async def test_a_failed_run_reaches_the_handler_as_failed(paths):
    handler = Handler()

    def failing(_config, _model):
        return [RuntimeError("provider down")]

    _, run, _ = await _one_run(paths, handler, failing)
    assert run.status == "failed"
    [(_, _, text, failed, _)] = handler.calls
    assert failed is True
    assert text


async def test_a_handler_that_raises_doesnt_deliver_twice(paths):
    handler = Handler(delivered=RuntimeError("bridge down"))
    _, run, requests = await _one_run(paths, handler)
    assert run.status == "completed"
    assert len(handler.calls) == 1
    assert len(requests) == 1
    assert run.delivery_error == "bridge down"


async def test_a_default_delivery_that_fails_is_recorded_on_the_run(paths):
    async def broken(_channel, _chat, _text):
        raise RuntimeError("chat is gone")

    _, run, _ = await _one_run(paths, None, notifier=broken)
    assert run.status == "completed"
    assert run.delivery_error == "chat is gone"


async def test_without_a_handler_the_outcome_goes_to_the_chat(paths):
    _, run, requests = await _one_run(paths, None)
    assert run.status == "completed"
    assert len(requests) == 2
