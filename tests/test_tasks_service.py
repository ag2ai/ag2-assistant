"""TaskService v2: a run is one agent chat turn on its own stream."""

import asyncio

import pytest

from assistant.config import Config
from assistant.gateway.tasks_service import TaskService
from assistant.hitl import InquiryStore
from assistant.tasks.store import TaskStore
from tests.support.fakes import fake_summary_factory


class FakeGateway:
    def __init__(self, reply="run reply", hang=False, folders=None, permissions=None):
        self.sent, self.cancelled, self.deleted = [], [], []
        self._reply, self._hang = reply, hang
        self._gate = asyncio.Event()
        self.folders = folders  # None unless a workdir-grant test wires one in
        self.permissions = permissions  # None unless a task-scoped-rules test wires one in

    async def send_message(
        self, text, chat_id="default", asker=None, surface="", llm_config_id=None, **kw
    ):
        self.sent.append(
            {"text": text, "chat_id": chat_id, "surface": surface, "model": llm_config_id}
        )
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


async def _svc(paths, tmp_path, gw, **summary):
    """A TaskService whose cheap-model leg (run summaries + task auto-naming) is a
    canned structured agent, so nothing here reaches an LLM."""
    svc = TaskService(
        config=Config.for_paths(paths),
        store=TaskStore(path=tmp_path / "tasks.db"),
        inquiry_store=InquiryStore(path=tmp_path / "inq.db"),
        summary_factory=fake_summary_factory(summary="one-liner", **summary),
    )
    svc.set_gateway(gw)
    return svc


async def test_create_validates_schedule_and_model(paths, tmp_path):
    svc = await _svc(paths, tmp_path, FakeGateway())
    with pytest.raises(ValueError):
        await svc.create_task(name="x", prompt="p", schedule={"kind": "cron", "cron": "junk"})
    with pytest.raises(ValueError):
        await svc.create_task(name="x", prompt="p", model="cfg_missing")
    t = await svc.create_task(name="Digest", prompt="collect news")
    assert t["schedule_desc"] == "manual" and t["last_run"] is None


async def test_run_executes_as_chat_turn(paths, tmp_path):
    gw = FakeGateway()
    svc = await _svc(paths, tmp_path, gw)
    t = await svc.create_task(name="Digest", prompt="collect news")
    run = await svc.start_run(t["id"])
    await asyncio.wait_for(svc._jobs_done(), 5)
    assert gw.sent[0]["chat_id"] == f"task-run:{run.id}"
    assert gw.sent[0]["text"] == "collect news"
    view = await svc.get_run(run.id)
    assert view["status"] == "completed" and view["summary"] == "one-liner"
    run2 = await svc.start_run(t["id"])
    await asyncio.wait_for(svc._jobs_done(), 5)
    detail = await svc.get_task(t["id"])
    assert [r["id"] for r in detail["runs"]] == [run2.id, run.id]


async def test_recall_off_by_default_carries_no_prior_runs(paths, tmp_path):
    """The default task looks back at nothing — a run about the present moment
    (weather) is not handed yesterday's outcome (ADR 0027)."""
    gw = FakeGateway()
    svc = await _svc(paths, tmp_path, gw)
    t = await svc.create_task(name="Weather", prompt="today's forecast")
    assert t["recall_depth"] == 0
    await svc.start_run(t["id"])
    await asyncio.wait_for(svc._jobs_done(), 5)
    await svc.start_run(t["id"])
    await asyncio.wait_for(svc._jobs_done(), 5)
    assert "one-liner" not in gw.sent[1]["surface"]
    assert "Earlier runs" not in gw.sent[1]["surface"]


async def test_recall_indexes_prior_runs_with_ids(paths, tmp_path):
    gw = FakeGateway()
    svc = await _svc(paths, tmp_path, gw)
    t = await svc.create_task(name="Digest", prompt="collect news", recall_depth=-1)
    run = await svc.start_run(t["id"])
    await asyncio.wait_for(svc._jobs_done(), 5)
    await svc.start_run(t["id"])
    await asyncio.wait_for(svc._jobs_done(), 5)
    surface = gw.sent[1]["surface"]
    assert "Earlier runs of this task" in surface
    assert f"- {run.id}" in surface and "one-liner" in surface
    assert 'read_run("<run id>")' in surface


