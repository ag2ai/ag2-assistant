"""AG2 Assistant agent built on AG2."""

from datetime import datetime
from pathlib import Path
from typing import Annotated, Literal

from ag2 import Agent, tool
from ag2.config import (
    AnthropicConfig,
    OllamaConfig,
    OpenAIConfig,
    OpenAIResponsesConfig,
)
from ag2.config.gemini import GeminiConfig
from ag2.policies import AlertPolicy
from ag2.tools import SkillSearchToolkit
from ag2.tools.skills import LocalRuntime, SkillPlugin
from pydantic import Field

from assistant.codex_auth import BACKEND_BASE, CodexAuth, default_headers
from assistant.config import Config, load_config
from assistant.folders import FolderStore
from assistant.hitl import Asker, build_hitl_hook
from assistant.integrations.google_auth import GoogleAuth
from assistant.memory import (
    build_compaction_config,
    build_knowledge_config,
    profile_assembly,
    read_profile_sync,
    remember_note,
)
from assistant.middleware import LLMRetryMiddleware, LLMTimeoutMiddleware
from assistant.observability import agent_logging_middleware, log_suppressed
from assistant.observers import build_observers
from assistant.permissions import PermissionManager, PermissionStore
from assistant.secrets import DEFAULT_OLLAMA_BASE, KEY_ENV, OLLAMA_BASE_ENV
from assistant.settings import profile_settings
from assistant.skills import FilteredSkillRuntime, SkillStateStore, skill_origin
from assistant.tools import build_agent_tools
from assistant.tools.docker_sandbox import build_docker_skill_runtime, docker_available

# Commands skill scripts must never run (defense-in-depth; skills can ship code).
_SKILL_BLOCKED = ["rm -rf /", "sudo", "shutdown", "reboot", "mkfs", ":(){"]

# Default (cheaper) model for the passive memory-aggregation pass, per provider.
# Used only when llm.aggregate_model isn't set. Override via AG2ASSISTANT_AGGREGATE_MODEL.
# Per-provider default for bulk/background LLM work (aggregation, compaction,
# research subtasks) — the provider's cheap tier, overridable via
# `config.llm.aggregate_model`. Ollama is absent on purpose: it's local (nothing
# to save) and any specific model here might not be pulled.
_DEFAULT_AGGREGATE_MODEL = {
    "gemini": "gemini-3.1-flash-lite",
    "anthropic": "claude-haiku-4-5-20251001",
    "openai": "gpt-5-mini",
}

# Providers that are a coding CLI driven over ACP rather than an HTTP API: the
# model config comes from acp_provider.BUILDERS and the per-call timeout does not
# apply (see _build_middleware). Kept as a plain tuple so the branch below can be
# taken without importing the coding package.
ACP_PROVIDERS = ("claude_code", "codex")


