"""AGClaw persistent user-profile memory.

A passive "observer" layer that learns the user over time and persists what it
learns to a SQLite-backed knowledge store. After each conversation, an
aggregation pass distills the user's preferences into a rolling profile that is
injected back into every future conversation.

The profile tracks four dimensions:
  1. How they like things done (style, format, level of detail)
  2. When they like things done (cadence, regularity, times of day)
  3. What they dislike (things to avoid)
  4. How they write (tone, phrasing, e.g. for emails)

Each observation is tagged with the platform it was seen on (cli, telegram,
discord, ...) so the single global profile still carries channel context.
"""

from pathlib import Path

from autogen.beta import KnowledgeConfig
from autogen.beta.aggregate import AggregateTrigger, WorkingMemoryAggregate
from autogen.beta.config.gemini import GeminiConfig
from autogen.beta.knowledge import SqliteKnowledgeStore
from autogen.beta.policies import ConversationPolicy, WorkingMemoryPolicy

# Path inside the knowledge store where the rolling profile lives.
PROFILE_PATH = "/memory/working.md"


def build_profile_prompt(platform: str) -> str:
    """Build the aggregation prompt for distilling the user profile.

    The platform is baked into the prompt so observations from this session are
    tagged with where they happened. `{existing}` and `{events}` are placeholders
    that AG2 interpolates (existing profile + this conversation's events).
    """
    return f"""You maintain a long-term profile of the user to help a personal
assistant serve them better over time. This conversation took place on the
platform: **{platform}**.

Update the profile below using the new conversation. Keep it concise, factual,
and organised under exactly these four headings:

## How they like things done
(style, format, level of detail, tools/approaches they prefer)

## When they like things done
(cadence, regularity, times of day, scheduling habits)

## What they dislike
(things to avoid, past corrections, pet peeves)

## How they write
(tone, phrasing, structure — especially for emails and messages)

Rules:
- Only record durable preferences, not one-off facts or task details.
- NEVER record permission or security decisions (allowing/denying access to
  folders, files, or running commands). Those are transient operational choices,
  not preferences — recording them would wrongly make the assistant stop trying.
- When a preference is clearly tied to a platform, note it, e.g.
  "(on {platform})". Preferences that seem general need no tag.
- Merge new observations with existing ones; remove anything contradicted.
- If the conversation reveals nothing about the user's preferences, return the
  existing profile unchanged.

OUTPUT FORMAT — this is strict:
- Output ONLY the profile itself: the four `##` headings and their bullet points.
- Do NOT include any preamble, narration, or commentary about your analysis
  (no "An analysis of the conversation shows…", no "The profile remains
  unchanged."). Return the document only — nothing before the first heading.

Existing profile:
{{existing}}

New conversation:
{{events}}

Return the updated profile document only (headings and bullets, no commentary)."""


def default_store_path() -> Path:
    """Default on-disk location for the profile store."""
    return Path.home() / ".agclaw" / "profile.db"


def build_profile_store(store_path: Path | None = None) -> SqliteKnowledgeStore:
    """Open the SQLite profile store, creating its parent directory."""
    if store_path is None:
        store_path = default_store_path()
    store_path.parent.mkdir(parents=True, exist_ok=True)
    return SqliteKnowledgeStore(str(store_path))


def build_knowledge_config(
    platform: str = "cli",
    store_path: Path | None = None,
    aggregate_config: GeminiConfig | None = None,
    store=None,
    every_n_turns: int = 4,
    on_end: bool = False,
) -> KnowledgeConfig:
    """Build a KnowledgeConfig that passively learns and persists the user profile.

    Args:
        platform: The channel this session is on (cli, telegram, discord, ...).
        store_path: Where to persist the SQLite profile DB (ignored if `store` given).
        aggregate_config: LLM config used for the (cheaper) aggregation call.
        store: An existing KnowledgeStore to reuse (e.g. a shared, locked store
            when several agents must write the same profile concurrently).
        every_n_turns: Distil the profile every N turns. Batches the (LLM-backed)
            aggregation so long sessions don't pay it on every message. 0 disables.
        on_end: Also distil when a conversation ends. Use for single-shot runs
            (CLI) so their one turn is still captured; leave off for long sessions
            to avoid an aggregation call per turn.

    Returns:
        A KnowledgeConfig wiring a SQLite store + working-memory aggregation.
    """
    if store is None:
        store = build_profile_store(store_path)

    if aggregate_config is None:
        import os

        aggregate_config = GeminiConfig(
            model="gemini-3.5-flash",
            api_key=os.environ.get("GEMINI_API_KEY", ""),
        )

    return KnowledgeConfig(
        store=store,
        # Passive only: don't hand the model a knowledge tool or dump event logs.
        expose_tool=False,
        write_event_log=False,
        aggregate=WorkingMemoryAggregate(
            config=aggregate_config,
            prompt=build_profile_prompt(platform),
        ),
        aggregate_trigger=AggregateTrigger(
            every_n_turns=every_n_turns, on_end=on_end
        ),
    )


def profile_assembly() -> list:
    """Assembly policies that inject the learned profile into each conversation."""
    return [
        WorkingMemoryPolicy(),  # injects the profile (/memory/working.md)
        ConversationPolicy(),
    ]


async def read_profile(store_path: Path | None = None) -> str:
    """Read the learned user profile, or empty string if none exists yet."""
    if store_path is None:
        store_path = default_store_path()
    if not store_path.exists():
        return ""
    store = SqliteKnowledgeStore(str(store_path))
    if not await store.exists(PROFILE_PATH):
        return ""
    return await store.read(PROFILE_PATH)


async def clear_profile(store_path: Path | None = None) -> bool:
    """Delete the learned user profile. Returns True if something was removed."""
    if store_path is None:
        store_path = default_store_path()
    if not store_path.exists():
        return False
    store = SqliteKnowledgeStore(str(store_path))
    if not await store.exists(PROFILE_PATH):
        return False
    await store.delete(PROFILE_PATH)
    return True
