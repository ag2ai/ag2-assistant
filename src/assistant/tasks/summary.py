"""One-line run summaries and task auto-naming, both distilled by the cheap
model (best-effort).

After a run completes we ask for a single concrete sentence — it feeds the
run list, the channel notification, and the next run's "previous outcomes"
context. When a task is created without a name, we ask the same cheap model
for a short name + one-sentence description from the task's prompt. Mirrors
``assistant/title.py``: structured output, never raises.
"""

from pydantic import BaseModel, Field

from assistant.config import Config

_MAX = 200

_PROMPT = (
    "A scheduled background task just ran. In ONE sentence (max 140 chars), state "
    "what this run actually did or produced — concrete facts first, no fluff.\n\n"
    "Task instructions: {prompt}\n\nRun result:\n{reply}"
)

_META_PROMPT = (
    "A user is creating a background task with this instruction. Come up with a short "
    "name (max 4 words) and a one-sentence description of what the task does. Return "
    "JSON {{name, description}}.\n\nInstruction: {prompt}"
)


class RunSummary(BaseModel):
    summary: str = Field(description="One concrete sentence on what the run did/produced.")


class TaskMeta(BaseModel):
    name: str = Field(description="Short task name, max 4 words.")
    description: str = Field(default="", description="One-sentence description of the task.")


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


async def suggest_task_meta(
    config: Config, prompt: str, agent_factory=None
) -> tuple[str, str]:
    """(name, description) for a task created without a name, distilled from its
    prompt by the cheap model. On any LLM/parsing failure, falls back to the
    first 40 characters of the prompt as the name and an empty description —
    a task must always have SOME name, so this path never raises."""
    try:
        agent = (agent_factory or _default_factory(config))()
        r = await agent.ask(
            _META_PROMPT.format(prompt=(prompt or "")[:2000]),
            response_schema=TaskMeta,
        )
        out = await r.content()
        name = " ".join(str(getattr(out, "name", "")).split())
        description = " ".join(str(getattr(out, "description", "")).split())
        if not name:
            raise ValueError("cheap model returned an empty name")
        return name, description
    except Exception:
        return (prompt or "")[:40].strip(), ""