def model_config(config: Config, model: str | None = None):
    """Build the AG2 ModelConfig for the configured provider.

    `model` overrides `config.llm.model` (used for the cheaper aggregation pass).
    The API key comes from ``config.secret_env`` by the provider's conventional var
    (the secrets store contributes it at resolve time), not the fixed api_key_env field.

    `config.llm.provider_options[provider]` (Settings → Model & Keys → Advanced, or
    config.json) is merged into the provider config's kwargs LAST, so any of its
    constructor settings — base_url, temperature, timeout, even api_key — can be
    set or overridden. A base_url is what points the OpenAI/Anthropic clients at
    OpenAI-API-compatible servers (llama.cpp, vLLM, LM Studio, LiteLLM).
    """
    model = model or config.llm.model
    provider = config.llm.provider.lower()
    api_key = config.secret_env.get(KEY_ENV.get(provider, config.llm.api_key_env), "")
    opts = dict(config.llm.provider_options.get(provider) or {})
    if provider in ACP_PROVIDERS:
        # Coding CLI over ACP: the CLI's own disk login is the auth (no key),
        # and the entry's Advanced options are ACPConfig constructor overrides.
        from assistant.coding import acp_provider

        return acp_provider.BUILDERS[provider](config, model=model, options=opts)
    if provider == "anthropic":
        return AnthropicConfig(
            **{"model": model, "api_key": api_key, "streaming": config.llm.streaming, **opts}
        )
    if provider == "openai":
        if config.llm.auth_mode == "subscription":
            # "Sign in with ChatGPT": route requests through the ChatGPT backend on
            # the user's Codex/ChatGPT subscription instead of a pay-per-token key.
            # The OAuth access token rides as api_key (SDK → Authorization: Bearer);
            # the account id + Codex headers go via default_headers. See codex_auth
            # (unofficial / gray-area vs OpenAI ToS). ensure_fresh refreshes the token.
            # best-effort (never raises) — building the agent must not 500 a reload
            # when the token can't be refreshed; the turn then fails with the real
            # OpenAI error (e.g. unsupported_country) instead.
            creds = CodexAuth(config.paths).creds_best_effort()
            # Advanced options (temperature, max_output_tokens, ...) merge first;
            # everything the subscription OWNS is forced afterwards, so options can
            # neither point elsewhere nor leak a key: the endpoint/token/headers are
            # the backend's contract, streaming is REQUIRED by it ("Stream must be
            # set to true") and response storage rejected ("Store must be set to
            # false") — both found live; the Codex CLI sends the same. "api" is our
            # own surface switch and meaningless here.
            sub_opts = {k: v for k, v in opts.items() if k != "api"}
            return OpenAIResponsesConfig(
                **{
                    **sub_opts,
                    "model": model,
                    "api_key": creds.access_token,
                    "base_url": BACKEND_BASE,
                    "default_headers": default_headers(creds),
                    "streaming": True,
                    "store": False,
                }
            )
        # OpenAI's Responses API (their preferred surface; also enables the native
        # image_generation tool). A custom base_url flips the default to the Chat
        # Completions API instead: OpenAI-compatible servers (llama.cpp, vLLM,
        # LM Studio) implement /v1/chat/completions far more reliably than
        # /v1/responses. Pin either with "api": "responses" | "chat" in the options.
        api = str(opts.pop("api", "") or "").lower()
        if not api:
            api = "chat" if opts.get("base_url") else "responses"
        if api not in ("responses", "chat", "chat_completions"):
            raise ValueError(f'openai option "api" must be "responses" or "chat", not {api!r}')
        kwargs = {"model": model, "api_key": api_key, "streaming": config.llm.streaming, **opts}
        if api == "responses":
            return OpenAIResponsesConfig(**kwargs)
        return OpenAIConfig(**kwargs)
    if provider == "ollama":
        return OllamaConfig(
            **{
                "model": model,
                "host": config.secret_env.get(OLLAMA_BASE_ENV, DEFAULT_OLLAMA_BASE),
                "streaming": config.llm.streaming,
                **opts,
            }
        )
    # Generous output budget so long research notes / briefings aren't truncated
    # mid-sentence. Gemini counts thinking tokens against max_output_tokens, so
    # this must cover reasoning plus the full report text.
    return GeminiConfig(
        **{
            "model": model,
            "api_key": api_key,
            "max_output_tokens": 32768,
            "streaming": config.llm.streaming,
            **opts,
        }
    )


def _build_middleware(config: Config) -> list:
    """The per-agent LLM middleware stack for this provider."""
    middleware = [
        agent_logging_middleware(),  # per-turn LLM/tool logs → ag2assistant.log
        # Retry a failed call before it kills the attempt/task. Listed BEFORE
        # the timeout so it wraps it (AG2 nests later middleware closer to the
        # call): each retry re-enters the timeout, getting a fresh window. A
        # transient hang or 429/5xx becomes a hiccup, not a failure.
        LLMRetryMiddleware(config.llm.call_retries),
    ]
    if config.llm.provider.lower() not in ACP_PROVIDERS:
        # Wall-clock ceiling per LLM call: a hung/stalled provider call raises
        # instead of awaiting forever (the incident's silent 2-hour hang). NOT
        # for the CLI agent: there one "call" is the CLI agent's whole inner
        # tool loop, so the per-call ceiling is ACPConfig.turn_timeout; the
        # silence watchdog covers wedges.
        middleware.append(LLMTimeoutMiddleware(config.llm.call_timeout_s))
    return middleware


def _default_aggregate_model(config: Config) -> str | None:
    """The provider's cheap-tier default for background work — suppressed when the
    provider is pointed at a custom base_url (an OpenAI-compatible server won't
    serve OpenAI's model names; reuse the main model instead, like Ollama)."""
    provider = config.llm.provider.lower()
    if (config.llm.provider_options.get(provider) or {}).get("base_url"):
        return None
    return _DEFAULT_AGGREGATE_MODEL.get(provider)


