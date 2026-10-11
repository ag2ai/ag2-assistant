import { test } from 'node:test'
import assert from 'node:assert/strict'
import { MODEL_PARAM_TEXT, advancedKeys, choiceLabel, modelParamText, parseParam } from './modelParams.ts'

// Mirrors assistant/model_params.py's registry. The Python side is the authority
// on which parameters exist; this table only supplies the words.
const OFFERED: Record<string, string[]> = {
  openai: ['reasoning_effort', 'temperature', 'max_tokens'],
  openai_responses: ['temperature', 'max_output_tokens'],
  gemini: ['thinking_level', 'temperature', 'max_output_tokens'],
  anthropic: ['temperature', 'max_tokens'],
  ollama: ['temperature', 'max_tokens'],
  openai_subscription: [],
  claude_code: [],
  codex: [],
}

test('every parameter the server can offer has words', () => {
  for (const [type, ids] of Object.entries(OFFERED)) {
    for (const id of ids) {
      const t = modelParamText(type, id)
      assert.ok(t.label && t.label !== id, `${type}.${id} has no label`)
      assert.ok(t.description, `${type}.${id} has no description`)
    }
  }
})

test('no words are written for a parameter its type does not offer', () => {
  for (const key of Object.keys(MODEL_PARAM_TEXT)) {
    const [type, id] = [key.slice(0, key.indexOf('.')), key.slice(key.indexOf('.') + 1)]
    assert.ok(OFFERED[type]?.includes(id), `${key} is not in the registry`)
  }
})

test('the same parameter reads in each provider own words', () => {
  assert.equal(modelParamText('openai', 'reasoning_effort').label, 'Reasoning effort')
  assert.equal(modelParamText('gemini', 'thinking_level').label, 'Thinking level')
})

test('lookups stay total for an id this table has never heard of', () => {
  assert.deepEqual(modelParamText('gemini', 'brand_new'), { label: 'brand_new', description: '' })
})

test('a level reads as a word, and an unknown one as itself capitalised', () => {
  assert.equal(choiceLabel('xhigh'), 'Extra high')
  assert.equal(choiceLabel('medium'), 'Medium')
  assert.equal(choiceLabel('brand_new'), 'Brand_new')
})

test('the output limit says what it counts on the provider that counts thinking', () => {
  assert.match(modelParamText('gemini', 'max_output_tokens').description, /thinking/)
  assert.doesNotMatch(modelParamText('anthropic', 'max_tokens').description, /thinking/)
})

test('a typed number becomes a value, and an emptied field becomes no value', () => {
  assert.equal(parseParam('float', '0.7'), 0.7)
  assert.equal(parseParam('int', '8000'), 8000)
  assert.equal(parseParam('int', '  '), undefined)
  assert.equal(parseParam('choice', 'high'), 'high')
  assert.equal(parseParam('choice', ''), undefined)
})

test('the Advanced box names the parameters it overrides', () => {
  assert.deepEqual([...advancedKeys('{"temperature": 0.1, "seed": 7}')], ['temperature', 'seed'])
})

test('Advanced extra_body overrides name the affected controls', () => {
  const keys = advancedKeys('{"extra_body":{"temperature":0.2,"max_tokens":128}}')
  assert.ok(keys.has('temperature'))
  assert.ok(keys.has('max_tokens'))
})

test('Advanced extra_body and top-level overrides share one set of names', () => {
  assert.deepEqual(
    [...advancedKeys('{"temperature":0.7,"extra_body":{"temperature":0.2,"max_tokens":128}}')],
    ['temperature', 'extra_body', 'max_tokens'],
  )
})

test('an extra_body that is not an object names no additional overrides', () => {
  for (const extra_body of [null, [], 7, 'temperature']) {
    assert.deepEqual([...advancedKeys(JSON.stringify({ seed: 7, extra_body }))], ['seed', 'extra_body'])
  }
})

test('an Advanced box that is empty or not an object overrides nothing', () => {
  assert.equal(advancedKeys('').size, 0)
  assert.equal(advancedKeys('{"temperature":').size, 0)
  assert.equal(advancedKeys('[1, 2]').size, 0)
})
