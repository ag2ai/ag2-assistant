"""Tests for AG2 Assistant tools.

Most tools are now native AG2 built-ins (DuckDuckSearchTool, SandboxShellTool,
SandboxCodeTool). We only test our provider-aware tool selection and the custom
web_fetch fallback that's kept for providers without native web fetch.
"""

import pytest

from assistant.tools import build_agent_tools


def test_build_agent_tools_has_core_capabilities():
    tools = build_agent_tools(provider="gemini")
    names = {t.name for t in tools}
    # search, shell, code, file read, fetch
    assert "duckduckgo_search" in names
    assert "run_shell_command" in names
    assert "run_code" in names
    assert "read_file" in names
    assert "web_fetch" in names
    assert len(tools) == 5


def test_build_agent_tools_gemini_uses_fallback_fetch():
    # Native WebFetchTool is server-side on Gemini and won't mix with function
    # tools, so Gemini gets the custom function-tool fallback.
    from assistant.tools import web_fetch_tool

    tools = build_agent_tools(provider="gemini")
    assert web_fetch_tool in tools


def test_build_agent_tools_anthropic_uses_native_fetch():
    from assistant.tools import web_fetch_tool

    tools = build_agent_tools(provider="anthropic")
    # Anthropic gets the native WebFetchTool, not our custom fallback object.
    assert web_fetch_tool not in tools
    assert any(t.name == "web_fetch" for t in tools)


# --- custom web_fetch fallback (plain function) ---


def test_web_fetch_html():
    from assistant.tools.web_fetch import web_fetch

    # example.com is an IANA-maintained, highly stable test domain.
    result = web_fetch(url="https://example.com", max_chars=5000)
    if result.startswith("Error fetching"):
        pytest.skip("network/example.com unavailable")
    assert isinstance(result, str)
    assert "Example Domain" in result


def test_web_fetch_json():
    from assistant.tools.web_fetch import web_fetch

    result = web_fetch(url="https://httpbin.org/json", max_chars=5000)
    if result.startswith("Error fetching") or "Error fetching" in result[:40]:
        pytest.skip("httpbin.org unavailable")
    assert "JSON" in result


def test_web_fetch_invalid_url():
    from assistant.tools.web_fetch import web_fetch

    result = web_fetch(url="https://thisdomaindoesnotexist.invalid", max_chars=1000)
    assert "Error" in result


@pytest.mark.integration
async def test_agent_uses_web_search():
    """Integration test: agent answers a current-info question using search."""
    from assistant.agent import ask

    response = await ask(
        "Search the web: what is the AG2 Python framework? One sentence.",
        memory=False,
    )
    assert isinstance(response, str)
    assert len(response) > 0


class _AutoAllowAsker:
    """Approves every permission/command prompt (for code-exec integration test)."""

    async def ask(self, question, timeout=None):
        from assistant.permissions import ALLOW_ONCE

        return ALLOW_ONCE


@pytest.mark.integration
async def test_agent_uses_code_execution():
    """Integration test: agent computes via code execution."""
    from assistant.agent import ask

    response = await ask(
        "Use code execution to calculate the factorial of 10.",
        memory=False,
        asker=_AutoAllowAsker(),
    )
    # Normalise digit grouping (commas/spaces) before checking the value.
    normalised = response.replace(",", "").replace(" ", "")
    assert "3628800" in normalised


def test_capability_scoping_limits_tools():
    """Tasks declare capabilities; the agent gets exactly those tool groups."""
    web = {t.name for t in build_agent_tools("gemini", capabilities=["web"])}
    assert "duckduckgo_search" in web and "web_fetch" in web
    assert "read_file" not in web
    assert not any("run_" in n for n in web)  # no shell/code

    code = {t.name for t in build_agent_tools("gemini", capabilities=["code"])}
    assert any("run_" in n for n in code)
    assert "duckduckgo_search" not in code

    files = {t.name for t in build_agent_tools("gemini", capabilities=["files"])}
    assert files == {"read_file"}

    assert build_agent_tools("gemini", capabilities=[]) == []  # no caps → no tools


def test_no_capabilities_filter_is_all_tools():
    """Chat path (capabilities=None) still gets the full default tool set."""
    names = {t.name for t in build_agent_tools("gemini")}
    assert {"duckduckgo_search", "web_fetch", "read_file"} <= names
    assert any("run_" in n for n in names)