def cheap_model(config: Config) -> str | None:
    """A faster/cheaper model for bulk work (research subtasks, verification)."""
    return config.llm.aggregate_model or _default_aggregate_model(config)


def bundled_skills_dir():
    """Directory of first-party skills shipped with AG2 Assistant (read-only)."""
    return Path(__file__).parent / "skills"


def build_skills_runtime(config: Config):
    """The runtime backing skill discovery, load, and script execution.

    Skills install into ``config.skills_dir``. Profile runtimes inherit the Global
    skills directory read-only, and every runtime inherits Bundled first-party
    skills. Search order is Profile → Global → Bundled.

    When the Docker sandbox is selected (`config.tools.sandbox == "docker"`),
    skill *scripts* run inside a one-shot, bind-mounted container — so untrusted
    skill code can't reach the user's files. Storage/discovery stay local.
    """
    config.skills_dir.mkdir(parents=True, exist_ok=True)
    global_skills = config.root_dir / "skills"
    extra = []
    if config.skills_dir.resolve() != global_skills.resolve():
        extra.append(str(global_skills))
    extra.append(str(bundled_skills_dir()))

    if config.tools.sandbox == "docker":
        if docker_available(config.search_path):
            return build_docker_skill_runtime(
                install_dir=config.skills_dir,
                blocked=_SKILL_BLOCKED,
                image=config.tools.docker_image,
                network=config.tools.docker_network,
                extra_paths=extra,
            )

    return LocalRuntime(dir=str(config.skills_dir), blocked=_SKILL_BLOCKED, extra_paths=extra)


def resolve_skills(config: Config, runtime):
    """`runtime` filtered down to the skills resolved available for `config`.

    This is the single resolution seam (ADR 0016): a skill turned off in the
    install-wide `SkillStateStore`, or suppressed for this profile, is absent from
    the view and unloadable through it. No other code path decides availability.
    Resolution is **default-on** (a skill is available unless a record turns it
    off) — the inverse of a Folders Grant; see `SkillStateStore` for why not to
    "fix" that. `.skills` on the result is what an agent build would see.
    """
    store = SkillStateStore(config.root_dir / "skills.json")
    profile = config.data_dir.name
    profile_root = config.skills_dir if config.data_dir != config.root_dir else None
    return FilteredSkillRuntime(
        runtime,
        lambda skill: store.is_available(
            skill.name,
            profile,
            origin=skill_origin(skill.location, bundled_skills_dir(), profile_root),
        ),
    )


def build_skills_plugin(config: Config, runtime):
    """Progressive-disclosure Skills plugin over `runtime`, filtered by skill state.

    `SkillPlugin` injects the `<available_skills>` catalog (name + description +
    location per skill) straight into the system prompt on startup — the model
    discovers what's available with no `list_skills` round-trip — and exposes
    `load_skill` / `read_skill_resource` / `run_skill_script` for those skills.

    What it may show is decided by `resolve_skills`, so a Disabled skill reaches
    neither the catalog nor the activation tools.

    The catalog and the activation tools are a **construction-time snapshot**: a
    skill installed or toggled mid-session isn't reflected until the next agent
    build (a `ProfileManager.reload`) picks it up — which is exactly what the
    /api/skills routes trigger on every change.
    """
    return SkillPlugin(resolve_skills(config, runtime))


def build_skills_install_tools(config: Config, runtime) -> list:
    """Registry search/install/remove tools (skills.sh), kept alongside the
    `SkillPlugin` so the agent can still grow its skill set.

    `SkillSearchToolkit` bundles the local list/load/read/run tools too, but the
    plugin already owns discovery and execution — so we take only the three
    registry tools to avoid registering duplicates. They share the plugin's
    `runtime`, so an install writes to the same store the plugin reads from.
    """
    toolkit = SkillSearchToolkit(runtime)
    return [toolkit.search_skills(), toolkit.install_skill(), toolkit.remove_skill()]


