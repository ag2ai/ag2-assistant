"""The code_with_cli_agent tool + capability wiring (assistant.tools.coding)."""

import pytest

from assistant.coding import detect
from assistant.coding import session as sessmod
from assistant.hitl.base import Asker
from assistant.permissions import PermissionManager
from assistant.tools import coding as toolmod


@pytest.fixture(autouse=True)
def _no_bridge_env(monkeypatch):
    """Stay hermetic: a developer's .env may configure a real ACP bridge."""
    monkeypatch.delenv("AG2ASSISTANT_ACP_BRIDGE", raising=False)
    monkeypatch.delenv("AG2ASSISTANT_ACP_BRIDGE_TOKEN", raising=False)


class Ctx:
    def __init__(self, deps):
        self.dependencies = deps
        self.stream = object()


async def test_tool_forwards_deps_and_args(monkeypatch):
    captured = {}

    async def fake_run(**kwargs):
        captured.update(kwargs)
        return "ok summary"

    monkeypatch.setattr(sessmod, "run_coding_session", fake_run)

    pm = object()
    asker = object()
    ctx = Ctx({PermissionManager: pm, Asker: asker})
    out = await toolmod.code_with_cli_agent(
        directory="/repo", task="add tests", context=ctx, agent="claude"
    )
    assert out == "ok summary"
    assert captured["directory"] == "/repo"
    assert captured["task"] == "add tests"
    assert captured["agent"] == "claude"
    assert captured["pm"] is pm
    assert captured["asker"] is asker
    assert captured["context"] is ctx


async def test_tool_handles_missing_permission_manager(monkeypatch):
    async def fake_run(**kwargs):
        assert kwargs["pm"] is None
        return "no authority"

    monkeypatch.setattr(sessmod, "run_coding_session", fake_run)
    ctx = Ctx({})
    out = await toolmod.code_with_cli_agent(directory="/r", task="t", context=ctx)
    assert out == "no authority"


async def test_list_coding_agents_reports_availability(monkeypatch):
    infos = [
        detect.AgentInfo("claude", "Claude Code", ["claude-agent-acp"], True, "/x"),
        detect.AgentInfo("codex", "Codex", ["codex-acp"], False, None),
        detect.AgentInfo("opencode", "OpenCode", ["opencode", "acp"], False, None),
    ]
    monkeypatch.setattr(detect, "detect_agents", lambda: infos)
    out = await toolmod.list_coding_agents()
    assert "Claude Code" in out
    assert "Codex" in out


def test_coding_capability_registered():
    from assistant.tools import CAPABILITIES, available_capabilities

    assert "coding" in CAPABILITIES
    assert "coding" in available_capabilities()


def test_build_agent_tools_includes_coding_tool():
    from assistant.tools import build_agent_tools

    tools = build_agent_tools(provider="gemini", capabilities=["coding"])
    names = {getattr(t, "name", "") for t in tools}
    assert "code_with_cli_agent" in names
