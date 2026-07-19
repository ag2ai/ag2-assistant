"""TaskService v2: a run is one agent chat turn on its own stream."""

import asyncio

import pytest

import assistant.gateway.tasks_service as tasks_service_mod
from assistant.config import Config
from assistant.gateway.tasks_service import TaskService
from assistant.hitl import InquiryStore
from assistant.tasks.store import TaskStore


class FakeGateway:
    def __init__(self, reply="run reply", hang=False, folders=None, permissions=None):
        self.sent, self.cancelled, self.deleted = [], [], []
        self._reply, self._hang = reply, hang
        self._gate = asyncio.Event()
        self.folders = folders  # None unless a workdir-grant test wires one in
        self.permissions = permissions  # None unless a task-scoped-rules test wires one in

    async def send_message(self, text, chat_id="default", asker=None, surface="", llm_config_id=None, **kw):
        self.sent.append({"text": text, "chat_id": chat_id, "surface": surface, "model": llm_config_id})
        if self._hang:
            await self._gate.wait()
            return ""  # a user-stopped turn returns "" (TurnCancelled path)
        return self._reply

    async def cancel_turn(self, chat_id, reason="Stopped"):
        self.cancelled.append(chat_id)
        self._gate.set()
        return True

    async def delete_chat(self, chat_id):
        self.deleted.append(chat_id)
        return True


async def _svc(tmp_path, gw, monkeypatch):
    svc = TaskService(
        config=Config(),
        store=TaskStore(path=tmp_path / "tasks.db"),
        inquiry_store=InquiryStore(path=tmp_path / "inq.db"),
    )
    svc.set_gateway(gw)

    async def fake_summary(config, prompt, reply, agent_factory=None):
        return "one-liner"

    monkeypatch.setattr(tasks_service_mod, "summarize_run", fake_summary)
    return svc


async def test_create_validates_schedule_and_model(tmp_path, monkeypatch):
    svc = await _svc(tmp_path, FakeGateway(), monkeypatch)
    with pytest.raises(ValueError):
        await svc.create_task(name="x", prompt="p", schedule={"kind": "cron", "cron": "junk"})
    with pytest.raises(ValueError):
        await svc.create_task(name="x", prompt="p", model="cfg_missing")
    t = await svc.create_task(name="Digest", prompt="collect news")
    assert t["schedule_desc"] == "manual" and t["last_run"] is None


async def test_run_executes_as_chat_turn_with_prior_context(tmp_path, monkeypatch):
    gw = FakeGateway()
    svc = await _svc(tmp_path, gw, monkeypatch)
    t = await svc.create_task(name="Digest", prompt="collect news")
    run = await svc.start_run(t["id"])
    await asyncio.wait_for(svc._jobs_done(), 5)
    assert gw.sent[0]["chat_id"] == f"task-run:{run.id}"
    assert gw.sent[0]["text"] == "collect news"
    view = await svc.get_run(run.id)
    assert view["status"] == "completed" and view["summary"] == "one-liner"
    # second run sees the first run's outcome in its surface
    run2 = await svc.start_run(t["id"])
    await asyncio.wait_for(svc._jobs_done(), 5)
    assert "one-liner" in gw.sent[1]["surface"]
    detail = await svc.get_task(t["id"])
    assert [r["id"] for r in detail["runs"]] == [run2.id, run.id]


async def test_run_with_workdir_mints_chat_grant_and_surface(tmp_path, monkeypatch):
    """A run of a task with an attached workdir mints a chat-scoped grant on the
    run's own stream before the turn, and the surface tells the agent about the
    folder + its access level."""
    from assistant.folders import READ_WRITE, FolderStore

    folders = FolderStore(path=tmp_path / "folders.json")
    gw = FakeGateway(folders=folders)
    svc = await _svc(tmp_path, gw, monkeypatch)
    wd = tmp_path / "proj"
    wd.mkdir()
    t = await svc.create_task(
        name="W", prompt="p", schedule=None, workdir=str(wd), workdir_access="read_write"
    )
    run = await svc.start_run(t["id"])
    await asyncio.wait_for(svc._jobs_done(), 5)
    assert folders.mode_for(wd, svc._config.data_dir.name, f"task-run:{run.id}") == READ_WRITE
    assert f"Working folder: {wd} (read-write)" in gw.sent[0]["surface"]


