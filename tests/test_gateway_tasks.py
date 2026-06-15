"""Gateway task API + durable HITL inquiry endpoints.

The planner/executor are stubbed so these run without an LLM; they exercise the
service wiring, serialisation, and the REST surface the Tasks GUI drives.
"""

import asyncio

from agclaw.gateway.tasks_service import TaskService
from agclaw.hitl import InquiryStore
from agclaw.hitl.base import Question
from agclaw.tasks import DeliverableStatus, TaskManager, TaskStatus, TaskStore


def _service(tmp_path, executor, planner=None):
    store = TaskStore(path=tmp_path / "tasks.db")
    inq = InquiryStore(path=tmp_path / "inq.db")
    mgr = TaskManager(store, executor, inquiry_store=inq)
    return TaskService(
        store=store, inquiry_store=inq, manager=mgr, executor=executor,
        planner_agent=planner or object(),
    )


# ---- service: list / get / cancel ----

async def test_list_and_get_task(tmp_path):
    async def executor(task_id, mgr, asker):
        pass

    svc = _service(tmp_path, executor)
    t = await svc.store.create("Research widgets", objective="A widget report")
    await svc.store.add_deliverable(t.id, "the report")

    listed = await svc.list_tasks()
    assert len(listed) == 1
    assert listed[0]["id"] == t.id
    assert listed[0]["deliverables"] == 1 and listed[0]["deliverables_done"] == 0

    detail = await svc.get_task(t.id)
    assert detail["title"] == "Research widgets"
    assert detail["deliverables"][0]["description"] == "the report"
    assert await svc.get_task("nope") is None


async def test_list_groups_inquiries_and_floats_needs_input_first(tmp_path):
    """Each task summary carries its subtree's pending inquiries, and tasks needing
    input sort to the top (newest-first otherwise)."""
    async def executor(task_id, mgr, asker):
        pass

    svc = _service(tmp_path, executor)
    older = await svc.store.create("older task")
    newer = await svc.store.create("newer task")
    # an inquiry against a SUBTASK should still surface on its root
    child = await svc.store.add_subtask(older.id, "leg", reopen_parent=False)
    inq = await svc.inquiries.create("Pick one?", task_id=child.id, options=["A", "B"])

    listed = await svc.list_tasks()
    assert listed[0]["id"] == older.id  # floated to top: it needs input
    assert listed[0]["inquiries"] and listed[0]["inquiries"][0]["id"] == inq.id
    assert listed[1]["id"] == newer.id and listed[1]["inquiries"] == []


async def test_get_task_includes_subtree_and_assets(tmp_path):
    async def executor(task_id, mgr, asker):
        pass

    svc = _service(tmp_path, executor)
    root = await svc.store.create("parent", objective="do it")
    child = await svc.store.add_subtask(root.id, "leg one", reopen_parent=False)
    d = await svc.store.add_deliverable(child.id, "leg output")
    await svc.store.set_deliverable_status(
        child.id, d["id"], DeliverableStatus.PRODUCED,
        asset={"name": "x", "kind": "text", "content": "THE RESULT"},
    )
    detail = await svc.get_task(root.id)
    assert detail["children"][0]["title"] == "leg one"
    assert detail["children"][0]["deliverables"][0]["asset"] == "THE RESULT"


async def test_cancel_task(tmp_path):
    async def executor(task_id, mgr, asker):
        await asyncio.Event().wait()  # block so it's cancellable

    svc = _service(tmp_path, executor)
    t = await svc.store.create("long one")
    await svc.store.add_deliverable(t.id, "out")
    await svc._manager.submit(t.id, asker=object())
    await asyncio.sleep(0.02)
    assert await svc.cancel(t.id) is True
    await svc._manager.wait(t.id)
    assert (await svc.store.get(t.id)).status == TaskStatus.CANCELLED
    assert await svc.cancel("missing") is False


# ---- service: durable inquiries ----

async def test_pending_and_answer_inquiry(tmp_path):
    async def executor(task_id, mgr, asker):
        pass

    svc = _service(tmp_path, executor)
    inq = await svc.inquiries.create("Proceed?", task_id="t1", options=["Yes", "No"])
    pending = await svc.pending_inquiries()
    assert [p["id"] for p in pending] == [inq.id]
    assert pending[0]["options"] == ["Yes", "No"]
    assert await svc.answer_inquiry(inq.id, "Yes") is True
    assert await svc.pending_inquiries() == []
    assert await svc.answer_inquiry("missing", "x") is False


