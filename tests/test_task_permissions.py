"""Permission management within tasks.

A running task must use the SAME permission/HITL system as a normal turn — it
can't have more access than usual (#11), and prompts go to the task's asker, i.e.
the surface that triggered it (#12). These verify the wiring deterministically,
plus a real end-to-end deny.
"""

import pytest

from agclaw.tasks import TaskManager, TaskStatus, TaskStore, make_task_executor


def _store(tmp_path):
    return TaskStore(path=tmp_path / "tasks.db")


async def test_runner_passes_asker_to_executor(tmp_path):
    """The asker (origin channel) is threaded into the executor → permissions
    and HITL during the task route back to that surface."""
    store = _store(tmp_path)
    t = await store.create("x")
    seen = {}

    async def executor(task_id, mgr, asker):
        seen["asker"] = asker

    mgr = TaskManager(store, executor)
    sentinel = object()
    await mgr.submit(t.id, asker=sentinel)
    await mgr.wait(t.id)
    assert seen["asker"] is sentinel


async def test_executor_binds_task_asker_to_agent(tmp_path, monkeypatch):
    """The real executor builds its agent with the task's asker, so the agent's
    PermissionManager/HITL are bound to the triggering surface (no extra access)."""
    captured = {}

    class _Reply:
        body = "done"

    class _Agent:
        async def ask(self, *a, **k):
            return _Reply()

    def fake_create_agent(config, memory=True, skills=True, asker=None, **k):
        captured["asker"] = asker
        return _Agent()

    import agclaw.agent as agent_mod
    import agclaw.tasks.executor as exec_mod

    monkeypatch.setattr(agent_mod, "create_agent", fake_create_agent)
    monkeypatch.setattr(agent_mod, "turn_prompt", lambda cfg: ["p"])
    # keep verification deterministic (no real LLM in this unit test)
    from agclaw.tasks.executor import _Verdict

    async def _ok(config, deliverable, output):
        return _Verdict(satisfied=True, reason="ok")

    monkeypatch.setattr(exec_mod, "_verify_deliverable", _ok)

    from agclaw.config import Config

    store = _store(tmp_path)
    t = await store.create("do work")
    await store.add_deliverable(t.id, "output")

    executor = make_task_executor(Config())
    asker = object()
    mgr = TaskManager(store, executor)
    await mgr.submit(t.id, asker=asker)
    await mgr.wait(t.id)

    assert captured["asker"] is asker  # agent built with the task's asker
    assert (await store.get(t.id)).status == TaskStatus.COMPLETED


async def test_no_asker_means_no_extra_access():
    """A task with no asker (e.g. unattended/scheduled) can't get permission for
    gated resources — the PermissionManager denies when there's no one to ask."""
    import tempfile
    from pathlib import Path

    from agclaw.permissions import PermissionManager, PermissionStore

    store = PermissionStore(path=Path(tempfile.mkdtemp()) / "p.json")
    mgr = PermissionManager(store, asker=None)
    assert await mgr.check("/some/ungranted/file.txt") is False
    assert await mgr.check_command("run_shell_command", "ls") is False


@pytest.mark.integration
async def test_task_respects_permission_deny(tmp_path):
    """End-to-end: a task asked to read an ungranted file gets a permission prompt
    on its OWN asker, and a deny keeps the file contents out of the deliverable."""
    from agclaw.config import load_config
    from agclaw.permissions import DENY

    secret_dir = tmp_path / "vault"
    secret_dir.mkdir()
    (secret_dir / "secret.txt").write_text("THE-PASSWORD-IS-HUNTER2")

    class DenyingAsker:
        def __init__(self):
            self.asked = 0

        async def ask(self, question, timeout=None):
            self.asked += 1
            return DENY

    cfg = load_config()
    store = _store(tmp_path)
    t = await store.create(
        f"Read the file {secret_dir / 'secret.txt'} and include its exact "
        "contents verbatim in your answer."
    )
    await store.add_deliverable(t.id, "the file contents")

    asker = DenyingAsker()
    mgr = TaskManager(store, make_task_executor(cfg))
    await mgr.submit(t.id, asker=asker)
    await mgr.wait(t.id)

    assert asker.asked >= 1  # the prompt was routed to the task's asker (#12)
    got = await store.get(t.id)
    content = (got.deliverables[0].get("asset") or {}).get("content", "")
    assert "HUNTER2" not in content  # deny held — no more access than normal (#11)
