// Shared store for the install-wide Secrets list (named reusable API keys) — the
// lib/llm.js pattern (ADR 0004): SecretsPage, LlmConfigForm and LiveConfigForm all
// subscribe; every mutation calls loadSecrets() so all surfaces stay in sync live.
// Lives in lib/ (not store.js) for the same import-cycle reason as llm.js.
import { writable } from 'svelte/store'
import { api } from '../transport/api/index.ts'
import { ApiError } from '../transport/http.ts'
import type { SecretDraft } from '../transport/api/secrets.ts'
import { SecretConflict, type Secret } from '../schemas/index.ts'

export type SecretsSnapshot = { secrets: Secret[]; loaded: boolean }

export const secretsStore = writable<SecretsSnapshot>({ secrets: [], loaded: false })

export async function loadSecrets(): Promise<void> {
  try {
    const r = await api.secrets()
    secretsStore.set({ secrets: r.secrets || [], loaded: true })
  } catch {
    secretsStore.set({ secrets: [], loaded: true })
  }
}

// Create-or-snap: mint a Secret for a pasted key; a 409 (value already stored —
// Secrets are unique by value) resolves to the EXISTING Secret's view instead.
export async function createOrSnap(body: SecretDraft): Promise<Secret> {
  try {
    return (await api.createSecret(body)).secret
  } catch (e) {
    // 409 = the value is already stored; the body carries the existing Secret's view.
    const conflict = e instanceof ApiError && e.status === 409 ? SecretConflict.safeParse(e.body) : null
    if (conflict?.success) return conflict.data.existing
    throw e
  }
}