# Always-on behavioural guidance, kept separate from the (user-customisable)
# persona so it applies even when someone overrides the system prompt.
BEHAVIOR_GUIDANCE = (
    "Do what the user asks directly, with the tools you actually have. Every tool "
    "describes what it covers and what it does not — read those descriptions and "
    "pick the one whose remit fits the request. A specialist tool is the fast, "
    "reliable path for the job it names; where it doesn't reach, your general "
    "research tools do. Reach for a tool rather than answering from memory whenever "
    "the answer depends on the real world right now.\n"
    "Do not answer a question a tool could answer by guessing, and do not press an "
    "unrelated tool into service to look successful. When something is genuinely out "
    "of reach — a tool fails, a resource isn't found, access is denied, or nothing "
    "you have covers it — deliver whatever you legitimately could, say plainly what "
    "you could not do and why, and ask how they'd like to proceed. An honest gap is "
    "always better than an invented answer.\n"
    "When a packaged skill in <available_skills> matches the request, prefer it: it "
    "is the curated path, and one structured call beats many guesswork searches.\n"
    "Running code and shell commands is for when the user wants code run, a command "
    "executed, or their own local files worked on. It is not a way to look around, "
    "orient yourself, inspect your environment, or work around a tool that failed.\n"
    "You cannot watch video or listen to audio unless a tool you hold accepts it. If "
    "asked about a video link, say so and offer to work from the page or from a "
    "transcript the user provides."
)


def behavior_guidance() -> str:
    return BEHAVIOR_GUIDANCE


# Shown only to an agent that actually holds Google tools — their content lives in
# the cloud, so it is reached through those tools rather than the local filesystem.
GOOGLE_GUIDANCE = (
    "The user's Google content (Gmail, Calendar, Drive/Docs/Sheets) lives in the "
    "cloud, not on this machine: reach it with the Google tools you hold, never with "
    "shell commands, code, or local file paths, and never invent a path. An item is "
    "addressed by id — search for it to get the id before reading it, and a link the "
    "user pastes can be passed straight to the reader."
)


# Describes the whole system to the one universal agent, so it knows what it can
# do and reaches for its system tools to find things / act — on any surface.
CAPABILITY_GUIDANCE = (
    "You are AG2 Assistant — ONE assistant the user works with everywhere: a web chat, a "
    "task's own page, and messaging channels (Telegram, etc.). It must always feel "
    "like the same entity. You can:\n"
    "- Chat and use your tools (web search/fetch, code, local files, and the user's "
    "Google when connected) to answer or act directly for small things.\n"
    "- Run background TASKS for substantial or multi-step work: create them, schedule "
    "them (one-off or recurring — standard 5-field cron, e.g. '0 9 * * 1-5'), inspect and "
    "edit them (add subtasks or deliverables, change the objective), reschedule, run "
    "now, cancel, or archive.\n"
    "- Look things up with your system tools instead of needing them in context: list/"
    "get tasks, list/read past conversations, and list/answer open questions.\n"
    "When the user asks what exists or its status ('what tasks do I have?', 'how's X "
    "going?', 'what did we discuss?'), USE a tool to check — never say you can't see "
    "it. Prefer doing small things now; spin up a task for big or long-running jobs."
)


# Tells the agent how its long-term memory works, so it stops claiming it "can't"
# save anything. Included whenever memory is on (the default everywhere).
MEMORY_GUIDANCE = (
    "You have long-term memory of the user that persists across every conversation "
    "and surface. You learn it automatically as you chat (it's distilled in the "
    "background), and what you've learned so far is given to you at the start of "
    "each conversation. When the user explicitly asks you to remember something, or "
    "states a lasting preference, call the `remember` tool to save it right away — "
    "never claim you have no way to remember. The user can also view and edit this "
    "memory themselves in Settings → Advanced."
)