async def test_recall_lists_newest_first(paths, tmp_path):
    """Same order as list_runs and the task page's run list, so what the user sees is
    what the run is given."""
    from assistant.tasks.model import RunStatus

    gw = FakeGateway()
    svc = await _svc(paths, tmp_path, gw)
    t = await svc.create_task(name="Digest", prompt="collect news", recall_depth=-1)
    ids = []
    for i in range(3):
        r = await svc._store.create_run(t["id"])
        await svc._store.set_run_status(r.id, RunStatus.COMPLETED, summary=f"outcome {i}")
        ids.append(r.id)

    await svc.start_run(t["id"])
    await asyncio.wait_for(svc._jobs_done(), 5)
    surface = gw.sent[0]["surface"]
    assert [surface.index(i) for i in ids] == sorted((surface.index(i) for i in ids), reverse=True)


async def test_recall_keeps_a_settled_run_that_has_no_summary(paths, tmp_path):
    """A failed run occupies a slot and points at itself rather than vanishing: it may
    have committed work before it settled (ADR 0018), and skipping it would also slide
    the window further into the past. Its error text stays out of the prompt."""
    from assistant.tasks.model import RunStatus

    gw = FakeGateway()
    svc = await _svc(paths, tmp_path, gw)
    t = await svc.create_task(name="Digest", prompt="collect news", recall_depth=2)
    dead = await svc._store.create_run(t["id"])
    await svc._store.set_run_status(dead.id, RunStatus.FAILED, error="400 Bad Request {json}")
    good = await svc._store.create_run(t["id"])
    await svc._store.set_run_status(good.id, RunStatus.COMPLETED, summary="wrote the digest")

    await svc.start_run(t["id"])
    await asyncio.wait_for(svc._jobs_done(), 5)
    surface = gw.sent[0]["surface"]
    assert f"- {dead.id} (" in surface and f'failed, use read_run("{dead.id}") to check.' in surface
    assert "400 Bad Request" not in surface
    # depth 2 spends a slot on the failure instead of reaching past it
    assert "wrote the digest" in surface


async def test_recall_budget_drops_oldest_and_says_how_many(paths, tmp_path):
    from assistant.gateway import tasks_service as ts
    from assistant.tasks.model import RunStatus

    gw = FakeGateway()
    svc = await _svc(paths, tmp_path, gw)
    t = await svc.create_task(name="Digest", prompt="collect news", recall_depth=-1)
    ids = []
    for i in range(6):
        r = await svc._store.create_run(t["id"])
        await svc._store.set_run_status(
            r.id, RunStatus.COMPLETED, summary=f"outcome {i} " + "x" * 80
        )
        ids.append(r.id)

    monkey = ts._RECALL_BUDGET
    ts._RECALL_BUDGET = 300  # room for a couple of rows, not six
    try:
        await svc.start_run(t["id"])
        await asyncio.wait_for(svc._jobs_done(), 5)
    finally:
        ts._RECALL_BUDGET = monkey
    surface = gw.sent[0]["surface"]
    assert ids[-1] in surface and ids[0] not in surface  # newest kept, oldest dropped
    assert "older runs — get_task lists every one" in surface


async def test_recall_depth_is_validated(paths, tmp_path):
    svc = await _svc(paths, tmp_path, FakeGateway())
    with pytest.raises(ValueError):
        await svc.create_task(name="x", prompt="p", recall_depth=-2)
    t = await svc.create_task(name="x", prompt="p")
    with pytest.raises(ValueError):
        await svc.update_task(t["id"], recall_depth=-5)


async def test_run_turn_names_the_task_model_to_the_gateway(paths, tmp_path):
    """The run's own turn passes the task's model explicitly — the wiring that keeps a
    task's automated work on the model it was configured with, above any Chat override
    on the run's thread (ADR 0025)."""
    from assistant.llm_configs import LlmConfigStore

    cfg = LlmConfigStore(paths).save_config({"name": "B", "type": "gemini", "model": "model-b"})
    gw = FakeGateway()
    svc = await _svc(paths, tmp_path, gw)
    t = await svc.create_task(name="Digest", prompt="collect news", model=cfg["id"])
    await svc.start_run(t["id"])
    await asyncio.wait_for(svc._jobs_done(), 5)
    assert gw.sent[0]["model"] == cfg["id"]


async def test_run_turn_does_not_mint_chat_grant(paths, tmp_path):
    """A run no longer mints a chat-scoped folder grant on its own stream — folder
    access is task-scoped now (task-run streams derive the task_id), so a run leaves
    the FolderStore untouched."""
    from assistant.folders import FolderStore

    folders = FolderStore(path=tmp_path / "folders.json")
    gw = FakeGateway(folders=folders)
    svc = await _svc(paths, tmp_path, gw)
    t = await svc.create_task(name="R", prompt="p")
    await svc.start_run(t["id"])
    await asyncio.wait_for(svc._jobs_done(), 5)
    grants = [g for f in folders.list_folders() for g in f["grants"]]
    assert grants == []  # nothing minted at all


