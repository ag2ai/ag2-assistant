"""Parameters a Text model can be tuned with, registered per LLM configuration type.

Ids are the provider's own constructor names; entries carry no user-facing strings.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class ModelParam:
    """One parameter as ONE configuration type offers it."""

    id: str
    # "choice" (one of ``choices``), "int" or "float" (within the bounds, if any).
    kind: str
    choices: tuple[str, ...] = ()
    minimum: float | None = None
    maximum: float | None = None


_REGISTRY: dict[str, tuple[ModelParam, ...]] = {}


def register(ctype: str, *params: ModelParam) -> None:
    """Declare the parameters one configuration type offers (possibly none)."""
    _REGISTRY[ctype] = params


def _temperature(maximum: float) -> ModelParam:
    return ModelParam("temperature", "float", minimum=0, maximum=maximum)


def _output_limit(name: str) -> ModelParam:
    return ModelParam(name, "int", minimum=1)


register(
    "openai",
    ModelParam(
        "reasoning_effort",
        "choice",
        choices=("none", "minimal", "low", "medium", "high", "xhigh"),
    ),
    _temperature(2),
    _output_limit("max_tokens"),
)

register("openai_responses", _temperature(2), _output_limit("max_output_tokens"))

register(
    "gemini",
    ModelParam("thinking_level", "choice", choices=("low", "medium", "high")),
    _temperature(2),
    _output_limit("max_output_tokens"),
)

register("anthropic", _temperature(1), _output_limit("max_tokens"))

register("ollama", _temperature(2), _output_limit("max_tokens"))

# Registered empty rather than omitted: "this type offers none" is an answer.
for _ctype in ("openai_subscription", "claude_code", "codex"):
    register(_ctype)


def params_for(ctype: str) -> tuple[ModelParam, ...]:
    """The parameters this configuration type offers, in display order."""
    return _REGISTRY.get(ctype, ())


def _checked(param: ModelParam, value: object) -> object:
    """``value`` if this parameter accepts it, else a ValueError naming the parameter."""
    if param.kind == "choice":
        if value not in param.choices:
            raise ValueError(f"{param.id} must be one of {', '.join(param.choices)}, not {value!r}")
        return value
    numeric = (int,) if param.kind == "int" else (int, float)
    if isinstance(value, bool) or not isinstance(value, numeric):
        wanted = "a whole number" if param.kind == "int" else "a number"
        raise ValueError(f"{param.id} must be {wanted}, not {value!r}")
    if param.minimum is not None and value < param.minimum:
        raise ValueError(f"{param.id} must be at least {param.minimum}, not {value!r}")
    if param.maximum is not None and value > param.maximum:
        raise ValueError(f"{param.id} must be at most {param.maximum}, not {value!r}")
    return value


def clean_params(ctype: str, raw: object) -> dict:
    """The stored parameters for one entry, filtered to what this type offers."""
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise ValueError("params must be a JSON object")
    cleaned = {}
    for param in params_for(ctype):
        value = raw.get(param.id)
        if value is not None:
            cleaned[param.id] = _checked(param, value)
    return cleaned
