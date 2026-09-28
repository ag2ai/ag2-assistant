"""Provider-native (server-side) tools, registered per LLM configuration type.

A builtin runs on the provider's own infrastructure: the model asks Anthropic to
search, Anthropic searches, and we never see the call. That is the opposite of
the function tools in :mod:`assistant.tools`, which the model asks *us* to run.
Both kinds ride the same ``tools=[...]`` list on the agent.

Registration is per ``llm_configs`` **type**, never shared with a support matrix
bolted on, because AG2's provider mappers read a different subset of each tool's
fields and silently drop the rest — ``WebSearchTool``'s ``blocked_domains`` maps
on Anthropic and Gemini but is discarded on OpenAI, and ``WebFetchTool``'s six
options all vanish on Gemini, which emits a bare ``url_context``. A type is also
the right key rather than a *provider*: ``PROVIDER_OF`` folds all three OpenAI
types into ``openai``, and only ``openai_responses`` has builtins at all.

Entries carry no user-facing strings. The words belong to
``web/src/lib/builtinTools.ts`` (the same split as ``lib/providerLabels.ts``):
the server is the authority on which tools exist, the web on what they are
called. The gateway therefore ships ids, not labels.

``suppresses`` names the local tools a builtin stands in for, so
``build_agent_tools`` drops them by data rather than by a branch. It is what
replaces the old ``_NATIVE_WEB_FETCH_PROVIDERS`` special case, with the default
inverted: that rule swapped in Anthropic's fetcher unconditionally, this one only
when the user asks (see ``llm_configs._seed_legacy_builtins`` for how existing
configs keep today's behaviour).
"""

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import TypedDict

from ag2.tools import CodeExecutionTool, WebFetchTool, WebSearchTool


@dataclass(frozen=True)
class BuiltinTool:
    """One provider-native tool as ONE configuration type offers it."""

    id: str
    # The AG2 class to construct. Called with the stored options as kwargs, so a
    # future wrapper (a one-shot sub-agent, say) can stand in for the raw class
    # without touching any caller.
    factory: Callable
    # The tools.CAPABILITIES group this belongs to. A builtin is the same
    # capability as the local tool it replaces, so a task scoped without "web"
    # gets no web search from either surface.
    capability: str = ""
    # Local tool names this replaces when enabled — ours and the builtin do the
    # same job, and offering both only invites the model to pick badly.
    suppresses: tuple[str, ...] = ()
    # Option names this type actually honours. Empty everywhere today: every
    # field on the three registered tools is optional, so nothing needs
    # configuring. Declared because the option set is per-type when it arrives.
    fields: tuple[str, ...] = field(default=())


_REGISTRY: dict[str, tuple[BuiltinTool, ...]] = {}


def register(ctype: str, *tools: BuiltinTool) -> None:
    """Declare the provider tools one configuration type offers (possibly none)."""
    _REGISTRY[ctype] = tools


class _KindFields(TypedDict, total=False):
    """The spec fields one KIND of builtin carries wherever it is offered, so each
    registration below states only its id, its factory and this."""

    capability: str
    suppresses: tuple[str, ...]


_SEARCH: _KindFields = {"capability": "web", "suppresses": ("duckduckgo_search",)}
_FETCH: _KindFields = {"capability": "web", "suppresses": ("web_fetch",)}
# Code execution ADDS a runner rather than replacing one: ours reaches the user's
# real files (approval-gated), the provider's runs on their servers and cannot.
_CODE: _KindFields = {"capability": "code"}

register(
    "anthropic",
    BuiltinTool("web_search", WebSearchTool, **_SEARCH),
    BuiltinTool("web_fetch", WebFetchTool, **_FETCH),
    BuiltinTool("code_execution", CodeExecutionTool, **_CODE),
)

register(
    "openai_responses",
    BuiltinTool("web_search", WebSearchTool, **_SEARCH),
    BuiltinTool("code_execution", CodeExecutionTool, **_CODE),
)

register(
    "gemini",
    BuiltinTool("web_search", WebSearchTool, **_SEARCH),
    BuiltinTool("web_fetch", WebFetchTool, **_FETCH),
    BuiltinTool("code_execution", CodeExecutionTool, **_CODE),
)

# Registered empty rather than omitted: "this type offers none" is an answer the
# form renders, not a lookup miss. Chat Completions maps no builtin at all (its
# mapper handles FunctionToolSchema and raises on the rest) and neither does
# Ollama; the CLI-login types get their tools from the ACP adapter; and the
# ChatGPT backend rejects parameters the real OpenAI API accepts (see
# llm_configs._clean_entry's option stripping), so its Responses-shaped tool
# support is not assumed.
for _ctype in ("openai", "openai_subscription", "ollama", "claude_code", "codex"):
    register(_ctype)


def builtin_tools_for(
    ctype: str, *, registry: Mapping[str, tuple[BuiltinTool, ...]] = _REGISTRY
) -> tuple[BuiltinTool, ...]:
    """The provider tools this configuration type offers, in display order.

    ``registry`` defaults to the module's own, so ordinary callers pass nothing
    and a test states the catalogue it means to exercise (the same shape
    ``llm_configs.deps_status`` uses for ``extras``)."""
    return tuple(registry.get(ctype, ()))


def builtin_ids_for(ctype: str) -> tuple[str, ...]:
    """Just the ids this type offers — what the gateway ships to the web."""
    return tuple(t.id for t in builtin_tools_for(ctype))


def find_builtin(ctype: str, tool_id: str) -> BuiltinTool | None:
    """One registered tool by type + id, or None when this type doesn't offer it."""
    return next((t for t in builtin_tools_for(ctype) if t.id == tool_id), None)


def active_builtins(
    ctype: str, enabled: Mapping[str, Mapping], capabilities: list[str] | None = None
) -> list[tuple[BuiltinTool, dict]]:
    """The (spec, options) pairs that should actually be built: enabled, offered by
    this type, and inside the requested capability groups.

    Ids this type doesn't offer are ignored rather than raising — a hand-edited
    config.yaml must not reach the provider. ``capabilities=None`` means "all"."""
    want = (lambda c: True) if capabilities is None else (lambda c: c in capabilities)
    pairs = []
    for tool_id, options in (enabled or {}).items():
        spec = find_builtin(ctype, tool_id)
        if spec is not None and want(spec.capability):
            pairs.append((spec, dict(options or {})))
    return pairs


def build_builtin_tools(active: list[tuple[BuiltinTool, dict]]) -> list:
    """Construct the AG2 tool objects for :func:`active_builtins`' output."""
    return [spec.factory(**options) for spec, options in active]


def suppressed_by(active: list[tuple[BuiltinTool, dict]]) -> set[str]:
    """Local tool names the active builtins stand in for."""
    names: set[str] = set()
    for spec, _ in active:
        names.update(spec.suppresses)
    return names
