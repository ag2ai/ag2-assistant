"""ask_user — let the model pose a question with tappable answer options.

`context.input()` (AG2's HumanInputRequest) carries only a prompt string, so
model-initiated questions were always free-text. This tool calls the turn's
`Asker` directly — the same seam permission prompts use — so a `Question` can
carry `options`, which flow through the durable Inquiry store onto the stream
and render as tappable choices (the ChoiceCard) in the GUI, answerable from any
surface and surviving restarts.

The asker is per-turn state: it rides in `context.dependencies` (registered
alongside the PermissionManager wherever ask kwargs are built).
"""

from typing import Annotated

from ag2 import Context, tool
from pydantic import Field

from assistant.hitl.base import Asker, Question

_MAX_OPTIONS = 6

_NO_ASKER = (
    "No user is available to answer right now. Proceed with your best judgement "
    "and state what you assumed."
)


@tool
async def ask_user(
    question: Annotated[str, Field(description="One clear question for the user.")],
    context: Context,
    options: Annotated[
        str,
        Field(
            description=(
                "2-5 short answer choices, comma-separated (e.g. 'Italian, Thai, "
                "Japanese'). Leave empty for a free-text answer."
            )
        ),
    ] = "",
    detail: Annotated[
        str, Field(description="Optional single line of context shown under the question.")
    ] = "",
) -> str:
    """Ask the user a question and wait for their answer. When the answer is one of
    a few known alternatives, pass them as `options` so the user can tap a choice
    instead of typing. Use for genuine clarifications and preferences before
    acting — never to re-confirm something the user already said.
    """
    asker = context.dependencies.get(Asker)
    if asker is None:
        return _NO_ASKER
    opts = [o.strip() for o in str(options or "").split(",") if o.strip()][:_MAX_OPTIONS]
    answer = await asker.ask(
        Question(
            text=str(question or "").strip(),
            options=opts or None,
            detail=str(detail or "").strip() or None,
        )
    )
    return f"The user answered: {answer}"
