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

from agclaw.tools.approval import require_command_approval
from agclaw.tools.files import read_file
from agclaw.tools.web_fetch import web_fetch, web_fetch_tool

# Providers that allow the native server-side WebFetchTool alongside function tools.
_NATIVE_WEB_FETCH_PROVIDERS = {"anthropic"}

# Commands the shell tool must never run.
_SHELL_BLOCKED = ["rm -rf /", "sudo", "shutdown", "reboot", "mkfs"]


def build_agent_tools(
    provider: str = "gemini",
    sandbox: str = "local",
    docker_image: str = "python:3.12-slim",
    docker_network: str = "bridge",
) -> list:
    """Build the agent's tool list, preferring native AG2 tools.

    Args:
        provider: The LLM provider (gemini, anthropic, openai, ollama, ...).
            Determines whether the native WebFetchTool is used or the custom
            portable fallback.
        sandbox: "local" (subprocess on the host — command-filtered and
            approval-gated so shell/code can't bypass file permissions) or
            "docker" (isolated container with no host filesystem access; the
            container *is* the boundary, so the per-command approval prompt is
            dropped). Falls back to "local" if Docker isn't available.
        docker_image: Image for the Docker sandbox.
        docker_network: Docker network mode ("bridge" allows outbound network
            e.g. for pip; "none" for the strictest isolation).
    """
    use_docker = False
    if sandbox == "docker":
        from agclaw.tools.docker_sandbox import docker_available

        use_docker = docker_available()
        if not use_docker:
            import warnings

            warnings.warn(
                "Docker sandbox requested but Docker is unavailable; "
                "falling back to the local sandbox with approval prompts.",
                stacklevel=2,
            )

    if use_docker:
        from agclaw.tools.docker_sandbox import DockerEnvironment

        # One environment → both tools share a container, so files written by
        # shell are visible to code and vice-versa within a session.
        env = DockerEnvironment(image=docker_image, network=docker_network)
        tools = [
            DuckDuckSearchTool(max_results=5),
            # No approval middleware: the container has no host FS, so there's
            # nothing to gate. Keep the blocked list as defence in depth.
            SandboxShellTool(environment=env, blocked=_SHELL_BLOCKED),
            SandboxCodeTool(environment=env),
            read_file,  # still permission-gated (it reads the *host* FS)
        ]
    else:
        # Button-based approval gate so shell/code can't bypass file permissions.
        approval = require_command_approval()
        tools = [
            DuckDuckSearchTool(max_results=5),
            SandboxShellTool(blocked=_SHELL_BLOCKED, middleware=[approval]),
            SandboxCodeTool(environment=LocalEnvironment(), middleware=[approval]),
            read_file,  # permission-gated local file reading (vision for PDFs/images)
        ]

    if provider in _NATIVE_WEB_FETCH_PROVIDERS:
        tools.append(WebFetchTool())
    else:
        tools.append(web_fetch_tool)

    # Google tools (Gmail/Calendar/Drive) only when the user is signed in.
    from agclaw.integrations.google_auth import has_token

    if has_token():
        from agclaw.tools.google import build_google_tools

        tools.extend(build_google_tools())

    return tools


__all__ = [
    "build_agent_tools",
    "read_file",
    "web_fetch",
    "web_fetch_tool",
]
