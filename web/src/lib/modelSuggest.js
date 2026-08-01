// What the Model field offers: which names, in what order, adorned how honestly.
// Store-free and transport-free — every rule here is a pure function.

import { contextLabel, knownModel, knownModelsFor, priceLabel } from './knownModels.js'
import { TYPE_LABEL } from './providerLabels.js'

// Which side can read a Model catalog for a type. The gateway probes what needs no
// credential of the user's: it is the side that can reach a host behind a Docker
// bridge or on an internal network, and no key's blast radius is at stake.
const CATALOG_SOURCE = { ollama: 'gateway' }

// '' when no catalog can be read for this type at all.
export function catalogSource(type) {
  return CATALOG_SOURCE[type] || ''
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
    if (!id || seen.has(id)) continue
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
