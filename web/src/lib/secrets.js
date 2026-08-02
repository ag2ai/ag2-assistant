// Shared store for the install-wide Secrets list (named reusable API keys) — the
// lib/llm.js pattern (ADR 0004): SecretsPage, LlmConfigForm and LiveConfigForm all
// subscribe; every mutation calls loadSecrets() so all surfaces stay in sync live.
// Lives in lib/ (not store.js) for the same import-cycle reason as llm.js.
import { writable } from 'svelte/store'
import { api } from '../transport/api/index.ts'

export const secretsStore = writable({ secrets: [], loaded: false })

export async function loadSecrets() {
  try {
    const r = await api.secrets()
    secretsStore.set({ secrets: r.secrets || [], loaded: true })
  } catch {
    secretsStore.set({ secrets: [], loaded: true })
  }
}

// Create-or-snap: mint a Secret for a pasted key; a 409 (value already stored —
// Secrets are unique by value) resolves to the EXISTING Secret's view instead.
export async function createOrSnap(body) {
  try {
    return (await api.createSecret(body)).secret
  } catch (e) {
    if (e.status === 409 && e.body?.existing) return e.body.existing
    throw e
  }
}
