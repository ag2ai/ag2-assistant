// The model streams its A2UI payload as raw text; splitA2UIText decides what the
// chat shows while that happens. Run: node --test src/lib
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { a2uiComposingSurfaceId, a2uiIconName, a2uiLink, a2uiPresent, a2uiText, a2uiTone, a2uiValue, actionContext, applyA2UIMessage, bindingPath, childSlots, metricParts, sparkPath, splitA2UIText, withA2UIValue } from './a2ui.ts'
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

// ── The Card vocabulary: repetition offsets, tone, sparklines, metrics ───────

test('a list can skip the items a layout already drew on its own', () => {
  const data = { quotes: [{ symbol: 'NVDA' }, { symbol: 'AAPL' }, { symbol: 'MSFT' }] }

  const slots = childSlots({ componentId: 'mover', path: '/quotes', start: 1 }, data)

  assert.deepEqual(slots, [
    { id: 'mover', scope: '/quotes/1' },
    { id: 'mover', scope: '/quotes/2' },
  ])
})

test('a list whose every item is skipped draws nothing', () => {
  const data = { quotes: [{ symbol: 'NVDA' }] }

  assert.deepEqual(childSlots({ componentId: 'mover', path: '/quotes', start: 1 }, data), [])
})

test('a rising value is toned positive and a falling one negative, from the data', () => {
  const data = { quotes: [{ changePercent: 0.76 }, { changePercent: -0.8 }, { changePercent: 0 }] }

  assert.equal(a2uiTone({ path: './changePercent' }, data, '/quotes/0'), 'positive')
  assert.equal(a2uiTone({ path: './changePercent' }, data, '/quotes/1'), 'negative')
  assert.equal(a2uiTone({ path: './changePercent' }, data, '/quotes/2'), 'neutral')
})

test('a tone named in a layout is taken as written, and anything else is neutral', () => {
  assert.equal(a2uiTone('muted'), 'muted')
  assert.equal(a2uiTone('accent'), 'accent')
  assert.equal(a2uiTone('chartreuse'), 'neutral')
  assert.equal(a2uiTone(undefined), 'neutral')
})

test('a sparkline maps a normalised series across its box, ending on the last point', () => {
  const path = sparkPath([0, 100], 100, 100)

  assert.ok(path)
  assert.equal(path.line, 'M3.0,97.0 L97.0,3.0')
  assert.equal(path.endX, '97.0')
  assert.equal(path.endY, '3.0')
  assert.ok(path.area.endsWith('L97.0,97.0 L3.0,97.0 Z'))
})

test('a series too short to draw a line is no sparkline at all', () => {
  assert.equal(sparkPath([50], 100, 100), null)
  assert.equal(sparkPath(undefined, 100, 100), null)
})

test('a metric signs and formats its delta, both absolute and percent', () => {
  const data = { price: 193.99, change: 1.46, changePercent: 0.76, currency: 'USD' }
  const metric = metricParts(
    {
      component: 'Metric',
      value: { path: '/price' },
      unit: { path: '/currency' },
      delta: { path: '/change' },
      deltaPercent: { path: '/changePercent' },
    },
    data,
  )

  assert.equal(metric.value, '193.99')
  assert.equal(metric.unit, 'USD')
  assert.equal(metric.arrow, '▲')
  assert.equal(metric.delta, '+1.46 (+0.76%)')
})

test('a falling metric carries the minus sign it was given', () => {
  const data = { price: 281.51, changePercent: -0.8 }
  const metric = metricParts(
    { component: 'Metric', value: { path: '/price' }, deltaPercent: { path: '/changePercent' } },
    data,
  )

  assert.equal(metric.value, '281.51')
  assert.equal(metric.arrow, '▼')
  assert.equal(metric.delta, '-0.80%')
})

test('a metric with no movement to report is just its value', () => {
  const metric = metricParts({ component: 'Metric', value: { path: '/price' } }, { price: 1234.5 })

  assert.equal(metric.value, '1,234.50')
  assert.equal(metric.arrow, '')
  assert.equal(metric.delta, '')
})

test('a component conditional on absent data is not drawn', () => {
  const data = { quotes: [{ symbol: 'NVDA' }], source: 'Yahoo Finance', tally: 0 }

  assert.equal(a2uiPresent({ path: '/quotes/1' }, data), false)
  assert.equal(a2uiPresent({ path: '/missing' }, data), false)
  assert.equal(a2uiPresent({ path: '/source' }, data), true)
  // Nothing is not the same as zero: a metric of 0 is still a metric.
  assert.equal(a2uiPresent({ path: '/tally' }, data), true)
})

test('a component with no condition is always drawn', () => {
  assert.equal(a2uiPresent(undefined, {}), true)
})

test('a condition bound to an array follows whether the array has rows', () => {
  assert.equal(a2uiPresent({ path: '/rows' }, { rows: [] }), false)
  assert.equal(a2uiPresent({ path: '/rows' }, { rows: ['one'] }), true)
})

