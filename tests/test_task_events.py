"""Phase 2: a task's lifecycle rides the AG2 stream as events.

The service translates each transition into the matching AG2 task event and emits
it onto the task's stream (`task:<id>`); scheduling emits a custom `TaskScheduled`.
We capture via a fake emitter so no gateway/LLM is needed.
"""

import pytest

from autogen.beta.events import TaskCompleted, TaskStarted

from assistant.events import TaskScheduled
from assistant.gateway.tasks_service import TaskService
from assistant.tasks import TaskStatus


def _service(tmp_path):
    cfg_dir = tmp_path / "d"
    from assistant.config import load_config

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


async def test_deliverable_produced_emits_event(tmp_path):
    from assistant.events import DeliverableProduced

    svc, emitted = await _started(tmp_path)
    await svc._manager.deliverable_produced("task-9", "dlv-1", "the report", "RBA held rates…")
    dp = [e for sid, e in emitted if isinstance(e, DeliverableProduced)]
    assert dp and dp[0].deliverable_id == "dlv-1" and dp[0].description == "the report"
    assert ("task:task-9", dp[0]) in emitted
    await svc.close()


async def test_inquiry_raised_then_answered_emit(tmp_path):
    from assistant.events import InquiryAnswered, InquiryRaised

    svc, emitted = await _started(tmp_path)
    inq = await svc.inquiries.create("Which city?", task_id="task-7",
                                     options=["Sydney", "Perth"], kind="question")
    raised = [e for _, e in emitted if isinstance(e, InquiryRaised)]
    assert raised and raised[0].inquiry_id == inq.id and raised[0].options == ["Sydney", "Perth"]

    await svc.inquiries.answer(inq.id, "Sydney")
    answered = [e for _, e in emitted if isinstance(e, InquiryAnswered)]
    assert answered and answered[0].inquiry_id == inq.id and answered[0].answer == "Sydney"
    await svc.close()


async def test_emit_task_card_helper(tmp_path):
    from assistant.events import TaskCreated
    from assistant.system_tools import _emit_task_card

    sent = []

    class _Ctx:
        async def send(self, event):
            sent.append(event)

    await _emit_task_card(_Ctx(), "task-3", "research ETFs", "task")
    assert isinstance(sent[0], TaskCreated) and sent[0].task_id == "task-3" and sent[0].kind == "task"
    await _emit_task_card(None, "task-3", "x", "task")  # no context → no-op, no error
