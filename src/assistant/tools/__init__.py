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

import warnings
from pathlib import Path

from ag2.tools import (
    DuckDuckSearchTool,
    FilesystemToolkit,
    LocalEnvironment,
    SandboxCodeTool,
    SandboxShellTool,
    WebFetchTool,
)

from assistant.config import Config
from assistant.integrations.google_auth import GoogleAuth
from assistant.settings import profile_settings
from assistant.tools import docker_sandbox
from assistant.tools.approval import require_command_approval
from assistant.tools.ask import ask_user
from assistant.tools.files import list_folder, read_file, write_file
from assistant.tools.finance import get_quotes
from assistant.tools.google import build_google_tools
from assistant.tools.image_gen import build_image_tool
from assistant.tools.mcp import build_mcp_tools
from assistant.tools.weather import get_weather
from assistant.tools.web_fetch import web_fetch, web_fetch_tool

# Providers that allow the native server-side WebFetchTool alongside function tools.
_NATIVE_WEB_FETCH_PROVIDERS = {"anthropic"}

# Commands the shell tool must never run.
_SHELL_BLOCKED = ["rm -rf /", "sudo", "shutdown", "reboot", "mkfs"]


# Capability groups → the tools they unlock. Tasks declare the capabilities they
# need so an agent is built with EXACTLY those (privacy, focus, speed); chat
# (capabilities=None) gets everything.
CAPABILITIES = (
    "web",
    "code",
    "coding",
    "files",
    "images",
    "skills",
    "mcp",
    "gmail",
    "calendar",
    "drive",
)

# What each capability unlocks, in the model's own terms. This is the ONE place a
# capability is described: every prompt that offers a choice of capabilities renders
# this catalogue rather than restating it, so adding or renaming a group updates them
# all. Describe the KIND of work the group covers, never the individual tool names.
CAPABILITY_DOCS = {
    "web": (
        "search the web, fetch pages, and read current real-world facts — anything "
        "happening now or otherwise outside your own knowledge (news, weather and "
        "forecasts, market/share/crypto prices, sport, live reference data)"
    ),
    "code": (
        "run code and shell commands — for work that is more reliably computed than "
        "reasoned (calculation, data transformation, parsing, verification)"
    ),
    "coding": (
        "write or edit code in the user's own repositories and folders, driven by a "
        "locally installed CLI coding agent — the path for real code changes on disk, "
        "as opposed to running snippets; the user approves the folder first"
    ),
    "files": "read the user's local files, and save files into the workspace",
    "images": "generate and edit images",
    "skills": "find, install and run packaged skills (curated procedures for known jobs)",
    "mcp": "the MCP servers configured in this profile",
    "gmail": "the user's Gmail",
    "calendar": "the user's Google Calendar",
    "drive": "the user's Google Drive, Docs and Sheets",
}


def capability_catalogue(capabilities=None) -> str:
    """Render the capability groups as a `- name: what it unlocks` list for a prompt."""
    caps = capabilities if capabilities is not None else CAPABILITIES
    return "\n".join(f"- {c}: {CAPABILITY_DOCS[c]}" for c in caps if c in CAPABILITY_DOCS)


_GOOGLE_GROUPS = {
    "gmail": {"gmail_search", "gmail_read", "gmail_send", "gmail_create_draft"},
    "calendar": {"calendar_list_events", "calendar_create_event"},
    "drive": {"drive_search", "drive_read"},
}


def available_capabilities(config: Config, *, google: "GoogleAuth | None" = None) -> list[str]:
    """Capabilities currently usable (Google ones need a token *and* the libs)."""
    caps = ["web", "code", "coding", "files", "images", "skills", "mcp"]
    if (google or GoogleAuth(config.paths)).google_ready():
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
    google: "GoogleAuth | None" = None,
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

    if capabilities is None:
        # Chat only: option-carrying user questions via the durable HITL channel
        # (tasks keep their scoped toolsets and their own inquiry path). Degrades
        # to a no-op message when the turn has no asker.
        tools.append(ask_user)

    if want("web"):
        tools.append(DuckDuckSearchTool(max_results=5))
        tools.append(WebFetchTool() if provider in _NATIVE_WEB_FETCH_PROVIDERS else web_fetch_tool)
        tools.append(get_weather)  # deterministic weather → WeatherPanel, not a search spray
        tools.append(get_quotes)  # deterministic global quotes → MarketBoard, not a search spray

    if want("code"):
        use_docker = False
        if sandbox == "docker":
            use_docker = docker_sandbox.docker_available()
            if not use_docker:
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
            from ag2.extensions.docker import (
                DockerEnvironment,  # local: lazy optional Docker backend
            )

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

    if want("coding"):
        # Drive host CLI coding agents (Claude Code / Codex / OpenCode) over ACP.
        # The tools resolve the PermissionManager/Asker from the turn's context at
        # call time; where the adapters live comes from the config's host facts.
        from assistant.coding.detect import parse_bridge
        from assistant.tools.coding import build_coding_tools

        tools += build_coding_tools(
            search_path=config.search_path if config is not None else (),
            bridge=parse_bridge(config.acp_bridge, config.acp_bridge_token)
            if config is not None
            else None,
        )

    if want("images") and config is not None:
        # generate_image: provider-aware image generation + editing → saved to the
        # workspace. Needs `config` (provider/keys) so it's skipped when unavailable.
        tools.append(build_image_tool(config, workspace_dir))

    if want("files"):
        # Host FS, Grant-gated (CONTEXT.md "Folders"): read_file/list_folder need a
        # read Grant, write_file a read+write Grant; the profile's own workspace is
        # implicit. write_file also serves the workspace (relative paths), so the
        # toolkit's write_file is dropped alongside its read_file (name clashes).
        tools += [read_file, list_folder, write_file]
        if workspace_dir:
            wd = Path(workspace_dir).expanduser()
            wd.mkdir(parents=True, exist_ok=True)
            fk = FilesystemToolkit(base_path=wd)
            tools += [t for t in fk.tools if t.name not in ("read_file", "write_file")]

    # Google tools (only when signed in AND the [google] extra is installed),
    # per requested group. Registering them without the libs would hand the model
    # a tool that can only fail — see GoogleAuth.google_ready().
    if config is not None:
        google = google or GoogleAuth(config.paths)
        keep: set[str] = set()
        if google.google_ready():
            for cap, names in _GOOGLE_GROUPS.items():
                if want(cap):
                    keep |= names
        if keep:
            tools += [t for t in build_google_tools(google) if t.name in keep]

    if want("mcp") and config is not None:
        # Read THIS profile's MCP server list (config.data_dir is the profile dir),
        # so an agent only loads the MCP servers configured in its own profile.
        settings = profile_settings(config.data_dir)
        tools += build_mcp_tools(settings.list_mcp_servers(include_env=True))

    return tools


__all__ = [
    "build_agent_tools",
    "available_capabilities",
    "capability_catalogue",
    "CAPABILITIES",
    "CAPABILITY_DOCS",
    "read_file",
    "list_folder",
    "write_file",
    "get_weather",
    "get_quotes",
    "web_fetch",
    "web_fetch_tool",
]
