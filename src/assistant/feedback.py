"""One-shot, AG2-native feedback learner.

When the user reacts to a generated item (👍/👎 + a mandatory reason), a cheap model
distils the reaction into a concise, generalised preference and writes it to the learned
memory profile via ``remember_note`` — the same store the assistant recalls every turn
(``WorkingMemoryPolicy``). Mirrors ``title.py``: a single structured-output call on the
cheap model, run fire-and-forget so it never delays the UI, and a no-LLM fallback so a
reason is never lost.

The learner is given both the user's *request* (intent) and the rated *content*, not
just the reason, so it generalises correctly (topic vs format vs instruction-following).
"""

from pydantic import BaseModel, Field

from assistant.config import Config


class FeedbackMemory(BaseModel):
    """The distilled memory note. The section is decided by the thumb (up → likes,
    down → dislikes), so the model only writes the note."""

    note: str = Field(
        description="One concise, GENERALISED statement about this user, third person, "
        "one sentence, no quotes, no leading bullet. For a thumbs-DOWN phrase it as what "
        "they DISLIKE/avoid (e.g. 'Dislikes verbose, list-heavy reports'); for a thumbs-UP "
        "phrase it as what they LIKE/prefer (e.g. 'Prefers concise, plain-language summaries'). "
        "Generalise — not about this single item."
    )


_PROMPT = """The user gave {thumb} on something the assistant produced. Turn it into ONE durable {polarity} worth remembering about this user.

What the user asked for:
{request}

What the assistant produced:
{content}

The user's reason:
{reason}

Write a single concise note in the third person, phrased as {polarity}, generalised to a reusable preference (about the user, not this one item)."""


async def learn(
    config: Config,
    *,
    sentiment: str,
    reason: str,
    content: str = "",
    request: str = "",
) -> None:
    """Distil one feedback into a memory bullet. Safe to fire-and-forget — swallows all
    errors and falls back to storing the raw reason so the signal is never lost."""
    from assistant.memory import remember_note

    down = sentiment == "down"
    category = (
        "dislikes" if down else "how"
    )  # down → "What they dislike", up → "How they like things done"
    thumb = "a thumbs-DOWN (disliked it)" if down else "a thumbs-UP (liked it)"
    polarity = "dislike" if down else "preference (a like)"
    try:
        from autogen.beta import Agent

        from assistant.agent import cheap_model, model_config

        cfg = model_config(config, cheap_model(config))
        agent = Agent("feedback-learner", config=cfg)
        prompt = _PROMPT.format(
            thumb=thumb,
            polarity=polarity,
            request=(request or "(not provided)")[:2000],
            content=(content or "(not provided)")[:2000],
            reason=(reason or "").strip()[:1000],
        )
        reply = await agent.ask(prompt, response_schema=FeedbackMemory)
        out = await reply.content()
        note = (getattr(out, "note", "") or "").strip()
        if note:
            await remember_note(note, category)
            return
    except Exception:
        pass  # fall through to the raw-reason fallback below

    fallback = (reason or "").strip()
    if fallback:
        try:
            await remember_note(fallback, category)
        except Exception:
            pass
