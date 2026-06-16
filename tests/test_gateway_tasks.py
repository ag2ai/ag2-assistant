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


async def test_archive_hides_from_drawer_and_filters_listing(tmp_path):
    async def executor(task_id, mgr, asker):
        pass

    svc = _service(tmp_path, executor)
    a = await svc.store.create("active one")  # pending
    done = await svc.store.create("done one", status=TaskStatus.COMPLETED)
    old = await svc.store.create("to archive", status=TaskStatus.COMPLETED)

    # archive one (completed → terminal → allowed); missing → not ok
    ok, _ = await svc.set_archived(old.id)
    assert ok
    ok, _ = await svc.set_archived("missing")
    assert not ok

    drawer_ids = {t["id"] for t in await svc.list_tasks()}
    assert old.id not in drawer_ids  # archived hidden from the drawer
    assert {a.id, done.id} <= drawer_ids

    all_ids = {t["id"] for t in await svc.list_all()}
    assert all_ids == {a.id, done.id}  # 'all' excludes archived too

    archived = await svc.list_all(status="archived")
    assert [t["id"] for t in archived] == [old.id] and archived[0]["archived"] is True

    active = await svc.list_all(status="active")
    assert {t["id"] for t in active} == {a.id}
    completed = await svc.list_all(status="completed")
    assert {t["id"] for t in completed} == {done.id}

    # an active (pending) task can't be archived — cancel it instead
    ok, reason = await svc.set_archived(a.id)
    assert not ok and reason == "active"
    assert a.id in {t["id"] for t in await svc.list_tasks()}  # still visible


def test_archive_and_all_rest_endpoints(monkeypatch, tmp_path):
    from fastapi.testclient import TestClient

    import agclaw.gateway.app as app_mod
    import agclaw.gateway.core as core_mod
    from agclaw.config import Config

    monkeypatch.setattr(core_mod, "create_agent", lambda *a, **k: object())
    app = app_mod.create_app(config=Config(data_dir=tmp_path), memory=False, persist=False)
    with TestClient(app) as client:
        svc = app.state.tasks

        async def _seed():
            done = await svc.store.create("t1", status=TaskStatus.COMPLETED)  # terminal
            active = await svc.store.create("t2")  # pending
            return done.id, active.id

        tid, active_id = asyncio.get_event_loop().run_until_complete(_seed())

        # /api/tasks/all must not be captured as a task id
        assert client.get("/api/tasks/all").status_code == 200
        assert any(t["id"] == tid for t in client.get("/api/tasks/all").json()["tasks"])

        # a finished task archives fine
        assert client.post(f"/api/tasks/{tid}/archive").json() == {"ok": True, "archived": True}
        assert all(t["id"] != tid for t in client.get("/api/tasks").json()["tasks"])  # gone from drawer
        assert any(t["id"] == tid for t in client.get("/api/tasks/all?status=archived").json()["tasks"])
        # unarchive
        assert client.post(f"/api/tasks/{tid}/archive", json={"archived": False}).json()["archived"] is False
        assert any(t["id"] == tid for t in client.get("/api/tasks").json()["tasks"])

        # an active task is rejected (409), stays visible
        assert client.post(f"/api/tasks/{active_id}/archive").status_code == 409
        assert any(t["id"] == active_id for t in client.get("/api/tasks").json()["tasks"])


async def test_schedule_task_creates_scheduled(tmp_path):
    async def executor(task_id, mgr, asker):
        pass

    svc = _service(tmp_path, executor)
    tid = await svc.schedule_task("nightly digest", when="2030-01-01T09:00:00", recurrence="daily")
    t = await svc.store.get(tid)
    assert t.status == TaskStatus.SCHEDULED
    assert t.scheduled_for == "2030-01-01T09:00:00" and t.recurrence == "daily"


async def test_fire_one_shot_runs_the_task(tmp_path):
    async def executor(task_id, mgr, asker):
        pass

    svc = _service(tmp_path, executor)
    ran = []
    svc._run_in_bg = lambda tid, ch, clarify=True: ran.append(tid)  # don't invoke the LLM planner
    tid = await svc.schedule_task("one off", when="2020-01-01T09:00:00")
    await svc._fire(tid)
    assert ran == [tid]
    assert (await svc.store.get(tid)).status == TaskStatus.PENDING  # left SCHEDULED


async def test_fire_recurring_spawns_run_and_rearms(tmp_path):
    from datetime import datetime

    async def executor(task_id, mgr, asker):
        pass

    svc = _service(tmp_path, executor)
    ran = []
    svc._run_in_bg = lambda tid, ch, clarify=True: ran.append(tid)
    tid = await svc.schedule_task("daily digest", when="2020-01-01T09:00:00", recurrence="daily")
    await svc._fire(tid)

    assert ran and ran[0] != tid           # a fresh run was spawned, not the template
    tmpl = await svc.store.get(tid)
    assert tmpl.status == TaskStatus.SCHEDULED  # template re-armed, still scheduled
    assert datetime.fromisoformat(tmpl.scheduled_for) > datetime.now().astimezone()


async def test_scheduled_runs_skip_clarification(tmp_path, monkeypatch):
    """An unattended scheduled run plans WITHOUT asking clarifying questions (no
    one to answer), so it never gets abandoned — intake is called with asker=None."""
    import agclaw.tasks.planner as planner_mod

    async def executor(task_id, mgr, asker):
        pass

    svc = _service(tmp_path, executor)
    captured = {}

    async def fake_prepare(store, task_id, agent, asker=None, capabilities=None):
        captured["asker"] = asker

    monkeypatch.setattr(planner_mod, "prepare_task", fake_prepare)

    # interactive path → a durable asker is used
    await svc._prepare_and_run("t-i", "web", clarify=True)
    assert captured["asker"] is not None

    # scheduled/unattended path → no asker (no clarifying questions)
    await svc._prepare_and_run("t-s", "web", clarify=False)
    assert captured["asker"] is None


