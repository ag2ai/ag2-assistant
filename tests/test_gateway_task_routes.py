"""REST task surface: the service calls the routes lean on, plus app import."""

import pytest

from assistant.config import Config
from assistant.gateway.tasks_service import TaskService
from assistant.hitl import InquiryStore
from assistant.tasks.store import TaskStore


def test_app_imports_cleanly():
    import assistant.gateway.app  # noqa: F401  (route wiring is executed at import)


async def test_update_task_patch_semantics(tmp_path):
    svc = TaskService(
        config=Config(),
        store=TaskStore(path=tmp_path / "tasks.db"),
        inquiry_store=InquiryStore(path=tmp_path / "inq.db"),
    )
    t = await svc.create_task(name="A", prompt="p")
    # partial patch: only what's sent changes
    out = await svc.update_task(t["id"], name="B")
    assert out["name"] == "B" and out["prompt"] == "p"
    out = await svc.update_task(t["id"], schedule={"kind": "cron", "cron": "@daily"}, paused=True)
    assert out["paused"] is True and out["schedule"]["cron"] == "0 0 * * *"
    with pytest.raises(ValueError):
        await svc.update_task(t["id"], schedule={"kind": "cron", "cron": "nope"})
    assert await svc.update_task("task-missing", name="X") is None
