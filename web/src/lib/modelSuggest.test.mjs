// The suggestion seam: which names the Model field offers, in what order, and how
// honestly each is adorned. No transport, no store, no browser.
// Run: npm test
import { test } from 'node:test'
import assert from 'node:assert/strict'
import {
  catalogNote,
  catalogSource,
  isNotChatModel,
  permanentNoCatalog,
  suggestModels,
} from './modelSuggest.js'
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

// ---- With a catalog: it decides membership, Known models only adorns ------------

test('a catalog decides which names are offered', () => {
  const rows = suggestModels({ type: 'ollama', catalog: ['llama3.2:latest', 'qwen3:8b'] })
  assert.deepEqual(ids(rows).sort(), ['llama3.2:latest', 'qwen3:8b'])
})

test('a Known model the catalog did not return is not offered', () => {
  // The table is not an inventory: a name the host does not have is not a name.
  const rows = suggestModels({ type: 'ollama', catalog: ['qwen3:8b'] })
  assert.deepEqual(ids(rows), ['qwen3:8b'])
})

test('a catalog name is never marked unverified — something confirmed it', () => {
  for (const row of suggestModels({ type: 'ollama', catalog: ['llama3.2', 'qwen3:8b'] })) {
    assert.equal(row.unverified, false)
  }
})

test('a catalog name the table recognises carries its price and context', () => {
  const row = suggestModels({ type: 'ollama', catalog: ['llama3.2'] })[0]
  assert.equal(row.label, 'Llama 3.2')
  assert.equal(row.price, 'Free')
  assert.equal(row.context, '128K context')
})

test('a catalog name the table has never heard of is offered plain', () => {
  // No price is the honest signal that the name is newer than this app — not a badge.
  const row = suggestModels({ type: 'ollama', catalog: ['qwen3:8b'] })[0]
  assert.deepEqual(row, { id: 'qwen3:8b', label: 'qwen3:8b', price: '', context: '', unverified: false })
})

test('featured names lead, then the rest the table knows, then the plain ones', () => {
  const rows = suggestModels({ type: 'anthropic', catalog: ['MiniMax-M2.5', 'zeta-9', 'claude-sonnet-5'] })
  assert.deepEqual(ids(rows), ['claude-sonnet-5', 'MiniMax-M2.5', 'zeta-9'])
})

test('typing filters catalog names too, by id and by label', () => {
  const catalog = ['llama3.2', 'qwen3:8b']
  assert.deepEqual(ids(suggestModels({ type: 'ollama', catalog, query: 'qwen' })), ['qwen3:8b'])
  assert.deepEqual(ids(suggestModels({ type: 'ollama', catalog, query: 'Llama 3' })), ['llama3.2'])
})

test('a catalog that answered empty offers nothing, not the table instead', () => {
  // An Ollama host with nothing pulled really has nothing; standing Known models in
  // would invent models the machine does not have.
  assert.deepEqual(suggestModels({ type: 'ollama', catalog: [] }), [])
})

test('no catalog at all is the case Known models stands in for', () => {
  assert.ok(suggestModels({ type: 'ollama', catalog: null }).length)
  assert.equal(suggestModels({ type: 'ollama', catalog: null })[0].unverified, true)
})

test('a duplicated catalog name is offered once', () => {
  const rows = suggestModels({ type: 'ollama', catalog: ['llama3.2', 'llama3.2', 'qwen3:8b'] })
  assert.deepEqual(ids(rows).sort(), ['llama3.2', 'qwen3:8b'])
})

test('blank catalog entries are dropped rather than offered as an empty row', () => {
  assert.deepEqual(ids(suggestModels({ type: 'ollama', catalog: ['', '  ', 'qwen3:8b'] })), ['qwen3:8b'])
})

// ---- Where a catalog comes from, and what is said when none came ----------------

test('a keyless local host is the gateway’s to probe, not the browser’s', () => {
  // The gateway is the side that can reach a host behind a Docker bridge.
  assert.equal(catalogSource('ollama'), 'gateway')
})

test('a type with no catalog to read asks nobody', () => {
  for (const type of ['claude_code', 'codex', 'openai_subscription', '', 'nonsense']) {
    assert.equal(catalogSource(type), '', type)
  }
})

