// What the Model field offers: which names, in what order, adorned how honestly —
// plus how the browser shapes and reads a probe of its own. Store-free and
// request-free: every rule here is a pure function, and the one call that is not
// pure lives in transport/modelCatalog.js.

import { contextLabel, knownModel, knownModelsFor, priceLabel } from './knownModels.js'
import { TYPE_LABEL } from './providerLabels.js'

// Which side can read a Model catalog for a type. The gateway probes what it holds
// the credential for, and what needs none: it is the side that can reach a host
// behind a Docker bridge or on an internal network.
const CATALOG_SOURCE = {
  ollama: 'gateway', gemini: 'gateway',
  openai: 'gateway', openai_responses: 'gateway', anthropic: 'gateway',
}
// The one type that always has something to ask with — Ollama needs no credential.
const KEYLESS = ['ollama']
// The types a browser can ask directly, holding a key the user pasted but has not
// saved. Ollama is absent on purpose: it takes no key, and only the gateway can
// reach a host behind a Docker bridge.
const BROWSER_PROBEABLE = ['gemini', 'openai', 'openai_responses', 'anthropic']

// Which side reads the catalog for this configuration; '' when nobody can. A keyed
// type with no credential yet is asked of nobody: that is not a failure to reach
// anything, so no request is made and nothing is reported.
export function catalogSource(
  type, { hasCredential = false, hasEndpoint = false, hasPastedKey = false } = {},
) {
  const source = CATALOG_SOURCE[type] || ''
  if (!source) return ''
  // A key that has only been pasted goes to the provider that owns it and never to
  // our backend — so while it is unsaved, the browser is the side that asks (ADR
  // 0024). Once it becomes a Secret the gateway takes over again.
  if (hasPastedKey && BROWSER_PROBEABLE.includes(type)) return 'browser'
  return KEYLESS.includes(type) || hasCredential || hasEndpoint ? source : ''
}

// The reason a type will never have a catalog, whatever the user does about it.
export function permanentNoCatalog(type) {
  return type === 'openai_subscription' ? 'not_probeable' : ''
}

// Names positively recognised as not a chat model. OpenAI is the only provider that
// returns embeddings, speech, image and moderation models alongside chat ones;
// Gemini and Ollama answer with their own metadata, and Anthropic's list is chat-only.
const NOT_CHAT = [
  'embedding', 'tts', 'whisper', 'transcribe', 'dall-e', 'moderation', 'audio', 'realtime',
  'image', 'sora', 'search-', 'computer-use',
]
const DENY_LISTED = { openai: true, openai_responses: true, openai_subscription: true }

// Whether a catalog name should be hidden for this type. Fails OPEN: a name is
// removed only when positively recognised as not a chat model, so a brand-new
// family the app has never heard of is always offered.
export function isNotChatModel(type, id) {
  if (!DENY_LISTED[type]) return false
  const name = String(id || '').toLowerCase()
  return NOT_CHAT.some((marker) => name.includes(marker))
}

// One offered name: `unverified` says nothing confirmed it exists, and an empty
// `price` says this install knows the name but not what it costs.
const row = (entry, unverified) => ({
  id: entry.id,
  label: entry.label || entry.id,
  price: priceLabel(entry),
  context: contextLabel(entry),
  unverified,
})

const matches = (query, entry) => {
  const q = query.trim().toLowerCase()
  if (!q) return true
  return entry.id.toLowerCase().includes(q) || (entry.label || '').toLowerCase().includes(q)
}

// The names a Model catalog returned, adorned by whatever Known models recognises.
// The catalog decides membership; the table only says what a name means, so a name
// it has never heard of is offered plain and a name it knows but the catalog omitted
// is not offered at all.
function fromCatalog(type, catalog) {
  const seen = new Set()
  const rows = []
  for (const raw of catalog) {
    const id = String(raw || '').trim()
    if (!id || seen.has(id) || isNotChatModel(type, id)) continue
    seen.add(id)
    rows.push(knownModel(id) || { id, label: id })
  }
  const family = knownModelsFor(type)
  const rank = (entry) => (family.includes(entry) ? (entry.featured ? 0 : 1) : 2)
  return rows.map((entry, i) => ({ entry, rank: rank(entry), i }))
    .sort((a, b) => a.rank - b.rank || a.i - b.i)
    .map((r) => r.entry)
}

