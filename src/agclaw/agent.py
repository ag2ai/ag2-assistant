"""AGClaw agent built on AG2 Beta."""

import os
from datetime import datetime

from autogen.beta import Agent
from autogen.beta.config.gemini import GeminiConfig

from agclaw.config import Config
from agclaw.memory import build_knowledge_config, profile_assembly
from agclaw.tools import build_agent_tools

# Commands skill scripts must never run (defense-in-depth; skills can ship code).
_SKILL_BLOCKED = ["rm -rf /", "sudo", "shutdown", "reboot", "mkfs", ":(){"]


def build_skills_toolkit(config: Config):
    """A toolkit that lets the agent search, install, and run skills.

    `SkillSearchToolkit` extends the local skills toolkit (list/load/read/run)
    with registry search + install from skills.sh. Skills install into
    `config.skills_dir`.
    """
    from autogen.beta.tools import SkillSearchToolkit
    from autogen.beta.tools.skills import LocalRuntime

    config.skills_dir.mkdir(parents=True, exist_ok=True)
    runtime = LocalRuntime(dir=str(config.skills_dir), blocked=_SKILL_BLOCKED)
    return SkillSearchToolkit(runtime)


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

    `ask(prompt=...)` replaces the base prompt for that turn, so we include both
    the persona and the refreshed environment each call.
    """
    return [config.agent.system_prompt, environment_context(config)]


def create_agent(
    config: Config | None = None,
    memory: bool = True,
    platform: str = "cli",
    knowledge_store=None,
    skills: bool = True,
    asker=None,
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
    """
    if config is None:
        config = Config()

    api_key = os.environ.get(config.llm.api_key_env, "")

    llm_config = GeminiConfig(
        model=config.llm.model,
        api_key=api_key,
    )

    knowledge = None
    assembly: list = []
    if memory:
        knowledge = build_knowledge_config(
            platform=platform,
            aggregate_config=llm_config,
            store=knowledge_store,
        )
        assembly = profile_assembly()

    tools = build_agent_tools(
        config.llm.provider,
        sandbox=config.tools.sandbox,
        docker_image=config.tools.docker_image,
        docker_network=config.tools.docker_network,
    )
    if skills:
        tools.append(build_skills_toolkit(config))

    from agclaw.permissions import PermissionManager

    # One injected authority for all permission decisions.
    dependencies: dict = {PermissionManager: PermissionManager(asker=asker)}

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
    """Send a message to the agent and return the response."""
    if config is None:
        config = Config()
    agent = create_agent(config, memory=memory, platform=platform, asker=asker)
    reply = await agent.ask(message, prompt=turn_prompt(config))
    return reply.body
