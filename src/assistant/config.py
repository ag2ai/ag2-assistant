"""AG2 Assistant configuration.

Resolution order (highest precedence first):
  1. Environment variables (AG2ASSISTANT_*), loaded from .env if present
  2. ~/.ag2assistant/config.yaml
  3. Built-in defaults

Use `load_config()` to get a fully resolved Config; bare `Config()` is just the
built-in defaults (handy in tests).
"""

import os
from pathlib import Path
from typing import TYPE_CHECKING

import yaml
from dotenv import load_dotenv
from pydantic import BaseModel, Field

if TYPE_CHECKING:
    from assistant.profiles import ProfileMeta

load_dotenv()

# Data/identity: secrets, config, memory, tasks, and Google auth live under
# ~/.ag2assistant; env-var overrides use the AG2ASSISTANT_ prefix.
_DATA_DIR_NAME = ".ag2assistant"
_ENV_PREFIX = "AG2ASSISTANT_"


def _default_root() -> Path:
    """The install root, honoring AG2ASSISTANT_DATA_DIR. Used by BOTH the layered
    Config defaults and the standalone secrets/settings resolvers below so the two
    never diverge (a container mounts persistent state at a fixed path via this env)."""
    if v := os.environ.get("AG2ASSISTANT_DATA_DIR"):
        return Path(v).expanduser()
    return Path.home() / _DATA_DIR_NAME


