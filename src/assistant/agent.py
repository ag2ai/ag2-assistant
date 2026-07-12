"""AG2 Assistant agent built on AG2."""

import os
from datetime import datetime

from ag2 import Agent

from assistant.config import Config, load_config
from assistant.memory import build_compaction_config, build_knowledge_config, profile_assembly
from assistant.tools import build_agent_tools

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


def model_config(config: Config, model: str | None = None):
    """Build the AG2 ModelConfig for the configured provider.

    `model` overrides `config.llm.model` (used for the cheaper aggregation pass).
    The API key is read from os.environ by the provider's conventional var (filled
    from the secrets store at startup / on reload), not the fixed api_key_env field.
    """
    from assistant.secrets import DEFAULT_OLLAMA_BASE, KEY_ENV, OLLAMA_BASE_ENV

    model = model or config.llm.model
    provider = config.llm.provider.lower()
    api_key = os.environ.get(KEY_ENV.get(provider, config.llm.api_key_env), "")
    if provider == "anthropic":
        from ag2.config import AnthropicConfig

        return AnthropicConfig(model=model, api_key=api_key, streaming=config.llm.streaming)
    if provider == "openai":
        # OpenAI's Responses API (their preferred surface; also enables the native
        # image_generation tool). Drop-in for the old Chat Completions OpenAIConfig.
        from ag2.config import OpenAIResponsesConfig

        return OpenAIResponsesConfig(model=model, api_key=api_key, streaming=config.llm.streaming)
    if provider == "ollama":
        from ag2.config import OllamaConfig

        return OllamaConfig(
            model=model,
            host=os.environ.get(OLLAMA_BASE_ENV, DEFAULT_OLLAMA_BASE),
            streaming=config.llm.streaming,
        )
    from ag2.config.gemini import GeminiConfig

    # Generous output budget so long research notes / briefings aren't truncated
    # mid-sentence. Gemini counts thinking tokens against max_output_tokens, so
    # this must cover reasoning plus the full report text.
    return GeminiConfig(
        model=model,
        api_key=api_key,
        max_output_tokens=32768,
        streaming=config.llm.streaming,
    )


def cheap_model(config: Config) -> str | None:
    """A faster/cheaper model for bulk work (research subtasks, verification)."""
    return config.llm.aggregate_model or _DEFAULT_AGGREGATE_MODEL.get(config.llm.provider.lower())


def bundled_skills_dir():
    """Directory of first-party skills shipped with AG2 Assistant (read-only)."""
    from pathlib import Path

    return Path(__file__).parent / "skills"


