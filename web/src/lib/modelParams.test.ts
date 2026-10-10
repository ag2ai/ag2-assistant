import { test } from 'node:test'
import assert from 'node:assert/strict'
import { MODEL_PARAM_TEXT, choiceLabel, modelParamText } from './modelParams.ts'

// Mirrors assistant/model_params.py's registry. The Python side is the authority
// on which parameters exist; this table only supplies the words.
const OFFERED: Record<string, string[]> = {
  openai: ['reasoning_effort'],
  gemini: ['thinking_level'],
  openai_responses: [],
  openai_subscription: [],
  anthropic: [],
  ollama: [],
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

test('the same setting reads in each provider own words', () => {
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