async def test_run_surface_lists_task_scope_folders(paths, tmp_path):
    """The run's surface lists the task's own folders resolved from live task-scope
    grants (not a stored workdir) so the unattended agent knows where to work."""
    from assistant.folders import READ_WRITE, FolderStore

    folders = FolderStore(path=tmp_path / "folders.json")
    gw = FakeGateway(folders=folders)
    svc = await _svc(paths, tmp_path, gw)
    wd = tmp_path / "proj"
    wd.mkdir()
    t = await svc.create_task(name="W", prompt="p")
    folders.grant_path(str(wd), READ_WRITE, svc._config.data_dir.name, task_id=t["id"])
    await svc.start_run(t["id"])
    await asyncio.wait_for(svc._jobs_done(), 5)
    assert f"Working folder: {wd} (read-write)" in gw.sent[0]["surface"]


async def test_migration_turns_workdir_into_task_scope_grant(paths, tmp_path):
    """start() migrates a legacy single-workdir task into a task-scope Folder Grant
    exactly once (idempotent — a second start() adds no duplicate)."""
    import json

    from assistant.folders import FolderStore

    root = tmp_path / "root"
    data = root / "profiles" / "p1"
    data.mkdir(parents=True)
    cfg = Config.for_paths(paths, root_dir=root, data_dir=data)
    store = TaskStore(path=data / "tasks.db")
    svc = TaskService(config=cfg, store=store, inquiry_store=InquiryStore(path=data / "inq.db"))
    t = await store.create_task(name="W", prompt="p")
    wd = tmp_path / "proj"
    wd.mkdir()
    from assistant.tasks.store import _TASKS

    raw = json.loads(await store._store.read(f"{_TASKS}{t.id}.json"))
    raw["workdir"] = str(wd)
    raw["workdir_access"] = "read_write"
    await store._store.write(f"{_TASKS}{t.id}.json", json.dumps(raw))

    await svc.start(scheduler=False)
    folders = FolderStore(root / "folders.json")
    assert folders.mode_for(wd, "p1", task_id=t.id) == "read_write"

    await svc.start(scheduler=False)  # idempotent
    folders = FolderStore(root / "folders.json")
    grants = [g for f in folders.list_folders() for g in f["grants"]]
    assert len(grants) == 1


async def test_migration_grant_failure_does_not_drop_later_tasks(paths, tmp_path):
    """A grant_path hiccup on one legacy task must not sink the whole migration —
    the next legacy task's grant is still minted."""
    import json

    from assistant.folders import FolderStore

    root = tmp_path / "root"
    data = root / "profiles" / "p1"
    data.mkdir(parents=True)
    cfg = Config.for_paths(paths, root_dir=root, data_dir=data)
    store = TaskStore(path=data / "tasks.db")
    svc = TaskService(config=cfg, store=store, inquiry_store=InquiryStore(path=data / "inq.db"))

    t1 = await store.create_task(name="W1", prompt="p")
    t2 = await store.create_task(name="W2", prompt="p")
    wd2 = tmp_path / "proj2"
    wd2.mkdir()
    # A legacy workdir that cannot be resolved at all (embedded NUL): granting it
    # genuinely raises, which is the hiccup the batch has to survive.
    unresolvable = f"{tmp_path}/proj1\x00"
    from assistant.tasks.store import _TASKS

    for tid, wd in ((t1.id, unresolvable), (t2.id, str(wd2))):
        raw = json.loads(await store._store.read(f"{_TASKS}{tid}.json"))
        raw["workdir"] = wd
        raw["workdir_access"] = "read_write"
        await store._store.write(f"{_TASKS}{tid}.json", json.dumps(raw))

    await svc.start(scheduler=False)

    folders = FolderStore(root / "folders.json")
    assert folders.mode_for(wd2, "p1", task_id=t2.id) == "read_write"  # still minted
    # the unresolvable one is simply absent — no half-written Folder for it
    assert [f["path"] for f in folders.list_folders()] == [str(wd2.resolve())]
    await svc.close()
    await svc.close()


