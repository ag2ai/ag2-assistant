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

There are TWO memory layers:
  - Per-profile (``profiles/<id>/profile.db``): persona preferences, learned
    passively by the aggregator and via the `remember` tool (scope="profile").
  - Universal (``root_dir/user.db``): "who the user is" — identity facts true
    across every persona. Shared by all profiles, NEVER passively aggregated;
    curated only by the `remember` tool (scope="universal"), onboarding, and
    manual edits. See ``read_universal`` / ``write_universal`` at the bottom.
"""

import hashlib
import sqlite3
from collections.abc import Sequence
from pathlib import Path

from ag2 import KnowledgeConfig
from ag2.aggregate import AggregateTrigger, WorkingMemoryAggregate
from ag2.annotations import Context
from ag2.compact import CompactTrigger
from ag2.config import ModelConfig
from ag2.events import (
    BaseEvent,
    ModelRequest,
    ModelResponse,
    ToolResultEvent,
    ToolResultsEvent,
    is_conversational,
    render_for_prompt,
)
from ag2.knowledge import KnowledgeStore, MemoryKnowledgeStore, SqliteKnowledgeStore
from ag2.policies import ConversationPolicy, WorkingMemoryPolicy
from ag2.stream import MemoryStream
from fast_depends.pydantic import PydanticSerializer

from assistant.compaction import CheckpointCompact, CompactionLimits, event_ref
from assistant.config import load_config

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
    existing = (await store.read(PROFILE_PATH) or "") if await store.exists(PROFILE_PATH) else ""
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


def bookmark_path(chat_id: str) -> str:
    """Where a chat's bookmark lives: the ref of the last event a profile update read."""
    return f"/bookmarks/{hashlib.sha256(chat_id.encode()).hexdigest()}"


def _finished_turns(events: Sequence[BaseEvent]) -> list[BaseEvent]:
    """The conversation up to its last final response, leaving out a turn still running."""
    conversation = [event for event in events if is_conversational(event)]
    for index in range(len(conversation) - 1, -1, -1):
        event = conversation[index]
        if isinstance(event, ModelResponse) and (not event.tool_calls or event.response_force):
            return conversation[: index + 1]
    return []


def unread_events(
    events: Sequence[BaseEvent], bookmark: str | None, every_n: int
) -> list[BaseEvent]:
    """The finished events after `bookmark`. With no bookmark in reach, the last `every_n`
    turns: earlier updates covered what came before them."""
    finished = _finished_turns(events)
    refs = [event_ref(event) for event in finished]
    if bookmark in refs:
        return finished[refs.index(bookmark) + 1 :]
    requests = [i for i, event in enumerate(finished) if isinstance(event, ModelRequest)]
    return finished[requests[-min(every_n, len(requests))] :] if requests else []


