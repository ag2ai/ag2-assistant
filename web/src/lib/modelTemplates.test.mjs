// The template-grid seam: every starting point the Models page offers lands under
// a heading, wears a chip naming what the user brings, and draws a brand mark.
// Run: npm test
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { MODEL_TEMPLATES } from './modelTemplates.js'
import { TYPE_GROUP, TYPE_CHIP, GROUP_ORDER, SUBSCRIPTION_GROUP, TYPE_LABEL } from './providerLabels.js'
import { brandMark } from './brandMarks.js'

const CHIPS = ['API key', 'OAuth', 'ACP', 'no key']
const label = (t) => t.card || t.name

test('every template lands under a heading the page actually renders', () => {
  for (const t of MODEL_TEMPLATES) {
    const group = TYPE_GROUP[t.type]
    assert.ok(group, `${label(t)} (${t.type}) has no group`)
    assert.ok(GROUP_ORDER.includes(group), `${label(t)} sits in an unrendered group ${group}`)
  }
})

test('every template wears one of the four chips', () => {
  for (const t of MODEL_TEMPLATES) {
    const chip = TYPE_CHIP[t.type]
    assert.ok(chip, `${label(t)} (${t.type}) has no chip`)
    assert.ok(CHIPS.includes(chip), `${label(t)} wears an unknown chip ${chip}`)
  }
})

test('no group in the order is empty', () => {
  for (const group of GROUP_ORDER) {
    const held = MODEL_TEMPLATES.filter((t) => TYPE_GROUP[t.type] === group)
    assert.ok(held.length, `${group} is a heading with nothing under it`)
  }
})

// The grouping rule, stated as an assertion: the three that need no API key sit
// under that heading, and the other six sit under their vendor's.
const NO_KEY_CARDS = ['OpenAI · Sign in with ChatGPT', 'Claude Code · CLI login', 'Codex · CLI login']

test('the templates needing no API key are exactly the subscription group', () => {
  const grouped = MODEL_TEMPLATES.filter((t) => TYPE_GROUP[t.type] === SUBSCRIPTION_GROUP)
  assert.deepEqual(grouped.map(label), NO_KEY_CARDS)
  for (const t of MODEL_TEMPLATES) {
    if (NO_KEY_CARDS.includes(label(t))) continue
    assert.notEqual(TYPE_GROUP[t.type], SUBSCRIPTION_GROUP, `${label(t)} needs a key and is grouped as if it does not`)
    assert.equal(TYPE_CHIP[t.type] === 'OAuth' || TYPE_CHIP[t.type] === 'ACP', false, `${label(t)} wears a subscription chip outside the group`)
  }
})

test('the subscription group comes first', () => {
  assert.equal(GROUP_ORDER[0], SUBSCRIPTION_GROUP)
})

test('every blurb says something the card label does not', () => {
  for (const t of MODEL_TEMPLATES) {
    assert.ok(t.blurb?.trim(), `${label(t)} has no blurb`)
    const blurb = t.blurb.trim().toLowerCase()
    // A blurb the title already contains — `Claude` under `Claude Code` — adds nothing.
    assert.ok(!label(t).trim().toLowerCase().includes(blurb), `${label(t)}’s blurb repeats its title`)
  }
})

// The first-party OpenAI path is Responses; `openai` now means a compatible endpoint.
test('no template offers the bare openai type without an endpoint', () => {
  for (const t of MODEL_TEMPLATES) {
    if (t.type === 'openai') assert.ok(t.base_url, `${label(t)} is a Chat Completions template in disguise`)
  }
})

// "compatible" is a Template's word — the card you look for when you have your own
// server. A type label names a wire, so it never says it.
test('“compatible” names a card, never a type', () => {
  for (const [type, text] of Object.entries(TYPE_LABEL)) {
    assert.ok(!text.toLowerCase().includes('compatible'), `${type} is labelled as a deployment, not a wire`)
  }
})

test('the two compatible cards are a matched pair', () => {
  const compatible = MODEL_TEMPLATES.filter((t) => label(t).endsWith('-compatible'))
  assert.deepEqual(compatible.map(label), ['OpenAI-compatible', 'Anthropic-compatible'])
  assert.equal(TYPE_CHIP[compatible[0].type], TYPE_CHIP[compatible[1].type])
  for (const t of compatible) assert.ok(t.base_url, `${label(t)} seeds no endpoint`)
})

test('every template draws a brand mark and names a known type', () => {
  for (const t of MODEL_TEMPLATES) {
    assert.ok(brandMark(t.type), `${label(t)} has no brand mark`)
    assert.ok(TYPE_LABEL[t.type], `${label(t)} has no type label`)
  }
})
