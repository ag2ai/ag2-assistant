// What the Model field offers: which names, in what order, adorned how honestly —
// plus how the browser shapes and reads a probe of its own. Every rule here is a
// pure function; the one call that is not lives in transport/modelCatalog.ts.

import { contextLabel, knownModel, knownModelsFor, priceLabel } from './knownModels.ts'
import type { KnownModel } from './knownModels.ts'
import { typeLabel } from './providerLabels.ts'

// A name on offer: the table's row where the table knows it, else just the name a
// live catalog returned.
type Offered = Partial<KnownModel> & { id: string }

// One row the Model combobox draws.
export type ModelSuggestion = {
  id: string
  label: string
  price: string
  context: string
  unverified: boolean
}

// Who reads the catalog for a configuration: the gateway, the browser holding a
// pasted key, or ('') nobody.
export type CatalogSource = '' | 'browser' | 'gateway'

// The shape of a browser-side probe, ready for fetch().
export type ProbeRequest = { url: string; headers: Record<string, string> }

// Why no catalog came back. The same four tokens provider_catalog.py names.
export const REASON = Object.freeze({
  UNAUTHORIZED: 'unauthorized',
  UNREACHABLE: 'unreachable',
  NO_LIST_ENDPOINT: 'no_list_endpoint',
  NOT_PROBEABLE: 'not_probeable',
})

// One row per type the app can read a catalog for: whether it needs a credential at
// all, and whether a browser holding a pasted key may ask the provider itself.
// Ollama is gateway-only — only that side reaches a host behind a Docker bridge.
const CATALOG_TYPE: Record<string, { keyless: boolean; browser: boolean }> = {
  ollama: { keyless: true, browser: false },
  gemini: { keyless: false, browser: true },
  openai: { keyless: false, browser: true },
  openai_responses: { keyless: false, browser: true },
  anthropic: { keyless: false, browser: true },
}

// Which side reads the catalog for this configuration; '' when nobody can. A keyed
// type with no credential yet is asked of nobody — no request, nothing reported.
export function catalogSource(
  type: string,
  { hasCredential = false, hasEndpoint = false, hasPastedKey = false }: {
    hasCredential?: boolean
    hasEndpoint?: boolean
    hasPastedKey?: boolean
  } = {},
): CatalogSource {
  const rules = CATALOG_TYPE[type]
  if (!rules) return ''
  // An unsaved key goes to the provider that owns it, never to our backend (ADR
  // 0024); once it becomes a Secret the gateway takes over again.
  if (hasPastedKey && rules.browser) return 'browser'
  return rules.keyless || hasCredential || hasEndpoint ? 'gateway' : ''
}

// The reason a type will never have a catalog, whatever the user does about it.
export function permanentNoCatalog(type: string): string {
  return type === 'openai_subscription' ? REASON.NOT_PROBEABLE : ''
}

// Names positively recognised as not a chat model. OpenAI is the only provider whose
// list mixes them in; Gemini and Ollama answer with their own metadata, and
// Anthropic's list is chat-only.
const NOT_CHAT = [
  'embedding', 'tts', 'whisper', 'transcribe', 'dall-e', 'moderation', 'audio', 'realtime',
  'image', 'sora',
]
const DENY_LISTED: Record<string, boolean> = {
  openai: true, openai_responses: true, openai_subscription: true,
}

// Whether a catalog name should be hidden for this type. Fails OPEN: only a name
// positively recognised as not a chat model is removed.
export function isNotChatModel(type: string, id: string | null | undefined): boolean {
  if (!DENY_LISTED[type]) return false
  const name = String(id || '').toLowerCase()
  return NOT_CHAT.some((marker) => name.includes(marker))
}

// One offered name: `unverified` says nothing confirmed it exists, an empty `price`
// that the table has never heard of it.
const row = (entry: Offered, unverified: boolean): ModelSuggestion => ({
  id: entry.id,
  label: entry.label || entry.id,
  price: priceLabel(entry),
  context: contextLabel(entry),
  unverified,
})

const matches = (query: string, entry: Offered) => {
  const q = query.trim().toLowerCase()
  if (!q) return true
  return entry.id.toLowerCase().includes(q) || (entry.label || '').toLowerCase().includes(q)
}

