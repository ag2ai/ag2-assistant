"""Phase 2: a task's lifecycle rides the AG2 stream as events.

The service translates each transition into the matching AG2 task event and emits
it onto the task's stream (`task:<id>`); scheduling emits a custom `TaskScheduled`.
We capture via a fake emitter so no gateway/LLM is needed.
"""

from ag2.events import TaskCompleted, TaskStarted

from assistant.events import TaskScheduled
from assistant.gateway.tasks_service import TaskService


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
        await svc.store.set_deliverable_status(
            task_id, d["id"], "produced", asset={"name": "pong", "kind": "text", "content": "pong"}
        )

    svc._manager.executor = fake_executor
    await (await svc._manager.submit(t.id))  # run to completion

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
    tid = await svc.schedule_task(
        "daily digest", when="2030-01-01T09:00:00+10:00", recurrence="0 9 * * *"
    )
    sched = [e for _, e in emitted if isinstance(e, TaskScheduled)]
    assert sched and sched[0].task_id == tid
    assert sched[0].recurrence == "0 9 * * *"
    await svc.close()


async def test_deliverable_produced_emits_event(tmp_path):
    from assistant.events import DeliverableProduced

    svc, emitted = await _started(tmp_path)
    await svc._manager.deliverable_produced(
        "task-9", "dlv-1", "the report", "RBA held rates…", path="deliverables/the-report.md"
    )
    dp = [e for sid, e in emitted if isinstance(e, DeliverableProduced)]
    assert dp and dp[0].deliverable_id == "dlv-1" and dp[0].description == "the report"
    assert dp[0].path == "deliverables/the-report.md"
    assert ("task:task-9", dp[0]) in emitted
    await svc.close()


async def test_raw_subagent_event_emits_on_task_stream(tmp_path):
    svc, emitted = await _started(tmp_path)
    event = TaskStarted(task_id="sub-1", agent_name="researcher", objective="find sources")

    await svc._manager.emit_event("task-9", event)

    assert ("task:task-9", event) in emitted
    await svc.close()


async def test_visible_subagent_emits_cancelled_when_interrupted(monkeypatch):
    import asyncio
    from types import SimpleNamespace

    import ag2.tools.subagents.run_task as run_task_mod
    from ag2.events import TaskCancelled

    import assistant.agent as agent_mod
    from assistant.config import Config
    from assistant.tasks.executor import _run_visible_subagent

    events = []

    class _Agent:
        name = "worker"
        _hitl_hook = None

    class _Manager:
        async def emit_event(self, task_id, event):
            events.append((task_id, event))

    async def cancelled_run_task(*args, **kwargs):
        raise asyncio.CancelledError()

    monkeypatch.setattr(agent_mod, "create_agent", lambda *a, **k: _Agent())
    monkeypatch.setattr(
        agent_mod, "turn_prompt", lambda cfg, memory=True, workspace=True: ["prompt"]
    )
    monkeypatch.setattr(run_task_mod, "run_task", cancelled_run_task)

    task = SimpleNamespace(id="task-1", title="do work", parent_id=None)
    try:
        await _run_visible_subagent(Config(), task, [], "context", True, object(), _Manager())
    except asyncio.CancelledError:
        pass

    assert len(events) == 1
    assert events[0][0] == "task-1"
    assert isinstance(events[0][1], TaskCancelled)
    assert events[0][1].task_id == "task-1:worker"
    assert events[0][1].agent_name == "worker"


