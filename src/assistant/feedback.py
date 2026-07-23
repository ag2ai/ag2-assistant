"""One-shot, AG2-native feedback learner.

When the user reacts to a generated item (👍/👎 + a mandatory reason), a cheap model
distils the reaction into a concise, generalised preference and applies it to the learned
memory profile — the same store the assistant recalls every turn (``WorkingMemoryPolicy``).
Mirrors ``title.py``: a single structured-output call on the cheap model, run
fire-and-forget so it never delays the UI, with a no-LLM fallback so a reason is never lost.

The learner is **memory-aware**: it sees the current profile so it can avoid duplicating an
existing preference and (conservatively) remove a bullet the new feedback directly
contradicts. It is also given the user's *request* (intent) and the rated *content*, not
just the reason, so it generalises correctly (topic vs format vs instruction-following).
"""

from pydantic import BaseModel, Field

from assistant.config import Config
from assistant.observability import log_suppressed


class FeedbackMemory(BaseModel):
    """The learner's decision: what to record, and which existing bullets (if any) this
    feedback contradicts and should replace."""

    note: str = Field(
        description="One concise, GENERALISED statement about this user, third person, one "
        "sentence, no quotes, no leading bullet. For a thumbs-DOWN phrase it as what they "
        "DISLIKE/avoid; for a thumbs-UP phrase it as what they LIKE/prefer. Return an EMPTY "
        "STRING if this preference is already captured in the current memory (don't duplicate). "
        "Generalise — not about this single item."
    )
    remove: list[str] = Field(
        default_factory=list,
        description="Existing memory bullets that DIRECTLY CONTRADICT this new feedback and "
        "should be deleted — copy each one's text EXACTLY (verbatim). Be conservative: only "
        "list a bullet that genuinely conflicts (the user now wants the opposite), never one "
        "that is merely related or still valid. Usually this is empty.",
    )


_PROMPT = """The user gave {thumb} on something the assistant produced. Update what we remember about this user.

What we currently remember (do NOT duplicate anything already here):
{profile}

What the user asked for:
{request}

What the assistant produced:
{content}

The user's reason:
{reason}

Decide:
- note: ONE concise {polarity}, third person, generalised to a reusable preference (about the user, not this one item). If it is already captured above, return an empty string.
- remove: any existing bullet above that this feedback DIRECTLY contradicts (the user now wants the opposite) — copy its text exactly so it can be removed. Be conservative; usually empty."""


async def learn(
    config: Config,
    *,
    sentiment: str,
    reason: str,
    content: str = "",
    request: str = "",
) -> None:
    """Distil one feedback into the memory profile. Safe to fire-and-forget — swallows all
    errors and falls back to appending the raw reason so the signal is never lost."""
    from assistant.memory import read_profile, record_preference, remember_note

    store_path = config.data_dir / "profile.db"  # this profile's learned memory
    down = sentiment == "down"
    category = (
        "dislikes" if down else "how"
    )  # down → What they dislike, up → How they like things done
    thumb = "a thumbs-DOWN (disliked it)" if down else "a thumbs-UP (liked it)"
    polarity = "dislike" if down else "preference (a like)"
    try:
        from ag2 import Agent

        from assistant.agent import cheap_model, model_config

        profile = (await read_profile(store_path)) or "(nothing yet)"
        cfg = model_config(config, cheap_model(config))
        agent = Agent("feedback-learner", config=cfg)
        prompt = _PROMPT.format(
            thumb=thumb,
            polarity=polarity,
            profile=profile[:4000],
            request=(request or "(not provided)")[:2000],
            content=(content or "(not provided)")[:2000],
            reason=(reason or "").strip()[:1000],
        )
        reply = await agent.ask(prompt, response_schema=FeedbackMemory)
        out = await reply.content()
        note = (getattr(out, "note", "") or "").strip()
        remove = getattr(out, "remove", None) or []
        # A successful run always returns (even a deliberate skip) — the fallback below
        # is only for an LLM failure, so we never double-write the raw reason.
        if note or remove:
            await record_preference(store_path, note, category, remove=remove)
        return
    except Exception as exc:
        # Log instead of swallowing: a silent failure here is why bogus bullets appeared
        # with no trace. Best-effort — never re-raises.
        log_suppressed("feedback learner", exc, sentiment=sentiment)

    # Fallback ONLY for a 👎: a raw complaint still has signal, but raw praise ("Spot
    # on!") is noise — better dropped than stored verbatim as a bogus preference.
    if down and (fallback := (reason or "").strip()):
        try:
            await remember_note(store_path, fallback, category)
        except Exception as exc:
            log_suppressed("feedback learner fallback", exc, sentiment=sentiment)
