"""AGClaw configuration.

Resolution order (highest precedence first):
  1. Environment variables (AGCLAW_*), loaded from .env if present
  2. ~/.agclaw/config.json
  3. Built-in defaults

Use `load_config()` to get a fully resolved Config; bare `Config()` is just the
built-in defaults (handy in tests).
"""

import json
import os
from pathlib import Path

from dotenv import load_dotenv
from pydantic import BaseModel, Field

load_dotenv()


class LLMConfig(BaseModel):
    """LLM provider configuration."""

    provider: str = "gemini"  # gemini | anthropic | openai
    model: str = "gemini-3.5-flash"
    api_key_env: str = "GEMINI_API_KEY"
    # Optional cheaper model for the passive memory-aggregation pass. None → reuse
    # the main model. Aggregation is a background summarisation, so a smaller/
    # cheaper model is usually fine and saves cost on long sessions.
    aggregate_model: str | None = None


class AgentConfig(BaseModel):
    """Agent configuration."""

    name: str = "agclaw"
    system_prompt: str = (
        "You are AGClaw, a helpful personal AI assistant. "
        "You are direct, concise, and helpful."
    )
    # Free-text user location (e.g. "Sydney, Australia"). Used for environment
    # context; local date/time is detected automatically from the system clock.
    location: str | None = None


class ToolsConfig(BaseModel):
    """Configuration for the agent's execution tools (shell/code)."""

    # "local" = subprocess on the host (command-filtered + approval-gated).
    # "docker" = isolated container with no host FS access (approval dropped).
    sandbox: str = "local"
    docker_image: str = "python:3.12-slim"
    # "bridge" allows outbound network (pip, fetches); "none" is strictest.
    docker_network: str = "bridge"


class MemoryConfig(BaseModel):
    """Configuration for the passive user-profile memory."""

    # Distil the profile every N conversation turns (an LLM call each time).
    aggregate_every_n_turns: int = 4


class Config(BaseModel):
    """Root AGClaw configuration (built-in defaults; see `load_config`)."""

    llm: LLMConfig = Field(default_factory=LLMConfig)
    agent: AgentConfig = Field(default_factory=AgentConfig)
    tools: ToolsConfig = Field(default_factory=ToolsConfig)
    memory: MemoryConfig = Field(default_factory=MemoryConfig)
    data_dir: Path = Field(default_factory=lambda: Path.home() / ".agclaw")
    # Where installed skills live (SKILL.md packages).
    skills_dir: Path = Field(default_factory=lambda: Path.home() / ".agclaw" / "skills")


def default_config_path() -> Path:
    """Where AGClaw looks for a JSON config file."""
    return Path.home() / ".agclaw" / "config.json"


def _apply_env_overrides(cfg: Config) -> None:
    """Layer AGCLAW_* environment variables on top (highest precedence)."""
    env = os.environ.get
    if v := env("AGCLAW_LLM_PROVIDER"):
        cfg.llm.provider = v
    if v := env("AGCLAW_MODEL"):
        cfg.llm.model = v
    if v := env("AGCLAW_API_KEY_ENV"):
        cfg.llm.api_key_env = v
    if v := env("AGCLAW_AGGREGATE_MODEL"):
        cfg.llm.aggregate_model = v
    if v := env("AGCLAW_LOCATION"):
        cfg.agent.location = v
    if v := env("AGCLAW_SANDBOX"):
        cfg.tools.sandbox = v
    if v := env("AGCLAW_DOCKER_IMAGE"):
        cfg.tools.docker_image = v
    if v := env("AGCLAW_DOCKER_NETWORK"):
        cfg.tools.docker_network = v
    if v := env("AGCLAW_AGGREGATE_EVERY_N"):
        try:
            cfg.memory.aggregate_every_n_turns = int(v)
        except ValueError:
            pass


def load_config(path: Path | None = None) -> Config:
    """Resolve config from defaults ← config.json ← environment (env wins)."""
    path = path or default_config_path()
    data: dict = {}
    if path and path.exists():
        try:
            data = json.loads(path.read_text())
        except Exception:
            data = {}  # a malformed config file falls back to defaults
    cfg = Config(**data)
    _apply_env_overrides(cfg)
    return cfg
