import assert from 'node:assert/strict'
import { test } from 'node:test'
import { tableColumn, tableRowVariant } from './table.ts'

test('column presentation reads the column, including in a nested Card instance', () => {
  const data = { _cards: { metrics: { columns: [
    { width: 'wide', align: 'start', overflow: 'ellipsis' },
    { width: 'narrow', align: 'end', overflow: 'nowrap' },
  ], rows: [{ variant: 'summary' }, { variant: 'baseline' }] } } }
  const table = {
    columnWidth: { path: './width' }, columnAlign: { path: './align' },
    columnOverflow: { path: './overflow' }, rowVariant: { path: './variant' },
  }
  assert.deepEqual(tableColumn(table, data, '/_cards/metrics/columns/0'), {
    track: 'minmax(240px, 3fr)', align: 'start', overflow: 'ellipsis',
  })
  assert.deepEqual(tableColumn(table, data, '/_cards/metrics/columns/1'), {
    track: 'minmax(64px, .65fr)', align: 'end', overflow: 'nowrap',
  })
  assert.equal(tableRowVariant(table, data, '/_cards/metrics/rows/0'), 'summary')
  assert.equal(tableRowVariant(table, data, '/_cards/metrics/rows/1'), 'baseline')
})

test('unconfigured persisted tables keep their original tracks and wrapping', () => {
  assert.deepEqual(tableColumn({}, {}, ''), {
    track: 'minmax(94px, 1fr)', align: 'start', overflow: 'wrap',
  })
  assert.equal(tableRowVariant({}, {}, ''), 'body')
})

test('table presentation accepts tokens without letting bound data inject CSS', () => {
  for (const width of ['500px', '__proto__', 'constructor', 'url(https://example.com)', null]) {
    const data = { column: { width, align: 'right', overflow: 'hidden' } }
    assert.deepEqual(tableColumn({
      columnWidth: { path: './width' }, columnAlign: { path: './align' },
      columnOverflow: { path: './overflow' },
    }, data, '/column'), { track: 'minmax(94px, 1fr)', align: 'start', overflow: 'wrap' })
  }
  assert.deepEqual(tableColumn({ columnWidth: 'narrow', columnAlign: 'center', columnOverflow: 'nowrap' }, {}, ''), {
    track: 'minmax(64px, .65fr)', align: 'center', overflow: 'nowrap',
  })
  assert.equal(tableRowVariant({ rowVariant: 'summary' }, {}, ''), 'summary')
  assert.equal(tableRowVariant({ rowVariant: 'background:red' }, {}, ''), 'body')
})
