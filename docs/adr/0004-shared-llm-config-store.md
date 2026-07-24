# Shared client store for LLM config state

The install-wide LLM configuration list is shown in two places that are alive at
the same time: Settings → Models (`ModelsPage`) and the composer's `ModelSwitcher`.
Because Settings is a modal overlay, the composer never unmounts while you edit —
so when each surface owned its *own* fetch (the original "self-contained, no shared
store" pattern shared with `McpServers`), a rename / add / active-switch made in
Settings stayed invisible in the switcher until a full page reload. We fixed this
by promoting the config list to a shared Svelte store (`llmConfigs` + `loadLlmConfigs()`
in `web/src/lib/llm.js`): both surfaces subscribe to it, and every mutation refreshes
it, so the two views stay in sync live.

## Considered Options

- **Shared store (chosen).** One source of truth; both surfaces update the instant
  either mutates. Directly reverses the "no shared store state" note the two
  components used to carry — hence this record.
- **`CustomEvent` broadcast** (the app's existing `ag2-accent-change` / `ag2-theme-change`
  idiom): each surface keeps its own fetch and re-fetches on an `ag2-llm-change`
  event. Rejected — keeps two copies of the same data loosely coupled by a string
  event; the store makes the shared state explicit.
- **Refetch the switcher on Settings close.** Simplest, but a blunt lifecycle
  coupling that refetches unconditionally and doesn't generalize.

## Consequences

- LLM config is now the one Settings feature that uses a shared store; `McpServers`
  and the others still follow the self-contained pattern. If a second surface ever
  needs live MCP state, this is the precedent to copy.
- The store lives in `lib/llm.js`, not `store.js`, on purpose: `store.js` imports
  only `svelte/store`, and `store.js → transport/api.js → lib/profile.js → store.js`
  would be an import cycle if the loader lived there.

## Amendment: per-profile Active override (ADR 0015)

The original "one install-wide Active LLM configuration" is relaxed to a per-profile
**Active override**: the shared config *list* stays install-wide and single-sourced
(everything above holds), but a Profile may pick which shared config is Active *for
it*, stored in the profile's config overlay. The install-wide Active becomes the
default; effective Active resolves **env pin > profile override > install-wide Active
> env fallback**. The composer's `ModelSwitcher` continues to set the install-wide
Active (`useLlmConfig`); the new per-profile switchers in Settings → Profiles set the
override instead. The shared store now also carries the effective/overridden active so
both surfaces stay honest. Same treatment applies to the Live model list. See ADR 0015.
