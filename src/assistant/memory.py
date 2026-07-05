"""AG2 Assistant persistent user-profile memory.

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

from ag2 import KnowledgeConfig
from ag2.aggregate import AggregateTrigger, WorkingMemoryAggregate
from ag2.config import ModelConfig
from ag2.knowledge import SqliteKnowledgeStore
from ag2.policies import ConversationPolicy, WorkingMemoryPolicy

# Path inside the knowledge store where the rolling profile lives.
PROFILE_PATH = "/memory/working.md"

# The canonical headings the profile is organised under. The passive aggregator
# (build_profile_prompt) writes exactly these four; the `remember` tool inserts
# explicit user requests under the matching one so the document stays consistent.
PROFILE_HEADINGS: dict[str, str] = {
    "how": "## How they like things done",
    "when": "## When they like things done",
    "dislikes": "## What they dislike",
    "writing": "## How they write",
}


def _blank_profile() -> str:
    """An empty profile scaffold with the four canonical headings."""
    return "\n\n".join(PROFILE_HEADINGS.values()) + "\n"


def _insert_bullet(doc: str, heading: str, bullet: str) -> str:
    """Insert `bullet` at the end of `heading`'s section, creating the heading if
    absent. De-dupes an identical bullet. Returns the updated document."""
    lines = doc.splitlines()
    if any(line.strip() == bullet for line in lines):
        return doc  # already remembered verbatim — nothing to do

    out: list[str] = []
    inserted = False
    i, n = 0, len(lines)
    while i < n:
        line = lines[i]
        out.append(line)
        if line.strip() == heading:
            # Walk to the end of this section (next '## ' heading or EOF), keeping
            # its existing bullets, then drop the new bullet at the bottom.
            j = i + 1
            section: list[str] = []
            while j < n and not lines[j].strip().startswith("## "):
                section.append(lines[j])
                j += 1
            while section and not section[-1].strip():
                section.pop()  # trim trailing blanks inside the section
            out.extend(section)
            out.append(bullet)
            inserted = True
            i = j
            continue
        i += 1

    if not inserted:  # heading wasn't present — append it at the end
        if out and out[-1].strip():
            out.append("")
        out.extend([heading, bullet])
    return "\n".join(out) + "\n"


def _strip_marker(text: str) -> str:
    """A bullet's text without its leading list marker — so `-`/`*` bullets compare equal."""
    return text.strip().lstrip("*-•").strip()


def _remove_bullets(doc: str, removals) -> str:
    """Drop bullet lines whose text matches any of `removals` (marker-insensitive, exact
    text). Headings/blank lines are always kept."""
    targets = {_strip_marker(r) for r in removals if r and r.strip()}
    if not targets:
        return doc
    kept = [
        ln
        for ln in doc.splitlines()
        if not (ln.lstrip().startswith(("*", "-")) and _strip_marker(ln) in targets)
    ]
    return "\n".join(kept) + ("\n" if doc.endswith("\n") else "")


async def record_preference(
    store_path: Path,
    note: str = "",
    category: str = "how",
    remove=(),
) -> str:
    """Apply a learned preference to the profile: first delete any directly-conflicting
    bullets (`remove`, matched verbatim & marker-insensitive), then append `note` under
    `category`. Both parts are optional — the feedback learner uses this to *revise*
    (drop a contradicted bullet + add the corrected one) or just dedupe (note=""), not
    only append. The aggregator still reorganises on its cadence. Returns the updated doc.
    """
    note = (note or "").strip()
    removals = [r for r in (remove or []) if r and r.strip()]
    store = build_profile_store(store_path)
    existing = await store.read(PROFILE_PATH) if await store.exists(PROFILE_PATH) else ""
    doc = existing if existing.strip() else _blank_profile()
    if not note and not removals:
        return doc  # nothing to do (e.g. the feedback is already captured)
    if removals:
        doc = _remove_bullets(doc, removals)
    if note:
        heading = PROFILE_HEADINGS.get(category, PROFILE_HEADINGS["how"])
        # "* " matches the aggregator's marker; _insert_bullet de-dupes verbatim.
        doc = _insert_bullet(doc, heading, "* " + note)
    await store.write(PROFILE_PATH, doc)
    return doc


async def remember_note(store_path: Path, note: str, category: str = "how") -> str:
    """Immediately save an explicit user preference/fact to the learned profile
    (append-only). Used by the agent's `remember` tool and as the feedback learner's
    fallback. The aggregator later reorganises/dedupes on its cadence. Returns the
    updated profile document.
    """
    return await record_preference(store_path, note, category)


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


