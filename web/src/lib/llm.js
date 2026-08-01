// Shared vocabulary for the install-wide named LLM configurations — one source of
// truth for the client-side "can this run right now?" predicate. Consumed by
// Settings → Models (ModelsPage) and the composer's model switcher so the two
// surfaces speak identically. Type labels live in lib/providerLabels.js, marks in
// lib/brandMarks.js.
import { writable } from 'svelte/store'
import { api } from '../transport/api.js'

// Shared install-wide LLM config state — the single source of truth for BOTH the
// composer's ModelSwitcher and Settings → Models. Two live views of the same list
// (rename / add / switch-active in Settings must show up in the composer without a
// reload), so the data can't be per-component local state. Lives here rather than
// store.js because it needs `api`, and store.js → api.js → lib/profile.js → store.js
// would be an import cycle. See docs/adr/0004-shared-llm-config-store.md.
// Mutate via the API, then call loadLlmConfigs() to refresh every subscriber.
export const llmConfigs = writable({
  configs: [], active: null, envOverride: null, providerDeps: {}, loaded: false,
})

export async function loadLlmConfigs() {
  const d = await api.llmConfigs()
  llmConfigs.set({
    configs: d.configs || [],
    active: d.active ?? null,
    envOverride: d.env_override ?? null,
    // type -> {ok, extra, install}, for every type (the template grid reads types
    // no config uses yet).
    providerDeps: d.provider_deps || {},
    loaded: true,
  })
}

// Whether a config can actually run right now — the signal behind the health dot.
// The API view carries no `usable` flag, so we derive it exactly as the server's
// llm_configs.usable() does: deps.ok false = provider library not installed,
// key_source 'none' = no key at all, subscription = needs signed_in. Everything else
// (ollama / custom base_url / own key / shared env key) resolves to a non-'none'
// source and is runnable.
export function isUsable(c) {
  if (c.deps && !c.deps.ok) return false
  if (c.key_source === 'none') return false
  if (c.type === 'openai_subscription') return !!c.signed_in
  return true
}
