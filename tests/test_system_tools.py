"""System tools — the universal agent's retrieval + action surface."""

from assistant.gateway.tasks_service import TaskService
from assistant.hitl import InquiryStore
from assistant.settings import Settings
from assistant.system_tools import _fmt_node, _followup_note, build_system_tools, format_task
from assistant.tasks import TaskManager, TaskStatus, TaskStore


def _settings(tmp_path):
    return Settings(tmp_path / "config.yaml")


def _service(tmp_path):
    store = TaskStore(path=tmp_path / "t.db")
    inq = InquiryStore(path=tmp_path / "i.db")

    async def executor(task_id, mgr, asker):
        pass

    mgr = TaskManager(store, executor, inquiry_store=inq)
    return TaskService(
        store=store, inquiry_store=inq, manager=mgr, executor=executor, planner_agent=object()
    )


class _Chats:
    async def list_chats(self):
        return [{"chat_id": "web-1", "turns": 2, "preview": "hi there"}]

    async def transcript(self, sid):
        return [{"role": "user", "text": "hello"}, {"role": "agent", "text": "hi"}]


def test_tool_set_covers_retrieval_and_actions(tmp_path):
    names = {
        t.name for t in build_system_tools(_service(tmp_path), _settings(tmp_path), chats=_Chats())
    }
    assert {
        "list_tasks",
        "get_task",
        "create_task",
        "schedule_task",
        "reschedule_task",
        "add_subtask",
        "add_deliverable",
        "set_task_objective",
        "cancel_task",
        "delete_task",
        "run_task_now",
        "list_open_questions",
        "answer_question",
        "list_chats",
        "read_chat",
    } <= names


def test_tool_set_without_chats(tmp_path):
    names = {t.name for t in build_system_tools(_service(tmp_path), _settings(tmp_path))}
    assert "list_chats" not in names and "list_tasks" in names


def test_followup_note_only_on_channels():

    assert _followup_note("gateway") == ""  # web: questions surface inline
    assert "web app" in _followup_note("telegram")
    assert "web app" in _followup_note("multi")


def test_build_system_tools_accepts_platform(tmp_path):
    # channels still get the full task toolset (platform only tunes confirmations)
    names = {
        t.name
        for t in build_system_tools(
            _service(tmp_path), _settings(tmp_path), chats=_Chats(), platform="telegram"
        )
    }
    assert {"create_task", "schedule_task", "get_task"} <= names


async def test_format_task_is_concise(tmp_path):
    svc = _service(tmp_path)
    t = await svc.store.create(
        "Trip",
        objective="plan a trip",
        status=TaskStatus.SCHEDULED,
        scheduled_for="2030-01-01T09:00:00",
        recurrence="0 9 * * 1-5",
    )
    await svc.store.add_deliverable(t.id, "itinerary")
    await svc.store.add_subtask(t.id, "book flights", reopen_parent=False)
    node = await svc.get_task(t.id)
    text = format_task(node)
    assert "Trip" in text and "scheduled" in text and "0 9 * * 1-5" in text
    assert "itinerary" in text and "book flights" in text


async def test_get_task_returns_full_asset_surface_previews(tmp_path):
    """The surface summary previews long output (clearly marked), but the get_task
    tool returns the COMPLETE deliverable so follow-ups are faithful."""
    svc = _service(tmp_path)
    t = await svc.store.create("News digest")
    d = await svc.store.add_deliverable(t.id, "headlines")
    body = "RBA holds rates.\n" + ("DETAIL LINE\n" * 400)  # well over the preview cap
    await svc.store.set_deliverable_status(
        t.id,
        d["id"],
        "produced",
        asset={"name": "headlines", "kind": "text", "content": body},
    )

    node = await svc.get_task(t.id)
    surface = format_task(node)  # ambient surface context (what get_task tool guards)
    assert "preview only" in surface  # ambient view is marked as partial
    assert body not in surface  # …and does not contain the whole thing

    full = _fmt_node(node, full=True)  # what the get_task tool returns
    assert body in full  # complete, untruncated output
    assert "preview only" not in full


async def test_run_now_on_scheduled_keeps_schedule(tmp_path):
    svc = _service(tmp_path)
    ran = []
    svc._run_in_bg = lambda tid, ch, clarify=True: ran.append(tid)
    t = await svc.store.create(
        "digest",
        status=TaskStatus.SCHEDULED,
        scheduled_for="2030-01-01T09:00:00",
        recurrence="0 9 * * 1-5",
    )
    msg = await svc.run_now(t.id)
    assert "now" in msg.lower()
    # a fresh occurrence (clone) was kicked off; the template stays scheduled
    assert ran and ran[0] != t.id
    tmpl = await svc.store.get(t.id)
    assert tmpl.status == TaskStatus.SCHEDULED
