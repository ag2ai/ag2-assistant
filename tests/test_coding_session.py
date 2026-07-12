"""Coding-run orchestration (assistant.coding.session).

The real ACP run is behind an injectable ``runner`` seam so these tests are fast
and deterministic (a Python in-process ACP double deadlocks under acp 0.10.1 on
py3.14; the real adapter is covered by the @integration test).
"""

import pytest
from ag2.acp.events import ACPPlan, ACPPlanEntry
from ag2.context import ConversationContext
from ag2.stream import MemoryStream

from assistant.coding import detect
from assistant.coding import session as sessmod
from assistant.events import A2UISurface

pytestmark = pytest.mark.asyncio


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


def _only_claude(monkeypatch, available=True):
    infos = [
        detect.AgentInfo(
            "claude", "Claude Code", ["claude-agent-acp"], available, "/x" if available else None
        )
    ]
    monkeypatch.setattr(detect, "available_agents", lambda: [a for a in infos if a.available])


async def test_no_agent_available_returns_message(monkeypatch):
    monkeypatch.setattr(detect, "available_agents", lambda: [])
    ctx, _ = _ctx_with_collector()
    pm = FakePM()
    out = await sessmod.run_coding_session(
        context=ctx, directory="/repo", task="t", agent="", pm=pm, runner=None
    )
    assert "no coding agent" in out.lower()
    assert pm.checked == []  # never gated a directory when nothing can run


async def test_directory_denied_refuses(monkeypatch, tmp_path):
    _only_claude(monkeypatch)
    ctx, _ = _ctx_with_collector()
    pm = FakePM(allow=False)
    called = []

    async def runner(config, task, context):
        called.append(True)
        return "should not run"

    out = await sessmod.run_coding_session(
        context=ctx, directory=str(tmp_path), task="t", pm=pm, runner=runner
    )
    assert called == []  # runner never invoked
    assert "permission" in out.lower() or "denied" in out.lower() or "declin" in out.lower()


async def test_happy_path_emits_surfaces_and_diff(monkeypatch, tmp_path):
    _only_claude(monkeypatch)
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
    )
    # a running surface, then a terminal done surface
    assert len(surfaces) >= 2
    final = surfaces[-1].component
    assert final["status"] == "done"
    assert any(f["path"] == "hello.py" and f["status"] == "added" for f in final["files"])
    assert final["plan"] == [{"content": "write hello", "status": "completed"}]
    assert "hello.py" in out


async def test_runner_failure_emits_failed_surface(monkeypatch, tmp_path):
    _only_claude(monkeypatch)
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
    )
    assert surfaces[-1].component["status"] == "failed"
    assert "adapter blew up" in surfaces[-1].component.get("error", "")
    assert "adapter blew up" in out or "failed" in out.lower()
