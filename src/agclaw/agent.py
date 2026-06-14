"""AGClaw agent built on AG2 Beta."""

import os
from datetime import datetime

from autogen.beta import Agent

from agclaw.config import Config, load_config
from agclaw.memory import build_knowledge_config, profile_assembly
from agclaw.tools import build_agent_tools

# Commands skill scripts must never run (defense-in-depth; skills can ship code).
_SKILL_BLOCKED = ["rm -rf /", "sudo", "shutdown", "reboot", "mkfs", ":(){"]


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

    return GeminiConfig(model=model, api_key=api_key)


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
    "Do what the user asks directly. If you cannot complete a request directly — "
    "a tool fails, a file or resource isn't found, or you're denied access — do "
    "NOT silently fall back to other methods (e.g. running shell commands or code "
    "to work around it). Instead, stop and tell the user plainly what happened, "
    "then ask how they'd like to proceed, offering specific alternatives when "
    "there are any. Only use an alternative approach once the user has chosen it."
)


def behavior_guidance() -> str:
    return BEHAVIOR_GUIDANCE


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
    return [config.agent.system_prompt, BEHAVIOR_GUIDANCE, environment_context(config)]


def create_agent(
    config: Config | None = None,
    memory: bool = True,
    platform: str = "cli",
    knowledge_store=None,
    skills: bool = True,
    asker=None,
    single_shot: bool = False,
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

    llm_config = model_config(config)

    knowledge = None
    assembly: list = []
    if memory:
        # A cheaper model for the background aggregation pass, if configured.
        agg_config = (
            model_config(config, config.llm.aggregate_model)
            if config.llm.aggregate_model
            else llm_config
        )
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
    """Send a message to the agent and return the response (single-shot)."""
    if config is None:
        config = load_config()
    agent = create_agent(
        config, memory=memory, platform=platform, asker=asker, single_shot=True
    )
    reply = await agent.ask(message, prompt=turn_prompt(config))
    return reply.body