async def test_run_with_missing_workdir_notes_it_in_surface(tmp_path, monkeypatch):
    """A workdir that no longer exists on disk (deleted/unmounted since the task
    was created) still runs — the surface just flags it so the agent doesn't
    silently assume the folder is there."""
    from assistant.folders import FolderStore

    gw = FakeGateway(folders=FolderStore(path=tmp_path / "folders.json"))
    svc = await _svc(tmp_path, gw, monkeypatch)
    missing = tmp_path / "gone"
    t = await svc.create_task(
        name="M", prompt="p", schedule=None, workdir=str(missing), workdir_access="read"
    )
    await svc.start_run(t["id"])
    await asyncio.wait_for(svc._jobs_done(), 5)
    assert "— path is missing" in gw.sent[0]["surface"]


async def test_stop_run_cancels_the_turn(tmp_path, monkeypatch):
    gw = FakeGateway(hang=True)
    svc = await _svc(tmp_path, gw, monkeypatch)
    t = await svc.create_task(name="Long", prompt="dig forever")
    run = await svc.start_run(t["id"])
    await asyncio.sleep(0.05)  # let the turn start
    assert await svc.stop_run(run.id) is True
    await asyncio.wait_for(svc._jobs_done(), 5)
    assert gw.cancelled == [f"task-run:{run.id}"]
    assert (await svc.get_run(run.id))["status"] == "cancelled"


async def test_fire_rearms_cron_and_exhausts_once(tmp_path, monkeypatch):
    gw = FakeGateway()
    svc = await _svc(tmp_path, gw, monkeypatch)
    t = await svc.create_task(name="C", prompt="p", schedule={"kind": "cron", "cron": "0 9 * * *"})
    before = (await svc.get_task(t["id"]))["next_run_at"]
    await svc._fire(t["id"])
    await asyncio.wait_for(svc._jobs_done(), 5)
    after = await svc.get_task(t["id"])
    assert after["next_run_at"] is not None and after["next_run_at"] != before
    o = await svc.create_task(
        name="O", prompt="p", schedule={"kind": "once", "at": "2026-01-01T00:00:00+00:00"}
    )
    await svc._fire(o["id"])
    await asyncio.wait_for(svc._jobs_done(), 5)
    got = await svc.get_task(o["id"])
    assert got["schedule"]["kind"] == "manual" and got["next_run_at"] is None
    assert got["runs"][0]["trigger"] == "once"


async def test_fire_after_downtime_skips_missed_slots(tmp_path, monkeypatch):
    """A cron task whose next_run_at went stale during downtime fires ONCE and
    re-arms into the future — no catch-up run per missed slot."""
    from datetime import datetime, timedelta

    gw = FakeGateway()
    svc = await _svc(tmp_path, gw, monkeypatch)
    t = await svc.create_task(name="C", prompt="p", schedule={"kind": "cron", "cron": "0 9 * * *"})
    stale = (datetime.now().astimezone() - timedelta(days=3)).isoformat()
    await svc.store.update_task(t["id"], next_run_at=stale)
    await svc._fire(t["id"])
    await asyncio.wait_for(svc._jobs_done(), 5)
    after = await svc.get_task(t["id"])
    assert after["next_run_at"] > datetime.now().astimezone().isoformat()  # future — not the next stale slot
    assert len(after["runs"]) == 1


async def test_failed_turn_marks_run_failed(tmp_path, monkeypatch):
    class Boom(FakeGateway):
        async def send_message(self, *a, **kw):
            raise RuntimeError("provider down")

    svc = await _svc(tmp_path, Boom(), monkeypatch)
    t = await svc.create_task(name="F", prompt="p")
    run = await svc.start_run(t["id"])
    await asyncio.wait_for(svc._jobs_done(), 5)
    view = await svc.get_run(run.id)
    assert view["status"] == "failed" and "provider down" in view["error"]


