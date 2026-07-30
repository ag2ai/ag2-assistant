"""Coding-run orchestration (assistant.coding.session).

Most tests drive ``run_coding_session`` through the injectable ``runner`` seam,
fast and deterministic. The default runner itself is covered by
``test_default_runner_survives_held_stream_turn_lock`` via ``fake_acp_config``
(an in-process scripted agent, no subprocess); the real adapter is covered by
the @integration test.
"""

import pytest
from ag2.acp.events import ACPPlan, ACPPlanEntry
from ag2.context import ConversationContext
from ag2.stream import MemoryStream

from assistant.coding import session as sessmod
from assistant.events import A2UISurface
from tests.support.stubs import write_stub

pytestmark = pytest.mark.asyncio

# No bridge is ever configured here: it arrives as an argument, so the default
# (None) already means "local subprocess mode" regardless of the developer's .env.


class FakePM:
    def __init__(self, allow=True):
        self.allow = allow
        self.checked = []

    async def check(self, target):
        self.checked.append(str(target))
        return self.allow


def _ctx_with_collector():
    stream = MemoryStream(id="s")
    surfaces: list = []

    async def collect(event):
        if isinstance(event, A2UISurface):
            surfaces.append(event)

    stream.subscribe(collect)
    return ConversationContext(stream=stream), surfaces


def _only_claude(tmp_path) -> list:
    """A search path where Claude Code's adapter is the one real executable."""
    bin_dir = tmp_path / "bin"
    write_stub(bin_dir / "claude-agent-acp")
    return [bin_dir]


async def test_no_agent_available_returns_message():
    """An empty search path is an empty inventory — nothing to run."""
    ctx, _ = _ctx_with_collector()
    pm = FakePM()
    out = await sessmod.run_coding_session(
        context=ctx, directory="/repo", task="t", agent="", pm=pm, runner=None, search_path=[]
    )
    assert "no coding agent" in out.lower()
    assert pm.checked == []  # never gated a directory when nothing can run


async def test_directory_denied_refuses(tmp_path):
    ctx, _ = _ctx_with_collector()
    pm = FakePM(allow=False)
    called = []

    async def runner(config, task, context):
        called.append(True)
        return "should not run"

    out = await sessmod.run_coding_session(
        context=ctx,
        directory=str(tmp_path),
        task="t",
        pm=pm,
        runner=runner,
        search_path=_only_claude(tmp_path),
    )
    assert called == []  # runner never invoked
    assert "permission" in out.lower() or "denied" in out.lower() or "declin" in out.lower()


async def test_happy_path_emits_surfaces_and_diff(tmp_path):
    ctx, surfaces = _ctx_with_collector()
    pm = FakePM(allow=True)

    async def runner(config, task, context):
        # config points at the approved dir; simulate a coding edit + a plan
        assert config.cwd == str(tmp_path)
        await context.send(ACPPlan([ACPPlanEntry("write hello", "completed", None)]))
        (tmp_path / "hello.py").write_text("print('hi')\n")
        return "Added hello.py"

    out = await sessmod.run_coding_session(
        context=ctx,
        directory=str(tmp_path),
        task="add hello",
        pm=pm,
        surface_id="cs1",
        runner=runner,
        search_path=_only_claude(tmp_path),
    )
    # a running surface, then a terminal done surface
    assert len(surfaces) >= 2
    final = surfaces[-1].component
    assert final["status"] == "done"
    assert any(f["path"] == "hello.py" and f["status"] == "added" for f in final["files"])
    assert final["plan"] == [{"content": "write hello", "status": "completed"}]
    assert "hello.py" in out


async def test_runner_failure_emits_failed_surface(tmp_path):
    ctx, surfaces = _ctx_with_collector()
    pm = FakePM(allow=True)

    async def runner(config, task, context):
        raise RuntimeError("adapter blew up")

    out = await sessmod.run_coding_session(
        context=ctx,
        directory=str(tmp_path),
        task="t",
        pm=pm,
        surface_id="cs1",
        runner=runner,
        search_path=_only_claude(tmp_path),
    )
    assert surfaces[-1].component["status"] == "failed"
    assert "adapter blew up" in surfaces[-1].component.get("error", "")
    assert "adapter blew up" in out or "failed" in out.lower()


