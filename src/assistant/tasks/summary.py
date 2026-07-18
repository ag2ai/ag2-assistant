"""One-line run summaries, distilled by the cheap model (best-effort).

After a run completes we ask for a single concrete sentence — it feeds the
run list, the channel notification, and the next run's "previous outcomes"
context. Mirrors ``assistant/title.py``: structured output, never raises.
"""

from pydantic import BaseModel, Field

from assistant.config import Config

_MAX = 200

_PROMPT = (
    "A scheduled background task just ran. In ONE sentence (max 140 chars), state "
    "what this run actually did or produced — concrete facts first, no fluff.\n\n"
    "Task instructions: {prompt}\n\nRun result:\n{reply}"
)


class RunSummary(BaseModel):
    summary: str = Field(description="One concrete sentence on what the run did/produced.")


def _default_factory(config: Config):
    def make():
        from ag2 import Agent

        from assistant.agent import cheap_model, model_config

        return Agent("run-summarizer", config=model_config(config, cheap_model(config)))

    return make


async def summarize_run(config: Config, task_prompt: str, reply: str, agent_factory=None) -> str:
    """One-line outcome of a run, or "" on any failure (summary is optional)."""
    try:
        agent = (agent_factory or _default_factory(config))()
        r = await agent.ask(
            _PROMPT.format(prompt=(task_prompt or "")[:2000], reply=(reply or "")[:4000]),
            response_schema=RunSummary,
        )
        out = await r.content()
        return " ".join(str(getattr(out, "summary", "")).split())[:_MAX]
    except Exception:
        return ""
