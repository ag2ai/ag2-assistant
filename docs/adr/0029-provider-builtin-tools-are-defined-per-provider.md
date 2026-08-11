# 0029 — A provider's builtin tools are defined per provider

## Status

Accepted.

## Context

Providers ship server-side tools of their own — Anthropic web search and code
execution, OpenAI web search and code interpreter, Gemini search grounding. They
run on the provider's infrastructure: the model asks them to search, they search,
and we never see the call. That is the opposite of the function tools in
`assistant.tools`, which the model asks *us* to run.

Nothing in a model configuration could reach them. A config carried `type`,
`model`, endpoint, key reference and free-form `options`, and that was all.

They were not, however, entirely absent. `build_agent_tools` carried
`_NATIVE_WEB_FETCH_PROVIDERS = {"anthropic"}` and swapped in Anthropic's native
`WebFetchTool` for every Anthropic agent — always on, no switch, nothing in the
UI saying so.

## Decisions

### 1. Availability is registered per configuration type, not shared

`assistant.builtin_tools` registers each type's tools separately. There is no
shared tool definition with a support matrix over it, because AG2's provider
mappers read a **different subset of each tool's fields and silently drop the
rest**: `WebSearchTool.blocked_domains` maps on Anthropic and Gemini but is
discarded on OpenAI; `WebFetchTool`'s six options all vanish on Gemini, whose
mapper emits a bare `url_context`. A shared definition would advertise knobs that
do nothing.

The key is the **type**, not the derived provider: `PROVIDER_OF` folds `openai`,
`openai_responses` and `openai_subscription` into `openai`, and only
`openai_responses` has builtins at all. `LLMConfig` therefore gained
`config_type` alongside `provider`.

Types that offer none are registered empty rather than omitted — "this type
offers none" is an answer the form renders, not a lookup miss.

### 2. A builtin declares which local tools it replaces

Each entry carries `suppresses`, naming the local tools it stands in for.
`build_agent_tools` builds the local set, drops what the enabled builtins name,
then appends the builtins. No per-tool branches, and the rule lives beside the
tool it describes.

Web search and web fetch replace their local equivalents — two tools for one job
only invites the model to pick badly. Code execution does not: ours reaches the
user's real files under approval and the provider's cannot, so they are not
substitutes.

Each entry also carries its `capability` group, so a task scoped without `web`
gets web search from neither surface.

### 3. The server owns availability; the web owns the words

`builtin_tools.py` carries ids, factories and suppression, and no user-facing
strings. `web/src/lib/builtinTools.ts` carries label, description and note, keyed
by `${type}.${id}` with a total lookup.

This follows `lib/providerLabels.ts`, which already holds `TYPE_LABEL` for all
eight types client-side rather than fetching it. The gateway ships
`builtin_tools_by_type: {type: [id]}` beside the existing `provider_deps`.

It duplicates a key set across the two languages — as `KEY_ENV`/`ENV_OF` and
`TYPES`/`TYPE_LABEL` already do — but not any content: the id list is sent at
runtime, so the only drift possible is an id with no words, which a test catches
and which degrades to showing the id.

The catalogue is code, not a data file. `factory` is a Python class, so YAML
could only carry its name plus a registry in code; and the table is correct only
for the pinned AG2 version, changing on a dependency bump rather than on user
configuration.

### 4. Opt-in, where the old rule was automatic — and an absent key carries the change

`_NATIVE_WEB_FETCH_PROVIDERS` said *replace by provider, always*. The switches say
*replace when the user asks*. Same mechanism, opposite default, so simply deleting
the old branch would have silently removed a capability from every existing
Anthropic install, with no control ever shown to restore it.

An **absent** `builtin_tools` key is therefore distinct from an empty one:

| stored | means | anthropic gets |
|---|---|---|
| key absent | predates the feature | `web_fetch` on |
| `{}` | user turned everything off | nothing |
| `{"web_fetch": {}}` | user chose it | `web_fetch` on |

The first save writes the seed out explicitly, after which the entry is ordinary
and the legacy branch never applies to it again. `_NATIVE_WEB_FETCH_PROVIDERS` and
the `provider` parameter of `build_agent_tools` are both deleted.

### 5. No option UI

Every field on the three registered tools is optional (`None` = the provider's
default), so across the eight (type × tool) pairs shipped, none requires
configuration. Storage is still a map of id → options so a panel can land later
without a data migration; the values are empty today.

## Consequences

- Adding a tool is one row in a type's `register(...)` block plus its words;
  adding a provider is one new block. Nothing else learns a tool name.
- One derivation seam: `LlmConfigStore.derive_onto` is now shared by
  `apply_active` and the gateway's per-chat model override, which previously
  duplicated it inline — a field added to one path can no longer miss the other.
- Gemini's three switches depend on an AG2 fix: its mapper never sets
  `tool_config.include_server_side_tool_invocations`, which the API now requires
  before it will accept builtins alongside function calling, and `GeminiConfig`
  exposes no seam to set it downstream. Verified live on `gemini-3.6-flash`; see
  `TEMP-ag2-bug-gemini-server-side-tool-mixing.md`. Anthropic and OpenAI
  Responses need no such fix.
- A narrow accepted regression: with an **empty** store (fresh install, or an
  `AG2ASSISTANT_LLM_PROVIDER=anthropic` env pin against the flat `llm:` block)
  there is no entry, so Anthropic gets our local fetcher where it previously got
  the native one. That path has no Settings UI to express a choice through, and
  re-adding a provider-keyed default would resurrect the constant being deleted.