async def test_delete_task_drops_task_scope_folder_grants(paths, tmp_path):
    """delete_task best-effort drops the deleted task's task-scope folder grants,
    and survives a folders store that raises (dropping grants is best-effort)."""
    from assistant.folders import READ_WRITE, FolderStore

    folders = FolderStore(path=tmp_path / "folders.json")
    gw = FakeGateway(folders=folders)
    svc = await _svc(paths, tmp_path, gw)
    profile = svc._config.data_dir.name
    t = await svc.create_task(name="D", prompt="p")
    wd = tmp_path / "proj"
    wd.mkdir()
    folders.grant_path(str(wd), READ_WRITE, profile, task_id=t["id"])
    assert folders.mode_for(wd, profile, task_id=t["id"]) == READ_WRITE

    assert await svc.delete_task(t["id"]) is True
    assert folders.mode_for(wd, profile, task_id=t["id"]) is None

    # a folders store that throws must not sink delete_task
    class _BoomFolders:
        def drop_task(self, task_id):
            raise RuntimeError("boom")

    gw.folders = _BoomFolders()
    t2 = await svc.create_task(name="D2", prompt="p")
    assert await svc.delete_task(t2["id"]) is True


async def test_stop_run_cancels_the_turn(paths, tmp_path):
    gw = FakeGateway(hang=True)
    svc = await _svc(paths, tmp_path, gw)
    t = await svc.create_task(name="Long", prompt="dig forever")
    run = await svc.start_run(t["id"])
    await asyncio.sleep(0.05)  # let the turn start
    assert await svc.stop_run(run.id) is True
    await asyncio.wait_for(svc._jobs_done(), 5)
    assert gw.cancelled == [f"task-run:{run.id}"]
    assert (await svc.get_run(run.id))["status"] == "cancelled"


async def test_fire_rearms_cron_and_exhausts_once(paths, tmp_path):
    gw = FakeGateway()
    svc = await _svc(paths, tmp_path, gw)
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


async def test_fire_after_downtime_skips_missed_slots(paths, tmp_path):
    """A cron task whose next_run_at went stale during downtime fires ONCE and
    re-arms into the future — no catch-up run per missed slot."""
    from datetime import datetime, timedelta

    gw = FakeGateway()
    svc = await _svc(paths, tmp_path, gw)
    t = await svc.create_task(name="C", prompt="p", schedule={"kind": "cron", "cron": "0 9 * * *"})
    stale = (datetime.now().astimezone() - timedelta(days=3)).isoformat()
    await svc.store.update_task(t["id"], next_run_at=stale)
    await svc._fire(t["id"])
    await asyncio.wait_for(svc._jobs_done(), 5)
    after = await svc.get_task(t["id"])
    assert (
        after["next_run_at"] > datetime.now().astimezone().isoformat()
    )  # future — not the next stale slot
    assert len(after["runs"]) == 1


async def test_failed_turn_marks_run_failed(paths, tmp_path):
    class Boom(FakeGateway):
        async def send_message(self, *a, **kw):
            raise RuntimeError("provider down")

    svc = await _svc(paths, tmp_path, Boom())
    t = await svc.create_task(name="F", prompt="p")
    run = await svc.start_run(t["id"])
    await asyncio.wait_for(svc._jobs_done(), 5)
    view = await svc.get_run(run.id)
    assert view["status"] == "failed" and "provider down" in view["error"]


async def test_delete_task_purges_runs_and_streams(paths, tmp_path):
    gw = FakeGateway()
    svc = await _svc(paths, tmp_path, gw)
    t = await svc.create_task(name="D", prompt="p")
    run = await svc.start_run(t["id"])
    await asyncio.wait_for(svc._jobs_done(), 5)
    assert await svc.delete_task(t["id"]) is True
    assert gw.deleted == [f"task-run:{run.id}"]
    assert await svc.get_task(t["id"]) is None and await svc.get_run(run.id) is None


async def test_delete_task_drops_task_scoped_permissions(paths, tmp_path):
    """delete_task best-effort drops the deleted task's task-scoped command grants —
    they'd otherwise be an orphaned, unreachable JSON entry (Task 4)."""
    from assistant.permissions import PermissionStore

    perms = PermissionStore(path=tmp_path / "perm.json")
    gw = FakeGateway(permissions=perms)
    svc = await _svc(paths, tmp_path, gw)
    t = await svc.create_task(name="D", prompt="p")
    perms.grant_command("run_shell_command(git *)", task_id=t["id"])
    assert perms.granted_commands(task_id=t["id"]) == ["run_shell_command(git *)"]

    assert await svc.delete_task(t["id"]) is True
    assert perms.granted_commands(task_id=t["id"]) == []


