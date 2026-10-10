"""Parameters a Text model can be tuned with, registered per LLM configuration type.

Ids are the provider's own constructor names; entries carry no user-facing strings.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class ModelParam:
    """One parameter as ONE configuration type offers it."""

    id: str
    kind: str
    choices: tuple[str, ...] = ()


_REGISTRY: dict[str, tuple[ModelParam, ...]] = {}


def register(ctype: str, *params: ModelParam) -> None:
    """Declare the parameters one configuration type offers (possibly none)."""
    _REGISTRY[ctype] = params


register(
    "openai",
    ModelParam(
        "reasoning_effort",
        "choice",
        choices=("none", "minimal", "low", "medium", "high", "xhigh"),
    ),
)

register("gemini", ModelParam("thinking_level", "choice", choices=("low", "medium", "high")))

# Registered empty rather than omitted: "this type offers none" is an answer.
for _ctype in (
    "openai_responses",
    "openai_subscription",
    "anthropic",
    "ollama",
    "claude_code",
    "codex",
):
    register(_ctype)


def params_for(ctype: str) -> tuple[ModelParam, ...]:
    """The parameters this configuration type offers, in display order."""
    return _REGISTRY.get(ctype, ())


def clean_params(ctype: str, raw: object) -> dict:
    """The stored parameters for one entry, filtered to what this type offers."""
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise ValueError("params must be a JSON object")
    cleaned = {}
    for param in params_for(ctype):
        value = raw.get(param.id)
        if value is None:
            continue
        if value not in param.choices:
            raise ValueError(f"{param.id} must be one of {', '.join(param.choices)}, not {value!r}")
        cleaned[param.id] = value
    return cleaned
