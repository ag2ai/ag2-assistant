// The model streams its A2UI payload as raw text; splitA2UIText decides what the
// chat shows while that happens. Run: node --test src/lib
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { a2uiComposingSurfaceId, a2uiValue, actionContext, applyA2UIMessage, bindingPath, childSlots, splitA2UIText, withA2UIValue } from './a2ui.ts'
import type { ThreadItem } from '../schemas/events.ts'

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

// ── Repeating one template over an array ────────────────────────────────────
// childSlots hands the renderer the id to draw and the data scope to draw it in.

const RUNS = {
  title: 'Runs this week',
  runs: [
    { day: 'Mon', distance: '8 km', done: true },
    { day: 'Wed', distance: '5 km', done: false },
    { day: 'Sat', distance: '21 km', done: false },
  ],
}
const TEMPLATE = { componentId: 'row', path: '/runs' }

test('a list bound to an array repeats its template once per item, in order', () => {
  assert.deepEqual(childSlots(TEMPLATE, RUNS), [
    { id: 'row', scope: '/runs/0' },
    { id: 'row', scope: '/runs/1' },
    { id: 'row', scope: '/runs/2' },
  ])
})

test('the same template layout serves an empty, a one-item and a many-item array', () => {
  const slots = (runs: unknown[]) => childSlots(TEMPLATE, { runs })
  assert.deepEqual(slots([]), [])
  assert.deepEqual(slots([{ day: 'Mon' }]), [{ id: 'row', scope: '/runs/0' }])
  assert.equal(slots(RUNS.runs).length, 3)
  // A path that holds no array at all draws nothing rather than throwing.
  assert.deepEqual(childSlots(TEMPLATE, {}), [])
  assert.deepEqual(childSlots({ componentId: 'row', path: '/title' }, RUNS), [])
})

test('growing the bound array grows the rendered rows, with no new layout', () => {
  const grown = { ...RUNS, runs: [...RUNS.runs, { day: 'Sun', distance: '3 km' }] }
  assert.equal(childSlots(TEMPLATE, grown).length, 4)
  assert.deepEqual(childSlots(TEMPLATE, { runs: [RUNS.runs[0]] }), [{ id: 'row', scope: '/runs/0' }])
})

test('an explicitly listed set of children keeps the scope it was drawn in', () => {
  assert.deepEqual(childSlots(['head', 'body'], RUNS), [
    { id: 'head', scope: '' },
    { id: 'body', scope: '' },
  ])
  assert.deepEqual(childSlots(['cell'], RUNS, '/runs/1'), [{ id: 'cell', scope: '/runs/1' }])
  assert.deepEqual(childSlots(undefined, RUNS), [])
  assert.deepEqual(childSlots([1, null, 'ok'], RUNS), [{ id: 'ok', scope: '' }])
})

test('a list nested inside a repeated row binds relative to its own item', () => {
  const data = { legs: [{ splits: ['a', 'b'] }, { splits: ['c'] }] }
  assert.deepEqual(childSlots({ componentId: 'split', path: './splits' }, data, '/legs/0'), [
    { id: 'split', scope: '/legs/0/splits/0' },
    { id: 'split', scope: '/legs/0/splits/1' },
  ])
  assert.deepEqual(childSlots({ componentId: 'split', path: './splits' }, data, '/legs/1'), [
    { id: 'split', scope: '/legs/1/splits/0' },
  ])
})

test('a binding inside a template resolves against the item, an absolute one against the model', () => {
  assert.equal(a2uiValue({ path: './day' }, RUNS, '/runs/2'), 'Sat')
  assert.equal(a2uiValue({ path: './done' }, RUNS, '/runs/0'), true)
  // The whole item, for an action context that carries the row it belongs to.
  assert.deepEqual(a2uiValue({ path: '.' }, RUNS, '/runs/1'), RUNS.runs[1])
  // An absolute path ignores the scope.
  assert.equal(a2uiValue({ path: '/title' }, RUNS, '/runs/2'), 'Runs this week')
  assert.equal(a2uiValue({ path: '/runs/0/day' }, RUNS, '/runs/2'), 'Mon')
  // Outside any repetition a relative path is just a top-level one.
  assert.equal(a2uiValue({ path: './title' }, RUNS), 'Runs this week')
})

