// The model streams its A2UI payload as raw text; splitA2UIText decides what the
// chat shows while that happens. Run: node --test src/lib
import { test } from 'node:test'
import assert from 'node:assert/strict'
import {
  a2uiComposingSurfaceId,
  a2uiValue,
  isGenericSurfaceTitle,
  splitA2UIText,
  withA2UIValue,
} from './a2ui.ts'
import { m } from '../paraglide/messages.js'
import { locales } from '../paraglide/runtime.js'

const PROSE = "Here's the current tech picture on OzBargain."
const BATCH =
  '[{"version":"v1.0","createSurface":{"surfaceId":"oz-tech","catalogId":"https://ag2.ai/assistant/a2ui/catalog.json"}},' +
  '{"version":"v1.0","updateComponents":{"surfaceId":"oz-tech","components":[{"id":"root","component":"NewsDigest","topic":"Tech","stories":[]}]}}]'

test('plain prose passes through untouched', () => {
  assert.deepEqual(splitA2UIText(PROSE), { text: PROSE, composing: false })
})

test('a complete bare payload is stripped, leaving only the prose', () => {
  assert.deepEqual(splitA2UIText(`${PROSE}\n${BATCH}`), { text: PROSE, composing: false })
})

test('a complete <a2ui-json> wrapped payload is stripped', () => {
  const text = `${PROSE}\n<a2ui-json>${BATCH}</a2ui-json>\nDone.`
  assert.deepEqual(splitA2UIText(text), { text: `${PROSE}\n\nDone.`, composing: false })
})

test('a payload mid-stream is hidden and flagged composing, at every prefix', () => {
  // Every truncation of the payload — including the first few characters, before any
  // A2UI key has been typed — must hide rather than leak into the prose.
  for (let n = 1; n <= BATCH.length; n++) {
    const { text, composing } = splitA2UIText(`${PROSE}\n${BATCH.slice(0, n)}`)
    assert.equal(text, PROSE, `leaked at prefix length ${n}: ${JSON.stringify(text)}`)
    assert.equal(composing, n < BATCH.length, `composing wrong at prefix length ${n}`)
  }
})

test('an unfinished <a2ui-json> block is hidden and flagged composing', () => {
  const { text, composing } = splitA2UIText(`${PROSE}\n<a2ui-json>[{"version":"v1.0","crea`)
  assert.equal(text, PROSE)
  assert.equal(composing, true)
})

test('a payload streaming with no prose yet leaves nothing to render', () => {
  assert.deepEqual(splitA2UIText('[{"version":"v1.0","createSurface":{"surf'), {
    text: '',
    composing: true,
  })
})

test('identifies the surface being updated while an A2UI payload streams', () => {
  assert.equal(a2uiComposingSurfaceId(BATCH.slice(0, 80)), 'oz-tech')
  assert.equal(a2uiComposingSurfaceId(BATCH), null)
  assert.equal(a2uiComposingSurfaceId('ordinary prose'), null)
})

test('non-A2UI JSON in the reply is preserved, complete or not', () => {
  const complete = 'The config is {"retries": 3, "mode": "fast"} — use it.'
  assert.deepEqual(splitA2UIText(complete), { text: complete, composing: false })
  const partial = 'The config is {"retries": 3, "mo'
  assert.deepEqual(splitA2UIText(partial), { text: partial, composing: false })
})

test('resolves and updates JSON Pointer data bindings for interactive components', () => {
  const data = { checklist: { done: false }, 'a/b': true }

  assert.equal(a2uiValue({ path: '/checklist/done' }, data), false)
  assert.equal(a2uiValue({ path: '/a~1b' }, data), true)
  assert.deepEqual(withA2UIValue(data, '/checklist/done', true), {
    checklist: { done: true },
    'a/b': true,
  })
  assert.deepEqual(data, { checklist: { done: false }, 'a/b': true })
})

test('markdown lists and other brackets are not mistaken for a payload', () => {
  const md = 'See [the deals](https://ozbargain.com.au) and {this}.'
  assert.deepEqual(splitA2UIText(md), { text: md, composing: false })
})

test('prose written after a finished payload still renders', () => {
  const { text, composing } = splitA2UIText(`${BATCH}\nThat's the picture.`)
  assert.equal(text, "That's the picture.")
  assert.equal(composing, false)
})

// A surface whose title is only our own "structured answer" fallback carries nothing
// worth drawing, and A2UISurface suppresses the empty card by asking this. The title is
// resolved when the surface is folded, so the guard has to recognise the fallback in
// EVERY language — matching English alone renders an empty card to everyone else.
test('the generic-title guard recognises our fallback in every language', () => {
  for (const locale of locales) {
    const title = m.a2ui_title_structured_answer({}, { locale })
    assert.equal(isGenericSurfaceTitle(title), true, `not matched in ${locale}: ${title}`)
  }
})

test('a missing title is generic; a real one is not', () => {
  assert.equal(isGenericSurfaceTitle(''), true)
  assert.equal(isGenericSurfaceTitle(null), true)
  assert.equal(isGenericSurfaceTitle('  Structured Answer  '), true)
  assert.equal(isGenericSurfaceTitle('Q3 revenue decision'), false)
  assert.equal(isGenericSurfaceTitle(m.a2ui_title_news_digest()), false)
})
