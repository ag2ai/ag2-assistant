"""AG2 Assistant configuration.

Resolution order (highest precedence first):
  1. Environment variables (AG2ASSISTANT_*), loaded from .env if present
  2. <root>/config.yaml
  3. Built-in defaults

`resolve_config(env, paths)` is the pure core: it reads nothing but the mapping and
the layout handed to it. `load_config()` is the only boundary that consults the real
process environment. A Config's path fields have no defaults — build one with
`Config.for_paths(paths)`.
"""

import os
from collections.abc import Mapping
from pathlib import Path
from typing import TYPE_CHECKING

from dotenv import load_dotenv
from pydantic import BaseModel, Field

from assistant.paths import Paths
from assistant.yamlio import read_yaml, write_yaml

if TYPE_CHECKING:
    from assistant.profiles import ProfileMeta  # type-only

load_dotenv()

# Env-var overrides use the AG2ASSISTANT_ prefix; the on-disk layout itself lives in
# assistant.paths.Paths.
_ENV_PREFIX = "AG2ASSISTANT_"


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
    # cheaper model is usually fine and saves cost on long chats.
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
# file (voice, focuses, mcp_servers, voice_provider) are read by
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
    """Root AG2 Assistant configuration. Path fields are required — build one with
    :meth:`for_paths` so the on-disk layout always comes from a resolved ``Paths``."""

    llm: LLMConfig = Field(default_factory=LLMConfig)
    agent: AgentConfig = Field(default_factory=AgentConfig)
    gateway: GatewayConfig = Field(default_factory=GatewayConfig)
    tools: ToolsConfig = Field(default_factory=ToolsConfig)
    memory: MemoryConfig = Field(default_factory=MemoryConfig)
    tasks: TasksConfig = Field(default_factory=TasksConfig)
    # The install root: holds only global files (profiles.json, secrets.json,
    # pricing.json, log) and the profiles/ tree. Stays fixed across with_profile().
    root_dir: Path
    # Profile-owned data dir. Equals root_dir for the base config; with_profile()
    # repoints it at root_dir/profiles/<id>.
    data_dir: Path
    # Where installed skills live (SKILL.md packages).
    skills_dir: Path
    # The agent's working file space — a real, visible folder it can read/write via
    # AG2's FilesystemToolkit (confined to here).
    workspace_dir: Path
    # Env vars the saved secrets contribute (provider keys, channel tokens, Ollama
    # base URL), merged with whatever of those the process env already carried.
    # Read instead of os.environ so no module below the boundary touches the process.
    secret_env: dict[str, str] = Field(default_factory=dict)
    # The INSTALL layout this config was resolved from. Unlike the fields above it is
    # never repointed per profile, so anything holding a Config can locate the global
    # stores (secrets, profiles, google, codex) without reading the environment.
    paths: Paths
    # Directories searched for external CLI binaries (the coding-agent ACP adapters,
    # docker) — the process PATH, split once at the boundary. Empty means "nothing
    # installed": no module below the boundary may fall back to os.environ.
    search_path: list[Path] = Field(default_factory=list)
    # The host ACP bridge to use instead of spawning coding agents locally, as
    # ``host[:port]`` plus its optional shared token (see coding.detect.parse_bridge).
    acp_bridge: str = ""
    acp_bridge_token: str = ""

    @classmethod
    def for_paths(cls, paths: Paths, **overrides) -> "Config":
        """A Config whose layout comes from ``paths``, every other field defaulted."""
        base = {
            "paths": paths,
            "root_dir": paths.root,
            "data_dir": paths.root,
            "skills_dir": paths.skills_dir,
            "workspace_dir": paths.workspace,
        }
        return cls(**{**base, **overrides})

    def with_profile(
        self, meta: "ProfileMeta", *, env: Mapping[str, str] | None = None
    ) -> "Config":
        """A deep copy whose path fields are reinterpreted for a profile: data_dir and
        skills_dir land under profiles/<id>, workspace_dir is that profile dir's
        ``workspace/`` subfolder (derived, not user-chosen), and the profile's config.yaml
        overlay is applied (explicit AG2ASSISTANT_* vars in ``env`` still win last).
        root_dir and ``paths`` are unchanged (the global files stay at the root)."""
        cfg = self.model_copy(deep=True)
        cfg.data_dir = self.paths.profile_dir(meta.id)
        cfg.skills_dir = cfg.data_dir / "skills"
        cfg.workspace_dir = cfg.data_dir / "workspace"
        apply_overlay(cfg, cfg.data_dir / "config.yaml")
        # Re-derive the active LLM config from this profile's Active override (ADR 0015),
        # so it wins over the install-wide active; the env re-apply below still wins last.
        try:
            # local imports: both modules import config, so top-level would cycle
            from assistant.llm_configs import LlmConfigStore
            from assistant.settings import profile_settings

            override = profile_settings(cfg.data_dir).get_llm_override()
            if override:
                LlmConfigStore(self.paths).apply_active(cfg, override_id=override)
        except Exception:
            pass
        apply_env_overrides(cfg, env or {})
        return cfg


def read_global_config(paths: Paths) -> dict:
    """The raw global config.yaml document (empty dict when absent/malformed)."""
    return read_yaml(paths.config_yaml)


