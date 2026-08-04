// The one-table seam: what this install knows *about* a model name, and the two
// screens that read it instead of holding their own copy.
// Run: npm test
import { test } from 'node:test'
import assert from 'node:assert/strict'
import {
  KNOWN_MODELS,
  contextLabel,
  familyOf,
  featuredModelsFor,
  knownModel,
  knownModelsFor,
  priceLabel,
} from './knownModels.js'
import { MODEL_TEMPLATES } from './modelTemplates.js'
import { TYPE_LABEL } from './providerLabels.js'

const FAMILIES = ['openai', 'anthropic', 'gemini', 'ollama']

test('every entry carries the six facts, and an id appears once', () => {
  const seen = new Set()
  for (const m of KNOWN_MODELS) {
    assert.ok(m.id?.trim(), 'an entry has no id')
    assert.ok(m.label?.trim(), `${m.id} has no label`)
    assert.ok(FAMILIES.includes(m.provider), `${m.id} names an unknown family ${m.provider}`)
    assert.equal(typeof m.price?.in, 'number', `${m.id} has no input price`)
    assert.equal(typeof m.price?.out, 'number', `${m.id} has no output price`)
    assert.equal(typeof m.context, 'number', `${m.id} has no context window`)
    assert.equal(typeof m.featured, 'boolean', `${m.id} does not say whether it is featured`)
    assert.ok(!seen.has(m.id), `${m.id} is listed twice`)
    seen.add(m.id)
  }
})

test('lookup by id finds an entry and misses cleanly', () => {
  assert.equal(knownModel('gemini-3.6-flash')?.provider, 'gemini')
  assert.equal(knownModel('a-model-nobody-shipped'), undefined)
  assert.equal(knownModel(''), undefined)
  assert.equal(knownModel(null), undefined)
})

test('every configurable type resolves to a family, or to none by name', () => {
  for (const type of Object.keys(TYPE_LABEL)) {
    const family = familyOf(type)
    assert.ok(family === '' || FAMILIES.includes(family), `${type} resolves to ${family}`)
  }
  // The CLI logins read a live catalog from their adapter; this table is not theirs.
  assert.equal(familyOf('claude_code'), '')
  assert.equal(familyOf('codex'), '')
})

test('the three OpenAI types share one family', () => {
  assert.equal(familyOf('openai'), 'openai')
  assert.equal(familyOf('openai_responses'), 'openai')
  assert.equal(familyOf('openai_subscription'), 'openai')
})

test('selection by type returns that family and nothing else', () => {
  const gemini = knownModelsFor('gemini')
  assert.ok(gemini.length)
  for (const m of gemini) assert.equal(m.provider, 'gemini')
  assert.deepEqual(knownModelsFor('openai_responses'), knownModelsFor('openai'))
  assert.deepEqual(knownModelsFor('claude_code'), [])
  assert.deepEqual(knownModelsFor('nonsense'), [])
})

test('featured is a subset of known, and every family features at least one', () => {
  for (const family of FAMILIES) {
    const featured = featuredModelsFor(family)
    assert.ok(featured.length, `${family} features nothing`)
    for (const m of featured) {
      assert.equal(m.featured, true)
      assert.ok(knownModel(m.id), `${m.id} is featured but not known`)
    }
  }
})

test('a price reads per million tokens, and a local model reads as free', () => {
  assert.match(priceLabel(knownModel('claude-sonnet-5')), /\$3.*\$15.*M/)
  assert.equal(priceLabel(knownModel('llama3.2')), 'Free')
  // Absence of a price is the signal that a name is newer than this table.
  assert.equal(priceLabel(undefined), '')
  assert.equal(priceLabel({}), '')
})

test('a context window reads in K or M', () => {
  assert.equal(contextLabel(knownModel('gemini-3.6-flash')), '1M context')
  assert.equal(contextLabel(knownModel('claude-sonnet-5')), '200K context')
  assert.equal(contextLabel(undefined), '')
  assert.equal(contextLabel({}), '')
})

// The anti-drift gate: a Template names a model this table describes, so a label and
// an id can no longer wander apart.
test('every template seeds a model the table knows', () => {
  for (const t of MODEL_TEMPLATES) {
    if (!t.model) continue
    assert.ok(knownModel(t.model), `${t.name} seeds ${t.model}, which the table has never heard of`)
  }
})

test('a template seeds a model of its own type family', () => {
  for (const t of MODEL_TEMPLATES) {
    if (!t.model) continue
    assert.equal(knownModel(t.model).provider, familyOf(t.type), `${t.name} seeds a foreign model`)
  }
})

// The drift this table exists to end: a label naming a release its id does not.
test('no label contradicts the version in its own id', () => {
  for (const m of KNOWN_MODELS) {
    const inId = m.id.match(/\d+\.\d+/)?.[0]
    if (!inId) continue
    const inLabel = m.label.match(/\d+\.\d+/)?.[0]
    if (!inLabel) continue
    assert.equal(inLabel, inId, `${m.label} is labelled a different release than ${m.id}`)
  }
})
