// The brand-mark seam: given a key, what does the app draw? Run: node --test src/lib
// Path geometry is content, not behaviour — nothing here pins a `d` string. What is
// asserted is the shape of the answer: which brands carry a colour, which do not, and
// that an unknown key is survivable.
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { brandMark } from './brandMarks.js'
import { CATALOG } from './integrations.js'

// The provider types both logo maps key by. Spelled out rather than imported, because
// lib/llm.js and lib/live.js pull in Svelte stores and the transport layer; the point
// of the check is that this list and the seam agree, so the list is the fixture.
const TEXT_PROVIDERS = [
  'openai', 'openai_responses', 'openai_subscription',
  'anthropic', 'gemini', 'ollama', 'claude_code', 'codex',
]
const VOICE_PROVIDERS = ['gemini', 'openai']

test('every platform in the integrations catalogue resolves to a mark', () => {
  for (const entry of CATALOG) {
    const mark = brandMark(entry.id)
    assert.ok(mark, `${entry.id} has no brand mark`)
  }
})

test('every provider type in the text and voice lookups resolves to a mark', () => {
  for (const type of [...TEXT_PROVIDERS, ...VOICE_PROVIDERS]) {
    assert.ok(brandMark(type), `${type} has no brand mark`)
  }
})

test('the OpenAI surfaces and Codex share one mark; Claude Code shares Anthropic’s', () => {
  const openai = brandMark('openai')
  for (const type of ['openai_responses', 'openai_subscription', 'codex']) {
    assert.equal(brandMark(type), openai, `${type} is not drawn as OpenAI`)
  }
  assert.equal(brandMark('claude_code'), brandMark('anthropic'))
})

test('Gemini resolves as a gradient with more than one stop', () => {
  const mark = brandMark('gemini')
  assert.equal(mark.kind, 'gradient')
  assert.ok(mark.stops.length > 1, 'a one-stop gradient is a flat fill wearing a hat')
  for (const stop of mark.stops) assert.match(stop, /^#[0-9a-f]{6}$/i)
})

test('the monochrome brands resolve with no fill declared', () => {
  for (const key of ['github', 'openai', 'ollama']) {
    const mark = brandMark(key)
    assert.equal(mark.kind, 'mono')
    assert.equal(mark.fill, undefined, `${key} declares a fill`)
  }
})

test('the coloured brands resolve with a fill', () => {
  for (const key of ['telegram', 'discord', 'anthropic']) {
    const mark = brandMark(key)
    assert.equal(mark.kind, 'solid')
    assert.match(mark.fill, /^#[0-9a-f]{6}$/i)
  }
})

test('Slack and Google resolve as multi-part, each part carrying its own fill', () => {
  for (const key of ['slack', 'google']) {
    const mark = brandMark(key)
    assert.equal(mark.kind, 'multi')
    assert.ok(mark.viewBox, `${key} has no viewBox`)
    assert.ok(mark.parts.length > 1, `${key} is not multi-part`)
    for (const part of mark.parts) {
      assert.ok(part.path)
      assert.match(part.fill, /^#([0-9a-f]{3}|[0-9a-f]{6})$/i)
    }
  }
})

test('an unknown key resolves to nothing rather than throwing', () => {
  // Reachable by downgrading the app while a Connection names a platform a newer
  // version added — the crash this seam exists to prevent.
  assert.equal(brandMark('a-platform-from-the-future'), null)
  assert.equal(brandMark(undefined), null)
  assert.equal(brandMark(''), null)
})

test('no brand entry declares both a flat fill and a gradient', () => {
  const keys = [...CATALOG.map((e) => e.id), ...TEXT_PROVIDERS, ...VOICE_PROVIDERS]
  for (const key of keys) {
    const mark = brandMark(key)
    assert.ok(!(mark.fill && mark.stops), `${key} is both flat and gradient`)
  }
})
