"""Tests for AGClaw tools.

Most tools are now native AG2 built-ins (DuckDuckSearchTool, SandboxShellTool,
SandboxCodeTool). We only test our provider-aware tool selection and the custom
web_fetch fallback that's kept for providers without native web fetch.
"""

import pytest

from agclaw.tools import build_agent_tools


def test_build_agent_tools_has_four_capabilities():
    tools = build_agent_tools(provider="gemini")
    names = {t.name for t in tools}
    # search, shell, code, fetch
    assert "duckduckgo_search" in names
    assert "run_shell_command" in names
    assert "run_code" in names
    assert "web_fetch" in names
    assert len(tools) == 4


def test_build_agent_tools_gemini_uses_fallback_fetch():
    # Native WebFetchTool is server-side on Gemini and won't mix with function
    # tools, so Gemini gets the custom function-tool fallback.
    from agclaw.tools import web_fetch_tool

    tools = build_agent_tools(provider="gemini")
    assert web_fetch_tool in tools


def test_build_agent_tools_anthropic_uses_native_fetch():
    from agclaw.tools import web_fetch_tool

    tools = build_agent_tools(provider="anthropic")
    # Anthropic gets the native WebFetchTool, not our custom fallback object.
    assert web_fetch_tool not in tools
    assert any(t.name == "web_fetch" for t in tools)


# --- custom web_fetch fallback (plain function) ---


def test_web_fetch_html():
    from agclaw.tools.web_fetch import web_fetch

    # example.com is an IANA-maintained, highly stable test domain.
    result = web_fetch(url="https://example.com", max_chars=5000)
    if result.startswith("Error fetching"):
        pytest.skip("network/example.com unavailable")
    assert isinstance(result, str)
    assert "Example Domain" in result


def test_web_fetch_json():
    from agclaw.tools.web_fetch import web_fetch

    result = web_fetch(url="https://httpbin.org/json", max_chars=5000)
    if result.startswith("Error fetching") or "Error fetching" in result[:40]:
        pytest.skip("httpbin.org unavailable")
    assert "JSON" in result


def test_web_fetch_invalid_url():
    from agclaw.tools.web_fetch import web_fetch

    result = web_fetch(url="https://thisdomaindoesnotexist.invalid", max_chars=1000)
    assert "Error" in result


@pytest.mark.integration
async def test_agent_uses_web_search():
    """Integration test: agent answers a current-info question using search."""
    from agclaw.agent import ask

    response = await ask(
        "Search the web: what is the AG2 Python framework? One sentence.",
        memory=False,
    )
    assert isinstance(response, str)
    assert len(response) > 0


@pytest.mark.integration
async def test_agent_uses_code_execution():
    """Integration test: agent computes via code execution."""
    from agclaw.agent import ask

    response = await ask(
        "Use code execution to calculate the factorial of 10.",
        memory=False,
    )
    # Normalise digit grouping (commas/spaces) before checking the value.
    normalised = response.replace(",", "").replace(" ", "")
    assert "3628800" in normalised
