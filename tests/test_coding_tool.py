"""The code_with_cli_agent tool + capability wiring (assistant.tools.coding).

The tools are closures over this host's facts, so each test builds them with a
real search path of executable stubs. No bridge is ever configured: it arrives as
an argument, so the default means local mode whatever the developer's .env says.
"""

from ag2.context import ConversationContext
from ag2.stream import MemoryStream

from assistant.config import Config
from assistant.hitl.base import Asker, PendingGuard
from assistant.permissions import PermissionManager
from assistant.tools.coding import build_coding_functions
from tests.support.stubs import write_stub


def Ctx(deps) -> ConversationContext:
    """A real turn context carrying the given dependency map."""
    return ConversationContext(stream=MemoryStream(id="s"), dependencies=deps)


class RecordingPM:
    """A permission authority that records what it was asked to approve."""

    def __init__(self, allow=True):
        self.allow = allow
        self.checked: list = []

    async def check(self, target):
        self.checked.append(str(target))
        return self.allow


class RecordingAsker(PendingGuard):
    async def ask(self, question, timeout=None):
        return "yes"


def _bin(tmp_path, *names: str):
    """A search path holding real executable stubs for these adapters."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    for name in names:
        write_stub(bin_dir / name)
    return [bin_dir]


def _tools(**kwargs) -> dict:
    """The tool functions by name (the tooled pair is checked separately below)."""
    return {fn.__name__: fn for fn in build_coding_functions(**kwargs)}


async def test_tool_forwards_deps_and_args(tmp_path):
    """The tool resolves pm/asker from the turn context and hands the run its
    directory, task and agent — verified through a real session run."""
    seen: dict = {}
    asker = RecordingAsker()

    async def runner(config, task, context):
        seen["cwd"] = config.cwd
        seen["task"] = task
        seen["context"] = context
        seen["asker_pending"] = asker.has_pending()
        return "ok summary"

    pm = RecordingPM()
    ctx = Ctx({PermissionManager: pm, Asker: asker})
    tool = _tools(search_path=_bin(tmp_path, "claude-agent-acp"), runner=runner)[
        "code_with_cli_agent"
    ]
    out = await tool(directory=str(tmp_path), task="add tests", context=ctx, agent="claude")

    assert "ok summary" in out
    assert seen["cwd"] == str(tmp_path)
    assert seen["task"] == "add tests"
    assert seen["context"] is ctx
    assert pm.checked == [str(tmp_path)]  # the PermissionManager dep was used
    assert seen["asker_pending"] is True  # the Asker dep was used to hold the clock


async def test_tool_forwards_the_requested_agent(tmp_path):
    """A named agent that isn't installed is refused, even though another is."""
    ran = []

    async def runner(config, task, context):
        ran.append(config)
        return "should not run"

    tool = _tools(search_path=_bin(tmp_path, "codex-acp"), runner=runner)["code_with_cli_agent"]
    out = await tool(
        directory=str(tmp_path),
        task="t",
        context=Ctx({PermissionManager: RecordingPM()}),
        agent="claude",
    )
    assert ran == []
    assert "no coding agent" in out.lower()


async def test_tool_handles_missing_permission_manager(tmp_path):
    """No PermissionManager in the turn's deps → the run refuses rather than
    writing to a folder nobody approved."""
    tool = _tools(search_path=_bin(tmp_path, "claude-agent-acp"))["code_with_cli_agent"]
    out = await tool(directory=str(tmp_path), task="t", context=Ctx({}))
    assert "permission authority" in out


async def test_list_coding_agents_reports_availability(tmp_path):
    tool = _tools(search_path=_bin(tmp_path, "claude-agent-acp"))["list_coding_agents"]
    out = await tool()
    assert "Claude Code" in out and "available" in out
    assert "Codex" in out and "not installed" in out


async def test_list_coding_agents_on_an_empty_host(tmp_path):
    out = await _tools(search_path=_bin(tmp_path))["list_coding_agents"]()
    assert "not installed" in out
    assert "available" not in out.replace("not installed", "")


def test_coding_capability_registered(paths):
    from assistant.tools import CAPABILITIES, available_capabilities

    assert "coding" in CAPABILITIES
    assert "coding" in available_capabilities(Config.for_paths(paths))


def test_build_agent_tools_includes_coding_tool(paths, tmp_path):
    """The toolset takes the search path from the config it is built for."""
    from assistant.tools import build_agent_tools

    config = Config.for_paths(paths, search_path=_bin(tmp_path, "claude-agent-acp"))
    tools = build_agent_tools("gemini", capabilities=["coding"], config=config)
    assert {getattr(t, "name", "") for t in tools} == {
        "code_with_cli_agent",
        "list_coding_agents",
    }
