"""One-shot, AG2-native chat title generation.

After a chat's first complete exchange (first user message + first agent reply) we
ask a cheap model for a short, human-readable title via ``response_schema`` — the
same structured-output primitive the planner/executor use. It runs exactly **once**
per chat (a single revision), fire-and-forget so it never delays the response.
"""

from ag2 import Agent
from pydantic import BaseModel, Field

from assistant.agent import cheap_model, model_config
from assistant.config import Config
from assistant.structured import aclose_config, ask_structured


class ChatTitle(BaseModel):
    """Structured title so the model can't ramble — we take just the field."""

    title: str = Field(
        description="A concise 2–6 word title summarising the conversation. "
        "Title Case, no surrounding quotes, no trailing punctuation."
    )


_PROMPT = (
    "Write a short, specific title (2–6 words, Title Case, no quotes, no trailing "
    "punctuation) for this conversation, based on what the user wants.\n\n"
    "User: {user}\n\nAssistant: {agent}"
)


def _clean_title(raw: str | None) -> str | None:
    """Normalise the model's title: strip quotes/whitespace/trailing punctuation,
    collapse newlines, cap length. Returns None if nothing usable remains."""
    if not raw:
        return None
    title = " ".join(str(raw).split()).strip().strip("\"'").strip()
    title = title.rstrip(".!,;:").strip()
    return title[:80] or None


async def generate_title(config: Config, user_text: str, agent_text: str) -> str | None:
    """Ask the cheap model for a chat title (None on any failure)."""
    cfg = model_config(config, cheap_model(config))
    agent = Agent("titler", config=cfg)
    prompt = _PROMPT.format(user=(user_text or "")[:2000], agent=(agent_text or "")[:2000])
    try:
        out = await ask_structured(agent, prompt, ChatTitle)
    finally:
        await aclose_config(cfg)  # one-shot agent: reap the ACP subprocess, if any
    return _clean_title(getattr(out, "title", None))