def build_memory_tool(store_path, user_store_path):
    """A tool the agent calls to save an explicit user preference/fact to long-term
    memory immediately (the passive aggregator otherwise only updates every few
    turns, and won't reliably capture a one-off 'remember this').

    Two layers, chosen by the tool's ``scope`` argument:
      - ``store_path`` is THIS profile's ``profile.db`` — persona-scoped memory;
      - ``user_store_path`` is the shared ``root_dir/user.db`` — the universal
        "who the user is" memory read by EVERY profile.
    The tool closes over both so a "remember this" lands in exactly one, never
    leaking a persona preference into the shared layer (or vice versa)."""

    @tool
    async def remember(
        note: Annotated[
            str,
            Field(
                description="The durable preference or fact about the user to save, "
                "in your own concise words (e.g. 'Always include citations and "
                "source links in research answers')."
            ),
        ],
        category: Annotated[
            Literal["how", "when", "dislikes", "writing"],
            Field(
                description="Which part of the profile this belongs under: "
                "how=how they like things done; when=timing/cadence/scheduling; "
                "dislikes=things to avoid or past corrections; "
                "writing=tone/phrasing for emails & messages."
            ),
        ] = "how",
        scope: Annotated[
            Literal["profile", "universal"],
            Field(
                description="Which memory layer to write to. "
                "universal = a lasting fact about the user AS A PERSON, true in any "
                "context regardless of which persona they're using (their name, "
                "location, timezone, family, health constraints, how they write). "
                "profile = a preference, correction, or bit of context for the work "
                "THIS persona does (how they want answers here, tools they favour). "
                "Default 'profile'; choose 'universal' only for genuine identity facts."
            ),
        ] = "profile",
    ) -> str:
        """Save a durable preference or fact about the user to long-term memory now.

        Use when the user says "remember ...", "from now on ...", or states a
        lasting preference. NOT for one-off task details. Pick `scope`: 'universal'
        for who-they-are identity facts shared across every profile, 'profile' for
        this persona's own preferences. What you save is injected into future
        conversations and is viewable/editable by the user in Settings → Memory.
        """
        target = user_store_path if scope == "universal" else store_path
        try:
            await remember_note(target, note, category)
        except Exception as exc:  # surface a clear failure rather than a tool error
            return f"Could not save to memory: {exc}"
        where = "shared 'who you are' memory" if scope == "universal" else "this profile's memory"
        return f"Saved to {where} (under '{category}')."

    return remember


def environment_context(config: Config) -> str:
    """Live environment context (date, time, location) for the agent.

    Local date/time is read from the system clock at call time; location comes
    from config if set. Passed each turn.

    When the timezone is probably wrong (container, no TZ) one line is added asking the
    agent to name the zone as it confirms a schedule, so "6:00 AM UTC" gives the user
    something to notice. It is deliberately omitted otherwise: once the clock is right,
    repeating "AEST" on every reply is noise, and this text ships on every turn.
    """
    now = datetime.now().astimezone()
    when = now.strftime("%A, %d %B %Y, %-I:%M %p")
    tz = now.strftime("%Z")
    offset = now.strftime("%z")  # e.g. +1000
    off = f"UTC{offset[:3]}:{offset[3:]}" if offset else ""
    lines = [f"- Current date and time: {when} {tz} ({off})".rstrip()]
    if config.agent.location:
        lines.append(f"- User location: {config.agent.location}")
    if config.tz_unset_in_container:
        lines.append("- Tasks fire in this timezone — say it when confirming a time.")
    return "Environment (live):\n" + "\n".join(lines)


def universal_memory_guidance(config: Config) -> str:
    """The shared "who the user is" document as a system-prompt part, or "" when it's
    empty. Read FRESH each turn from ``config.root_dir / "user.db"`` (mirroring
    ``focuses_guidance``'s read-settings-per-turn pattern) so an edit or a
    remember(scope="universal") shows up on the next turn without a reload — and so
    EVERY profile's agent injects the same identity facts. Empty doc → no section."""
    try:
        doc = read_profile_sync(config.root_dir / "user.db")
    except Exception:
        return ""
    if not doc.strip():
        return ""
    return "Who the user is (shared across all profiles):\n" + doc.strip()


def focuses_guidance(config: Config) -> str:
    """The profile's focus areas as a persona line, or "" when none are set.

    Focuses are a per-profile persona attribute chosen in onboarding / Settings and
    persisted to that profile's ``config.yaml``. Read here (mirroring core.py's
    ``profile_settings(config.data_dir)`` pattern) so a reference-swap reload picks up
    changes on the next turn. Empty focuses → no line at all."""
    try:
        focuses = profile_settings(config.data_dir).get_focuses()
    except Exception:
        return ""
    if not focuses:
        return ""
    return "The user's focus areas for this profile: " + ", ".join(focuses) + "."


def workspace_guidance(config: Config) -> str:
    """Tell the agent about its working file space (only when it has the file tools)."""
    return (
        "You have a persistent working folder (your workspace) where you can save "
        "and organise files for the user: use write_file to create a file (e.g. a "
        "markdown report), update_file to edit one, find_files to list them, and "
        "delete_file to remove one. Paths are relative to the workspace and you may "
        "use subfolders. When asked to produce or save a file, write it there and "
        f"tell the user the filename. Your workspace is at: {config.workspace_dir}."
    )