// The names a Model catalog returned, adorned by whatever Known models recognises.
// The catalog decides membership, so an unrecognised name is offered plain.
function fromCatalog(type: string, catalog: readonly unknown[]): Offered[] {
  const seen = new Set<string>()
  const rows: Offered[] = []
  for (const raw of catalog) {
    const id = String(raw || '').trim()
    if (!id || seen.has(id) || isNotChatModel(type, id)) continue
    seen.add(id)
    rows.push(knownModel(id) || { id, label: id })
  }
  const family = knownModelsFor(type)
  const rank = (entry: Offered) =>
    family.some((m) => m === entry) ? (entry.featured ? 0 : 1) : 2
  return rows.map((entry, i) => ({ entry, rank: rank(entry), i }))
    .sort((a, b) => a.rank - b.rank || a.i - b.i)
    .map((r) => r.entry)
}

// The names to offer for a config type, ranked and filtered by what the user has
// typed. With no Model catalog to consult, Known models stand in, marked unverified.
export function suggestModels({ type, query = '', catalog = null }: {
  type: string
  query?: string
  catalog?: readonly unknown[] | null
}): ModelSuggestion[] {
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
// A request builder and a response parser, so every per-provider quirk is tested
// without injecting `fetch`. The addresses are provider_catalog.py's.

const DEFAULT_BASE_URL: Record<string, string> = {
  gemini: 'https://generativelanguage.googleapis.com/v1beta',
  openai: 'https://api.openai.com/v1',
  openai_responses: 'https://api.openai.com/v1',
  anthropic: 'https://api.anthropic.com/v1',
}
const ANTHROPIC_VERSION = '2023-06-01'

// Where to ask for this configuration's models with a pasted key, and with what
// headers; null when the browser has no such request to make.
export function browserProbeRequest({ type, baseUrl = '', key = '' }: {
  type: string
  baseUrl?: string
  key?: string
}): ProbeRequest | null {
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
export function parseCatalogPayload(type: string, payload: unknown): string[] | null {
  if (!payload || typeof payload !== 'object') return null
  const body = payload as { models?: unknown; data?: unknown }
  if (type === 'gemini') {
    if (!Array.isArray(body.models)) return null
    return body.models
      .filter((e): e is Record<string, unknown> => !!e && typeof e === 'object')
      // Gemini's own metadata decides; an entry declaring no methods is kept.
      .filter((e) => {
        const methods = e.supportedGenerationMethods
        return !Array.isArray(methods) || !methods.length || methods.includes('generateContent')
      })
      .map((e) => String(e.name || '').replace(/^models\//, ''))
      .filter(Boolean)
  }
  if (!Array.isArray(body.data)) return null
  return body.data
    .filter((e): e is Record<string, unknown> => !!e && typeof e === 'object')
    .map((e) => String(e.id || ''))
    .filter(Boolean)
}

// What an HTTP status says about why no catalog came back; '' when it says nothing
// went wrong. A 5xx or a 429 is a bad moment, not an endpoint without a list.
export function probeStatusReason(status: number): string {
  if (status === 401 || status === 403) return REASON.UNAUTHORIZED
  if (status === 429 || status >= 500) return REASON.UNREACHABLE
  if (status >= 400) return REASON.NO_LIST_ENDPOINT
  return ''
}

// What the quiet hint line says when no catalog could be read — never the red error
// line, which is Test's.
export function catalogNote(reason: string | undefined, type: string): string {
  const provider = typeLabel(type)
  if (reason === REASON.UNAUTHORIZED)
    return `${provider} rejected this credential, so its model list couldn't be read. Known names are offered below.`
  if (reason === REASON.UNREACHABLE)
    return `Couldn't reach ${provider} for its model list. Known names are offered below.`
  if (reason === REASON.NO_LIST_ENDPOINT)
    return 'This endpoint answered but publishes no model list. Type the name it expects — known names are offered below.'
  if (reason === REASON.NOT_PROBEABLE)
    return `${provider} has no model list to read. Known names are offered below.`
  return ''
}
