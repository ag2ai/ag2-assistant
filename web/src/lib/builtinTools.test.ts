import { test } from 'node:test'
import assert from 'node:assert/strict'
import { BUILTIN_TOOL_TEXT, builtinToolText, builtinChip } from './builtinTools.ts'

// Mirrors assistant/builtin_tools.py's registry. The Python side is the authority
// on which tools exist and ships them as ids; this table only supplies the words,
// so the one drift it allows is an id with no entry. That is what this pins.
const OFFERED: Record<string, string[]> = {
  anthropic: ['web_search', 'web_fetch', 'code_execution'],
  openai_responses: ['web_search', 'code_execution'],
  gemini: ['web_search', 'web_fetch', 'code_execution'],
  openai: [],
  openai_subscription: [],
  ollama: [],
  claude_code: [],
  codex: [],
}

test('every tool the server can offer has words', () => {
  for (const [type, ids] of Object.entries(OFFERED)) {
    for (const id of ids) {
      const t = builtinToolText(type, id)
      assert.ok(t.label && t.label !== id, `${type}.${id} has no label`)
      assert.ok(t.description, `${type}.${id} has no description`)
    }
  }
})

test('no words are written for a tool its provider does not offer', () => {
  for (const key of Object.keys(BUILTIN_TOOL_TEXT)) {
    const [type, id] = [key.slice(0, key.indexOf('.')), key.slice(key.indexOf('.') + 1)]
    assert.ok(OFFERED[type]?.includes(id), `${key} is not in the registry`)
  }
})

test('the same id reads in each provider own words', () => {
  // One WebFetchTool class, but Gemini's maps to url_context and honours none of
  // its options — calling both "Web fetch" would paper over that.
  assert.equal(builtinToolText('anthropic', 'web_fetch').label, 'Web fetch')
  assert.equal(builtinToolText('gemini', 'web_fetch').label, 'URL context')
  assert.equal(builtinToolText('gemini', 'web_search').label, 'Google Search grounding')
  assert.equal(builtinToolText('openai_responses', 'code_execution').label, 'Code interpreter')
})

test('a tool that replaces a local one says so, and one that adds does not', () => {
  assert.match(builtinToolText('gemini', 'web_search').note!, /DuckDuckGo/)
  assert.match(builtinToolText('anthropic', 'web_fetch').note!, /page fetcher/)
  // Code execution adds a runner; its note explains the local sandbox stays.
  assert.match(builtinToolText('anthropic', 'code_execution').note!, /local sandbox/)
})

test('lookups stay total for an id this table has never heard of', () => {
  // The server is the authority on availability; a tool added there before its
  // words land here must still render, not vanish from the form.
  assert.deepEqual(builtinToolText('gemini', 'brand_new'), { label: 'brand_new', description: '' })
  assert.deepEqual(builtinToolText('no_such_type', 'web_search'), {
    label: 'web_search',
    description: '',
  })
})

test('row chips are short, and fall back to the id', () => {
  assert.equal(builtinChip('web_search'), 'search')
  assert.equal(builtinChip('web_fetch'), 'fetch')
  assert.equal(builtinChip('code_execution'), 'code')
  assert.equal(builtinChip('brand_new'), 'brand_new')
})