def read_yaml(path: Path) -> dict:
    """Parse a YAML mapping file. A missing, malformed, or non-mapping file reads
    as an empty dict — the same tolerance a malformed config.json had."""
    try:
        data = yaml.safe_load(Path(path).read_text())
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def write_yaml(path: Path, data: dict) -> None:
    """Atomically write a YAML mapping (tmp file + os.replace, so a crashed write
    never leaves a truncated config behind)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(yaml.safe_dump(data, sort_keys=False, allow_unicode=True))
    os.replace(tmp, path)


class LLMConfig(BaseModel):
    """LLM provider configuration."""

    provider: str = "gemini"  # gemini | anthropic | openai | ollama
    model: str = "gemini-3.5-flash"
    api_key_env: str = "GEMINI_API_KEY"
    # How the OpenAI provider authenticates:
    #   "api_key"      — pay-per-token via OPENAI_API_KEY (default, unchanged path)
    #   "subscription" — Sign in with ChatGPT (OAuth); routes through the ChatGPT
    #                    backend on the user's Codex/ChatGPT subscription. See
    #                    assistant.codex_auth (unofficial / gray-area vs OpenAI ToS).
    # Ignored for non-OpenAI providers (env: AG2ASSISTANT_OPENAI_AUTH_MODE).
    auth_mode: str = "api_key"
    # AG2 emits ModelMessageChunk events only when provider configs opt into
    # streaming. The web/task UI is built to consume those chunks live.
    streaming: bool = True
    # Optional cheaper model for the passive memory-aggregation pass. None → reuse
    # the main model. Aggregation is a background summarisation, so a smaller/
    # cheaper model is usually fine and saves cost on long sessions.
    aggregate_model: str | None = None
    # Per-provider advanced options, keyed by provider name; each entry is extra
    # constructor kwargs for that provider's AG2 config, merged in last (so they
    # can also override model/api_key/streaming). This is how the OpenAI and
    # Anthropic clients reach OpenAI-API-compatible servers (llama.cpp, vLLM,
    # LM Studio, LiteLLM): {"openai": {"base_url": "http://host:8080/v1"}}.
    # A config.json value is the install-wide base; each profile's
    # Settings → Model & Keys → Advanced overlays its own entries on top.
    provider_options: dict[str, dict] = Field(default_factory=dict)
    # Hard wall-clock ceiling on a single LLM call. Provider SDKs sometimes hang a
    # streaming request indefinitely (no error, no timeout) — a stuck turn then sits
    # "running" forever. The per-call timeout middleware wraps each call and raises
    # after this many seconds so the turn fails cleanly (env: AG2ASSISTANT_LLM_TIMEOUT).
    call_timeout_s: float = 180.0
    # How many times to RE-TRY a failed LLM call (a per-call timeout or a transient
    # provider error — 429/5xx) before letting it propagate. 2 retries = up to 3
    # total attempts, each with its own fresh timeout window. Turns a one-off wedged
    # or rate-limited call into a hiccup instead of an attempt/task death
    # (env: AG2ASSISTANT_LLM_RETRIES). 0 disables retrying.
    call_retries: int = 2
    # Event-silence watchdog: emit a CRITICAL alert onto the turn's stream when NO
    # event has been seen for this long during an active turn — the trigger-driven
    # observers can't fire on the ABSENCE of events (env: AG2ASSISTANT_SILENCE_ALERT).
    silence_alert_s: float = 300.0
    # Second, harder silence threshold: when set (>0) the watchdog escalates to a
    # FATAL alert (→ HaltEvent) so a truly dead turn terminates deterministically
    # rather than alerting forever (env: AG2ASSISTANT_SILENCE_HALT).
    silence_halt_s: float = 900.0


class AgentConfig(BaseModel):
    """Agent configuration."""

    name: str = "ag2-assistant"
    system_prompt: str = (
        "You are AG2 Assistant, a helpful personal AI assistant. "
        "You are direct, concise, and helpful."
    )
    # Free-text user location (e.g. "Sydney, Australia"). Used for environment
    # context; local date/time is detected automatically from the system clock.
    location: str | None = None


class GatewayConfig(BaseModel):
    """Configuration for interactive gateway turns."""

    # Wall-clock limit for one chat turn, including clarification waits, model calls,
    # and tool execution. Long-running work belongs in a background task instead.
    reply_timeout_s: float = 600.0


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
    # Summarise the oldest stream events (an LLM call on the cheap model) once a
    # conversation's history crosses this many tokens, keeping long chats and
    # task runs inside the context window.
    compact_max_tokens: int = 20_000


class TasksConfig(BaseModel):
    """Recurring-task run-history knobs (see docs/task-run-history-plan.md)."""

    # How many prior completed runs of a template feed the next run's context.
    history_runs: int = 3
    # Bounded background digest pipeline: worker count, max backlog before a
    # completion's digest is dropped (safe — the run still shows via its stub),
    # and the per-digest wall-clock cap.
    digest_concurrency: int = 2
    digest_queue_max: int = 64
    digest_timeout_s: int = 30


# The Config sections a profile's config.yaml may overlay. Settings keys in the same
# file (voice, focuses, mcp_servers, project_folder, voice_provider) are read by
# assistant.settings, not here.
_OVERLAY_SECTIONS = ("llm", "agent", "gateway", "tools", "memory", "tasks")


def apply_overlay(cfg: "Config", path: Path) -> None:
    """Merge a profile's config.yaml onto ``cfg`` in place, field-wise per known
    section (a key present in the overlay wins; absent keys inherit the global).
    A section that fails validation is skipped wholesale — same tolerance as a
    malformed global config file."""
    data = read_yaml(path)
    for section in _OVERLAY_SECTIONS:
        raw = data.get(section)
        if not isinstance(raw, dict) or not raw:
            continue
        current = getattr(cfg, section)
        try:
            setattr(cfg, section, type(current)(**{**current.model_dump(), **raw}))
        except Exception:
            continue


class Config(BaseModel):
    """Root AG2 Assistant configuration (built-in defaults; see `load_config`)."""

    llm: LLMConfig = Field(default_factory=LLMConfig)
    agent: AgentConfig = Field(default_factory=AgentConfig)
    gateway: GatewayConfig = Field(default_factory=GatewayConfig)
    tools: ToolsConfig = Field(default_factory=ToolsConfig)
    memory: MemoryConfig = Field(default_factory=MemoryConfig)
    tasks: TasksConfig = Field(default_factory=TasksConfig)
    # The install root: holds only global files (profiles.json, secrets.json,
    # pricing.json, log) and the profiles/ tree. Stays fixed across with_profile().
    root_dir: Path = Field(default_factory=_default_root)
    # Profile-owned data dir. Equals root_dir for the base config; with_profile()
    # repoints it at root_dir/profiles/<id>.
    data_dir: Path = Field(default_factory=_default_root)
    # Where installed skills live (SKILL.md packages).
    skills_dir: Path = Field(default_factory=lambda: _default_root() / "skills")
    # The agent's working file space — a real, visible folder it can read/write via
    # AG2's FilesystemToolkit (confined to here). Configurable via AG2ASSISTANT_WORKSPACE.
    workspace_dir: Path = Field(default_factory=lambda: Path.home() / "Documents" / "AG2 Assistant")

    def with_profile(self, meta: "ProfileMeta") -> "Config":
        """A deep copy whose path fields are reinterpreted for a profile: data_dir and
        skills_dir land under root_dir/profiles/<id>, workspace_dir is that profile dir's
        ``workspace/`` subfolder (derived, not user-chosen), and the profile's config.yaml
        overlay is applied (explicit AG2ASSISTANT_* env vars still win last). root_dir is
        unchanged (the global files stay at the root)."""
        cfg = self.model_copy(deep=True)
        cfg.data_dir = cfg.root_dir / "profiles" / meta.id
        cfg.skills_dir = cfg.data_dir / "skills"
        cfg.workspace_dir = cfg.data_dir / "workspace"
        apply_overlay(cfg, cfg.data_dir / "config.yaml")
        _apply_env_overrides(cfg, include_paths=False)
        return cfg


def default_config_path() -> Path:
    """Where AG2 Assistant looks for the global YAML config file."""
    return _default_root() / "config.yaml"


def read_global_config() -> dict:
    """The raw global config.yaml document (empty dict when absent/malformed)."""
    return read_yaml(default_config_path())


def update_global_section(key: str, value) -> None:
    """Read-modify-write one top-level section of the global config.yaml, preserving
    every other key (the file is shared: Config sections, llm_configs, data_dir)."""
    data = read_yaml(default_config_path())
    data[key] = value
    write_yaml(default_config_path(), data)


def data_dir() -> Path:
    """Resolve the data directory WITHOUT the full config layering, so the secrets /
    settings stores can locate their files without recursing back into load_config()
    (which itself consults settings). AG2ASSISTANT_DATA_DIR wins (highest precedence,
    matching _apply_env_overrides); then a config.yaml data_dir; then the default root."""
    if v := os.environ.get("AG2ASSISTANT_DATA_DIR"):
        return Path(v).expanduser()
    d = read_yaml(default_config_path()).get("data_dir")
    if d:
        return Path(d).expanduser()
    return _default_root()


def _apply_env_overrides(cfg: Config, *, include_paths: bool = True) -> None:
    """Layer AG2ASSISTANT_* environment variables on top (highest precedence).

    ``include_paths=False`` re-applies only the non-path overrides — used by
    with_profile() after the overlay, where the profile paths are already final and
    AG2ASSISTANT_DATA_DIR/WORKSPACE must not clobber them back to the root."""
    env = os.environ.get
    if v := env("AG2ASSISTANT_LLM_PROVIDER"):
        cfg.llm.provider = v
    if v := env("AG2ASSISTANT_MODEL"):
        cfg.llm.model = v
    if v := env("AG2ASSISTANT_API_KEY_ENV"):
        cfg.llm.api_key_env = v
    if v := env("AG2ASSISTANT_OPENAI_AUTH_MODE"):
        cfg.llm.auth_mode = v.strip().lower()
    if v := env("AG2ASSISTANT_STREAMING"):
        cfg.llm.streaming = v.strip().lower() not in {"0", "false", "no", "off"}
    if v := env("AG2ASSISTANT_AGGREGATE_MODEL"):
        cfg.llm.aggregate_model = v
    if v := env("AG2ASSISTANT_LLM_TIMEOUT"):
        try:
            cfg.llm.call_timeout_s = float(v)
        except ValueError:
            pass
    if v := env("AG2ASSISTANT_LLM_RETRIES"):
        try:
            cfg.llm.call_retries = int(v)
        except ValueError:
            pass
    if v := env("AG2ASSISTANT_SILENCE_ALERT"):
        try:
            cfg.llm.silence_alert_s = float(v)
        except ValueError:
            pass
    if v := env("AG2ASSISTANT_SILENCE_HALT"):
        try:
            cfg.llm.silence_halt_s = float(v)
        except ValueError:
            pass
    if v := env("AG2ASSISTANT_LOCATION"):
        cfg.agent.location = v
    if v := env("AG2ASSISTANT_REPLY_TIMEOUT"):
        try:
            cfg.gateway.reply_timeout_s = float(v)
        except ValueError:
            pass
    if include_paths:
        if v := env("AG2ASSISTANT_WORKSPACE"):
            cfg.workspace_dir = Path(v).expanduser()
        if v := env("AG2ASSISTANT_DATA_DIR"):
            # Redirect the whole install root (global files + profiles/ tree). Mirrors the
            # default layout so with_profile() keeps repointing data_dir/skills_dir under it.
            # Primarily for containers, which mount persistent state at a fixed path.
            root = Path(v).expanduser()
            cfg.root_dir = root
            cfg.data_dir = root
            cfg.skills_dir = root / "skills"
    if v := env("AG2ASSISTANT_SANDBOX"):
        cfg.tools.sandbox = v
    if v := env("AG2ASSISTANT_DOCKER_IMAGE"):
        cfg.tools.docker_image = v
    if v := env("AG2ASSISTANT_DOCKER_NETWORK"):
        cfg.tools.docker_network = v
    if v := env("AG2ASSISTANT_AGGREGATE_EVERY_N"):
        try:
            cfg.memory.aggregate_every_n_turns = int(v)
        except ValueError:
            pass
    if v := env("AG2ASSISTANT_COMPACT_MAX_TOKENS"):
        try:
            cfg.memory.compact_max_tokens = int(v)
        except ValueError:
            pass
    for env_name, field in (
        ("AG2ASSISTANT_TASKS_HISTORY_RUNS", "history_runs"),
        ("AG2ASSISTANT_TASKS_DIGEST_CONCURRENCY", "digest_concurrency"),
        ("AG2ASSISTANT_TASKS_DIGEST_QUEUE_MAX", "digest_queue_max"),
        ("AG2ASSISTANT_TASKS_DIGEST_TIMEOUT", "digest_timeout_s"),
    ):
        if v := env(env_name):
            try:
                setattr(cfg.tasks, field, int(v))
            except ValueError:
                pass


def load_config(path: Path | None = None) -> Config:
    """Resolve config from defaults ← config.yaml ← environment (env wins)."""
    path = path or default_config_path()
    data: dict = read_yaml(path) if path else {}
    cfg = Config(**data)
    # root_dir tracks whatever data_dir resolves to (config.json may override it); the
    # profiles/ tree and global files live under this root. Profile derivation is via
    # Config.with_profile(); load_config() itself is profile-agnostic.
    cfg.root_dir = cfg.data_dir
    # Derive the active named LLM configuration onto the flat cfg.llm fields
    # (provider/model/provider_options) so model_config & friends stay untouched.
    # Lazy import breaks the cycle (llm_configs imports config.data_dir); a malformed
    # store is swallowed like a malformed config.json — the flat defaults then apply.
    try:
        from assistant import llm_configs

        llm_configs.apply_active(cfg)
    except Exception:
        pass
    _apply_env_overrides(cfg)  # explicit AG2ASSISTANT_* env still wins last
    return cfg