async def test_visible_subagent_forwards_inner_work_as_trace(monkeypatch):
    """The subagent's inner events ride to the parent task as SubagentTrace, so the
    GUI can nest them under the card (and a nested lifecycle nests recursively)."""
    from types import SimpleNamespace

    import ag2.tools.subagents.run_task as run_task_mod
    from ag2.events import TaskStarted

    import assistant.agent as agent_mod
    from assistant.config import Config
    from assistant.events import SubagentTrace
    from assistant.tasks.executor import _run_visible_subagent

    events = []

    class _Agent:
        name = "worker"
        _hitl_hook = None

    class _Manager:
        async def emit_event(self, task_id, event):
            events.append((task_id, event))

    async def fake_run_task(
        agent, objective, *, parent_context, context="", stream=None, task_id=None, **kw
    ):
        # Simulate inner work: a nested subagent's lifecycle on the work stream.
        from ag2.context import ConversationContext

        ev = TaskStarted(task_id="task-1:worker:deep", agent_name="researcher", objective="dig")
        await stream.send(ev, ConversationContext(stream=stream))
        return SimpleNamespace(completed=True, result="done", error=None, stream=stream)

    monkeypatch.setattr(agent_mod, "create_agent", lambda *a, **k: _Agent())
    monkeypatch.setattr(
        agent_mod, "turn_prompt", lambda cfg, memory=True, workspace=True: ["prompt"]
    )
    monkeypatch.setattr(run_task_mod, "run_task", fake_run_task)

    task = SimpleNamespace(id="task-1", title="do work", parent_id=None)
    result = await _run_visible_subagent(Config(), task, [], "ctx", True, object(), _Manager())

    assert result.completed
    traces = [e for _, e in events if isinstance(e, SubagentTrace)]
    assert len(traces) == 1
    assert traces[0].subagent_id == "task-1:worker"
    assert traces[0].inner["type"].split(".")[-1] == "TaskStarted"
    assert traces[0].inner["data"]["agent_name"] == "researcher"


async def test_visible_subagent_surfaces_generated_image_bare(monkeypatch):
    """An ImageGenerated event from a task subagent is user-facing output, not a
    trace — it rides the task stream BARE (never wrapped in SubagentTrace), so the
    GUI renders the same inline thumbnail + viewer as a chat-generated image."""
    from types import SimpleNamespace

    import ag2.tools.subagents.run_task as run_task_mod

    import assistant.agent as agent_mod
    from assistant.config import Config
    from assistant.events import ImageGenerated, SubagentTrace
    from assistant.tasks.executor import _run_visible_subagent

    events = []

    class _Agent:
        name = "worker"
        _hitl_hook = None

    class _Manager:
        async def emit_event(self, task_id, event):
            events.append((task_id, event))

    async def fake_run_task(
        agent, objective, *, parent_context, context="", stream=None, task_id=None, **kw
    ):
        from ag2.context import ConversationContext

        ev = ImageGenerated("images/sunrise.jpg", prompt="a sunrise", media_type="image/jpeg")
        await stream.send(ev, ConversationContext(stream=stream))
        return SimpleNamespace(completed=True, result="done", error=None, stream=stream)

    monkeypatch.setattr(agent_mod, "create_agent", lambda *a, **k: _Agent())
    monkeypatch.setattr(
        agent_mod, "turn_prompt", lambda cfg, memory=True, workspace=True: ["prompt"]
    )
    monkeypatch.setattr(run_task_mod, "run_task", fake_run_task)

    task = SimpleNamespace(id="task-1", title="make an image", parent_id=None)
    result = await _run_visible_subagent(Config(), task, [], "ctx", True, object(), _Manager())

    assert result.completed
    images = [(tid, e) for tid, e in events if isinstance(e, ImageGenerated)]
    assert len(images) == 1
    tid, img = images[0]
    assert tid == "task-1"  # on the parent task stream, not the subagent's
    assert img.path == "images/sunrise.jpg" and img.prompt == "a sunrise"
    assert not [e for _, e in events if isinstance(e, SubagentTrace)]