def build_profile_store(store_path: Path) -> SqliteKnowledgeStore:
    """Open the SQLite profile store, creating its parent directory."""
    store_path = Path(store_path)
    store_path.parent.mkdir(parents=True, exist_ok=True)
    return SqliteKnowledgeStore(str(store_path))


def _default_aggregate_config() -> ModelConfig:
    """Fallback config for aggregation/compaction LLM calls when the caller
    didn't pass one: the user's configured provider, on its cheap aggregate
    model when one is known (falling back to their main model). Imported
    lazily — agent.py imports this module at load time."""
    from assistant.agent import cheap_model, model_config
    from assistant.config import load_config

    config = load_config()
    return model_config(config, cheap_model(config))


def build_knowledge_config(
    platform: str = "cli",
    store_path: Path | None = None,
    aggregate_config: ModelConfig | None = None,
    store=None,
    every_n_turns: int = 4,
    on_end: bool = False,
    compact: bool = False,
    compact_max_tokens: int = 20_000,
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
        compact: Bound a long conversation's context by summarising the oldest
            events (an LLM call on the cheap model) when the stream grows large.
        compact_max_tokens: Stream size (approx. tokens) that triggers compaction.

    Returns:
        A KnowledgeConfig wiring a SQLite store + working-memory aggregation.
    """
    if store is None:
        store = build_profile_store(store_path)

    if aggregate_config is None:
        aggregate_config = _default_aggregate_config()

    compact_kwargs: dict = {}
    if compact:
        from ag2.compact import CompactTrigger, SummarizeCompact

        compact_kwargs = {
            "compact": SummarizeCompact(target=60, config=aggregate_config),
            "compact_trigger": CompactTrigger(max_tokens=compact_max_tokens),
        }

    return KnowledgeConfig(
        store=store,
        # Passive only: don't hand the model a knowledge tool or dump event logs.
        expose_tool=False,
        write_event_log=False,
        aggregate=WorkingMemoryAggregate(
            config=aggregate_config,
            prompt=build_profile_prompt(platform),
        ),
        aggregate_trigger=AggregateTrigger(every_n_turns=every_n_turns, on_end=on_end),
        **compact_kwargs,
    )


def build_compaction_config(
    aggregate_config: ModelConfig | None = None,
    max_tokens: int = 20_000,
) -> KnowledgeConfig:
    """Compaction-only harness wiring for agents that run without profile memory
    (task subagents). Bounds a long run's context by summarising the oldest
    events (an LLM call on the cheap model) once the stream crosses `max_tokens`.
    Backed by an ephemeral in-memory store — nothing persists, no knowledge tool,
    no event log; the KnowledgeConfig exists purely to carry the compactor."""
    from ag2.compact import CompactTrigger, SummarizeCompact
    from ag2.knowledge import MemoryKnowledgeStore

    if aggregate_config is None:
        aggregate_config = _default_aggregate_config()

    return KnowledgeConfig(
        store=MemoryKnowledgeStore(),
        expose_tool=False,
        write_event_log=False,
        compact=SummarizeCompact(target=60, config=aggregate_config),
        compact_trigger=CompactTrigger(max_tokens=max_tokens),
    )


def profile_assembly() -> list:
    """Assembly policies that inject the learned profile into each conversation."""
    return [
        WorkingMemoryPolicy(),  # injects the profile (/memory/working.md)
        ConversationPolicy(),
    ]


async def write_profile(text: str, store_path: Path) -> None:
    """Overwrite the learned user profile (a user edit via the GUI). The passive
    aggregator treats this as the new base it merges future conversation into."""
    store = build_profile_store(store_path)
    await store.write(PROFILE_PATH, text or "")


async def read_profile(store_path: Path) -> str:
    """Read the learned user profile, or empty string if none exists yet."""
    store_path = Path(store_path)
    if not store_path.exists():
        return ""
    store = SqliteKnowledgeStore(str(store_path))
    if not await store.exists(PROFILE_PATH):
        return ""
    return await store.read(PROFILE_PATH)


async def clear_profile(store_path: Path) -> bool:
    """Delete the learned user profile. Returns True if something was removed."""
    store_path = Path(store_path)
    if not store_path.exists():
        return False
    store = SqliteKnowledgeStore(str(store_path))
    if not await store.exists(PROFILE_PATH):
        return False
    await store.delete(PROFILE_PATH)
    return True
