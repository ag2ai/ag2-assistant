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


async def test_subtask_hitl_bubbles_to_task_asker(tmp_path):
    """A subtask's executor receives the SAME asker as the root, so a sub-agent's
    clarification/confirmation bubbles up to the channel that triggered the task
    (#12) — nothing is swallowed at the sub-agent level."""
    store = _store(tmp_path)
    parent = await store.create("parent")
    child = await store.add_subtask(parent.id, "child", reopen_parent=False)
    await store.add_deliverable(child.id, "child output")
    seen: list[tuple[str, object]] = []

    async def executor(task_id, mgr, asker):
        seen.append((task_id, asker))

    mgr = TaskManager(store, executor)
    sentinel = object()
    await mgr.submit(parent.id, asker=sentinel)
    await mgr.wait(parent.id)

    child_askers = [a for (tid, a) in seen if tid == child.id]
    assert child_askers, "the subtask's executor never ran"
    assert all(a is sentinel for a in child_askers)  # same asker → bubbles to user


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

    async def _ok(config, deliverable, output, evidence=None):
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


async def test_subtask_prompt_inherits_parent_context(tmp_path, monkeypatch):
    """A subtask's prompt carries the parent's objective + the user's clarifications,
    so a leaf doesn't work blind (e.g. it knows the trip is to Lisbon)."""
    prompts: list[str] = []

    class _Reply:
        body = "done"

    class _Agent:
        async def ask(self, built_prompt, *a, **k):
            prompts.append(built_prompt)
            return _Reply()

    def fake_create_agent(config, **k):
        return _Agent()

    import agclaw.agent as agent_mod
    import agclaw.tasks.executor as exec_mod

    monkeypatch.setattr(agent_mod, "create_agent", fake_create_agent)
    monkeypatch.setattr(agent_mod, "turn_prompt", lambda cfg: ["p"])
    from agclaw.tasks.executor import _Verdict

    async def _ok(config, deliverable, output, evidence=None):
        return _Verdict(satisfied=True, reason="ok")

    monkeypatch.setattr(exec_mod, "_verify_deliverable", _ok)

    from agclaw.config import Config

    store = _store(tmp_path)
    parent = await store.create("trip prep")
    await store.update(
        parent.id, objective="Prepare a Lisbon travel guide",
        intake={"Where are you going?": "Lisbon"},
    )
    child = await store.add_subtask(parent.id, "Research the weather", reopen_parent=False)
    await store.add_deliverable(child.id, "packing checklist")

    mgr = TaskManager(store, make_task_executor(Config()))
    await mgr.submit(parent.id, asker=object())
    await mgr.wait(parent.id)

    child_prompts = [p for p in prompts if "Research the weather" in p or "packing checklist" in p]
    assert child_prompts, "subtask executor never produced a prompt"
    blob = "\n".join(child_prompts)
    assert "Lisbon" in blob  # parent objective + clarification reached the subtask
    assert "Where are you going?" in blob


async def test_executor_prompt_includes_original_request(tmp_path, monkeypatch):
    """Content supplied IN the request (e.g. 'summarise THIS text: …') survives to
    the executor prompt — the objective is only a paraphrase and would lose it."""
    prompts: list[str] = []

    class _Reply:
        body = "done"

    class _Agent:
        async def ask(self, built_prompt, *a, **k):
            prompts.append(built_prompt)
            return _Reply()

    import agclaw.agent as agent_mod
    import agclaw.tasks.executor as exec_mod

    monkeypatch.setattr(agent_mod, "create_agent", lambda config, **k: _Agent())
    monkeypatch.setattr(agent_mod, "turn_prompt", lambda cfg: ["p"])
    from agclaw.tasks.executor import _Verdict

    async def _ok(config, deliverable, output, evidence=None):
        return _Verdict(satisfied=True, reason="ok")

    monkeypatch.setattr(exec_mod, "_verify_deliverable", _ok)

    from agclaw.config import Config

    store = _store(tmp_path)
    secret = "The mitochondrion is the powerhouse of the cell."
    t = await store.create(f"Summarise this in two sentences: '{secret}'")
    # objective deliberately paraphrases away the actual text
    await store.update(t.id, objective="A two-sentence summary of the provided text.")
    await store.add_deliverable(t.id, "the summary")

    mgr = TaskManager(store, make_task_executor(Config()))
    await mgr.submit(t.id, asker=object())
    await mgr.wait(t.id)

    assert prompts and secret in prompts[0]  # the text to summarise reached the agent


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