async def test_chat_inquiry_surfaces_on_its_session_stream(tmp_path):
    """A durable inquiry bound to a chat session emits InquiryRaised on THAT session
    stream (so it renders inline in the chat), and answering it out of band resolves
    the asker — durable, inline chat HITL with no separate live channel."""
    import asyncio

    from assistant.events import InquiryRaised
    from assistant.hitl import DurableAsker, NullAsker, Question

    svc, emitted = await _started(tmp_path)
    asker = DurableAsker(NullAsker(), svc.inquiries, session="web-chat-1")

    pending = asyncio.ensure_future(
        asker.ask(Question(text="Which city?", options=["Sydney", "Perth"]))
    )
    await asyncio.sleep(0.05)  # let create + emit happen before it blocks on the answer

    raised = [e for _, e in emitted if isinstance(e, InquiryRaised)]
    assert raised, "InquiryRaised should be emitted"
    assert ("web-chat-1", raised[0]) in emitted  # on the chat session stream, not task:

    await svc.answer_inquiry(raised[0].inquiry_id, "Sydney")
    assert await asyncio.wait_for(pending, timeout=2) == "Sydney"
    await svc.close()


async def test_inline_answer_falls_back_to_inquiry_store(tmp_path):
    """Answering inline on a task page sends the InquiryStore id over the WS. The
    HitlServer won't know it (different id space), so the gateway falls back to the
    inquiry store — which is what actually resolves a durable task inquiry."""
    from assistant.hitl import HitlServer
    from assistant.hitl.inquiry import InquiryStatus

    svc, _ = await _started(tmp_path)
    inq = await svc.inquiries.create(
        "Which city?", task_id="task-x", options=["Sydney", "Perth"], kind="question"
    )
    hitl = HitlServer()  # does NOT know this id

    iid, ans = inq.id, "Sydney"
    # Mirror app.py's WS answer dispatch: HitlServer first, else the inquiry store.
    if not hitl.answer(iid, ans):
        await svc.answer_inquiry(iid, ans)

    resolved = await svc.inquiries.get(iid)
    assert resolved.status == InquiryStatus.ANSWERED and resolved.answer == "Sydney"
    await svc.close()


async def test_inquiry_raised_then_answered_emit(tmp_path):
    from assistant.events import InquiryAnswered, InquiryRaised

    svc, emitted = await _started(tmp_path)
    inq = await svc.inquiries.create(
        "Which city?", task_id="task-7", options=["Sydney", "Perth"], kind="question"
    )
    raised = [e for _, e in emitted if isinstance(e, InquiryRaised)]
    assert raised and raised[0].inquiry_id == inq.id and raised[0].options == ["Sydney", "Perth"]

    await svc.inquiries.answer(inq.id, "Sydney")
    answered = [e for _, e in emitted if isinstance(e, InquiryAnswered)]
    assert answered and answered[0].inquiry_id == inq.id and answered[0].answer == "Sydney"
    assert answered[0].status == "answered"
    await svc.close()


async def test_inquiry_expire_and_cancel_emit_resolution(tmp_path):
    """Expiry and task-cancel are terminal resolutions too — each must emit an
    InquiryAnswered carrying its status, so the GUI can retire the card instead of
    leaving live-looking buttons that answer nothing (the stranded-permission bug)."""
    from assistant.events import InquiryAnswered

    svc, emitted = await _started(tmp_path)
    exp = await svc.inquiries.create("Run it?", task_id="task-e", kind="permission")
    await svc.inquiries.expire(exp.id)
    can = await svc.inquiries.create("Run it?", task_id="task-c", kind="permission")
    await svc.inquiries.cancel(can.id)

    res = {e.inquiry_id: e for _, e in emitted if isinstance(e, InquiryAnswered)}
    assert res[exp.id].status == "expired" and res[exp.id].answer == ""
    assert res[can.id].status == "cancelled"
    await svc.close()


async def test_emit_task_card_helper(tmp_path):
    from assistant.events import TaskCreated
    from assistant.system_tools import _emit_task_card

    sent = []

    class _Ctx:
        async def send(self, event):
            sent.append(event)

    await _emit_task_card(_Ctx(), "task-3", "research ETFs", "task")
    assert (
        isinstance(sent[0], TaskCreated) and sent[0].task_id == "task-3" and sent[0].kind == "task"
    )
    await _emit_task_card(None, "task-3", "x", "task")  # no context → no-op, no error