async def test_schedule_plans_up_front_then_fire_executes(tmp_path):
    """Scheduling clarifies + plans NOW; firing a recurring template clones the
    baked plan into a fresh run and EXECUTES it (no re-planning at run time)."""
    from datetime import datetime, timedelta

    from agclaw.tasks.planner import PlanDeliverable, TaskPlan

    async def executor(task_id, mgr, asker):
        pass

    class _Reply:
        def __init__(self, plan):
            self._plan = plan

        async def content(self):
            return self._plan

    class _Planner:  # returns a runnable plan with no clarifying questions
        async def ask(self, msg, response_schema=None, **k):
            return _Reply(TaskPlan(trivial=True, objective="Daily AI digest",
                                   deliverables=[PlanDeliverable(description="the digest")]))

    svc = _service(tmp_path, executor, planner=_Planner())
    past = (datetime.now().astimezone() - timedelta(minutes=1)).isoformat()
    tid = await svc.schedule_task("daily ai digest", when=past, recurrence="daily")

    for _ in range(400):  # wait for schedule-time planning to bake the deliverable
        t = await svc.store.get(tid)
        if t.deliverables:
            break
        await asyncio.sleep(0.01)
    assert t.status == TaskStatus.SCHEDULED          # armed
    assert t.objective == "Daily AI digest" and t.deliverables  # planned up front

    submitted = []
    orig = svc._manager.submit

    async def spy(task_id, asker=None):
        submitted.append(task_id)
        return await orig(task_id, asker=asker)

    svc._manager.submit = spy
    await svc._fire(tid)

    assert submitted and submitted[0] != tid          # a cloned run was executed
    run = await svc.store.get(submitted[0])
    assert run.objective == "Daily AI digest" and run.deliverables  # plan cloned in
    tmpl = await svc.store.get(tid)
    assert tmpl.status == TaskStatus.SCHEDULED        # template re-armed
    assert datetime.fromisoformat(tmpl.scheduled_for) > datetime.now().astimezone()


def test_schedule_rest_endpoint(monkeypatch, tmp_path):
    from fastapi.testclient import TestClient

    import agclaw.gateway.app as app_mod
    import agclaw.gateway.core as core_mod
    from agclaw.config import Config

    monkeypatch.setattr(core_mod, "create_agent", lambda *a, **k: object())
    app = app_mod.create_app(config=Config(data_dir=tmp_path), memory=False, persist=False)
    with TestClient(app) as client:
        r = client.post("/api/tasks/schedule", json={
            "text": "weekly report", "when": "2030-06-01T09:00:00", "recurrence": "weekly",
        })
        tid = r.json()["id"]
        detail = client.get(f"/api/tasks/{tid}").json()["task"]
        assert detail["status"] == "scheduled"
        assert detail["scheduled_for"] == "2030-06-01T09:00:00"
        assert detail["recurrence"] == "weekly"


def test_build_schedule_task_tool_named():
    from agclaw.agent import _build_schedule_task_tool

    t = _build_schedule_task_tool(lambda r, w, rec: None)
    assert t.name == "schedule_task"


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


def test_build_start_task_tool_named():
    from agclaw.agent import _build_start_task_tool

    t = _build_start_task_tool(lambda r: None)
    assert t.name == "start_task"


def test_ws_emits_task_card_when_agent_spawns(monkeypatch):
    """When the agent starts a background task this turn, the WS pushes a task_card
    after the reply so the chat can link to the task view."""
    from fastapi.testclient import TestClient

    import agclaw.agent as agent_mod
    import agclaw.gateway.app as app_mod

    class _SpawnGateway:
        async def send_message(self, text, session_id="default", asker=None, attachments=None):
            lst = agent_mod.started_tasks_var.get()  # the start_task tool would do this
            if lst is not None:
                lst.append({"id": "task-xyz", "title": text})
            return "Starting that in the background."

        def status(self):
            return {"status": "ok", "sessions": 0}

    app = app_mod.create_app(gateway=_SpawnGateway())
    with TestClient(app) as client:
        with client.websocket_connect("/api/ws") as ws:
            ws.send_json({"text": "research X and write a report", "session_id": "s1"})
            assert ws.receive_json()["type"] == "thinking"
            assert ws.receive_json()["type"] == "reply"
            card = ws.receive_json()
            assert card["type"] == "task_card"
            assert card["id"] == "task-xyz"
            assert card["title"] == "research X and write a report"


def test_ui_has_tasks_hooks(monkeypatch, tmp_path):
    from fastapi.testclient import TestClient

    import agclaw.gateway.app as app_mod
    import agclaw.gateway.core as core_mod
    from agclaw.config import Config

    monkeypatch.setattr(core_mod, "create_agent", lambda *a, **k: object())
    app = app_mod.create_app(config=Config(data_dir=tmp_path), memory=False, persist=False)
    with TestClient(app) as client:
        page = client.get("/").text
        assert 'id="tabTasks"' in page         # the Tasks tab in the unified drawer
        assert 'id="paneTasks"' in page
        assert "/api/tasks" in page            # it drives the task API
        assert "/api/inquiries/" in page       # and answers durable inquiries