test('a text names the label its value stands for, so a stored code still reads', () => {
  const component = {
    component: 'Text',
    text: { path: '/status' },
    map: { open: 'Market open', closed: 'Market closed' },
  }

  assert.equal(a2uiText(component, { status: 'open' }), 'Market open')
  assert.equal(a2uiText(component, { status: 'closed' }), 'Market closed')
  // A value the map does not name is printed as it is, not swallowed.
  assert.equal(a2uiText(component, { status: 'halted' }), 'halted')
  assert.equal(a2uiText(component, {}), '')
})

test('a text formats a timestamp instead of printing the ISO string', () => {
  const at = new Date()
  at.setHours(9, 5, 0, 0)
  const shown = a2uiText({ component: 'Text', text: { path: '/at' }, format: 'time' }, { at: at.toISOString() })

  assert.match(shown, /9:05/)
  assert.equal(a2uiText({ component: 'Text', text: { path: '/at' }, format: 'time' }, {}), '')
})

test('a text with nothing bound prints nothing', () => {
  assert.equal(a2uiText({ component: 'Text', text: { path: '/gone' } }, {}), '')
  assert.equal(a2uiText({ component: 'Text', text: 'Movers' }, {}), 'Movers')
})

test('a condition bound to false is absent, a condition bound to zero is not', () => {
  assert.equal(a2uiPresent({ path: '/flag' }, { flag: false }), false)
  assert.equal(a2uiPresent({ path: '/flag' }, { flag: true }), true)
})

test('a condition can name alternatives, and any one of them is enough', () => {
  assert.equal(a2uiPresent([{ path: '/source' }, { path: '/asOf' }], { asOf: '2026-01-01' }), true)
  assert.equal(a2uiPresent([{ path: '/source' }, { path: '/asOf' }], { source: 'Yahoo' }), true)
  assert.equal(a2uiPresent([{ path: '/source' }, { path: '/asOf' }], {}), false)
})

// ── Card links (ticket 07) ──────────────────────────────────────────────────

const KNOWN = { tasks: ['t_a1b2c3'], chats: ['web-7f3a'] }

test('a row names the task it describes, read off the row it is drawn in', () => {
  const data = { tasks: [{ id: 't_a1b2c3' }] }

  assert.deepEqual(a2uiLink({ component: 'Link', task: { path: './id' } }, data, '/tasks/0', KNOWN), {
    kind: 'task',
    value: 't_a1b2c3',
  })
})

test('a link to a task or a chat that is no longer there is no link at all', () => {
  assert.equal(a2uiLink({ component: 'Link', task: 't_gone' }, {}, '', KNOWN), null)
  assert.equal(a2uiLink({ component: 'Link', chat: 'web-gone' }, {}, '', KNOWN), null)
  assert.deepEqual(a2uiLink({ component: 'Link', chat: 'web-7f3a' }, {}, '', KNOWN), {
    kind: 'chat',
    value: 'web-7f3a',
  })
})

test('a link waits for the lists rather than reading "not polled yet" as gone', () => {
  const cold = { tasks: null, chats: null }

  assert.deepEqual(a2uiLink({ component: 'Link', task: 't_a1b2c3' }, {}, '', cold), {
    kind: 'task',
    value: 't_a1b2c3',
  })
})

test('a file and a folder are linked by path, which the shell does not list', () => {
  assert.deepEqual(a2uiLink({ component: 'Link', file: 'reports/q3.md' }, {}, '', KNOWN), {
    kind: 'file',
    value: 'reports/q3.md',
  })
  assert.deepEqual(a2uiLink({ component: 'Link', folder: 'reports' }, {}, '', KNOWN), {
    kind: 'folder',
    value: 'reports',
  })
})

test('an external link is followed only on http(s)', () => {
  const url = (value: string) => a2uiLink({ component: 'Link', url: value }, {}, '', KNOWN)

  assert.deepEqual(url('https://mail.google.com/mail/u/0/#all/19'), {
    kind: 'url',
    value: 'https://mail.google.com/mail/u/0/#all/19',
  })
  assert.equal(url('javascript:alert(1)'), null)
  assert.equal(url('data:text/html,<script>'), null)
})

test('a link whose target is not in the data points at nothing', () => {
  const layout = { component: 'Link', url: { path: './joinUrl' } }

  assert.equal(a2uiLink(layout, { events: [{ title: 'Sync' }] }, '/events/0', KNOWN), null)
})

test('a status word picks the tone its card names for it', () => {
  const tone = { path: './status', map: { active: 'positive', failed: 'negative' } }
  const data = { tasks: [{ status: 'active' }, { status: 'failed' }, { status: 'retired' }] }

  assert.equal(a2uiTone(tone, data, '/tasks/0'), 'positive')
  assert.equal(a2uiTone(tone, data, '/tasks/1'), 'negative')
  // A status the table does not name is not a tone either — it stays neutral.
  assert.equal(a2uiTone(tone, data, '/tasks/2'), 'neutral')
})

test('an icon draws the mark its card names for a value', () => {
  const icon = { component: 'Icon', name: { path: './status' }, map: { done: 'check', failed: 'x' } }
  const data = { items: [{ status: 'done' }, { status: 'failed' }, { status: 'odd' }] }

  assert.equal(a2uiIconName(icon, data, '/items/0'), 'check')
  assert.equal(a2uiIconName(icon, data, '/items/1'), 'x')
  assert.equal(a2uiIconName(icon, data, '/items/2'), 'odd')
})
