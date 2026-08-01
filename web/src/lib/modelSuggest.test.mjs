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
  browserProbeRequest,
  parseCatalogPayload,
  probeStatusReason,
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

test('a chat model whose name merely mentions a tool is still offered', () => {
  // Search and computer-use are things a chat model does, not other kinds of model.
  const catalog = ['gpt-4o-search-preview', 'computer-use-preview']
  assert.deepEqual(ids(suggestModels({ type: 'openai', catalog })).sort(), catalog.sort())
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

// ---- A pasted key is the browser's to send (ADR 0024) --------------------------

test('a pasted key makes the probe the browser’s, not the gateway’s', () => {
  // The key never reaches our backend until the user commits to a Secret from it.
  for (const type of ['gemini', 'openai', 'openai_responses', 'anthropic']) {
    assert.equal(catalogSource(type, { hasPastedKey: true }), 'browser', type)
  }
})

test('a pasted key wins over a saved Secret while it is still unsaved', () => {
  assert.equal(catalogSource('openai', { hasCredential: true, hasPastedKey: true }), 'browser')
})

test('clearing the pasted key falls back to the Secret’s gateway probe', () => {
  assert.equal(catalogSource('openai', { hasCredential: true, hasPastedKey: false }), 'gateway')
})

test('a keyless local host stays the gateway’s even if a key was pasted', () => {
  // Ollama takes no key, and only the gateway can reach a host behind a bridge.
  assert.equal(catalogSource('ollama', { hasPastedKey: true }), 'gateway')
})

test('a type that can never be probed is not probed by the browser either', () => {
  assert.equal(catalogSource('openai_subscription', { hasPastedKey: true }), '')
  assert.equal(catalogSource('codex', { hasPastedKey: true }), '')
})

test('OpenAI takes the pasted key as a bearer token', () => {
  const req = browserProbeRequest({ type: 'openai', key: 'sk-test' })
  assert.equal(req.url, 'https://api.openai.com/v1/models')
  assert.equal(req.headers.Authorization, 'Bearer sk-test')
})

test('Gemini takes the pasted key in the query string', () => {
  const req = browserProbeRequest({ type: 'gemini', key: 'AIza-test' })
  assert.equal(req.url, 'https://generativelanguage.googleapis.com/v1beta/models?key=AIza-test')
  assert.deepEqual(req.headers, {})
})

test('a pasted key with URL-hostile characters still reaches Gemini intact', () => {
  const req = browserProbeRequest({ type: 'gemini', key: 'a+b/c=d&e' })
  assert.ok(req.url.endsWith('/models?key=a%2Bb%2Fc%3Dd%26e'))
})

test('Anthropic needs its own header, plus permission to be called from a browser', () => {
  const req = browserProbeRequest({ type: 'anthropic', key: 'sk-ant' })
  assert.equal(req.url, 'https://api.anthropic.com/v1/models')
  assert.equal(req.headers['x-api-key'], 'sk-ant')
  assert.ok(req.headers['anthropic-version'])
  assert.equal(req.headers['anthropic-dangerous-direct-browser-access'], 'true')
})

test('a key is never carried in a header Anthropic would ignore', () => {
  const req = browserProbeRequest({ type: 'anthropic', key: 'sk-ant' })
  assert.equal(req.headers.Authorization, undefined)
})

test('a custom endpoint is asked at its own address, not the vendor’s', () => {
  const req = browserProbeRequest({ type: 'openai', baseUrl: 'http://localhost:8080/v1/', key: 'k' })
  assert.equal(req.url, 'http://localhost:8080/v1/models')
})

test('with nothing pasted there is no browser request to make', () => {
  assert.equal(browserProbeRequest({ type: 'openai', key: '' }), null)
  assert.equal(browserProbeRequest({ type: 'openai', key: '   ' }), null)
})

test('the browser never builds a request for a type it cannot probe', () => {
  // Ollama is the gateway's, and a subscription has no key to ask with at all.
  for (const type of ['ollama', 'openai_subscription', 'codex', 'claude_code', '']) {
    assert.equal(browserProbeRequest({ type, key: 'k' }), null, type)
  }
})

test('an OpenAI payload parses to the ids it listed', () => {
  const ids_ = parseCatalogPayload('openai', { data: [{ id: 'gpt-5.4' }, { id: 'gpt-5.4-nano' }] })
  assert.deepEqual(ids_, ['gpt-5.4', 'gpt-5.4-nano'])
})

test('an Anthropic payload parses on the same shape as OpenAI’s', () => {
  assert.deepEqual(parseCatalogPayload('anthropic', { data: [{ id: 'claude-sonnet-5' }] }), ['claude-sonnet-5'])
})

test('Gemini names lose the prefix its API wraps them in', () => {
  const payload = { models: [{ name: 'models/gemini-3.6-flash' }] }
  assert.deepEqual(parseCatalogPayload('gemini', payload), ['gemini-3.6-flash'])
})

test('Gemini’s own metadata says what is a chat model', () => {
  const payload = { models: [
    { name: 'models/gemini-3.6-flash', supportedGenerationMethods: ['generateContent'] },
    { name: 'models/text-embedding-005', supportedGenerationMethods: ['embedContent'] },
  ] }
  assert.deepEqual(parseCatalogPayload('gemini', payload), ['gemini-3.6-flash'])
})

test('a Gemini entry declaring no methods is offered, never hidden', () => {
  const payload = { models: [{ name: 'models/gemini-9' }, { name: 'models/gemini-8', supportedGenerationMethods: [] }] }
  assert.deepEqual(parseCatalogPayload('gemini', payload), ['gemini-9', 'gemini-8'])
})

test('a payload in no shape we recognise is no model list', () => {
  // The endpoint answered, but it publishes nothing we can read as a catalog.
  for (const payload of [null, 'hello', {}, { data: 'nope' }, { models: 3 }]) {
    assert.equal(parseCatalogPayload('openai', payload), null)
    assert.equal(parseCatalogPayload('gemini', payload), null)
  }
})

test('entries with no id are dropped rather than offered blank', () => {
  assert.deepEqual(parseCatalogPayload('openai', { data: [{ id: '' }, null, { id: 'gpt-5.4' }] }), ['gpt-5.4'])
})

test('a rejected pasted key reads as unauthorized, not as a dead endpoint', () => {
  assert.equal(probeStatusReason(401), 'unauthorized')
  assert.equal(probeStatusReason(403), 'unauthorized')
})

test('an endpoint that answered an error publishes no list we can read', () => {
  assert.equal(probeStatusReason(404), 'no_list_endpoint')
  assert.equal(probeStatusReason(400), 'no_list_endpoint')
})

test('a provider having a bad moment is unreachable, not a missing list', () => {
  // no_list_endpoint is permanent and stops the user waiting; a 5xx or a rate
  // limit is worth retrying, so it must not be told in those words.
  for (const status of [429, 500, 502, 503]) {
    assert.equal(probeStatusReason(status), 'unreachable', String(status))
  }
})

test('a plain answer carries no reason at all', () => {
  assert.equal(probeStatusReason(200), '')
})
