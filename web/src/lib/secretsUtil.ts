// Pure helpers for the Secret picker/creation flows. Kept import-free so they
// unit-test under node:test without a DOM (the api-coupled store is lib/secrets.js).

// Auto-name for a paste-to-create Secret: "<config name> key …last4" — instantly
// minted from the model form, renameable later in Settings → Secrets.
export function autoSecretName(configName, value) {
  const last4 = (value || '').trim().slice(-4)
  const base = (configName || '').trim() || 'API'
  return `${base} key …${last4}`
}

// Picker ordering: provider-tag matches first (the tag is SOFT — it sorts, never
// filters out; any Secret stays selectable for any model).
export function sortForProvider(secrets, provider) {
  if (!provider) return secrets
  return [
    ...secrets.filter((s) => s.provider === provider),
    ...secrets.filter((s) => s.provider !== provider),
  ]
}