async def test_submit_request_runs_intake_and_inquiries(tmp_path):
    """submit_request drives intake in the background; a clarifying question shows
    up as a pending inquiry, and answering it lets the task proceed to run."""
    from agclaw.tasks.planner import ClarifyQuestion, PlanDeliverable, TaskPlan

    ran = {}

    async def executor(task_id, mgr, asker):
        ran["id"] = task_id
        tk = await mgr.store.get(task_id)
        for d in tk.pending_deliverables():
            await mgr.store.set_deliverable_status(task_id, d["id"], DeliverableStatus.PRODUCED)

    class _Reply:
        def __init__(self, plan):
            self._plan = plan

        async def content(self):
            return self._plan

    class _Planner:
        """Asks one clarifying question, then (re-plan) returns a runnable plan."""

        def __init__(self):
            self.calls = 0

        async def ask(self, msg, response_schema=None, **k):
            self.calls += 1
            if self.calls == 1:
                return _Reply(TaskPlan(trivial=False, objective="prov",
                                       questions=[ClarifyQuestion(text="Which widget?")]))
            return _Reply(TaskPlan(trivial=False, objective="Report on gizmo widgets",
                                   deliverables=[PlanDeliverable(description="the report")]))

    svc = _service(tmp_path, executor, planner=_Planner())
    task_id = await svc.submit_request("research widgets")

    # the intake question appears as a pending inquiry
    for _ in range(200):
        pend = await svc.pending_inquiries(task_id)
        if pend:
            break
        await asyncio.sleep(0.01)
    assert pend and "Which widget?" in pend[0]["text"]

    await svc.answer_inquiry(pend[0]["id"], "gizmo")

    for _ in range(300):  # task should now plan → run → complete
        cur = await svc.store.get(task_id)
        if cur.is_terminal:
            break
        await asyncio.sleep(0.01)
    assert ran.get("id") == task_id
    assert (await svc.store.get(task_id)).status == TaskStatus.COMPLETED


# ---- REST surface ----

def test_task_rest_endpoints(monkeypatch, tmp_path):
    from fastapi.testclient import TestClient

    import agclaw.gateway.app as app_mod
    import agclaw.gateway.core as core_mod
    from agclaw.config import Config

    monkeypatch.setattr(core_mod, "create_agent", lambda *a, **k: object())
    app = app_mod.create_app(config=Config(data_dir=tmp_path), memory=False, persist=False)
    with TestClient(app) as client:
        # seed a task directly through the started service
        svc = app.state.tasks

        async def _seed():
            t = await svc.store.create("seeded task", objective="obj")
            await svc.store.add_deliverable(t.id, "out")
            i = await svc.inquiries.create("Confirm?", task_id=t.id, options=["Yes", "No"])
            return t.id, i.id

        task_id, inq_id = asyncio.get_event_loop().run_until_complete(_seed())

        tasks = client.get("/api/tasks").json()["tasks"]
        assert any(t["id"] == task_id for t in tasks)

        detail = client.get(f"/api/tasks/{task_id}").json()["task"]
        assert detail["objective"] == "obj"
        assert client.get("/api/tasks/nope").status_code == 404

        pend = client.get("/api/inquiries/pending").json()["pending"]
        assert any(p["id"] == inq_id for p in pend)

        ok = client.post(f"/api/inquiries/{inq_id}/answer", json={"answer": "Yes"})
        assert ok.json()["ok"] is True
        assert client.post("/api/inquiries/missing/answer", json={"answer": "x"}).status_code == 404

        assert client.post(f"/api/tasks/{task_id}/cancel").json()["ok"] is True


async def test_chat_routes_to_control_agent(tmp_path):
    """TaskService.chat builds a task-scoped agent and returns its reply; an unknown
    task returns None (→ 404 at the REST layer)."""
    async def executor(task_id, mgr, asker):
        pass

    svc = _service(tmp_path, executor)
    t = await svc.store.create("research", objective="obj")

    captured = {}

    class _Reply:
        body = "Added the subtask."

    class _Agent:
        async def ask(self, text, stream=None, prompt=None, **k):
            captured["text"] = text
            captured["prompt"] = prompt
            return _Reply()

    # inject a fake controller so no LLM is needed
    svc._control_agents[t.id] = (_Agent(), object())

    reply = await svc.chat(t.id, "also research xAI")
    assert reply == "Added the subtask."
    assert captured["text"] == "also research xAI"
    assert any("Current state of the task" in p for p in captured["prompt"])  # snapshot injected
    assert await svc.chat("missing", "hi") is None


def test_task_chat_endpoint(monkeypatch, tmp_path):
    from fastapi.testclient import TestClient

    import agclaw.gateway.app as app_mod
    import agclaw.gateway.core as core_mod
    from agclaw.config import Config

    monkeypatch.setattr(core_mod, "create_agent", lambda *a, **k: object())
    app = app_mod.create_app(config=Config(data_dir=tmp_path), memory=False, persist=False)
    with TestClient(app) as client:
        svc = app.state.tasks

        class _Reply:
            body = "done"

        class _Agent:
            async def ask(self, text, stream=None, prompt=None, **k):
                return _Reply()

        async def _seed():
            t = await svc.store.create("seeded")
            svc._control_agents[t.id] = (_Agent(), object())
            return t.id

        task_id = asyncio.get_event_loop().run_until_complete(_seed())
        r = client.post(f"/api/tasks/{task_id}/chat", json={"text": "status?"})
        assert r.json()["reply"] == "done"
        assert client.post("/api/tasks/nope/chat", json={"text": "x"}).status_code == 404


def test_ui_has_tasks_hooks(monkeypatch, tmp_path):
    from fastapi.testclient import TestClient

    import agclaw.gateway.app as app_mod
    import agclaw.gateway.core as core_mod
    from agclaw.config import Config

    monkeypatch.setattr(core_mod, "create_agent", lambda *a, **k: object())
    app = app_mod.create_app(config=Config(data_dir=tmp_path), memory=False, persist=False)
    with TestClient(app) as client:
        page = client.get("/").text
        assert 'id="tdrawer"' in page          # the Tasks drawer
        assert "/api/tasks" in page            # it drives the task API
        assert "/api/inquiries/" in page       # and answers durable inquiries
