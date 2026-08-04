// Shared vocabulary for the install-wide named LIVE (voice) configurations — the
// spoken counterpart of lib/llm.js. One source of truth for the "can this run right
// now?" predicate. Consumed by Settings → Models → Live (VoiceSection). Mutate via
// the API, then loadLiveConfigs() to refresh. Provider labels live in
// lib/providerLabels.js, marks in lib/brandMarks.js.
import { writable } from 'svelte/store'
import { api } from '../transport/api.js'

// { configs, active, providers, loaded }. `providers` is the server catalog
// ([{name, default_model, default_voice}]) that seeds the add-form + templates.
export const liveConfigs = writable({ configs: [], active: null, providers: [], loaded: false })

export async function loadLiveConfigs() {
  const d = await api.liveConfigs()
  liveConfigs.set({
    configs: d.configs || [],
    active: d.active ?? null,
    providers: d.providers || [],
    loaded: true,
  })
}

// Whether a config can actually open a voice session right now — mirrors the server's
// live_configs.usable(): 'none' key_source (no own key and no shared provider key) is
// dead; anything else (own key / shared env key) is runnable.
export function isUsable(c) {
  return c.key_source !== 'none'
}
