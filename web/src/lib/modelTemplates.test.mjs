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

// An empty heading is a promise of options the page cannot keep.
test('no group in the order is empty', () => {
  for (const group of GROUP_ORDER) {
    const held = MODEL_TEMPLATES.filter((t) => TYPE_GROUP[t.type] === group)
    assert.ok(held.length, `${group} is a heading with nothing under it`)
  }
})

// The grouping rule, stated as an assertion: subscription-backed ⟺ that group.
test('the subscription-backed templates are exactly the subscription group', () => {
  for (const t of MODEL_TEMPLATES) {
    const subscriptionBacked = TYPE_CHIP[t.type] === 'OAuth' || TYPE_CHIP[t.type] === 'ACP'
    const grouped = TYPE_GROUP[t.type] === SUBSCRIPTION_GROUP
    assert.equal(grouped, subscriptionBacked, `${label(t)} breaks the grouping rule`)
  }
  const subscription = MODEL_TEMPLATES.filter((t) => TYPE_GROUP[t.type] === SUBSCRIPTION_GROUP)
  assert.equal(subscription.length, 3)
})

// It answers a question the user does not know to ask, so it cannot be scrolled to.
test('the subscription group comes first', () => {
  assert.equal(GROUP_ORDER[0], SUBSCRIPTION_GROUP)
})

test('every blurb says something the card label does not', () => {
  for (const t of MODEL_TEMPLATES) {
    assert.ok(t.blurb?.trim(), `${label(t)} has no blurb`)
    assert.notEqual(t.blurb.trim().toLowerCase(), label(t).trim().toLowerCase(), `${label(t)}’s blurb repeats its title`)
  }
})

// The first-party OpenAI path is Responses; `openai` now means a compatible endpoint.
test('no template offers the bare openai type without an endpoint', () => {
  for (const t of MODEL_TEMPLATES) {
    if (t.type === 'openai') assert.ok(t.base_url, `${label(t)} is a Chat Completions template in disguise`)
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