def chat_turn_timeout_guidance(config: Config) -> str:
    """Tell the universal chat agent the gateway's total turn budget."""
    seconds = config.gateway.reply_timeout_s
    return (
        f"This chat turn must complete within {seconds:g} seconds total, including time waiting "
        "for a user answer, model calls, and tool execution. Before work that might exceed that "
        "budget (such as recursive filesystem scans), use a bounded or shallow probe first. "
        "For substantial or long-running work, create a background task and report its progress; "
        "do not start an unbounded scan or command in this chat turn."
    )


def turn_prompt(
    config: Config,
    memory: bool = True,
    workspace: bool = True,
    google: bool | None = None,
    google_auth: "GoogleAuth | None" = None,
) -> list[str]:
    """Per-turn system prompt: persona + live environment context.

    `ask(prompt=...)` replaces the base prompt for that turn, so we include the
    persona, the always-on behaviour guidance, optional memory guidance, and the
    refreshed environment.

    `google` says whether this agent holds the Google tools; None means "whenever the
    user is signed in". A scoped agent (a task subagent) passes False so it is never
    told to reach for tools its capability list left out.
    """
    parts = [config.agent.system_prompt, BEHAVIOR_GUIDANCE]
    if memory:
        parts.append(MEMORY_GUIDANCE)
        universal = universal_memory_guidance(config)  # shared "who the user is" (root/user.db)
        if universal:
            parts.append(universal)
    if workspace:
        parts.append(workspace_guidance(config))
    try:
        ready = (google_auth or GoogleAuth(config.paths)).google_ready()
        if ready if google is None else google:
            parts.append(GOOGLE_GUIDANCE)
    except Exception as exc:
        log_suppressed("google token check for turn prompt", exc)
    parts.append(environment_context(config))
    return parts


