// The API-interface seam: a Text model picks its wire exactly when it names its own
// endpoint. Run: npm test
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { API_INTERFACES, usesBaseUrl, offersApiInterface, settleWithoutBaseUrl } from './apiInterface.ts'
import { TYPE_LABEL, TYPE_GROUP } from './providerLabels.ts'

// Every configuration the app supports — read from the presentation lookup so a
// ninth type added later is covered here without anyone remembering to add it.
const ALL_TYPES = Object.keys(TYPE_LABEL)

test('no Base URL, no choice — for every type the app supports', () => {
  for (const type of ALL_TYPES) {
    assert.equal(offersApiInterface(type, ''), false, `${type} offers a wire with no endpoint`)
    assert.equal(offersApiInterface(type, undefined), false, `${type} offers a wire with no endpoint`)
  }
})

test('a whitespace-only Base URL is no Base URL', () => {
  for (const type of ALL_TYPES) {
    assert.equal(offersApiInterface(type, '   '), false, `${type} treats blanks as an endpoint`)
    assert.equal(offersApiInterface(type, '\n\t '), false, `${type} treats blanks as an endpoint`)
  }
})

test('naming an endpoint reveals the choice — and only for the endpoint-addressable types', () => {
  for (const type of ALL_TYPES) {
    const shown = offersApiInterface(type, 'http://localhost:8080/v1')
    assert.equal(shown, (API_INTERFACES as readonly string[]).includes(type), `${type} is offered the wrong way round`)
  }
})

// Ollama types a local address into a *different* field; a daemon on localhost is
// not a server speaking somebody else's wire. Excluded by type, deliberately.
test('Ollama, Gemini and the CLI logins can never reach the choice', () => {
  for (const type of ['ollama', 'gemini', 'openai_subscription', 'claude_code', 'codex']) {
    assert.equal(offersApiInterface(type, 'http://localhost:11434'), false, `${type} reached the choice`)
  }
})

test('the offer is exactly three surfaces, Responses first', () => {
  assert.deepEqual(API_INTERFACES, ['openai_responses', 'openai', 'anthropic'])
})

// One membership, two questions: a type shows a Base URL field exactly when that
// URL could reveal a wire choice — so the field can never appear with no control.
test('the types that take a Base URL are the types that can choose a wire', () => {
  for (const type of ALL_TYPES) {
    assert.equal(usesBaseUrl(type), (API_INTERFACES as readonly string[]).includes(type), `${type} shows the wrong endpoint field`)
    if (usesBaseUrl(type)) assert.ok(offersApiInterface(type, 'http://x/v1'), `${type} takes a URL and offers nothing`)
  }
})

test('every offerable surface can be presented', () => {
  for (const type of API_INTERFACES) {
    assert.ok(TYPE_LABEL[type], `${type} is offerable and unlabelled`)
    assert.ok(TYPE_GROUP[type], `${type} is offerable and ungrouped`)
  }
})

test('withdrawing the endpoint settles Chat Completions onto OpenAI’s own surface', () => {
  assert.equal(settleWithoutBaseUrl('openai'), 'openai_responses')
})

test('withdrawing the endpoint leaves every other type alone', () => {
  for (const type of ALL_TYPES) {
    if (type === 'openai') continue
    assert.equal(settleWithoutBaseUrl(type), type, `${type} was moved for no reason`)
  }
})

test('settling is idempotent', () => {
  for (const type of ALL_TYPES) {
    const once = settleWithoutBaseUrl(type)
    assert.equal(settleWithoutBaseUrl(once), once, `${type} keeps moving`)
  }
})

test('settling always lands on a type the app supports', () => {
  for (const type of ALL_TYPES) {
    assert.ok(TYPE_LABEL[settleWithoutBaseUrl(type)], `${type} settles onto nothing`)
  }
})
