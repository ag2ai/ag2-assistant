// What the Model field offers: which names, in what order, adorned how honestly.
// Store-free and transport-free — every rule here is a pure function.

import { contextLabel, knownModel, knownModelsFor, priceLabel } from './knownModels.js'

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

// The names to offer for a config type, ranked and filtered by what the user has
// typed. With no Model catalog to consult, Known models stand in, marked unverified.
export function suggestModels({ type, query = '' }) {
  const known = knownModelsFor(type)
  const featured = known.filter((m) => m.featured)
  const rest = known.filter((m) => !m.featured)
  return [...featured, ...rest].filter((m) => matches(query, m)).map((m) => row(m, true))
}
