"""Gateway task API + durable HITL inquiry endpoints.

The planner/executor are stubbed so these run without an LLM; they exercise the
service wiring, serialisation, and the REST surface the Tasks GUI drives.
"""

import asyncio

from assistant.gateway.tasks_service import TaskService
from assistant.hitl import InquiryStore
from assistant.tasks import DeliverableStatus, TaskManager, TaskStatus, TaskStore


def _service(tmp_path, executor, planner=None):
    store = TaskStore(path=tmp_path / "tasks.db")
    inq = InquiryStore(path=tmp_path / "inq.db")
    mgr = TaskManager(store, executor, inquiry_store=inq)
    return TaskService(
        store=store,
        inquiry_store=inq,
        manager=mgr,
        executor=executor,
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
        child.id,
        d["id"],
        DeliverableStatus.PRODUCED,
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
    from assistant.tasks.planner import ClarifyQuestion, PlanDeliverable, TaskPlan

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
                return _Reply(
                    TaskPlan(
                        trivial=False,
                        objective="prov",
                        questions=[ClarifyQuestion(text="Which widget?")],
                    )
                )
            return _Reply(
                TaskPlan(
                    trivial=False,
                    objective="Report on gizmo widgets",
                    deliverables=[PlanDeliverable(description="the report")],
                )
            )

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


def _runtime_tasks(client, pid):
    """The started profile runtime's TaskService (for seeding through the store)."""
    return client.app.state.profiles.get(pid).tasks


def test_task_rest_endpoints(monkeypatch):
    from fastapi.testclient import TestClient

    from tests.conftest import api, make_profile_app, use_fake_agent

    use_fake_agent(monkeypatch, lambda *a, **k: object())
    app, pid = make_profile_app()
    with TestClient(app) as client:
        # seed a task directly through the started service
        svc = _runtime_tasks(client, pid)

        async def _seed():
            t = await svc.store.create("seeded task", objective="obj")
            await svc.store.add_deliverable(t.id, "out")
            i = await svc.inquiries.create("Confirm?", task_id=t.id, options=["Yes", "No"])
            return t.id, i.id

        task_id, inq_id = asyncio.run(_seed())

        tasks = client.get(api(pid, "/tasks")).json()["tasks"]
        assert any(t["id"] == task_id for t in tasks)

        detail = client.get(api(pid, f"/tasks/{task_id}")).json()["task"]
        assert detail["objective"] == "obj"
        assert client.get(api(pid, "/tasks/nope")).status_code == 404

        pend = client.get(api(pid, "/inquiries/pending")).json()["pending"]
        assert any(p["id"] == inq_id for p in pend)

        ok = client.post(api(pid, f"/inquiries/{inq_id}/answer"), json={"answer": "Yes"})
        assert ok.json()["ok"] is True
        assert (
            client.post(api(pid, "/inquiries/missing/answer"), json={"answer": "x"}).status_code
            == 404
        )

        assert client.post(api(pid, f"/tasks/{task_id}/cancel")).json()["ok"] is True


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


def test_archive_and_all_rest_endpoints(monkeypatch):
    from fastapi.testclient import TestClient

    from tests.conftest import api, make_profile_app, use_fake_agent

    use_fake_agent(monkeypatch, lambda *a, **k: object())
    app, pid = make_profile_app()
    with TestClient(app) as client:
        svc = _runtime_tasks(client, pid)

        async def _seed():
            done = await svc.store.create("t1", status=TaskStatus.COMPLETED)  # terminal
            active = await svc.store.create("t2")  # pending
            return done.id, active.id

        tid, active_id = asyncio.run(_seed())

        # /tasks/all must not be captured as a task id
        assert client.get(api(pid, "/tasks/all")).status_code == 200
        assert any(t["id"] == tid for t in client.get(api(pid, "/tasks/all")).json()["tasks"])

        # a finished task archives fine
        assert client.post(api(pid, f"/tasks/{tid}/archive")).json() == {
            "ok": True,
            "archived": True,
        }
        assert all(
            t["id"] != tid for t in client.get(api(pid, "/tasks")).json()["tasks"]
        )  # gone from drawer
        assert any(
            t["id"] == tid
            for t in client.get(api(pid, "/tasks/all?status=archived")).json()["tasks"]
        )
        # unarchive
        assert (
            client.post(api(pid, f"/tasks/{tid}/archive"), json={"archived": False}).json()[
                "archived"
            ]
            is False
        )
        assert any(t["id"] == tid for t in client.get(api(pid, "/tasks")).json()["tasks"])

        # an active task is rejected (409), stays visible
        assert client.post(api(pid, f"/tasks/{active_id}/archive")).status_code == 409
        assert any(t["id"] == active_id for t in client.get(api(pid, "/tasks")).json()["tasks"])


async def test_schedule_task_creates_scheduled(tmp_path):
    async def executor(task_id, mgr, asker):
        pass

    svc = _service(tmp_path, executor)
    tid = await svc.schedule_task("nightly digest", when="2030-01-01T09:00:00", recurrence="daily")
    t = await svc.store.get(tid)
    assert t.status == TaskStatus.SCHEDULED
    assert t.scheduled_for.startswith("2030-01-01T09:00:00") and t.recurrence == "daily"


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

    assert ran and ran[0] != tid  # a fresh run was spawned, not the template
    tmpl = await svc.store.get(tid)
    assert tmpl.status == TaskStatus.SCHEDULED  # template re-armed, still scheduled
    assert datetime.fromisoformat(tmpl.scheduled_for) > datetime.now().astimezone()


async def test_template_detail_lists_its_runs(tmp_path):
    """A recurring template's detail includes the runs spawned from it (newest
    first), so its page links to what each occurrence actually did."""

    async def executor(task_id, mgr, asker):
        pass

    svc = _service(tmp_path, executor)
    svc._run_in_bg = lambda tid, ch, clarify=True: None
    tid = await svc.schedule_task("daily digest", when="2020-01-01T09:00:00", recurrence="daily")
    await svc._fire(tid)  # occurrence 1
    await svc._fire(tid)  # occurrence 2

    detail = await svc.get_task(tid)
    runs = detail["runs"]
    assert len(runs) == 2
    assert all(r["id"] != tid for r in runs)
    assert runs[0]["created_at"] >= runs[1]["created_at"]  # newest first
    # a run's own detail carries the back-pointer (and no runs of its own)
    run_detail = await svc.get_task(runs[0]["id"])
    assert run_detail["run_of"] == tid
    assert run_detail["runs"] == []


async def test_scheduled_runs_skip_clarification(tmp_path, monkeypatch):
    """An unattended scheduled run plans WITHOUT asking clarifying questions (no
    one to answer), so it never gets abandoned — intake is called with asker=None."""
    import assistant.tasks.planner as planner_mod

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

    from assistant.tasks.planner import PlanDeliverable, TaskPlan

    async def executor(task_id, mgr, asker):
        pass

    class _Reply:
        def __init__(self, plan):
            self._plan = plan

        async def content(self):
            return self._plan

    class _Planner:  # returns a runnable plan with no clarifying questions
        async def ask(self, msg, response_schema=None, **k):
            return _Reply(
                TaskPlan(
                    trivial=True,
                    objective="Daily AI digest",
                    deliverables=[PlanDeliverable(description="the digest")],
                )
            )

    svc = _service(tmp_path, executor, planner=_Planner())
    past = (datetime.now().astimezone() - timedelta(minutes=1)).isoformat()
    tid = await svc.schedule_task("daily ai digest", when=past, recurrence="daily")

    for _ in range(400):  # wait for schedule-time planning to bake the deliverable
        t = await svc.store.get(tid)
        if t.deliverables:
            break
        await asyncio.sleep(0.01)
    assert t.status == TaskStatus.SCHEDULED  # armed
    assert t.objective == "Daily AI digest" and t.deliverables  # planned up front

    submitted = []
    orig = svc._manager.submit

    async def spy(task_id, asker=None):
        submitted.append(task_id)
        return await orig(task_id, asker=asker)

    svc._manager.submit = spy
    await svc._fire(tid)

    assert submitted and submitted[0] != tid  # a cloned run was executed
    run = await svc.store.get(submitted[0])
    assert run.objective == "Daily AI digest" and run.deliverables  # plan cloned in
    tmpl = await svc.store.get(tid)
    assert tmpl.status == TaskStatus.SCHEDULED  # template re-armed
    assert datetime.fromisoformat(tmpl.scheduled_for) > datetime.now().astimezone()


def test_schedule_rest_endpoint(monkeypatch):
    from fastapi.testclient import TestClient

    from tests.conftest import api, make_profile_app, use_fake_agent

    use_fake_agent(monkeypatch, lambda *a, **k: object())
    app, pid = make_profile_app()
    with TestClient(app) as client:
        r = client.post(
            api(pid, "/tasks/schedule"),
            json={
                "text": "weekly report",
                "when": "2030-06-01T09:00:00",
                "recurrence": "weekly",
            },
        )
        tid = r.json()["id"]
        detail = client.get(api(pid, f"/tasks/{tid}")).json()["task"]
        assert detail["status"] == "scheduled"
        assert detail["scheduled_for"].startswith("2030-06-01T09:00:00")
        assert detail["recurrence"] == "weekly"


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


def test_task_chat_routes_to_universal_agent_with_surface(monkeypatch):
    """The task page talks to the SAME gateway agent, given the task as surface
    context (id + snapshot) — not a separate controller."""
    from fastapi.testclient import TestClient

    from tests.conftest import api, make_profile_app, use_fake_agent

    seen = {}

    class _Reply:
        body = "on it"

    class _Agent:  # the one universal gateway agent
        tools = []

        async def ask(self, *msg, stream=None, prompt=None, **k):
            seen["prompt"] = prompt
            seen["session"] = getattr(stream, "id", None)
            return _Reply()

    use_fake_agent(monkeypatch, lambda *a, **k: _Agent())
    app, pid = make_profile_app()
    with TestClient(app) as client:
        svc = _runtime_tasks(client, pid)
        task_id = asyncio.run(svc.store.create("seeded", objective="do it")).id

        r = client.post(api(pid, f"/tasks/{task_id}/chat"), json={"text": "what's the status?"})
        assert r.json()["reply"] == "on it"
        assert seen["session"] == f"task:{task_id}"  # per-task stream
        assert any(task_id in p for p in seen["prompt"])  # task surface injected
        assert client.post(api(pid, "/tasks/nope/chat"), json={"text": "x"}).status_code == 404
