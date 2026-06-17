"""Phase 2: a task's lifecycle rides the AG2 stream as events.

The service translates each transition into the matching AG2 task event and emits
it onto the task's stream (`task:<id>`); scheduling emits a custom `TaskScheduled`.
We capture via a fake emitter so no gateway/LLM is needed.
"""

import pytest

from autogen.beta.events import TaskCompleted, TaskStarted

from agclaw.events import TaskScheduled
from agclaw.gateway.tasks_service import TaskService
from agclaw.tasks import TaskStatus


def _service(tmp_path):
    cfg_dir = tmp_path / "d"
    from agclaw.config import load_config

    cfg = load_config()
    cfg.data_dir = cfg_dir
    return TaskService(config=cfg)


async def _started(tmp_path):
    svc = _service(tmp_path)
    emitted: list = []

    async def emitter(session_id, event):
        emitted.append((session_id, event))

    svc.set_emitter(emitter)
    await svc.start()
    return svc, emitted


async def test_run_emits_started_then_completed_on_task_stream(tmp_path):
    svc, emitted = await _started(tmp_path)
    # a trivial task with one deliverable an executor "produces" immediately
    t = await svc.store.create("ping", objective="say pong")
    d = await svc.store.add_deliverable(t.id, "pong")

    async def fake_executor(task_id, manager, asker):
        await svc.store.set_deliverable_status(task_id, d["id"], "produced",
                                               asset={"name": "pong", "kind": "text", "content": "pong"})

    svc._manager.executor = fake_executor
    await (await svc._manager.submit(t.id))   # run to completion

    kinds = [type(e).__name__ for _, e in emitted]
    assert "TaskStarted" in kinds and "TaskCompleted" in kinds
    # all on the task's own stream
    assert all(sid == f"task:{t.id}" for sid, _ in emitted)
    started = next(e for _, e in emitted if isinstance(e, TaskStarted))
    assert started.task_id == t.id and started.objective == "say pong"
    completed = next(e for _, e in emitted if isinstance(e, TaskCompleted))
    assert completed.task_stream == f"task:{t.id}"
    await svc.close()


async def test_failed_run_emits_task_failed(tmp_path):
    svc, emitted = await _started(tmp_path)
    t = await svc.store.create("flaky", objective="do x")
    await svc.store.add_deliverable(t.id, "x")  # never produced → FAILED

    async def noop_executor(task_id, manager, asker):
        return  # produces nothing

    svc._manager.executor = noop_executor
    await (await svc._manager.submit(t.id))

    assert "TaskFailed" in [type(e).__name__ for _, e in emitted]
    await svc.close()


async def test_schedule_emits_task_scheduled(tmp_path):
    svc, emitted = await _started(tmp_path)
    tid = await svc.schedule_task("daily digest", when="2030-01-01T09:00:00+10:00", recurrence="daily")
    sched = [e for _, e in emitted if isinstance(e, TaskScheduled)]
    assert sched and sched[0].task_id == tid
    assert sched[0].recurrence == "daily"
    await svc.close()