def test_mcp_tools_are_namespaced_to_avoid_native_name_collisions(monkeypatch):
    """MCP servers may expose generic names like read_file; present namespaced
    tool names so providers do not receive duplicate function schemas."""
    from assistant import settings
    from assistant.tools.mcp import NamespacedMCPToolkit, namespaced_tool_name

    monkeypatch.setattr(
        settings,
        "list_mcp_servers",
        lambda include_env=False: [
            {
                "name": "repo-files",
                "command": "npx",
                "args": ["-y", "@modelcontextprotocol/server-filesystem", "."],
                "env": {},
                "cwd": None,
                "allowed_tools": ["read_file", "list_directory"],
                "blocked_tools": [],
            }
        ],
    )

    tools = build_agent_tools("gemini")
    assert any(getattr(t, "name", None) == "read_file" for t in tools)
    assert any(isinstance(t, NamespacedMCPToolkit) for t in tools)
    assert namespaced_tool_name("repo-files", "read_file") == "repo_files_read_file"

    mcp_only = build_agent_tools("gemini", capabilities=["mcp"])
    assert len(mcp_only) == 1
    assert isinstance(mcp_only[0], NamespacedMCPToolkit)


async def test_mcp_namespaced_toolkit_discovers_filters_and_invokes(monkeypatch):
    """The namespaced adapter keeps AG2 MCP execution behavior behind our local
    compatibility surface while presenting provider-safe names."""
    from contextlib import asynccontextmanager
    from types import SimpleNamespace

    from ag2 import ToolResult
    from ag2.context import ConversationContext
    from ag2.events import ToolCallEvent
    from ag2.stream import MemoryStream
    from ag2.tools import MCPStdioServerConfig

    import assistant.tools.mcp as mcp_mod
    from assistant.tools.mcp import NamespacedMCPToolkit

    calls = []

    class _Session:
        async def list_tools(self):
            return SimpleNamespace(
                tools=[
                    SimpleNamespace(
                        name="read_file",
                        description="Read a file",
                        inputSchema={"type": "object"},
                    ),
                    SimpleNamespace(name="write_file", description="", inputSchema={}),
                    SimpleNamespace(name="search", description="", inputSchema={}),
                ]
            )

        async def call_tool(self, name, args):
            calls.append((name, args))
            return SimpleNamespace(isError=False, content=[])

    @asynccontextmanager
    async def fake_session(config):
        yield _Session()

    monkeypatch.setattr(mcp_mod, "resolve_config", lambda config, context: config)
    monkeypatch.setattr(mcp_mod, "mcp_session", fake_session)
    monkeypatch.setattr(mcp_mod, "extract_content", lambda result: ToolResult("ok"))

    toolkit = NamespacedMCPToolkit(
        MCPStdioServerConfig(
            command="mcp",
            server_label="repo-files",
            allowed_tools=["read_file", "write_file"],
            blocked_tools=["write_file"],
        )
    )
    context = ConversationContext(stream=MemoryStream())
    schemas = list(await toolkit.schemas(context))

    assert [s.function.name for s in schemas] == ["repo_files_read_file"]

    proxy = next(t for t in toolkit.tools if t.name == "repo_files_read_file")
    result = await proxy(
        ToolCallEvent(id="call-1", name=proxy.name, arguments='{"path":"x"}'), context
    )

    assert result.name == proxy.name
    assert calls == [("read_file", {"path": "x"})]


def test_files_capability_wires_workspace_toolkit(tmp_path):
    """The `files` capability adds AG2's filesystem toolkit (write/update/find/
    delete) scoped to the workspace, creating it — and keeps exactly one `read_file`
    (our host reader; AG2's is dropped to avoid a duplicate tool name)."""
    ws = tmp_path / "workspace"
    tools = build_agent_tools(provider="gemini", capabilities=["files"], workspace_dir=ws)
    names = [t.name for t in tools if getattr(t, "name", None)]
    assert {"write_file", "update_file", "find_files", "delete_file"} <= set(names)
    assert names.count("read_file") == 1  # only the custom host reader
    assert ws.exists()


def test_images_capability_adds_generate_image(tmp_path):
    """The `images` capability wires generate_image — but only when a config is given
    (it needs the provider/keys)."""
    from assistant.config import Config

    cfg = Config()
    with_cfg = {
        t.name
        for t in build_agent_tools(
            "gemini", capabilities=["images"], workspace_dir=tmp_path, config=cfg
        )
    }
    assert "generate_image" in with_cfg
    without_cfg = {
        t.name for t in build_agent_tools("gemini", capabilities=["images"], workspace_dir=tmp_path)
    }
    assert "generate_image" not in without_cfg  # no config → skipped


def test_no_workspace_dir_means_no_fs_tools():
    """Without a workspace_dir, only the custom read_file is present (no FS toolkit)."""
    tools = build_agent_tools(provider="gemini", capabilities=["files"], workspace_dir=None)
    names = [t.name for t in tools if getattr(t, "name", None)]
    assert "read_file" in names and "write_file" not in names