async def test_delete_task_purges_runs_and_streams(tmp_path, monkeypatch):
    gw = FakeGateway()
    svc = await _svc(tmp_path, gw, monkeypatch)
    t = await svc.create_task(name="D", prompt="p")
    run = await svc.start_run(t["id"])
    await asyncio.wait_for(svc._jobs_done(), 5)
    assert await svc.delete_task(t["id"]) is True
    assert gw.deleted == [f"task-run:{run.id}"]
    assert await svc.get_task(t["id"]) is None and await svc.get_run(run.id) is None


async def test_delete_task_drops_task_scoped_permissions(tmp_path, monkeypatch):
    """delete_task best-effort drops the deleted task's task-scoped command grants —
    they'd otherwise be an orphaned, unreachable JSON entry (Task 4)."""
    from assistant.permissions import PermissionStore

    perms = PermissionStore(path=tmp_path / "perm.json")
    gw = FakeGateway(permissions=perms)
    svc = await _svc(tmp_path, gw, monkeypatch)
    t = await svc.create_task(name="D", prompt="p")
    perms.grant_command("run_shell_command(git *)", task_id=t["id"])
    assert perms.granted_commands(task_id=t["id"]) == ["run_shell_command(git *)"]

    assert await svc.delete_task(t["id"]) is True
    assert perms.granted_commands(task_id=t["id"]) == []


async def test_delete_task_tolerates_gateway_without_permissions(tmp_path, monkeypatch):
    """A gateway stub with no `.permissions` (plain FakeGateway, as most tests use)
    must not break delete_task — dropping task rules is best-effort."""
    svc = await _svc(tmp_path, FakeGateway(), monkeypatch)
    t = await svc.create_task(name="D2", prompt="p")
    assert await svc.delete_task(t["id"]) is True


async def test_list_tasks_carries_last_run_and_unread(tmp_path, monkeypatch):
    gw = FakeGateway()
    svc = await _svc(tmp_path, gw, monkeypatch)
    t = await svc.create_task(name="U", prompt="p")
    run = await svc.start_run(t["id"])
    await asyncio.wait_for(svc._jobs_done(), 5)
    row = (await svc.list_tasks())[0]
    assert row["unread"] == 1 and row["last_run"]["id"] == run.id
    await svc.mark_run_seen(run.id)
    assert (await svc.list_tasks())[0]["unread"] == 0


class _AskingGateway(FakeGateway):
    """A gateway whose turn blocks on the run's own asker (a durable inquiry) —
    used to exercise stop/delete against a run parked on `DurableAsker.ask`."""

    async def send_message(self, text, chat_id="default", asker=None, surface="", llm_config_id=None, **kw):
        self.sent.append({"text": text, "chat_id": chat_id, "surface": surface, "model": llm_config_id})
        from assistant.hitl.base import Question

        await asker.ask(Question(text="proceed?"))
        return "done"  # never reached in these tests — the ask() is cancelled first

    async def cancel_turn(self, chat_id, reason="Stopped"):
        # Not handled at the transport level (no live channel to cancel) — mirrors
        # an orphaned/needs_input run: TaskService falls back to cancelling the job.
        self.cancelled.append(chat_id)
        return False


async def test_stop_run_releases_pending_inquiry(tmp_path, monkeypatch):
    gw = _AskingGateway()
    svc = await _svc(tmp_path, gw, monkeypatch)
    t = await svc.create_task(name="Ask", prompt="need input")
    run = await svc.start_run(t["id"])
    await asyncio.sleep(0.05)  # let the turn start and raise its inquiry
    pending = await svc.pending_inquiries()
    assert len(pending) == 1 and pending[0]["run_id"] == run.id

    assert await svc.stop_run(run.id) is True
    await asyncio.wait_for(svc._jobs_done(), 5)

    assert (await svc.get_run(run.id))["status"] == "cancelled"
    assert await svc.pending_inquiries() == []  # the strip no longer strands it


