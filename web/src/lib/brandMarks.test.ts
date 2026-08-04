// The brand-mark seam: given a key, what does the app draw? Run: npm test
// Path geometry is content — nothing here pins a `d` string.
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { brandMark, type BrandMark } from './brandMarks.ts'
import { CATALOG, platformLabel } from './integrations.ts'
import { TYPE_LABEL, PROVIDER_LABEL } from './providerLabels.ts'

const TEXT_PROVIDERS = Object.keys(TYPE_LABEL)
const VOICE_PROVIDERS = Object.keys(PROVIDER_LABEL)

// Every assertion below is about a mark that must exist, so resolve-or-fail once
// rather than null-guarding in each test.
function markOf(key: string): BrandMark {
  const mark = brandMark(key)
  assert.ok(mark, `${key} has no brand mark`)
  return mark
}

test('every platform in the integrations catalogue resolves to a mark', () => {
  for (const entry of CATALOG) markOf(entry.id)
})

test('every provider type in the text and voice lookups resolves to a mark', () => {
  for (const type of [...TEXT_PROVIDERS, ...VOICE_PROVIDERS]) markOf(type)
})

test('the OpenAI surfaces and Codex share one mark; Claude Code shares Anthropic’s', () => {
  const openai = brandMark('openai')
  for (const type of ['openai_responses', 'openai_subscription', 'codex']) {
    assert.equal(brandMark(type), openai, `${type} is not drawn as OpenAI`)
  }
  assert.equal(brandMark('claude_code'), brandMark('anthropic'))
})

test('Gemini resolves as a gradient with more than one stop', () => {
  const mark = markOf('gemini')
  assert.equal(mark.kind, 'gradient')
  assert.ok(mark.stops, 'a gradient with no stops')
  assert.ok(mark.stops.length > 1, 'a one-stop gradient is a flat fill wearing a hat')
  for (const stop of mark.stops) assert.match(stop, /^#[0-9a-f]{6}$/i)
})

// No fill is how a natively monochrome brand asks for currentColor.
test('the monochrome brands resolve with no fill declared', () => {
  for (const key of ['github', 'openai', 'ollama']) {
    const mark = markOf(key)
    assert.equal(mark.kind, 'solid')
    assert.equal(mark.fill, undefined, `${key} declares a fill`)
  }
})

test('the coloured brands resolve with a fill', () => {
  for (const key of ['telegram', 'discord', 'anthropic']) {
    const mark = markOf(key)
    assert.equal(mark.kind, 'solid')
    assert.ok(mark.fill, `${key} declares no fill`)
    assert.match(mark.fill, /^#[0-9a-f]{6}$/i)
  }
})

test('Slack and Google resolve as multi-part, each part carrying its own fill', () => {
  for (const key of ['slack', 'google']) {
    const mark = markOf(key)
    assert.equal(mark.kind, 'multi')
    assert.ok(mark.viewBox, `${key} has no viewBox`)
    assert.ok(mark.parts, `${key} has no parts`)
    assert.ok(mark.parts.length > 1, `${key} is not multi-part`)
    for (const part of mark.parts) {
      assert.ok(part.path)
      assert.match(part.fill, /^#[0-9a-f]{6}$/i)
    }
  }
})

// White is a light-background assumption, not a brand colour: it fringes on dark.
test('no mark declares a white fill', () => {
  const keys = [...CATALOG.map((e) => e.id), ...TEXT_PROVIDERS, ...VOICE_PROVIDERS]
  for (const key of keys) {
    const mark = markOf(key)
    for (const fill of [mark.fill, ...(mark.parts || []).map((p) => p.fill), ...(mark.stops || [])]) {
      if (fill) assert.ok(!/^#(fff|ffffff)$/i.test(fill), `${key} declares a white fill`)
    }
  }
})

test('an unknown key resolves to nothing rather than throwing', () => {
  assert.equal(brandMark('a-platform-from-the-future'), null)
  assert.equal(brandMark(undefined), null)
  assert.equal(brandMark(''), null)
})

test('an unknown platform still has a label to show, and it is not blank', () => {
  for (const entry of CATALOG) assert.equal(platformLabel(entry.id), entry.label)
  assert.equal(platformLabel('a-platform-from-the-future'), 'a-platform-from-the-future')
})

test('no brand entry declares both a flat fill and a gradient', () => {
  const keys = [...CATALOG.map((e) => e.id), ...TEXT_PROVIDERS, ...VOICE_PROVIDERS]
  for (const key of keys) {
    const mark = markOf(key)
    assert.ok(!(mark.fill && mark.stops), `${key} is both flat and gradient`)
  }
})
