"""Structured one-shot asks that also work on ACP-backed models.

``agent.ask(prompt, response_schema=X)`` relies on the provider enforcing JSON
output — but ag2's ACP client (Claude Code et al.) IGNORES ``response_schema``
entirely: the prompt goes out as plain text and ``reply.content()`` then fails
validating prose as JSON. Every side-pass consumer (chat titles, the feedback
learner, task run summaries) hits this on the ``claude_code`` provider.

:func:`ask_structured` keeps the native path for real providers and, for ACP
configs, embeds the JSON Schema in the prompt and parses the reply leniently
(code fences / surrounding prose tolerated). Failures raise — every caller
already degrades gracefully (suppressed / fallback), so a bad parse costs one
optional feature, never a turn.
"""

import json

from ag2.acp import ACPConfig


def _extract_json(text: str) -> str:
    """The outermost ``{...}`` block: models wrap JSON in fences/prose at will."""
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end <= start:
        raise ValueError(f"no JSON object in model reply: {text[:120]!r}")
    return text[start : end + 1]


async def ask_structured(agent, prompt: str, schema_cls):
    """One validated ``schema_cls`` instance from a one-shot ask on ``agent``."""
    if not isinstance(agent.config, ACPConfig):
        reply = await agent.ask(prompt, response_schema=schema_cls)
        return await reply.content()
    schema_json = json.dumps(schema_cls.model_json_schema())
    reply = await agent.ask(
        f"{prompt}\n\nReply with ONLY a JSON object matching this JSON Schema — "
        f"no prose, no code fences:\n{schema_json}"
    )
    return schema_cls.model_validate_json(_extract_json(reply.body))


async def aclose_config(config) -> None:
    """Tear down per-call model-config resources for a one-shot agent the
    CALLER owns — on ACP configs every side-pass ask spawns a fresh adapter
    subprocess that nothing else reaps (ag2's GC finalizer is too lazy for a
    long-lived gateway; observed as accumulating ``claude-agent-acp`` procs).
    Ordinary provider configs have no ``aclose`` and are a no-op. Never raises:
    teardown must not mask the pass's own result. NOT for shared/gateway-cached
    agents — their lifecycle belongs to ``Gateway._aclose_agents``."""
    aclose = getattr(config, "aclose", None)
    if aclose is None:
        return
    try:
        await aclose()
    except Exception as exc:
        from assistant.observability import log_suppressed

        log_suppressed("closing one-shot ACP model session", exc)