test('a control inside a repeated row writes back to that item and no other', () => {
  assert.equal(bindingPath({ path: './done' }, '/runs/1'), '/runs/1/done')
  assert.equal(bindingPath({ path: '/title' }, '/runs/1'), '/title')
  assert.equal(bindingPath('literal', '/runs/1'), '')

  const next = withA2UIValue(RUNS, '/runs/1/done', true)
  assert.equal(a2uiValue({ path: './done' }, next, '/runs/1'), true)
  assert.deepEqual(next.runs, [RUNS.runs[0], { ...RUNS.runs[1], done: true }, RUNS.runs[2]])
  assert.ok(Array.isArray(next.runs), 'the bound array must stay an array')
  assert.equal(RUNS.runs[1].done, false, 'the durable surface payload is not mutated')
})

test('a data-model update grows the bound array the layout already repeats over', () => {
  const items: ThreadItem[] = []
  applyA2UIMessage(items, {
    version: 'v1.0',
    updateComponents: {
      surfaceId: 's1',
      components: [
        { id: 'root', component: 'Column', children: { componentId: 'row', path: '/trip/legs' } },
        { id: 'row', component: 'Text', text: { path: './day' } },
      ],
    },
  })
  const surface = items[0] as Extract<ThreadItem, { kind: 'a2ui' }>
  assert.deepEqual(childSlots(surface.component.children, surface.data), [])

  applyA2UIMessage(items, {
    version: 'v1.0',
    updateDataModel: { surfaceId: 's1', path: '/trip', value: { legs: [{ day: 'Mon' }, { day: 'Tue' }] } },
  })
  assert.equal(childSlots(surface.component.children, surface.data).length, 2)
  assert.equal(a2uiValue({ path: './day' }, surface.data, '/trip/legs/1'), 'Tue')
})

test('a button in a repeated row submits the item it belongs to', () => {
  const context = { run: { path: '.' }, day: { path: './day' }, title: { path: '/title' }, tag: 'literal' }

  assert.deepEqual(actionContext(context, RUNS, '/runs/1'), {
    run: RUNS.runs[1],
    day: 'Wed',
    title: 'Runs this week',
    tag: 'literal',
  })
  assert.deepEqual(actionContext([{ path: './day' }], RUNS, '/runs/2'), ['Sat'])
})

test('a write no row answers to is dropped rather than flattening the array', () => {
  assert.deepEqual(withA2UIValue(RUNS, '/runs/7/day', 'Sun'), RUNS)
  assert.deepEqual(withA2UIValue(RUNS, '/runs/day', 'Sun'), RUNS)
})

test('a nested data-model path writes nested, not under a slash-joined key', () => {
  const items: ThreadItem[] = []
  applyA2UIMessage(items, { version: 'v1.0', updateComponents: { surfaceId: 's1', components: [{ id: 'root' }] } })
  applyA2UIMessage(items, {
    version: 'v1.0',
    updateDataModel: { surfaceId: 's1', path: '/trip/legs', value: [{ day: 'Mon' }] },
  })
  const surface = items[0] as Extract<ThreadItem, { kind: 'a2ui' }>
  assert.deepEqual(surface.data.trip, { legs: [{ day: 'Mon' }] })
  assert.equal(surface.data['trip/legs'], undefined)
})

test('a card drawn as primitives is titled by the data model that arrives after it', () => {
  // The server draws a Card into Card/Column/List primitives and sends its fields as
  // data — the surface's title comes from that data, not from a Card type the
  // renderer would have to know.
  const items: ThreadItem[] = []
  applyA2UIMessage(items, {
    version: 'v1.0',
    updateComponents: {
      surfaceId: 's1',
      components: [
        { id: 'root', component: 'Card', child: 'root__body' },
        { id: 'root__body', component: 'Column', children: ['root__heading'] },
        { id: 'root__heading', component: 'Text', text: { path: '/title' } },
      ],
    },
  })
  const surface = items[0] as Extract<ThreadItem, { kind: 'a2ui' }>
  assert.equal(surface.title, 'Interactive view')

  applyA2UIMessage(items, {
    version: 'v1.0',
    updateDataModel: { surfaceId: 's1', path: '/title', value: 'Ship it' },
  })
  assert.equal(surface.title, 'Ship it')
  assert.equal(a2uiValue({ path: '/title' }, surface.data), 'Ship it')
})

test('two instances of one card read their own rows, not each other\'s', () => {
  // Nested instances are namespaced by their own id, so one card's template repeats
  // over its own array.
  const data = { _cards: { one: { items: ['a'] }, two: { items: ['b', 'c'] } } }

  assert.deepEqual(childSlots({ componentId: 'one__step', path: '/_cards/one/items' }, data), [
    { id: 'one__step', scope: '/_cards/one/items/0' },
  ])
  assert.equal(childSlots({ componentId: 'two__step', path: '/_cards/two/items' }, data).length, 2)
  assert.equal(a2uiValue({ path: '.' }, data, '/_cards/two/items/1'), 'c')
})
