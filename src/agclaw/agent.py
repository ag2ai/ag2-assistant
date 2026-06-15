"""AGClaw agent built on AG2 Beta."""

import os
from contextvars import ContextVar
from datetime import datetime

from autogen.beta import Agent

from agclaw.config import Config, load_config
from agclaw.memory import build_knowledge_config, profile_assembly
from agclaw.tools import build_agent_tools

# Set (to a list) by a caller that wants to learn which tasks the agent spawned
# this turn via the `start_task` tool — e.g. the gateway, to push a chat task-card.
started_tasks_var: ContextVar = ContextVar("agclaw_started_tasks", default=None)

# Commands skill scripts must never run (defense-in-depth; skills can ship code).
_SKILL_BLOCKED = ["rm -rf /", "sudo", "shutdown", "reboot", "mkfs", ":(){"]

# Default (cheaper) model for the passive memory-aggregation pass, per provider.
# Used only when llm.aggregate_model isn't set. Override via AGCLAW_AGGREGATE_MODEL.
_DEFAULT_AGGREGATE_MODEL = {"gemini": "gemini-3.1-flash-lite"}


def model_config(config: Config, model: str | None = None):
    """Build the AG2 ModelConfig for the configured provider.

    `model` overrides `config.llm.model` (used for the cheaper aggregation pass).
    """
    api_key = os.environ.get(config.llm.api_key_env, "")
    model = model or config.llm.model
    provider = config.llm.provider.lower()
    if provider == "anthropic":
        from autogen.beta.config import AnthropicConfig

        return AnthropicConfig(model=model, api_key=api_key)
    if provider in ("openai", "oai"):
        from autogen.beta.config import OpenAIConfig

        return OpenAIConfig(model=model, api_key=api_key)
    from autogen.beta.config.gemini import GeminiConfig

    # Generous output budget so long research notes / briefings aren't truncated
    # mid-sentence (the default is small).
    return GeminiConfig(model=model, api_key=api_key, max_output_tokens=8192)


def cheap_model(config: Config) -> str | None:
    """A faster/cheaper model for bulk work (research subtasks, verification)."""
    return config.llm.aggregate_model or _DEFAULT_AGGREGATE_MODEL.get(
        config.llm.provider.lower()
    )


def bundled_skills_dir():
    """Directory of first-party skills shipped with AGClaw (read-only)."""
    from pathlib import Path

    return Path(__file__).parent / "skills"


def build_skills_toolkit(config: Config):
    """A toolkit that lets the agent search, install, and run skills.

    `SkillSearchToolkit` extends the local skills toolkit (list/load/read/run)
    with registry search + install from skills.sh. Skills install into
    `config.skills_dir`; AGClaw's bundled first-party skills are always available
    too (read-only, via `extra_paths`), so it's capable on first run.

    When the Docker sandbox is selected (`config.tools.sandbox == "docker"`),
    skill *scripts* run inside a one-shot, bind-mounted container — so untrusted
    skill code can't reach the user's files. Storage/discovery stay local.
    """
    from autogen.beta.tools import SkillSearchToolkit

    config.skills_dir.mkdir(parents=True, exist_ok=True)
    extra = [str(bundled_skills_dir())]

    if config.tools.sandbox == "docker":
        from agclaw.tools.docker_sandbox import (
            build_docker_skill_runtime,
            docker_available,
        )

        if docker_available():
            runtime = build_docker_skill_runtime(
                install_dir=config.skills_dir,
                blocked=_SKILL_BLOCKED,
                image=config.tools.docker_image,
                network=config.tools.docker_network,
                extra_paths=extra,
            )
            return SkillSearchToolkit(runtime)

    from autogen.beta.tools.skills import LocalRuntime

    runtime = LocalRuntime(
        dir=str(config.skills_dir), blocked=_SKILL_BLOCKED, extra_paths=extra
    )
    return SkillSearchToolkit(runtime)


