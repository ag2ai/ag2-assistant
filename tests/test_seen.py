"""Run 'seen' tracking: mark_seen only stamps a *finished* task (a peek at a still-
running task must not pre-empt its unread indicator), is idempotent, and surfaces in
the summary."""

import pytest

from assistant.config import Config
from assistant.gateway.tasks_service import TaskService
from assistant.tasks.model import Task, TaskStatus


def test_task_seen_at_back_compat():
    # an existing task JSON without seen_at loads fine (defaults to None)
    t = Task.from_dict({"id": "t1", "title": "old task"})
    assert t.seen_at is None


@pytest.mark.asyncio
async def test_mark_seen_only_after_finished_idempotent_and_summarised(tmp_path):

    svc = TaskService(Config(data_dir=tmp_path))
    await svc.start()
    try:
        task = await svc.store.create("a one-off task")  # PENDING
        # not seen yet
        rows = {r["id"]: r for r in await svc.list_all("all")}
        assert rows[task.id]["seen"] is False

        # Peeking while it is still running must NOT stamp seen_at — otherwise the
        # unread indicator would never fire once the task finishes. mark_seen still
        # reports success (the task exists) but records nothing.
        assert await svc.mark_seen(task.id) is True
        assert (await svc.store.get(task.id)).seen_at is None
        rows = {r["id"]: r for r in await svc.list_all("all")}
        assert rows[task.id]["seen"] is False

        # Once finished, opening it stamps seen_at.
        await svc.store.set_status(task.id, TaskStatus.COMPLETED)
        assert await svc.mark_seen(task.id) is True
        first = (await svc.store.get(task.id)).seen_at
        assert first is not None

        # idempotent: a second call doesn't overwrite the timestamp
        assert await svc.mark_seen(task.id) is True
        assert (await svc.store.get(task.id)).seen_at == first

        rows = {r["id"]: r for r in await svc.list_all("all")}
        assert rows[task.id]["seen"] is True

        # unknown task → False
        assert await svc.mark_seen("nope") is False
    finally:
        await svc.close()