def build_skills_toolkit(config: Config):
    """A toolkit that lets the agent search, install, and run skills.

    `SkillSearchToolkit` extends the local skills toolkit (list/load/read/run)
    with registry search + install from skills.sh. Skills install into
    `config.skills_dir`; AG2 Assistant's bundled first-party skills are always available
    too (read-only, via `extra_paths`), so it's capable on first run.

    When the Docker sandbox is selected (`config.tools.sandbox == "docker"`),
    skill *scripts* run inside a one-shot, bind-mounted container — so untrusted
    skill code can't reach the user's files. Storage/discovery stay local.
    """
    from ag2.tools import SkillSearchToolkit

    config.skills_dir.mkdir(parents=True, exist_ok=True)
    extra = [str(bundled_skills_dir())]

    if config.tools.sandbox == "docker":
        from assistant.tools.docker_sandbox import (
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

    from ag2.tools.skills import LocalRuntime

    runtime = LocalRuntime(dir=str(config.skills_dir), blocked=_SKILL_BLOCKED, extra_paths=extra)
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
    "Before improvising a task with general web searches, check the <available_skills> "
    "catalog: when a request matches a skill's description, load and follow that skill — "
    "it's the curated, efficient path (one structured call beats many guesswork "
    "searches). Use open-web search only when no skill fits.\n"
    "The shell and code-execution tools are ONLY for when the user explicitly "
    "asks you to run a command, execute code, or work with local files. NEVER use "
    "them to 'look around', orient yourself, explore the filesystem (e.g. `ls`), "
    "inspect your environment, or as a fallback when unsure — they have nothing to "
    "do with web pages, videos, messages, or cloud data.\n"
    "If a sandboxed runner is offered (e.g. run_code_sandboxed / run_shell_sandboxed), "
    "prefer it; only reach for a host runner (run_code_local / run_shell_local) when "
    "the task truly needs the user's own files — it will ask their permission.\n"
    "When the user asks you to WRITE or EDIT code in one of their repositories/folders, "
    "use the code_with_cli_agent tool (it drives a local coding CLI like Claude Code) — "
    "not the shell/code runners. It needs the folder path and asks the user to approve "
    "it the first time; if no coding agent is installed it will say so.\n"
    "To create or edit images, use the generate_image tool — never shell/osascript or "
    "code. To change an image you already made, call it again with source_image set to "
    "that image's path (returned by the previous call)."
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
    "memory themselves in Settings → Memory."
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
    from typing import Annotated, Literal

    from ag2 import tool
    from pydantic import Field

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
        from assistant.memory import remember_note

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


def universal_memory_guidance(config: Config) -> str:
    """The shared "who the user is" document as a system-prompt part, or "" when it's
    empty. Read FRESH each turn from ``config.root_dir / "user.db"`` (mirroring
    ``focuses_guidance``'s read-settings-per-turn pattern) so an edit or a
    remember(scope="universal") shows up on the next turn without a reload — and so
    EVERY profile's agent injects the same identity facts. Empty doc → no section."""
    from assistant.memory import read_profile_sync

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
    persisted to that profile's ``settings.json``. Read here (mirroring core.py's
    ``Settings(config.data_dir / "settings.json")`` pattern) so a reference-swap
    reload picks up changes on the next turn. Empty focuses → no line at all."""
    from assistant.settings import Settings

    try:
        focuses = Settings(config.data_dir / "settings.json").get_focuses()
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


def turn_prompt(config: Config, memory: bool = True, workspace: bool = True) -> list[str]:
    """Per-turn system prompt: persona + live environment context.

    `ask(prompt=...)` replaces the base prompt for that turn, so we include the
    persona, the always-on behaviour guidance, optional memory guidance, and the
    refreshed environment.
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
        from assistant.integrations.google_auth import has_token

        if has_token():
            parts.append(GOOGLE_GUIDANCE)
    except Exception as exc:
        from assistant.observability import log_suppressed

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
        from assistant.integrations.google_auth import has_token

        if has_token():
            parts.append(GOOGLE_GUIDANCE)
    except Exception as exc:
        from assistant.observability import log_suppressed

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
    # Profile aggregation and stream compaction are both just summarisation, so
    # run them on a cheaper model when one is configured (or a sensible
    # per-provider default). Falls back to the main model if neither applies.
    agg_model = config.llm.aggregate_model or _DEFAULT_AGGREGATE_MODEL.get(
        config.llm.provider.lower()
    )
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
    if skills and (capabilities is None or "skills" in capabilities):
        tools.append(build_skills_toolkit(config))

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

    from assistant.permissions import PermissionManager, PermissionStore

    # One injected authority for all permission decisions (knows the sandbox mode
    # so prompts can say where a command actually runs — host vs container). Backed
    # by the install-wide persistent grant store (root_dir) so a grant is global —
    # allowing a folder/command in one profile pre-authorises it everywhere.
    dependencies: dict = {
        PermissionManager: PermissionManager(
            PermissionStore(config.root_dir / "permissions.json"),
            asker=asker,
            sandbox=config.tools.sandbox,
        )
    }
    if asker is not None:
        # The ask_user tool pulls the turn's asker from dependencies so the model
        # can pose option-carrying Questions (context.input is string-only).
        from assistant.hitl import Asker

        dependencies[Asker] = asker

    hitl_hook = None
    if asker is not None:
        from assistant.hitl import build_hitl_hook

        hitl_hook = build_hitl_hook(asker)

    from ag2.policies import AlertPolicy

    from assistant.middleware import LLMRetryMiddleware, LLMTimeoutMiddleware
    from assistant.observability import agent_logging_middleware
    from assistant.observers import build_observers

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
        knowledge=knowledge,
        assembly=assembly,
        hitl_hook=hitl_hook,
        dependencies=dependencies,
        middleware=[
            agent_logging_middleware(),  # per-turn LLM/tool logs → ag2assistant.log
            # Retry a failed call before it kills the attempt/task. Listed BEFORE
            # the timeout so it wraps it (AG2 nests later middleware closer to the
            # call): each retry re-enters the timeout, getting a fresh window. A
            # transient hang or 429/5xx becomes a hiccup, not a failure.
            LLMRetryMiddleware(config.llm.call_retries),
            # Wall-clock ceiling per LLM call: a hung/stalled provider call raises
            # instead of awaiting forever (the incident's silent 2-hour hang).
            LLMTimeoutMiddleware(config.llm.call_timeout_s),
        ],
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
