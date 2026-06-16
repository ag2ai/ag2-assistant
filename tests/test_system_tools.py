"""System tools — the universal agent's retrieval + action surface."""

from agclaw.gateway.tasks_service import TaskService
from agclaw.hitl import InquiryStore
from agclaw.system_tools import build_system_tools, format_task
from agclaw.tasks import TaskManager, TaskStatus, TaskStore


def _service(tmp_path):
    store = TaskStore(path=tmp_path / "t.db")
    inq = InquiryStore(path=tmp_path / "i.db")

    async def executor(task_id, mgr, asker):
        pass

    mgr = TaskManager(store, executor, inquiry_store=inq)
    return TaskService(store=store, inquiry_store=inq, manager=mgr, executor=executor,
                       planner_agent=object())


class _Chats:
    async def list_sessions(self):
        return [{"session_id": "web-1", "turns": 2, "preview": "hi there"}]

    async def transcript(self, sid):
        return [{"role": "user", "text": "hello"}, {"role": "agent", "text": "hi"}]


def test_tool_set_covers_retrieval_and_actions(tmp_path):
    names = {t.name for t in build_system_tools(_service(tmp_path), chats=_Chats())}
    assert {
        "list_tasks", "get_task", "create_task", "schedule_task", "reschedule_task",
        "add_subtask", "add_deliverable", "set_task_objective", "cancel_task",
        "archive_task", "run_task_now", "list_open_questions", "answer_question",
        "list_chats", "read_chat",
    } <= names


def test_tool_set_without_chats(tmp_path):
    names = {t.name for t in build_system_tools(_service(tmp_path))}
    assert "list_chats" not in names and "list_tasks" in names


async def test_format_task_is_concise(tmp_path):
    svc = _service(tmp_path)
    t = await svc.store.create("Trip", objective="plan a trip",
                               status=TaskStatus.SCHEDULED,
                               scheduled_for="2030-01-01T09:00:00", recurrence="weekdays")
    await svc.store.add_deliverable(t.id, "itinerary")
    await svc.store.add_subtask(t.id, "book flights", reopen_parent=False)
    node = await svc.get_task(t.id)
    text = format_task(node)
    assert "Trip" in text and "scheduled" in text and "weekdays" in text
    assert "itinerary" in text and "book flights" in text


async def test_run_now_on_scheduled_keeps_schedule(tmp_path):
    svc = _service(tmp_path)
    ran = []
    svc._run_in_bg = lambda tid, ch, clarify=True: ran.append(tid)
    t = await svc.store.create("digest", status=TaskStatus.SCHEDULED,
                               scheduled_for="2030-01-01T09:00:00", recurrence="weekdays")
    msg = await svc.run_now(t.id)
    assert "now" in msg.lower()
    # a fresh occurrence (clone) was kicked off; the template stays scheduled
    assert ran and ran[0] != t.id
    tmpl = await svc.store.get(t.id)
    assert tmpl.status == TaskStatus.SCHEDULED