test('a keyed type is the gateway’s once a credential exists', () => {
  for (const type of ['gemini', 'openai', 'openai_responses', 'anthropic']) {
    assert.equal(catalogSource(type, { hasCredential: true }), 'gateway', type)
  }
})

test('a keyed type with no credential yet is asked of nobody', () => {
  // Not a failure to reach anything — nothing to ask with. No request, no report.
  for (const type of ['gemini', 'openai', 'openai_responses', 'anthropic']) {
    assert.equal(catalogSource(type), '', type)
  }
})

test('a keyless custom endpoint is asked anyway', () => {
  assert.equal(catalogSource('openai', { hasEndpoint: true }), 'gateway')
})

test('the ChatGPT subscription can never have a catalog, and says which', () => {
  assert.equal(permanentNoCatalog('openai_subscription'), 'not_probeable')
  for (const type of ['gemini', 'ollama', 'anthropic', 'codex']) {
    assert.equal(permanentNoCatalog(type), '', type)
  }
})

test('each reason is worded separately, and names the provider where it can', () => {
  const notes = ['unauthorized', 'unreachable', 'no_list_endpoint', 'not_probeable']
    .map((reason) => catalogNote(reason, 'ollama'))
  assert.equal(new Set(notes).size, notes.length, 'two reasons read the same')
  for (const note of notes) assert.ok(note.trim())
  assert.match(catalogNote('unauthorized', 'gemini'), /Gemini/)
})

test('a rejected credential is named as rejected, before Test is ever pressed', () => {
  assert.match(catalogNote('unauthorized', 'anthropic'), /rejected/i)
})

test('an endpoint with no list says so as a property, not as a retryable failure', () => {
  const note = catalogNote('no_list_endpoint', 'openai')
  assert.match(note, /publishes no model list/)
  assert.doesNotMatch(note, /try again|retry/i)
})

test('every reason ends by saying known names are still on offer', () => {
  for (const reason of ['unauthorized', 'unreachable', 'no_list_endpoint', 'not_probeable']) {
    assert.match(catalogNote(reason, 'ollama'), /known names are offered below/i)
  }
})

test('a catalog that answered has nothing to explain', () => {
  assert.equal(catalogNote('', 'ollama'), '')
  assert.equal(catalogNote(undefined, 'ollama'), '')
})

// ---- The deny-list: it fails open, and only OpenAI needs it ---------------------

test('an OpenAI catalog containing an embeddings model does not offer it', () => {
  const rows = suggestModels({ type: 'openai', catalog: ['gpt-5.6-terra', 'text-embedding-3-large'] })
  assert.deepEqual(ids(rows), ['gpt-5.6-terra'])
})

test('speech, image, moderation and realtime models are dropped too', () => {
  const catalog = ['tts-1', 'whisper-1', 'dall-e-3', 'omni-moderation-latest', 'gpt-realtime', 'gpt-audio']
  assert.deepEqual(suggestModels({ type: 'openai_responses', catalog }), [])
})

test('a brand-new family the app has never heard of is still offered', () => {
  // The filter fails open: removal needs positive recognition, never a guess.
  const rows = suggestModels({ type: 'openai', catalog: ['gpt-7-quasar', 'o9-preview', 'zeta-9'] })
  assert.deepEqual(ids(rows).sort(), ['gpt-7-quasar', 'o9-preview', 'zeta-9'])
})

test('dated snapshots stay visible beside their floating alias', () => {
  const catalog = ['gpt-5.6-terra', 'gpt-5.6-terra-2026-04-01']
  assert.deepEqual(ids(suggestModels({ type: 'openai', catalog })).sort(), catalog.sort())
})

test('only the OpenAI family is deny-listed — the others answer with their own metadata', () => {
  for (const type of ['gemini', 'anthropic', 'ollama']) {
    assert.equal(isNotChatModel(type, 'text-embedding-005'), false, type)
  }
  assert.equal(isNotChatModel('openai', 'text-embedding-3-large'), true)
})

test('an Anthropic catalog is offered exactly as it came', () => {
  const catalog = ['claude-sonnet-5', 'claude-opus-4-8']
  assert.deepEqual(ids(suggestModels({ type: 'anthropic', catalog })).sort(), catalog.slice().sort())
})
