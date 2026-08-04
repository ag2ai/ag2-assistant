// Secrets — named reusable API keys (app.py 945-1027). `value` is WRITE-ONLY:
// no route ever echoes it back, views carry a last-4 hint instead.
import { globalApi as G } from '../../lib/profile.ts'
import { del, get, post } from '../http.ts'
import { Ok, SecretList, SecretSaved } from '../../schemas/index.ts'

// SecretCreateRequest / SecretUpdateRequest in app.py.
export type SecretDraft = {
  name: string
  value: string
  provider?: string
  default?: boolean
}

// SecretUpdateRequest (app.py 530): null leaves the field unchanged — the edit
// form sends value:null to keep the stored key.
export type SecretPatch = {
  name?: string | null
  value?: string | null
  provider?: string | null
  default?: boolean | null
}

export const secretsApi = {
  // The legacy per-provider shared key (settings.json), not a named Secret.
  setKey: (provider: string, value: string) => post(G('/secrets/key'), { provider, value }, Ok),

  secrets: () => get(G('/secrets'), SecretList),

  // 409 carries err.body.existing when the value is already stored (unique by
  // value) — callers snap to it (lib/secrets.js createOrSnap).
  createSecret: (s: SecretDraft) => post(G('/secrets'), s, SecretSaved),

  updateSecret: (id: string, patch: SecretPatch) =>
    post(G('/secrets/' + encodeURIComponent(id)), patch, SecretSaved),

  deleteSecret: (id: string) => del(G('/secrets/' + encodeURIComponent(id)), Ok),
}
