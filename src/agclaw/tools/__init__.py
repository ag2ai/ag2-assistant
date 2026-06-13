"""AGClaw agent tools.

Prefers AG2's native built-in tools (provider-agnostic local toolkits) and
falls back to a small custom implementation only where a native tool isn't
portable across providers.

| Capability    | Tool                              | Portability        |
|---------------|-----------------------------------|--------------------|
| Web search    | `DuckDuckSearchTool` (native)     | all providers      |
| Shell         | `SandboxShellTool` (native)       | all providers      |
| Code exec     | `SandboxCodeTool` (native, local) | all providers      |
| Web fetch     | `WebFetchTool` (native)           | Anthropic          |
| Web fetch     | `web_fetch` (custom fallback)     | Gemini & others    |

Note on web fetch: the native `WebFetchTool` is a *server-side* built-in. On
Gemini it cannot be combined with local function-calling tools (search / shell /
code) without erroring, so for Gemini — and any provider that treats it as a
server-side tool — we use the custom function-tool fallback, which composes
cleanly. Anthropic permits mixing server-side and function tools, so it gets the
native one.
"""

from autogen.beta.tools import (
    DuckDuckSearchTool,
    LocalEnvironment,
    SandboxCodeTool,
    SandboxShellTool,
    WebFetchTool,
)

from agclaw.tools.web_fetch import web_fetch, web_fetch_tool

# Providers that allow the native server-side WebFetchTool alongside function tools.
_NATIVE_WEB_FETCH_PROVIDERS = {"anthropic"}

# Commands the shell tool must never run.
_SHELL_BLOCKED = ["rm -rf /", "sudo", "shutdown", "reboot", "mkfs"]


def build_agent_tools(provider: str = "gemini") -> list:
    """Build the agent's tool list, preferring native AG2 tools.

    Args:
        provider: The LLM provider (gemini, anthropic, openai, ollama, ...).
            Determines whether the native WebFetchTool is used or the custom
            portable fallback.
    """
    tools = [
        DuckDuckSearchTool(max_results=5),
        SandboxShellTool(blocked=_SHELL_BLOCKED),
        SandboxCodeTool(environment=LocalEnvironment()),
    ]

    if provider in _NATIVE_WEB_FETCH_PROVIDERS:
        tools.append(WebFetchTool())
    else:
        tools.append(web_fetch_tool)

    return tools


__all__ = [
    "build_agent_tools",
    "web_fetch",
    "web_fetch_tool",
]
