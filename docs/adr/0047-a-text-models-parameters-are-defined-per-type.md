# 0047 — A Text model's parameters are defined per type, and live on the Text model

## Status

Accepted.

## Context

How hard a model reasons, how varied its answers are and how long they may run were
reachable only through a **Text model**'s free-form `options` — the "Advanced (JSON)"
box. The person had to know the setting existed and how that provider spells it:
`reasoning_effort` on OpenAI Chat Completions, `thinking_level` on Gemini. A misspelt
name was saved without complaint and failed, or did nothing, on the next turn.

## Decisions

### 1. Parameters are registered per configuration type, under the provider's own names

`assistant.model_params` registers each type's **Parameters** separately, the way
`assistant.builtin_tools` registers provider tools (ADR 0029), and for the same reason.
The providers do not share a setting here, only a theme: OpenAI's effort has six levels
and Gemini's thinking has three, and "high" on one is not "high" on the other. A
normalised `reasoning: high` would promise an equivalence that does not exist.

A Parameter's id is therefore the constructor name AG2's provider configuration takes,
and its choices or bounds are that provider's: the output limit is `max_output_tokens`
on Gemini and OpenAI · Responses and `max_tokens` elsewhere, and temperature stops at 1
on Anthropic and at 2 on the rest. Types that offer none are registered empty — the
ChatGPT subscription, whose backend refuses every tunable, and the CLI-login types.

### 2. A Parameter is an attribute of the Text model, not of a Chat

Parameters are stored on the Text model's entry as `params`, beside `builtin_tools`.
The levels beneath keep choosing *which* shared Text model is **Active** and nothing
else (ADR 0004, 0015, 0025). Tuning one conversation differently means keeping two Text
models — the Codex type already folds its reasoning effort into the model name — and
switching between them with the **Chat override**.

A per-Chat Parameter was rejected: it would add a sixth layer to a resolution chain
that already has five, and a second place to look for why a turn behaved as it did.

### 3. Absent means the provider's default

A Parameter that is not set is not stored and not sent. An entry with no `params` key
is one that sets none, so there is nothing to migrate and nothing to seed — unlike
`builtin_tools`, no behaviour was ever automatic here. A set Parameter does replace a
default the app itself supplies, such as Gemini's output budget.

### 4. "Advanced (JSON)" keeps the last word

Parameters merge onto the provider configuration before `options`. A Text model tuned
by hand before this change keeps behaving as it did, and the escape hatch stays one.
The editor marks a control whose Parameter the Advanced box also names.

### 5. The server validates; the web names

A value outside a Parameter's choices or bounds is refused on save, and a Parameter the
type does not offer is dropped, as an unoffered provider tool is. The same filter runs
again when a turn derives the Text model, leniently: a hand-edited Global config never
passes through a save, and a Parameter it got wrong is left out rather than sent.
Numeric values must be finite; NaN and infinities are refused on save and omitted on read.
The gateway applies the same lenient filter to the model list, so malformed stored
Parameters can be corrected in Settings. The gateway ships
`params_by_type` — id, kind, choices and bounds — and `web/src/lib/modelParams.ts`
supplies the words, the same split as `lib/builtinTools.ts`.

## Consequences

- Adding a Parameter is one row in a type's `register(...)` block plus its words.
  `tests/test_model_params.py` builds the real provider configuration with every
  registered Parameter, so an id AG2 does not take fails there rather than on a turn.
- **OpenAI · Responses and Anthropic offer no reasoning Parameter.** AG2's
  `OpenAIResponsesConfig` and `AnthropicConfig` have no reasoning field, and the former
  has no request-body passthrough either. Each becomes one row once AG2 exposes it.
- **The Chat Completions output limit is `max_tokens`.** That type exists for
  OpenAI-compatible endpoints, which take it universally; OpenAI's own reasoning models
  want `max_completion_tokens` and refuse this one, loudly, where an endpoint that
  ignored the newer name would do so silently.
- The catalogue is per type, not per model name. A level or a value one particular
  model rejects is not predicted; the editor's Test button is how the person finds out.