def universal_turn_prompt(config: Config, surface: str = "") -> list[str]:
    """Per-turn prompt for the universal agent: persona + behaviour + capability
    map + (Google when signed in) + the SURFACE it's being addressed on + live env.

    `surface` is a short paragraph the caller builds describing where the user is
    (web chat / new-task box / a specific task + its state / a channel) so the one
    agent has the right local context without changing identity.
    """
    parts = [
        config.agent.system_prompt,
        BEHAVIOR_GUIDANCE,
        CAPABILITY_GUIDANCE,
        chat_turn_timeout_guidance(config),
        MEMORY_GUIDANCE,
        workspace_guidance(config),  # the universal agent always has the file tools
    ]
    universal = universal_memory_guidance(config)  # shared "who the user is" (root/user.db)
    if universal:
        parts.append(universal)
    focuses = focuses_guidance(config)  # per-profile persona attribute (settings.json)
    if focuses:
        parts.append(focuses)
    try:
        if GoogleAuth(config.paths).google_ready():
            parts.append(GOOGLE_GUIDANCE)
    except Exception as exc:
        log_suppressed("google token check for universal prompt", exc)
    if surface:
        parts.append(surface)
    parts.append(environment_context(config))
    return parts


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
    extra_tools: list | None = None,
    compact: bool = False,
) -> Agent:
    """Create an AG2 Assistant agent with the given configuration.

    Args:
        config: AG2 Assistant configuration (defaults to Config()).
        memory: Whether to enable the persistent user-profile memory.
        platform: The channel this session is on (cli, telegram, discord, ...).
            Observations learned this session are tagged with it.
        knowledge_store: A shared KnowledgeStore to reuse for the profile. Pass a
            locked/shared store when multiple agents write the same profile (e.g.
            the gateway's per-chat agents).
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
    # Profile aggregation and stream compaction are both just summarisation, so
    # run them on a cheaper model when one is configured (or a sensible
    # per-provider default). Falls back to the main model if neither applies.
    agg_model = config.llm.aggregate_model or _default_aggregate_model(config)
    agg_config = model_config(config, agg_model) if agg_model else llm_config
    if memory:
        knowledge = build_knowledge_config(
            platform=platform,
            store_path=config.data_dir / "profile.db",  # this profile's learned memory
            aggregate_config=agg_config,
            store=knowledge_store,
            every_n_turns=config.memory.aggregate_every_n_turns,
            on_end=single_shot,
            compact=compact,
            compact_max_tokens=config.memory.compact_max_tokens,
        )
        assembly = profile_assembly()
    elif compact:
        # Memory-less agents (task subagents) still get their context bounded.
        knowledge = build_compaction_config(
            aggregate_config=agg_config,
            max_tokens=config.memory.compact_max_tokens,
        )

    tools = build_agent_tools(
        config.llm.provider,
        sandbox=config.tools.sandbox,
        docker_image=config.tools.docker_image,
        docker_network=config.tools.docker_network,
        capabilities=capabilities,
        workspace_dir=config.workspace_dir,
        config=config,  # enables generate_image (needs provider/keys)
    )
    plugins: list = []
    if skills and (capabilities is None or "skills" in capabilities):
        # One runtime backs both the disclosure/run plugin and the registry
        # install tools, so an install writes to the store the plugin reads from.
        skills_runtime = build_skills_runtime(config)
        plugins.append(build_skills_plugin(config, skills_runtime))
        tools.extend(build_skills_install_tools(config, skills_runtime))

    # Self-knowledge: read-only tools reporting this agent's own live state (folder
    # access for this persona+chat, what's connected, active model). The bundled
    # `self-knowledge` skill is the static map; these answer the live half. Wired
    # here rather than in build_system_tools so every surface gets them.
    # Chat only, like ask_user: a scoped task subagent answers to the task, not to
    # questions about the product.
    if capabilities is None:
        from assistant.self_tools import build_self_tools
        from assistant.settings import profile_settings

        settings = profile_settings(config.data_dir, voice_provider=config.voice_provider)
        tools.extend(build_self_tools(config, settings))

    # system tools (retrieval + actions over tasks/chats/questions) — these make
    # the agent "universal": it can know and do everything via tools (create/
    # schedule included; the chat agent no longer needs a separate start_task tool).
    if extra_tools:
        tools.extend(extra_tools)

    # When the profile memory is on, let the agent commit an explicit "remember
    # this" immediately (the passive aggregator alone is slow and may filter it).
    # The tool closes over BOTH stores: THIS profile's ``profile.db`` (persona
    # memory) and the shared ``root_dir/user.db`` (universal "who the user is"
    # memory). Its `scope` argument picks the layer — a persona preference never
    # leaks into the shared layer, and an identity fact is written once for all.
    if memory:
        tools.append(build_memory_tool(config.data_dir / "profile.db", config.root_dir / "user.db"))

    # One injected authority for all permission decisions (knows the sandbox mode
    # so prompts can say where a command actually runs — host vs container).
    # Commands: the install-wide permissions.json. Folder access: the install-wide
    # Folder registry + this profile's Grants (ADR 0006); the profile's own
    # workspace is implicitly read+write.
    dependencies: dict = {
        PermissionManager: PermissionManager(
            PermissionStore(config.root_dir / "permissions.json"),
            asker=asker,
            sandbox=config.tools.sandbox,
            folders=FolderStore(config.root_dir / "folders.json"),
            profile=config.data_dir.name,
            workspace_dir=config.workspace_dir,
        )
    }
    if asker is not None:
        # The ask_user tool pulls the turn's asker from dependencies so the model
        # can pose option-carrying Questions (context.input is string-only).
        dependencies[Asker] = asker

    hitl_hook = None
    if asker is not None:
        hitl_hook = build_hitl_hook(asker)

    # AlertPolicy delivers observer alerts to the model and, on a FATAL alert,
    # emits a HaltEvent that AG2's halt middleware turns into a short-circuited
    # turn. Appending it makes `assembly` non-empty for EVERY agent (task
    # subagents included), which is what wires that halt path in — so the
    # SilenceWatchdog's FATAL escalation deterministically terminates a wedged
    # turn even on the memory-less subagents that hang in the incident.
    assembly = list(assembly) + [AlertPolicy()]

    agent = Agent(
        config.agent.name,
        prompt=config.agent.system_prompt,
        config=llm_config,
        tools=tools,
        plugins=plugins,
        knowledge=knowledge,
        assembly=assembly,
        hitl_hook=hitl_hook,
        dependencies=dependencies,
        middleware=_build_middleware(config),
        observers=build_observers(  # stuck-turn + wedged-turn guards → stream alerts
            silence_alert_s=config.llm.silence_alert_s,
            silence_halt_s=config.llm.silence_halt_s,
        ),
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
    agent = create_agent(config, memory=memory, platform=platform, asker=asker, single_shot=True)
    reply = await agent.ask(message, prompt=turn_prompt(config))
    return reply.body
