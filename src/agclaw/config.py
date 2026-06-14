"""AGClaw configuration."""

import os
from pathlib import Path

from dotenv import load_dotenv
from pydantic import BaseModel, Field

load_dotenv()


class LLMConfig(BaseModel):
    """LLM provider configuration."""

    provider: str = "gemini"
    model: str = "gemini-3.5-flash"
    api_key_env: str = "GEMINI_API_KEY"


class AgentConfig(BaseModel):
    """Agent configuration."""

    name: str = "agclaw"
    system_prompt: str = (
        "You are AGClaw, a helpful personal AI assistant. "
        "You are direct, concise, and helpful."
    )
    # Free-text user location (e.g. "Sydney, Australia"). Used for environment
    # context; local date/time is detected automatically from the system clock.
    # Defaults from the AGCLAW_LOCATION env var (set it in .env).
    location: str | None = Field(
        default_factory=lambda: os.environ.get("AGCLAW_LOCATION")
    )


class ToolsConfig(BaseModel):
    """Configuration for the agent's execution tools (shell/code)."""

    # "local" = subprocess on the host (command-filtered + approval-gated).
    # "docker" = isolated container with no host FS access (approval dropped).
    sandbox: str = Field(
        default_factory=lambda: os.environ.get("AGCLAW_SANDBOX", "local")
    )
    docker_image: str = Field(
        default_factory=lambda: os.environ.get(
            "AGCLAW_DOCKER_IMAGE", "python:3.12-slim"
        )
    )
    # "bridge" allows outbound network (pip, fetches); "none" is strictest.
    docker_network: str = Field(
        default_factory=lambda: os.environ.get("AGCLAW_DOCKER_NETWORK", "bridge")
    )


class MemoryConfig(BaseModel):
    """Configuration for the passive user-profile memory."""

    # Distil the profile every N conversation turns (an LLM call each time).
    # Batching keeps long chat sessions cheap instead of firing every message.
    aggregate_every_n_turns: int = Field(
        default_factory=lambda: int(os.environ.get("AGCLAW_AGGREGATE_EVERY_N", "4"))
    )


class Config(BaseModel):
    """Root AGClaw configuration."""

    llm: LLMConfig = Field(default_factory=LLMConfig)
    agent: AgentConfig = Field(default_factory=AgentConfig)
    tools: ToolsConfig = Field(default_factory=ToolsConfig)
    memory: MemoryConfig = Field(default_factory=MemoryConfig)
    data_dir: Path = Field(default_factory=lambda: Path.home() / ".agclaw")
    # Where installed skills live (SKILL.md packages).
    skills_dir: Path = Field(default_factory=lambda: Path.home() / ".agclaw" / "skills")
