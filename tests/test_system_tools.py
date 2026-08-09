"""The 6 task tools mirror the UI/REST surface exactly."""

from assistant.config import Config
from assistant.gateway.tasks_service import TaskService
from assistant.hitl import InquiryStore
from assistant.peers import PeerStore
from assistant.system_tools import _origin, _schedule_arg, build_system_tools
from assistant.tasks.store import TaskStore
from tests.support.apps import make_paths


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
        config=Config.for_paths(make_paths(tmp_path)),
        store=TaskStore(path=tmp_path / "tasks.db"),
        inquiry_store=InquiryStore(path=tmp_path / "inq.db"),
    )


def _tools(*args, **kwargs):
    """Map tool-name -> the underlying async callable (bypassing the ag2 `@tool`
    FunctionTool wrapper, which exposes `.name` rather than `__name__` and expects
    a ToolCallEvent rather than plain kwargs — its `.model.call` is the original
    function, still callable directly with normal kwargs)."""
    return {t.name: t.model.call for t in build_system_tools(*args, **kwargs)}


def test_origin_is_the_peer_a_chat_was_started_from(paths):
    """The origin is the Connection the conversation arrived on, so the outcome goes
    back out through that bot rather than through whichever one of its platform."""
    chat = PeerStore(paths).start_chat("cn-work", "42", platform="telegram")
    assert _origin(PeerStore(paths), _Ctx(chat)) == ("cn-work", "42")
    assert _origin(PeerStore(paths), _Ctx("web-abc")) == (None, None)
    assert _origin(PeerStore(paths), _Ctx("task-run:run_1")) == (None, None)


def test_origin_survives_the_peer_moving_to_another_chat(paths):
    """A task delivers back to the conversation, not to the Chat it was created in."""
    chat = PeerStore(paths).start_chat("cn-work", "42", platform="telegram")
    PeerStore(paths).start_chat("cn-work", "42", platform="telegram")
    assert _origin(PeerStore(paths), _Ctx(chat)) == ("cn-work", "42")


def test_schedule_arg_shapes():
    assert _schedule_arg("", "", "") == {"kind": "manual", "at": None, "cron": None}
    assert _schedule_arg("cron", "", "@daily") == {"kind": "cron", "at": None, "cron": "@daily"}
    assert _schedule_arg("once", "2026-08-01T09:00:00", "") == {
        "kind": "once",
        "at": "2026-08-01T09:00:00",
        "cron": None,
    }


async def test_create_update_run_delete_via_tools(tmp_path, paths):
    svc = _svc(tmp_path)

    class _Settings:  # the task tools never touch settings; voice tools do
        pass

    tools = _tools(svc, _Settings(), peers=PeerStore(paths))
    for name in (
        "create_task",
        "update_task",
        "list_tasks",
        "get_task",
        "run_task_now",
        "delete_task",
    ):
        assert name in tools, f"missing tool {name}"
    ctx = _Ctx(PeerStore(paths).start_chat("cn-work", "42", platform="telegram"))
    msg = await tools["create_task"](
        name="Digest", prompt="collect news", schedule_kind="cron", cron="0 9 * * *", context=ctx
    )
    assert "Created task task-" in msg
    tid = msg.split("Created task ")[1].split(" ")[0].rstrip(".—").strip()
    detail = await svc.get_task(tid)
    assert detail["schedule"]["cron"] == "0 9 * * *"
    # origin captured from the channel stream for later delivery
    raw = await svc.store.get_task(tid)
    assert raw.origin_channel == "cn-work" and raw.origin_chat == "42"
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


async def test_task_tools_carry_description(tmp_path, paths):
    """Task tools carry the description to/from the service."""
    svc = _svc(tmp_path)

    class _Settings:
        pass

    tools = _tools(svc, _Settings(), peers=PeerStore(paths))
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


async def test_task_tools_carry_recall_depth(tmp_path, paths):
    """The agent can set look-back when it creates a task from a chat or channel, and
    reads it back — '-1' is the answer to "a different topic each time" (ADR 0027)."""
    svc = _svc(tmp_path)

    class _Settings:
        pass

    tools = _tools(svc, _Settings(), peers=PeerStore(paths))
    msg = await tools["create_task"](
        name="N", prompt="a different topic each time", recall_depth=-1, context=_Ctx("web-1")
    )
    tid = msg.split("Created task ")[1].split(" ")[0].rstrip(".—").strip()
    assert (await svc.store.get_task(tid)).recall_depth == -1
    assert "all previous runs" in await tools["get_task"](task_id=tid)
    # the string arg turns it off; a non-numeric one is correctable, not an exception
    await tools["update_task"](task_id=tid, recall_depth="0")
    assert (await svc.store.get_task(tid)).recall_depth == 0
    assert "whole number" in await tools["update_task"](task_id=tid, recall_depth="lots")


async def test_read_run_returns_a_run_transcript(tmp_path, paths):
    """A run's thread is a chat on `task-run:<id>`, so the agent can open one in full
    when the one-line summary is too coarse."""
    svc = _svc(tmp_path)

    class _Settings:
        pass

    class _Chats:
        async def list_chats(self):
            return []

        async def transcript(self, chat_id):
            if chat_id != "task-run:run-abc":
                return []
            return [{"role": "user", "text": "collect news"}, {"role": "assistant", "text": "done"}]

    tools = _tools(svc, _Settings(), chats=_Chats(), peers=PeerStore(paths))
    out = await tools["read_run"](run_id="run-abc")
    assert "collect news" in out and "done" in out
    assert "No such run" in await tools["read_run"](run_id="run-nope")