async def test_delete_task_releases_pending_inquiry(tmp_path, monkeypatch):
    gw = _AskingGateway()
    svc = await _svc(tmp_path, gw, monkeypatch)
    t = await svc.create_task(name="Ask2", prompt="need input")
    await svc.start_run(t["id"])
    await asyncio.sleep(0.05)  # let the turn start and raise its inquiry
    assert len(await svc.pending_inquiries()) == 1

    assert await svc.delete_task(t["id"]) is True
    await asyncio.wait_for(svc._jobs_done(), 5)

    assert await svc.pending_inquiries() == []


async def test_on_inquiry_flips_run_needs_input_and_back(tmp_path, monkeypatch):
    """A raised inquiry flips its RUNNING run to NEEDS_INPUT; answering it flips
    the run back to RUNNING — TaskService._on_inquiry, exercised through the
    real InquiryStore (create/answer), not a mock of the hook itself."""
    svc = await _svc(tmp_path, FakeGateway(), monkeypatch)
    t = await svc.create_task(name="Ask3", prompt="p")
    run = await svc.store.create_run(t["id"])  # RUNNING by default; no job attached
    assert (await svc.get_run(run.id))["status"] == "running"

    inq = await svc.inquiries.create("proceed?", task_id=run.id, channel="web", chat=run.stream_id)
    assert (await svc.get_run(run.id))["status"] == "needs_input"

    assert await svc.answer_inquiry(inq.id, "yes") is True
    assert (await svc.get_run(run.id))["status"] == "running"


async def test_create_task_autonames_when_name_empty(tmp_path, monkeypatch):
    svc = await _svc(tmp_path, FakeGateway(), monkeypatch)

    async def fake_meta(config, prompt, agent_factory=None):
        return "Auto name", "Auto description."

    monkeypatch.setattr(tasks_service_mod, "suggest_task_meta", fake_meta)
    t = await svc.create_task(name="", prompt="do things", schedule=None)
    assert t["name"] == "Auto name" and t["description"] == "Auto description."
    t2 = await svc.create_task(name="Manual", prompt="p", schedule=None, description="given")
    assert t2["name"] == "Manual" and t2["description"] == "given"


async def test_create_task_enforces_workdir_access_invariant(tmp_path, monkeypatch):
    svc = await _svc(tmp_path, FakeGateway(), monkeypatch)
    folder = tmp_path / "work"
    folder.mkdir()

    t = await svc.create_task(name="x", prompt="p", workdir=str(folder))
    assert t["workdir"] == str(folder) and t["workdir_access"] == "read"

    t2 = await svc.create_task(name="y", prompt="p")
    assert t2["workdir"] is None and t2["workdir_access"] is None

    with pytest.raises(ValueError):
        await svc.create_task(name="z", prompt="p", workdir=str(folder), workdir_access="bogus")


async def test_update_task_clearing_workdir_clears_access(tmp_path, monkeypatch):
    svc = await _svc(tmp_path, FakeGateway(), monkeypatch)
    folder = tmp_path / "work"
    folder.mkdir()
    t = await svc.create_task(name="x", prompt="p", workdir=str(folder), workdir_access="read_write")
    assert t["workdir_access"] == "read_write"

    updated = await svc.update_task(t["id"], workdir=None)
    assert updated["workdir"] is None and updated["workdir_access"] is None


async def test_notifier_gets_channel_outcomes_only(tmp_path, monkeypatch):
    gw = FakeGateway()
    svc = await _svc(tmp_path, gw, monkeypatch)
    pushed = []

    async def notify(platform, chat_id, text):
        pushed.append((platform, chat_id, text))

    svc.set_notifier(notify)
    web = await svc.create_task(name="W", prompt="p")  # no origin → no push
    tg = await svc.create_task(
        name="T", prompt="p", origin_channel="telegram", origin_chat="42"
    )
    await svc.start_run(web["id"])
    await svc.start_run(tg["id"])
    await asyncio.wait_for(svc._jobs_done(), 5)
    assert len(pushed) == 1
    platform, chat_id, text = pushed[0]
    assert platform == "telegram" and chat_id == "42" and "one-liner" in text
