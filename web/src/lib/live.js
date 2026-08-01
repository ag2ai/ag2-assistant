// Shared vocabulary for the install-wide named LIVE (voice) configurations — the
// spoken counterpart of lib/llm.js. One source of truth for the human label and the
// "can this run right now?" predicate. Consumed by Settings → Models → Live
// (VoiceSection). Mutate via the API, then loadLiveConfigs() to refresh.
// What a provider looks like is lib/brandMarks.js's business, not this file's.
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

// provider -> logo (only realtime-capable providers exist here: gemini, openai).
// provider -> the label the UI shows for it.
export const PROVIDER_LABEL = { gemini: 'Gemini', openai: 'OpenAI' }

// Whether a config can actually open a voice session right now — mirrors the server's
// live_configs.usable(): 'none' key_source (no own key and no shared provider key) is
// dead; anything else (own key / shared env key) is runnable.
export function isUsable(c) {
  return c.key_source !== 'none'
}
