// The suggestion seam: which names the Model field offers, in what order, and how
// honestly each is adorned. No transport, no store, no browser.
// Run: npm test
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { suggestModels } from './modelSuggest.js'
import { KNOWN_MODELS, knownModel } from './knownModels.js'

const ids = (rows) => rows.map((r) => r.id)

test('with no catalog, the type’s Known models are offered', () => {
  const rows = suggestModels({ type: 'gemini' })
  assert.ok(rows.length)
  for (const row of rows) assert.equal(knownModel(row.id).provider, 'gemini')
})

test('with no catalog, every row is marked unverified', () => {
  // Nothing has confirmed these names exist, and the user is not told otherwise.
  for (const row of suggestModels({ type: 'anthropic' })) assert.equal(row.unverified, true)
})

test('featured names rank ahead of the rest', () => {
  const rows = suggestModels({ type: 'anthropic' })
  const featured = rows.filter((r) => knownModel(r.id).featured)
  assert.deepEqual(ids(rows).slice(0, featured.length), ids(featured))
})

test('a row carries its price and context window ready to render', () => {
  const row = suggestModels({ type: 'anthropic' }).find((r) => r.id === 'claude-sonnet-5')
  assert.equal(row.label, 'Claude Sonnet 5')
  assert.match(row.price, /\$3.*\$15/)
  assert.equal(row.context, '200K context')
})

test('typing filters by substring of the id', () => {
  assert.deepEqual(ids(suggestModels({ type: 'openai', query: 'nano' })), ['gpt-5.4-nano'])
})

test('typing filters by substring of the label, ignoring case', () => {
  const rows = suggestModels({ type: 'anthropic', query: 'HAIKU' })
  assert.deepEqual(ids(rows), ['claude-haiku-4.5'])
})

test('a query nothing matches offers nothing rather than everything', () => {
  assert.deepEqual(suggestModels({ type: 'gemini', query: 'zzz-no-such-model' }), [])
})

test('surrounding whitespace in the query is not a failure to match', () => {
  assert.deepEqual(ids(suggestModels({ type: 'openai', query: '  nano  ' })), ['gpt-5.4-nano'])
})

test('the three OpenAI types see one list, including the subscription one', () => {
  const chat = ids(suggestModels({ type: 'openai' }))
  assert.deepEqual(ids(suggestModels({ type: 'openai_responses' })), chat)
  assert.deepEqual(ids(suggestModels({ type: 'openai_subscription' })), chat)
})

test('the CLI-login types are offered nothing — their adapter is the authority', () => {
  assert.deepEqual(suggestModels({ type: 'codex' }), [])
  assert.deepEqual(suggestModels({ type: 'claude_code' }), [])
})

test('an unknown type offers nothing rather than every model there is', () => {
  assert.deepEqual(suggestModels({ type: '' }), [])
  assert.deepEqual(suggestModels({ type: 'not-a-type' }), [])
})

test('no row is offered twice, whatever the ranking did', () => {
  const seen = new Set()
  for (const row of suggestModels({ type: 'openai' })) {
    assert.ok(!seen.has(row.id), `${row.id} is offered twice`)
    seen.add(row.id)
  }
})

test('the table is offered whole — ranking hides nothing', () => {
  const offered = ids(suggestModels({ type: 'gemini' })).sort()
  const known = KNOWN_MODELS.filter((m) => m.provider === 'gemini').map((m) => m.id).sort()
  assert.deepEqual(offered, known)
})