def _capped(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return f"{text[:limit]}\n[{len(text) - limit} more characters cut]"


def _render(event: BaseEvent, tool_result_chars: int) -> str:
    """An event as profile-update text, each tool result within `tool_result_chars`."""
    if isinstance(event, ToolResultEvent):
        event = ToolResultsEvent([event])
    if isinstance(event, ToolResultsEvent):
        return "\n".join(
            _capped(render_for_prompt(ToolResultsEvent([result])), tool_result_chars)
            for result in event.results
        )
    return render_for_prompt(event)


# How many merges one update tries before leaving a profile that keeps changing alone.
_MERGE_ATTEMPTS = 2


class ProfileUpdate:
    """One merge of chat events into the profile, redone when the profile changes
    during the merge so an explicit edit is never overwritten."""

    def __init__(self, config: ModelConfig, prompt: str, tool_result_chars: int) -> None:
        self._config, self._prompt, self._tool_result_chars = config, prompt, tool_result_chars
        self._serializer = PydanticSerializer(
            pydantic_config={"arbitrary_types_allowed": True},
            use_fastdepends_errors=False,
        )
        self.responses: list[ModelResponse] = []

    async def run(self, events: Sequence[BaseEvent], store: KnowledgeStore) -> bool:
        """Merge `events` into the stored profile; True when the profile was saved."""
        for _ in range(_MERGE_ATTEMPTS):
            existing = await store.read(PROFILE_PATH) or ""
            updated = await self._merge(existing, events)
            if (await store.read(PROFILE_PATH) or "") == existing:
                if not updated:
                    return False
                await store.write(PROFILE_PATH, updated)
                return True
        return False

    async def _merge(self, existing: str, events: Sequence[BaseEvent]) -> str:
        prompt = self._prompt.format(
            existing=existing or "(empty)",
            events="\n".join(_render(event, self._tool_result_chars) for event in events),
        )
        response = await self._config.create()(
            [ModelRequest.ensure_request([prompt])],
            Context(MemoryStream()),
            tools=[],
            response_schema=None,
            serializer=self._serializer,
        )
        self.responses.append(response)
        return response.content or ""


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
    from assistant.agent import (  # local: import cycle (assistant.agent imports memory)
        cheap_model,
        model_config,
    )

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
    compact_max_tokens: int = 50_000,
    compaction_limits: CompactionLimits | None = None,
    compaction_store: KnowledgeStore | None = None,
) -> KnowledgeConfig:
    """Learn profile memory with the aggregation model and compact with the Turn's model."""
    if store is None:
        if store_path is None:
            raise ValueError("build_knowledge_config needs a store_path when no store is given")
        store = build_profile_store(store_path)

    if aggregate_config is None:
        aggregate_config = _default_aggregate_config()

    compact_kwargs: dict = {}
    if compact:
        compact_kwargs = {
            "compact": CheckpointCompact(
                compaction_limits or CompactionLimits(max_tokens=compact_max_tokens),
                compaction_store,
            ),
            "compact_trigger": CompactTrigger(),
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
    max_tokens: int = 50_000,
    *,
    store: KnowledgeStore | None = None,
    limits: CompactionLimits | None = None,
) -> KnowledgeConfig:
    """Bound history with the Turn's model without persisting profile memory."""

    return KnowledgeConfig(
        store=store if store is not None else MemoryKnowledgeStore(),
        expose_tool=False,
        write_event_log=False,
        compact=CheckpointCompact(limits or CompactionLimits(max_tokens=max_tokens), store),
        compact_trigger=CompactTrigger(),
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
    return await store.read(PROFILE_PATH) or ""


def read_profile_sync(store_path: Path) -> str:
    """Synchronously read the profile document from a SqliteKnowledgeStore file, or
    "" if the file/row is absent. A read-only, connection-free stdlib sqlite3 query
    (same ``entries(path, content, …)`` schema the store uses) so the per-turn,
    synchronous prompt builders can inject the universal document without spinning up
    the async store. Never raises on a missing/locked/corrupt DB — returns ""."""
    store_path = Path(store_path)
    if not store_path.exists():
        return ""
    try:
        conn = sqlite3.connect(f"file:{store_path}?mode=ro", uri=True)
        try:
            cur = conn.execute("SELECT content FROM entries WHERE path = ?", (PROFILE_PATH,))
            row = cur.fetchone()
        finally:
            conn.close()
    except Exception:
        return ""
    if not row:
        return ""
    content = row[0]
    return content.decode("utf-8") if isinstance(content, bytes | bytearray) else str(content)


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


# --- Universal ("who the user is") memory layer ----------------------------- #
#
# A second, install-wide document shared by EVERY profile — identity facts that
# are true in any context (name, location, timezone, family, health constraints,
# writing voice). It reuses the same SqliteKnowledgeStore mechanics as the
# per-profile profile.db (build_profile_store / PROFILE_PATH), just at a
# different path: config.root_dir / "user.db". Unlike the per-profile store it is
# NEVER touched by the passive aggregator; it is curated, written only by the
# `remember` tool (scope="universal"), the onboarding interview, and manual edits
# via the API/UI. read_profile / write_profile already take an explicit path, so
# they serve this layer too — these thin wrappers just name the intent.


async def read_universal(user_store_path: Path) -> str:
    """Read the shared "who the user is" document (config.root_dir/user.db), or
    empty string if none exists yet."""
    return await read_profile(user_store_path)


async def write_universal(text: str, user_store_path: Path) -> None:
    """Overwrite the shared "who the user is" document (a user edit via the GUI,
    or the onboarding seed)."""
    await write_profile(text, user_store_path)