async def test_delete_task_tolerates_gateway_without_permissions(paths, tmp_path):
    """A gateway stub with no `.permissions` (plain FakeGateway, as most tests use)
    must not break delete_task — dropping task rules is best-effort."""
    svc = await _svc(paths, tmp_path, FakeGateway())
    t = await svc.create_task(name="D2", prompt="p")
    assert await svc.delete_task(t["id"]) is True


async def test_list_tasks_carries_last_run_and_unread(paths, tmp_path):
    gw = FakeGateway()
    svc = await _svc(paths, tmp_path, gw)
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

    async def send_message(
        self, text, chat_id="default", asker=None, surface="", llm_config_id=None, **kw
    ):
        self.sent.append(
            {"text": text, "chat_id": chat_id, "surface": surface, "model": llm_config_id}
        )
        from assistant.hitl.base import Question

        await asker.ask(Question(text="proceed?"))
        return "done"  # never reached in these tests — the ask() is cancelled first

    async def cancel_turn(self, chat_id, reason="Stopped"):
        # Not handled at the transport level (no live channel to cancel) — mirrors
        # an orphaned/needs_input run: TaskService falls back to cancelling the job.
        self.cancelled.append(chat_id)
        return False


async def test_stop_run_releases_pending_inquiry(paths, tmp_path):
    gw = _AskingGateway()
    svc = await _svc(paths, tmp_path, gw)
    t = await svc.create_task(name="Ask", prompt="need input")
    run = await svc.start_run(t["id"])
    await asyncio.sleep(0.05)  # let the turn start and raise its inquiry
    pending = await svc.pending_inquiries()
    assert len(pending) == 1 and pending[0]["run_id"] == run.id

    assert await svc.stop_run(run.id) is True
    await asyncio.wait_for(svc._jobs_done(), 5)

    assert (await svc.get_run(run.id))["status"] == "cancelled"
    assert await svc.pending_inquiries() == []  # the strip no longer strands it


async def test_delete_task_releases_pending_inquiry(paths, tmp_path):
    gw = _AskingGateway()
    svc = await _svc(paths, tmp_path, gw)
    t = await svc.create_task(name="Ask2", prompt="need input")
    await svc.start_run(t["id"])
    await asyncio.sleep(0.05)  # let the turn start and raise its inquiry
    assert len(await svc.pending_inquiries()) == 1

    assert await svc.delete_task(t["id"]) is True
    await asyncio.wait_for(svc._jobs_done(), 5)

    assert await svc.pending_inquiries() == []


async def test_on_inquiry_flips_run_needs_input_and_back(paths, tmp_path):
    """A raised inquiry flips its RUNNING run to NEEDS_INPUT; answering it flips
    the run back to RUNNING — TaskService._on_inquiry, exercised through the
    real InquiryStore (create/answer), not a mock of the hook itself."""
    svc = await _svc(paths, tmp_path, FakeGateway())
    t = await svc.create_task(name="Ask3", prompt="p")
    run = await svc.store.create_run(t["id"])  # RUNNING by default; no job attached
    assert (await svc.get_run(run.id))["status"] == "running"

    inq = await svc.inquiries.create("proceed?", task_id=run.id, channel="web", chat=run.stream_id)
    assert (await svc.get_run(run.id))["status"] == "needs_input"

    assert await svc.answer_inquiry(inq.id, "yes") is True
    assert (await svc.get_run(run.id))["status"] == "running"


async def test_create_task_autonames_when_name_empty(paths, tmp_path):
    svc = await _svc(
        paths, tmp_path, FakeGateway(), name="Auto name", description="Auto description."
    )
    t = await svc.create_task(name="", prompt="do things", schedule=None)
    assert t["name"] == "Auto name" and t["description"] == "Auto description."
    t2 = await svc.create_task(name="Manual", prompt="p", schedule=None, description="given")
    assert t2["name"] == "Manual" and t2["description"] == "given"


async def test_notifier_gets_channel_outcomes_only(paths, tmp_path):
    gw = FakeGateway()
    svc = await _svc(paths, tmp_path, gw)
    pushed = []

    async def notify(platform, chat_id, text):
        pushed.append((platform, chat_id, text))

    svc.set_notifier(notify)
    web = await svc.create_task(name="W", prompt="p")  # no origin → no push
    tg = await svc.create_task(name="T", prompt="p", origin_channel="telegram", origin_chat="42")
    await svc.start_run(web["id"])
    await svc.start_run(tg["id"])
    await asyncio.wait_for(svc._jobs_done(), 5)
    assert len(pushed) == 1
    platform, chat_id, text = pushed[0]
    assert platform == "telegram" and chat_id == "42" and "one-liner" in text
