"""AGClaw agent built on AG2 Beta."""

import os
from datetime import datetime

from autogen.beta import Agent
from autogen.beta.config.gemini import GeminiConfig

from agclaw.config import Config
from agclaw.memory import build_knowledge_config, profile_assembly
from agclaw.tools import build_agent_tools


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

    agent = Agent(
        config.agent.name,
        prompt=config.agent.system_prompt,
        config=llm_config,
        tools=build_agent_tools(config.llm.provider),
        knowledge=knowledge,
        assembly=assembly,
    )

    return agent


async def ask(
    message: str,
    config: Config | None = None,
    memory: bool = True,
    platform: str = "cli",
) -> str:
    """Send a message to the agent and return the response."""
    if config is None:
        config = Config()
    agent = create_agent(config, memory=memory, platform=platform)
    reply = await agent.ask(message, prompt=turn_prompt(config))
    return reply.body
