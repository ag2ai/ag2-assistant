"""Run 'seen' tracking: mark_run_seen only stamps a *finished* run (a peek at a
still-running run must not pre-empt its unread indicator), is idempotent, and
survives old records that predate the field."""

import asyncio

from assistant.config import Config
from assistant.gateway.tasks_service import TaskService
from assistant.hitl import InquiryStore
from assistant.tasks.model import Run
from assistant.tasks.store import TaskStore


class _HangingGateway:
    """A gateway whose turn never resolves on its own — mirrors FakeGateway's
    `hang=True` mode in test_tasks_service.py so the run stays RUNNING until
    explicitly stopped."""

    def __init__(self):
        self._gate = asyncio.Event()

    async def send_message(self, *a, **kw):
        await self._gate.wait()
        return ""  # a user-stopped turn returns "" (TurnCancelled path)

    async def cancel_turn(self, chat_id, reason="Stopped"):
        self._gate.set()
        return True

    async def delete_chat(self, chat_id):
        return True


def test_run_seen_at_back_compat():
    # an existing run JSON without seen_at loads fine (defaults to None)
    r = Run.from_dict({"id": "run-1", "task_id": "task-1"})
    assert r.seen_at is None


async def test_mark_run_seen_only_after_finished_idempotent(paths, tmp_path):
    svc = TaskService(
        config=Config.for_paths(paths),
        store=TaskStore(path=tmp_path / "tasks.db"),
        inquiry_store=InquiryStore(path=tmp_path / "inq.db"),
    )
    svc.set_gateway(_HangingGateway())
    try:
        task = await svc.create_task(name="digest", prompt="p")
        run = await svc.start_run(task["id"])
        await asyncio.sleep(0.05)  # let the turn start (still running)

        # Peeking while it is still running must NOT stamp seen_at — otherwise the
        # unread indicator would never fire once the run finishes. mark_run_seen
        # still reports success (the run exists) but records nothing.
        assert await svc.mark_run_seen(run.id) is True
        assert (await svc.store.get_run(run.id)).seen_at is None

        # Once finished (stopped → a terminal status), opening it stamps seen_at.
        assert await svc.stop_run(run.id) is True
        await asyncio.wait_for(svc._jobs_done(), 5)
        assert await svc.mark_run_seen(run.id) is True
        first = (await svc.store.get_run(run.id)).seen_at
        assert first is not None

        # idempotent: a second call doesn't overwrite the timestamp
        assert await svc.mark_run_seen(run.id) is True
        assert (await svc.store.get_run(run.id)).seen_at == first

        # unknown run → False
        assert await svc.mark_run_seen("nope") is False
    finally:
        await svc.close()


async def test_mark_task_runs_seen_stamps_only_this_task_s_finished_runs(paths, tmp_path):
    """The bulk path carries the same invariant as the single one: a run that has not
    finished stays unread, so its indicator still fires when it does."""
    svc = TaskService(
        config=Config.for_paths(paths),
        store=TaskStore(path=tmp_path / "tasks.db"),
        inquiry_store=InquiryStore(path=tmp_path / "inq.db"),
    )
    svc.set_gateway(_HangingGateway())
    try:
        task = await svc.create_task(name="digest", prompt="p")
        other = await svc.create_task(name="weather", prompt="p")

        finished = [await svc.start_run(task["id"]) for _ in range(2)]
        for run in finished:
            assert await svc.stop_run(run.id) is True
        elsewhere = await svc.start_run(other["id"])
        assert await svc.stop_run(elsewhere.id) is True
        await asyncio.wait_for(svc._jobs_done(), 5)

        still_running = await svc.start_run(task["id"])
        await asyncio.sleep(0.05)

        assert await svc.mark_task_runs_seen(task["id"]) == 2
        for run in finished:
            assert (await svc.store.get_run(run.id)).seen_at is not None
        # the live run of this task, and the other task's run, are untouched
        assert (await svc.store.get_run(still_running.id)).seen_at is None
        assert (await svc.store.get_run(elsewhere.id)).seen_at is None

        # idempotent: nothing left to stamp
        assert await svc.mark_task_runs_seen(task["id"]) == 0

        # unknown task → nothing stamped, no error
        assert await svc.mark_task_runs_seen("nope") == 0

        assert await svc.stop_run(still_running.id) is True
        await asyncio.wait_for(svc._jobs_done(), 5)
    finally:
        await svc.close()
