"""The 6 task tools mirror the UI/REST surface exactly."""

from assistant import peers
from assistant.config import Config
from assistant.gateway.tasks_service import TaskService
from assistant.hitl import InquiryStore
from assistant.system_tools import _origin, _schedule_arg, build_system_tools
from assistant.tasks.store import TaskStore


class _Stream:
    def __init__(self, id):
        self.id = id


class _Ctx:
    def __init__(self, sid):
        self.stream = _Stream(sid)

    async def send(self, event):
        pass


def _svc(tmp_path):
    return TaskService(
        config=Config(),
        store=TaskStore(path=tmp_path / "tasks.db"),
        inquiry_store=InquiryStore(path=tmp_path / "inq.db"),
    )


def _tools(*args, **kwargs):
    """Map tool-name -> the underlying async callable (bypassing the ag2 `@tool`
    FunctionTool wrapper, which exposes `.name` rather than `__name__` and expects
    a ToolCallEvent rather than plain kwargs — its `.model.call` is the original
    function, still callable directly with normal kwargs)."""
    return {t.name: t.model.call for t in build_system_tools(*args, **kwargs)}


def test_origin_is_the_peer_a_chat_was_started_from():
    chat = peers.start_chat("telegram", "42")
    assert _origin(_Ctx(chat)) == ("telegram", "42")
    assert _origin(_Ctx("web-abc")) == (None, None)
    assert _origin(_Ctx("task-run:run_1")) == (None, None)


def test_origin_survives_the_peer_moving_to_another_chat():
    """A task delivers back to the conversation, not to the Chat it was created in."""
    chat = peers.start_chat("telegram", "42")
    peers.start_chat("telegram", "42")
    assert _origin(_Ctx(chat)) == ("telegram", "42")


def test_schedule_arg_shapes():
    assert _schedule_arg("", "", "") == {"kind": "manual", "at": None, "cron": None}
    assert _schedule_arg("cron", "", "@daily") == {"kind": "cron", "at": None, "cron": "@daily"}
    assert _schedule_arg("once", "2026-08-01T09:00:00", "") == {
        "kind": "once",
        "at": "2026-08-01T09:00:00",
        "cron": None,
    }


async def test_create_update_run_delete_via_tools(tmp_path):
    svc = _svc(tmp_path)

    class _Settings:  # the task tools never touch settings; voice tools do
        pass

    tools = _tools(svc, _Settings())
    for name in (
        "create_task",
        "update_task",
        "list_tasks",
        "get_task",
        "run_task_now",
        "delete_task",
    ):
        assert name in tools, f"missing tool {name}"
    ctx = _Ctx(peers.start_chat("telegram", "42"))
    msg = await tools["create_task"](
        name="Digest", prompt="collect news", schedule_kind="cron", cron="0 9 * * *", context=ctx
    )
    assert "Created task task-" in msg
    tid = msg.split("Created task ")[1].split(" ")[0].rstrip(".—").strip()
    detail = await svc.get_task(tid)
    assert detail["schedule"]["cron"] == "0 9 * * *"
    # origin captured from the channel stream for later delivery
    raw = await svc.store.get_task(tid)
    assert raw.origin_channel == "telegram" and raw.origin_chat == "42"
    # bad cron comes back as a correctable message, not an exception
    bad = await tools["create_task"](
        name="X", prompt="p", schedule_kind="cron", cron="junk", context=ctx
    )
    assert "cron" in bad
    out = await tools["update_task"](task_id=tid, paused="true")
    assert "paused" in out.lower()
    listing = await tools["list_tasks"]()
    assert "Digest" in listing and tid in listing
    assert tid in await tools["get_task"](task_id=tid)
    assert "Deleted" in await tools["delete_task"](task_id=tid)


async def test_task_tools_carry_description(tmp_path):
    """Task tools carry the description to/from the service."""
    svc = _svc(tmp_path)

    class _Settings:
        pass

    tools = _tools(svc, _Settings())
    msg = await tools["create_task"](
        name="N",
        prompt="p",
        description="short desc",
        context=_Ctx("web-1"),
    )
    assert "Created task task-" in msg
    tid = msg.split("Created task ")[1].split(" ")[0].rstrip(".—").strip()
    detail = await tools["get_task"](task_id=tid)
    assert "short desc" in detail