// The names to offer for a config type, ranked and filtered by what the user has
// typed. With no Model catalog to consult, Known models stand in, marked unverified —
// nothing has confirmed those names exist and the user is not told otherwise.
export function suggestModels({ type, query = '', catalog = null }) {
  const unverified = !catalog
  const entries = catalog
    ? fromCatalog(type, catalog)
    : (() => {
        const known = knownModelsFor(type)
        return [...known.filter((m) => m.featured), ...known.filter((m) => !m.featured)]
      })()
  return entries.filter((m) => matches(query, m)).map((m) => row(m, unverified))
}

// ---- The browser's own probe, as pure pieces -----------------------------------
//
// A request builder and a response parser, so every per-provider quirk is tested
// without injecting `fetch` and without a network. The vendor addresses mirror
// provider_catalog.py's — the two paths ask the same endpoints the same way, and
// differ only in who is holding the key.

const DEFAULT_BASE_URL = {
  gemini: 'https://generativelanguage.googleapis.com/v1beta',
  openai: 'https://api.openai.com/v1',
  openai_responses: 'https://api.openai.com/v1',
  anthropic: 'https://api.anthropic.com/v1',
}
const ANTHROPIC_VERSION = '2023-06-01'

// Where to ask for this configuration's models with a pasted key, and with what
// headers; null when the browser has no such request to make. A custom endpoint is
// asked at its own address, not at the vendor whose wire it speaks.
export function browserProbeRequest({ type, baseUrl = '', key = '' }) {
  const secret = String(key || '').trim()
  if (!secret || !DEFAULT_BASE_URL[type]) return null
  const base = (String(baseUrl || '').trim() || DEFAULT_BASE_URL[type]).replace(/\/+$/, '')
  if (type === 'gemini') return { url: `${base}/models?key=${encodeURIComponent(secret)}`, headers: {} }
  if (type === 'anthropic')
    return {
      url: `${base}/models`,
      headers: {
        'x-api-key': secret,
        'anthropic-version': ANTHROPIC_VERSION,
        // Anthropic refuses browser-originated calls unless asked to allow them.
        'anthropic-dangerous-direct-browser-access': 'true',
      },
    }
  return { url: `${base}/models`, headers: { Authorization: `Bearer ${secret}` } }
}

// The model ids a provider's payload lists, or null when it is in no shape we can
// read as a catalog — an endpoint that answered but publishes no list.
export function parseCatalogPayload(type, payload) {
  if (!payload || typeof payload !== 'object') return null
  if (type === 'gemini') {
    if (!Array.isArray(payload.models)) return null
    return payload.models
      .filter((e) => e && typeof e === 'object')
      // Gemini's own metadata decides what is a chat model. An entry declaring no
      // generation methods is kept — an unrecognised shape is offered, never hidden.
      .filter((e) => {
        const methods = e.supportedGenerationMethods
        return !Array.isArray(methods) || !methods.length || methods.includes('generateContent')
      })
      .map((e) => String(e.name || '').replace(/^models\//, ''))
      .filter(Boolean)
  }
  if (!Array.isArray(payload.data)) return null
  return payload.data
    .filter((e) => e && typeof e === 'object')
    .map((e) => String(e.id || ''))
    .filter(Boolean)
}

// What an HTTP status says about why the catalog is empty; '' when it says nothing
// went wrong. A refusal of the credential is worth more than a generic failure —
// it is what surfaces a bad key at focus time rather than at Test time.
export function probeStatusReason(status) {
  if (status === 401 || status === 403) return 'unauthorized'
  if (status >= 400) return 'no_list_endpoint'
  return ''
}

// What the quiet hint line says when no catalog could be read. Never the red error
// line: a key found wrong by a background probe is a hint, one found wrong by Test
// is a verdict.
export function catalogNote(reason, type) {
  const provider = TYPE_LABEL[type] || 'the provider'
  if (reason === 'unauthorized')
    return `${provider} rejected this credential, so its model list couldn't be read. Known names are offered below.`
  if (reason === 'unreachable')
    return `Couldn't reach ${provider} for its model list. Known names are offered below.`
  if (reason === 'no_list_endpoint')
    return 'This endpoint answered but publishes no model list. Type the name it expects — known names are offered below.'
  if (reason === 'not_probeable')
    return `${provider} has no model list to read. Known names are offered below.`
  return ''
}