def update_global_section(paths: Paths, key: str, value) -> None:
    """Read-modify-write one top-level section of the global config.yaml, preserving
    every other key (the file is shared: Config sections, llm_configs, data_dir)."""
    data = read_yaml(paths.config_yaml)
    data[key] = value
    write_yaml(paths.config_yaml, data)


def apply_env_overrides(cfg: Config, env: Mapping[str, str]) -> None:
    """Layer AG2ASSISTANT_* variables from ``env`` on top (highest precedence).

    Path fields are deliberately NOT touched: the on-disk layout is resolved once by
    ``Paths.from_env`` (which reads the same AG2ASSISTANT_DATA_DIR/WORKSPACE), so
    re-applying it here could only make ``cfg.paths`` and ``cfg.root_dir`` disagree."""
    get = env.get
    # Ambient host facts every module below the boundary must be handed, never read:
    # where external CLIs live, and whether a host ACP bridge replaces local spawns.
    # Same split as coding.detect.default_search_path (the boundary for callers with
    # no Config, e.g. the acp-bridge daemon).
    if v := get("PATH"):
        cfg.search_path = [Path(p) for p in v.split(os.pathsep) if p]
    if v := get("AG2ASSISTANT_ACP_BRIDGE"):
        cfg.acp_bridge = v.strip()
    if v := get("AG2ASSISTANT_ACP_BRIDGE_TOKEN"):
        cfg.acp_bridge_token = v.strip()
    if v := get("AG2ASSISTANT_LLM_PROVIDER"):
        cfg.llm.provider = v
    if v := get("AG2ASSISTANT_MODEL"):
        cfg.llm.model = v
    if v := get("AG2ASSISTANT_API_KEY_ENV"):
        cfg.llm.api_key_env = v
    if v := get("AG2ASSISTANT_OPENAI_AUTH_MODE"):
        cfg.llm.auth_mode = v.strip().lower()
    if v := get("AG2ASSISTANT_STREAMING"):
        cfg.llm.streaming = v.strip().lower() not in {"0", "false", "no", "off"}
    if v := get("AG2ASSISTANT_AGGREGATE_MODEL"):
        cfg.llm.aggregate_model = v
    if v := get("AG2ASSISTANT_LLM_TIMEOUT"):
        try:
            cfg.llm.call_timeout_s = float(v)
        except ValueError:
            pass
    if v := get("AG2ASSISTANT_LLM_RETRIES"):
        try:
            cfg.llm.call_retries = int(v)
        except ValueError:
            pass
    if v := get("AG2ASSISTANT_SILENCE_ALERT"):
        try:
            cfg.llm.silence_alert_s = float(v)
        except ValueError:
            pass
    if v := get("AG2ASSISTANT_SILENCE_HALT"):
        try:
            cfg.llm.silence_halt_s = float(v)
        except ValueError:
            pass
    if v := get("AG2ASSISTANT_LOCATION"):
        cfg.agent.location = v
    if v := get("AG2ASSISTANT_REPLY_TIMEOUT"):
        try:
            cfg.gateway.reply_timeout_s = float(v)
        except ValueError:
            pass
    if v := get("AG2ASSISTANT_SANDBOX"):
        cfg.tools.sandbox = v
    if v := get("AG2ASSISTANT_DOCKER_IMAGE"):
        cfg.tools.docker_image = v
    if v := get("AG2ASSISTANT_DOCKER_NETWORK"):
        cfg.tools.docker_network = v
    if v := get("AG2ASSISTANT_AGGREGATE_EVERY_N"):
        try:
            cfg.memory.aggregate_every_n_turns = int(v)
        except ValueError:
            pass
    if v := get("AG2ASSISTANT_COMPACT_MAX_TOKENS"):
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
        if v := get(env_name):
            try:
                setattr(cfg.tasks, field, int(v))
            except ValueError:
                pass


def resolve_config(env: Mapping[str, str], paths: Paths) -> Config:
    """Resolve a Config from defaults ← config.yaml ← ``env`` (env wins).

    Pure: the only environment it sees is ``env`` and the only layout is ``paths``.
    The saved secrets' env overlay lands on ``cfg.secret_env`` (and feeds the
    AG2ASSISTANT_* layering) so nothing downstream has to read the process env."""
    data = read_yaml(paths.config_yaml)
    data.pop("data_dir", None)  # Paths already resolved the layout
    cfg = Config.for_paths(paths, **data)
    # Derive the active named LLM configuration onto the flat cfg.llm fields
    # (provider/model/provider_options) so model_config & friends stay untouched.
    # Lazy import breaks the cycle; a malformed store is swallowed like a malformed
    # config.yaml — the flat defaults then apply.
    try:
        from assistant.llm_configs import LlmConfigStore  # local: import cycle

        LlmConfigStore(paths).apply_active(cfg)
    except Exception:
        pass
    try:
        from assistant.secrets import SecretStore  # local: import cycle

        cfg.secret_env = SecretStore(paths).merged_env(env)
    except Exception:
        pass
    # Saved secrets may carry AG2ASSISTANT_* values too, but an explicit env entry
    # always wins over the store.
    apply_env_overrides(cfg, {**cfg.secret_env, **dict(env)})
    return cfg


def load_config() -> Config:
    """The entry-point boundary: the one place that reads os.environ and Path.home()
    for configuration. Everything below takes the resolved Config/Paths."""
    paths = Paths.from_env(os.environ, Path.home())
    return resolve_config(os.environ, paths)