async def test_missing_directory_is_created_after_approval(tmp_path):
    """The tool contract allows pointing at a not-yet-existing folder ("start a
    new project in ..."); the adapter needs a real cwd, so the run creates it
    once the permission gate passes."""
    ctx, surfaces = _ctx_with_collector()
    target = tmp_path / "new-project"

    async def runner(config, task, context):
        return "ok"

    out = await sessmod.run_coding_session(
        context=ctx,
        directory=str(target),
        task="t",
        pm=FakePM(),
        runner=runner,
        search_path=_only_claude(tmp_path),
    )
    assert target.is_dir()
    assert surfaces[-1].component["status"] == "done"
    assert "failed" not in out.lower()


async def test_plan_update_streams_onto_running_surface(tmp_path):
    """An ACPPlan arriving mid-run re-emits the running surface with the plan,
    so the workshop panel shows progress instead of 'warming up' forever."""
    ctx, surfaces = _ctx_with_collector()

    async def runner(config, task, context):
        await context.send(ACPPlan([ACPPlanEntry("step one", "in_progress", None)]))
        return "done"

    await sessmod.run_coding_session(
        context=ctx,
        directory=str(tmp_path),
        task="t",
        pm=FakePM(),
        surface_id="cs1",
        runner=runner,
        search_path=_only_claude(tmp_path),
    )
    running = [s.component for s in surfaces if s.component["status"] == "running"]
    assert running[-1]["plan"] == [{"content": "step one", "status": "in_progress"}]


async def test_default_runner_survives_held_stream_turn_lock():
    """Regression for the 'Warming up the workshop' hang: the caller's turn
    holds ag2's per-stream turn lock while the coding tool executes, so the
    nested ask must NOT run on the caller's stream — that deadlocks before the
    adapter even spawns. The runner uses a private stream and forwards plan
    updates back to the caller's stream."""
    import asyncio

    import acp
    from ag2.acp.testing import ACPTurn, fake_acp_config
    from ag2.agent import _get_stream_turn_lock

    ctx, _ = _ctx_with_collector()
    forwarded: list = []

    async def collect_plans(event):
        if isinstance(event, ACPPlan):
            forwarded.append(event)

    ctx.stream.subscribe(collect_plans)

    config = fake_acp_config(
        ACPTurn(
            updates=[
                acp.update_plan([acp.plan_entry("write hello", status="in_progress")]),
                acp.update_agent_message_text("hi from coder"),
            ]
        )
    )

    lock = _get_stream_turn_lock(ctx.stream)
    async with lock:  # what Agent._execute holds while the tool runs
        out = await asyncio.wait_for(sessmod._default_runner(config, "do it", ctx), timeout=10)

    assert out == "hi from coder"
    assert [e.content for p in forwarded for e in p.entries] == ["write hello"]


async def test_run_holds_askers_pending_guard(tmp_path):
    """The coding run pauses the turn clock (asker.has_pending) while the CLI
    agent works — a long run must not be killed by the gateway turn timeout
    (the run is already bounded by its own ACP turn_timeout)."""
    ctx, _ = _ctx_with_collector()

    from assistant.hitl.base import PendingGuard

    class _Asker(PendingGuard):
        async def ask(self, question, timeout=None):
            return "yes"

    asker = _Asker()
    seen = {}

    async def runner(config, task, context):
        seen["during"] = asker.has_pending()
        return "done"

    await sessmod.run_coding_session(
        context=ctx,
        directory=str(tmp_path),
        task="t",
        pm=FakePM(),
        asker=asker,
        runner=runner,
        search_path=_only_claude(tmp_path),
    )
    assert seen["during"] is True
    assert asker.has_pending() is False


async def test_the_resolved_adapter_is_what_the_run_would_spawn(tmp_path):
    """The config handed to the runner carries the adapter found on OUR search
    path — not a bare name a differently-PATHed subprocess could re-resolve."""
    ctx, _ = _ctx_with_collector()
    search_path = _only_claude(tmp_path)
    seen: list = []

    async def runner(config, task, context):
        seen.append(config.command)
        return "done"

    await sessmod.run_coding_session(
        context=ctx,
        directory=str(tmp_path),
        task="t",
        pm=FakePM(),
        runner=runner,
        search_path=search_path,
    )
    assert seen == [[str(search_path[0] / "claude-agent-acp")]]
