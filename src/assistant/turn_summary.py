"""A title, an outcome and an optional parent Turn for one finished chat Turn, from
one cheap-model call; a parent is kept only when offered, and flattened to its root."""

from collections.abc import Callable, Sequence

from ag2 import Agent
from pydantic import BaseModel, Field

from assistant.agent import cheap_model, model_config
from assistant.config import Config
from assistant.events import TurnSummarized
from assistant.structured import aclose_config, ask_structured_reply

TITLE_MAX = 60
OUTCOME_AIM = 120
OUTCOME_MAX = 150
RECENT = 5
_TEXT_MAX = 2000


class TurnSummary(BaseModel):
    """What the model returns for one Turn."""

    title: str = Field(
        description=f"What the user asked for, as a short imperative (at most {TITLE_MAX} "
        "characters, no quotes, no trailing punctuation)."
    )
    outcome: str = Field(
        description=f"What was done, in the past tense (at most {OUTCOME_AIM} characters)."
    )
    parent_turn_id: str | None = Field(
        default=None,
        description="The id of the earlier turn whose work this turn continues, or null.",
    )


_PROMPT = (
    "Summarise this chat turn for an activity list.\n"
    "- title: what the user asked for, as a short imperative.\n"
    "- outcome: what was done, in the past tense.\n"
    "- parent_turn_id: if this turn continues the work of one of the earlier turns "
    "below, that turn's id; otherwise null.\n\n"
    "Earlier turns, oldest first:\n{recent}\n\n"
    "User: {user}\n\nAssistant: {agent}"
)


def default_turn_summarizer(config: Config) -> Agent:
    """The production summarizer: a one-shot agent on the cheap model."""
    return Agent("turn-summarizer", config=model_config(config, cheap_model(config)))


def clip(text: str | None, limit: int) -> str:
    """One line of at most ``limit`` characters, ending in an ellipsis when cut."""
    line = " ".join(str(text or "").split()).strip().strip("\"'").strip()
    if len(line) <= limit:
        return line
    return line[: limit - 1].rstrip() + "…"


def parent_of(chosen: str | None, recent: Sequence[TurnSummarized]) -> str | None:
    """The root of the offered Turn ``chosen``, or None when it was not offered."""
    for summary in recent:
        if chosen and summary.turn_id == chosen:
            return summary.parent_turn_id or summary.turn_id
    return None


def build_prompt(user_text: str, reply_text: str, recent: Sequence[TurnSummarized]) -> str:
    """The summarizer prompt for one Turn and the Chat's recent summaries."""
    lines = [f"- {s.turn_id}: {s.title} → {s.outcome}" for s in recent] or ["(none)"]
    return _PROMPT.format(
        recent="\n".join(lines),
        user=(user_text or "")[:_TEXT_MAX],
        agent=(reply_text or "")[:_TEXT_MAX],
    )


async def summarize_turn(
    config: Config,
    chat_id: str,
    turn_id: str,
    user_text: str,
    reply_text: str,
    recent: Sequence[TurnSummarized],
    *,
    agent_factory: Callable[[Config], Agent] = default_turn_summarizer,
) -> tuple[TurnSummarized | None, object | None]:
    """The Turn's summary event (None when the title is empty) and the model response."""
    agent = agent_factory(config)
    try:
        out, reply = await ask_structured_reply(
            agent, build_prompt(user_text, reply_text, recent), TurnSummary
        )
    finally:
        await aclose_config(getattr(agent, "config", None))
    response = getattr(reply, "response", None)
    title = clip(getattr(out, "title", None), TITLE_MAX).rstrip(".!,;:")
    if not title:
        return None, response
    event = TurnSummarized(
        chat_id,
        turn_id=turn_id,
        title=title,
        outcome=clip(getattr(out, "outcome", None), OUTCOME_MAX),
        parent_turn_id=parent_of(getattr(out, "parent_turn_id", None), recent),
    )
    return event, response