# Always-on behavioural guidance, kept separate from the (user-customisable)
# persona so it applies even when someone overrides the system prompt.
BEHAVIOR_GUIDANCE = (
    "Do what the user asks directly, using the most appropriate tool. If you "
    "cannot complete a request directly — a tool fails, a resource isn't found, "
    "you're denied access, or you simply have no suitable tool — do NOT improvise "
    "with other tools. Stop, tell the user plainly what happened or that you "
    "can't do it, and ask how they'd like to proceed (offering alternatives when "
    "there are any). Only take an alternative approach once the user chooses it.\n"
    "Pick the right tool for the medium: web pages and URLs → the web-fetch tool; "
    "the open web → the search tool; the user's Gmail/Calendar/Drive → the Google "
    "tools. You CANNOT watch video or audio — if asked about a YouTube/video link, "
    "say so and offer to fetch the page or work from a transcript they provide.\n"
    "The shell and code-execution tools are ONLY for when the user explicitly "
    "asks you to run a command, execute code, or work with local files. NEVER use "
    "them to 'look around', orient yourself, explore the filesystem (e.g. `ls`), "
    "inspect your environment, or as a fallback when unsure — they have nothing to "
    "do with web pages, videos, messages, or cloud data."
)


def behavior_guidance() -> str:
    return BEHAVIOR_GUIDANCE


# Shown only when the user is signed in to Google — steers tool selection so the
# agent uses the Google tools (not shell/code or local paths) for cloud content.
GOOGLE_GUIDANCE = (
    "The user is connected to Google. For anything in their Gmail, Google "
    "Calendar, or Google Drive/Docs/Sheets, use the Google tools (gmail_search, "
    "gmail_read, calendar_list_events, drive_search, drive_read, etc.) — never "
    "shell commands, code, or local file paths, and never invent paths. To open a "
    "Drive/Docs/Sheets item the user names, call drive_search first to get its id; "
    "if they paste a Docs/Sheets link, pass it straight to drive_read."
)


def environment_context(config: Config) -> str:
    """Live environment context (date, time, location) for the agent.

    Local date/time is read from the system clock at call time; location comes
    from config if set. Pass this per turn (it goes stale if baked in once).
    """
    now = datetime.now().astimezone()
    when = now.strftime("%A, %d %B %Y, %-I:%M %p")
    tz = now.strftime("%Z")
    offset = now.strftime("%z")  # e.g. +1000
    off = f"UTC{offset[:3]}:{offset[3:]}" if offset else ""
    lines = [f"- Current date and time: {when} {tz} ({off})".rstrip()]
    if config.agent.location:
        lines.append(f"- User location: {config.agent.location}")
    return "Environment (live):\n" + "\n".join(lines)


def turn_prompt(config: Config) -> list[str]:
    """Per-turn system prompt: persona + live environment context.

    `ask(prompt=...)` replaces the base prompt for that turn, so we include the
    persona, the always-on behaviour guidance, and the refreshed environment.
    """
    parts = [config.agent.system_prompt, BEHAVIOR_GUIDANCE]
    try:
        from agclaw.integrations.google_auth import has_token

        if has_token():
            parts.append(GOOGLE_GUIDANCE)
    except Exception:
        pass
    parts.append(environment_context(config))
    return parts


def _build_start_task_tool(task_starter):
    """A `start_task` tool that spawns a background task and records it for the UI."""
    from typing import Annotated

    from autogen.beta import tool
    from pydantic import Field

    @tool
    async def start_task(
        request: Annotated[
            str, Field(description="The full job to carry out in the background.")
        ],
    ) -> str:
        """Spin up a background TASK for substantial, multi-step work the user wants
        done over time — research + a written report, multi-part jobs, anything
        long-running or that should keep going after this reply. Do NOT use this for
        quick questions or things you can answer directly now — just answer those.
        The task asks its own clarifying questions and runs on its own."""
        task_id = await task_starter(request)
        lst = started_tasks_var.get()
        if lst is not None:
            lst.append({"id": task_id, "title": request})
        return (
            f"Started a background task ({task_id}). It will ask any clarifying "
            "questions and run on its own; the user can open it in the Tasks view."
        )

    return start_task


