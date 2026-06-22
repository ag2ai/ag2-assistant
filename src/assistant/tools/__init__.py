"""AG2 Assistant agent tools.

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

from assistant.tools.approval import require_command_approval
from assistant.tools.files import read_file
from assistant.tools.web_fetch import web_fetch, web_fetch_tool

# Providers that allow the native server-side WebFetchTool alongside function tools.
_NATIVE_WEB_FETCH_PROVIDERS = {"anthropic"}

# Commands the shell tool must never run.
_SHELL_BLOCKED = ["rm -rf /", "sudo", "shutdown", "reboot", "mkfs"]


# Capability groups → the tools they unlock. Tasks declare the capabilities they
# need so an agent is built with EXACTLY those (privacy, focus, speed); chat
# (capabilities=None) gets everything.
CAPABILITIES = ("web", "code", "files", "images", "skills", "mcp", "gmail", "calendar", "drive")

_GOOGLE_GROUPS = {
    "gmail": {"gmail_search", "gmail_read", "gmail_send", "gmail_create_draft"},
    "calendar": {"calendar_list_events", "calendar_create_event"},
    "drive": {"drive_search", "drive_read"},
}


def available_capabilities() -> list[str]:
    """Capabilities currently usable (Google ones only when signed in)."""
    from assistant.integrations.google_auth import has_token

    caps = ["web", "code", "files", "images", "skills", "mcp"]
    if has_token():
        caps += ["gmail", "calendar", "drive"]
    return caps


def build_agent_tools(
    provider: str = "gemini",
    sandbox: str = "local",
    docker_image: str = "python:3.12-slim",
    docker_network: str = "bridge",
    capabilities: list[str] | None = None,
    workspace_dir=None,
    config=None,
) -> list:
    """Build the agent's tools.

    `capabilities=None` → all tools (chat). A list → only those capability groups
    (used by tasks so a research subtask can't reach your Drive or run code, etc.).

    Args:
        provider: LLM provider (selects native vs custom web fetch).
        sandbox: "local" (approval-gated) or "docker" (container-isolated).
        capabilities: subset of CAPABILITIES, or None for everything.
    """
    want = (lambda c: True) if capabilities is None else (lambda c: c in capabilities)
    tools: list = []

    if want("web"):
        tools.append(DuckDuckSearchTool(max_results=5))
        tools.append(WebFetchTool() if provider in _NATIVE_WEB_FETCH_PROVIDERS else web_fetch_tool)

    if want("code"):
        use_docker = False
        if sandbox == "docker":
            from assistant.tools.docker_sandbox import docker_available

            use_docker = docker_available()
            if not use_docker:
                import warnings

                warnings.warn(
                    "Docker sandbox requested but Docker is unavailable; "
                    "falling back to the local sandbox with approval prompts.",
                    stacklevel=2,
                )
        if use_docker:
            # Docker is available → give the agent BOTH a sandboxed runner (isolated,
            # silent) and a host runner (approval-gated), and let it choose per call.
            # Distinct names are required (providers reject duplicate tool names).
            # The agent is steered by the descriptions below, not the prompt — so when
            # only one runner exists (no Docker, below) there's nothing to confuse it.
            from autogen.beta.extensions.docker import DockerEnvironment

            # AG2's official Docker backend: a long-lived, cached container with no
            # host mount — code/shell can't touch the user's files, which is why these
            # carry no approval middleware. State persists across calls in a session.
            env = DockerEnvironment(image=docker_image, network_mode=docker_network)
            approval = require_command_approval()
            tools += [
                SandboxCodeTool(
                    environment=env,
                    name="run_code_sandboxed",
                    description=(
                        "Run code in an isolated container with NO access to the "
                        "user's files; runs immediately without asking. PREFER THIS "
                        "for calculations, data transforms, and any code that doesn't "
                        "need the user's real files. Supported languages: {languages}."
                    ),
                ),
                SandboxShellTool(
                    environment=env,
                    blocked=_SHELL_BLOCKED,
                    name="run_shell_sandboxed",
                    description=(
                        "Run a shell command in an isolated container with NO access "
                        "to the user's files; runs immediately without asking. Prefer "
                        "this whenever you don't need the user's real files."
                    ),
                ),
                SandboxCodeTool(
                    environment=LocalEnvironment(),
                    middleware=[approval],
                    name="run_code_local",
                    description=(
                        "Run code on the USER'S COMPUTER with access to their real "
                        "files — REQUIRES the user's approval each time. Use ONLY when "
                        "the task genuinely needs to read or write the user's own "
                        "files outside the workspace; otherwise use run_code_sandboxed."
                    ),
                ),
                SandboxShellTool(
                    blocked=_SHELL_BLOCKED,
                    middleware=[approval],
                    name="run_shell_local",
                    description=(
                        "Run a shell command on the USER'S COMPUTER with access to "
                        "their real files — REQUIRES the user's approval each time. "
                        "Use ONLY when the task genuinely needs host file access; "
                        "otherwise use run_shell_sandboxed."
                    ),
                ),
            ]
        else:
            approval = require_command_approval()
            tools += [
                SandboxShellTool(blocked=_SHELL_BLOCKED, middleware=[approval]),
                SandboxCodeTool(environment=LocalEnvironment(), middleware=[approval]),
            ]

    if want("images") and config is not None:
        # generate_image: provider-aware image generation + editing → saved to the
        # workspace. Needs `config` (provider/keys) so it's skipped when unavailable.
        from assistant.tools.image_gen import build_image_tool

        tools.append(build_image_tool(config, workspace_dir))

    if want("files"):
        tools.append(read_file)  # permission-gated (host FS, any path, vision)
        # AG2's sandboxed filesystem toolkit, confined to the agent's workspace —
        # write/update/list/delete its own files (no approval needed; it's the
        # agent's own dir). We drop its `read_file` to avoid a name clash with the
        # custom host reader above (providers require unique tool names); reading
        # workspace files goes through that reader.
        if workspace_dir:
            from pathlib import Path

            from autogen.beta.tools import FilesystemToolkit

            wd = Path(workspace_dir).expanduser()
            wd.mkdir(parents=True, exist_ok=True)
            fk = FilesystemToolkit(base_path=wd)
            tools += [t for t in fk.tools if t.name != "read_file"]

    # Google tools (only when signed in), per requested group.
    from assistant.integrations.google_auth import has_token

    if has_token():
        keep: set[str] = set()
        for cap, names in _GOOGLE_GROUPS.items():
            if want(cap):
                keep |= names
        if keep:
            from assistant.tools.google import build_google_tools

            tools += [t for t in build_google_tools() if t.name in keep]

    if want("mcp"):
        from assistant import settings
        from assistant.tools.mcp import build_mcp_tools

        tools += build_mcp_tools(settings.list_mcp_servers(include_env=True))

    return tools


__all__ = [
    "build_agent_tools",
    "available_capabilities",
    "CAPABILITIES",
    "read_file",
    "web_fetch",
    "web_fetch_tool",
]