def create_agent(
    config: Config | None = None,
    memory: bool = True,
    platform: str = "cli",
    knowledge_store=None,
    skills: bool = True,
    asker=None,
    single_shot: bool = False,
    capabilities: list[str] | None = None,
    model: str | None = None,
    task_starter=None,
) -> Agent:
    """Create an AGClaw agent with the given configuration.

    Args:
        config: AGClaw configuration (defaults to Config()).
        memory: Whether to enable the persistent user-profile memory.
        platform: The channel this session is on (cli, telegram, discord, ...).
            Observations learned this session are tagged with it.
        knowledge_store: A shared KnowledgeStore to reuse for the profile. Pass a
            locked/shared store when multiple agents write the same profile (e.g.
            the gateway's per-session agents).
        skills: Whether to give the agent the skill search/install/run toolkit.
        asker: An `Asker` for human-in-the-loop questions (routes `context.input()`
            to the requesting surface). If None, the agent has no HITL hook.
        single_shot: True for one-turn runs (CLI). Aggregates the profile on
            conversation end so the single turn is captured; multi-turn callers
            (gateway/channels) leave this False and rely on the every-N-turns
            cadence to avoid an aggregation call per message.
    """
    if config is None:
        config = load_config()

    llm_config = model_config(config, model)

    knowledge = None
    assembly: list = []
    if memory:
        # The background profile-aggregation pass is just summarisation, so run it
        # on a cheaper model when one is configured (or a sensible per-provider
        # default). Falls back to the main model if neither applies.
        agg_model = config.llm.aggregate_model or _DEFAULT_AGGREGATE_MODEL.get(
            config.llm.provider.lower()
        )
        agg_config = model_config(config, agg_model) if agg_model else llm_config
        knowledge = build_knowledge_config(
            platform=platform,
            aggregate_config=agg_config,
            store=knowledge_store,
            every_n_turns=config.memory.aggregate_every_n_turns,
            on_end=single_shot,
        )
        assembly = profile_assembly()

    tools = build_agent_tools(
        config.llm.provider,
        sandbox=config.tools.sandbox,
        docker_image=config.tools.docker_image,
        docker_network=config.tools.docker_network,
        capabilities=capabilities,
    )
    if skills and (capabilities is None or "skills" in capabilities):
        tools.append(build_skills_toolkit(config))

    # When a task_starter is wired (the gateway), give the agent the ability to
    # spin up a background task for big jobs — and record it so the surface can
    # show a task card.
    if task_starter is not None:
        tools.append(_build_start_task_tool(task_starter))

    from agclaw.permissions import PermissionManager

    # One injected authority for all permission decisions (knows the sandbox mode
    # so prompts can say where a command actually runs — host vs container).
    dependencies: dict = {
        PermissionManager: PermissionManager(asker=asker, sandbox=config.tools.sandbox)
    }

    hitl_hook = None
    if asker is not None:
        from agclaw.hitl import build_hitl_hook

        hitl_hook = build_hitl_hook(asker)

    agent = Agent(
        config.agent.name,
        prompt=config.agent.system_prompt,
        config=llm_config,
        tools=tools,
        knowledge=knowledge,
        assembly=assembly,
        hitl_hook=hitl_hook,
        dependencies=dependencies,
    )

    return agent


async def ask(
    message: str,
    config: Config | None = None,
    memory: bool = True,
    platform: str = "cli",
    asker=None,
) -> str:
    """Send a message to the agent and return the response (single-shot)."""
    if config is None:
        config = load_config()
    agent = create_agent(
        config, memory=memory, platform=platform, asker=asker, single_shot=True
    )
    reply = await agent.ask(message, prompt=turn_prompt(config))
    return reply.body
