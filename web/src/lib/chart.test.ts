import assert from 'node:assert/strict'
import { test } from 'node:test'
import { chartGeometry, readChart } from './chart.ts'
import type { ChartData } from '../schemas/chart.ts'

const data: ChartData = {
  labels: ['01.08', '08.08', '15.08', '22.08'],
  series: [{ label: 'Omitted', values: [2, null, 0, 1] }, { label: 'Incorrect', values: [1, null, 2, 3] }],
  annotations: [{ index: 1.5, label: 'Lessons start' }],
}

test('stacked bars preserve gaps, zero and individual values, and scale by totals', () => {
  const geometry = chartGeometry(data, 'bar', true)
  assert.equal(geometry.bars.length, 6)
  assert.equal(geometry.bars.some(bar => bar.category === 1), false)
  assert.equal(geometry.bars.find(bar => bar.category === 2 && bar.series === 0)?.height, 0)
  assert.deepEqual(geometry.bars.filter(bar => bar.category === 0).map(bar => [bar.value, bar.start, bar.end]), [[2, 0, 2], [1, 2, 3]])
  assert.ok(geometry.max >= 4)
  assert.equal(geometry.annotations[0].x, (geometry.categories[1].x + geometry.categories[2].x) / 2)
})

test('grouped bars share a zero baseline and do not add series to the scale', () => {
  const geometry = chartGeometry(data, 'bar', false)
  const pair = geometry.bars.filter(bar => bar.category === 0)
  assert.equal(pair[0].start, 0)
  assert.equal(pair[1].start, 0)
  assert.ok(pair[0].x + pair[0].width <= pair[1].x)
  assert.ok(geometry.max >= 3 && geometry.max < 4)
})

test('line segments stop at missing measurements and retain measured zero', () => {
  const geometry = chartGeometry(data, 'line', false)
  assert.equal(geometry.lines[0].paths.length, 2)
  assert.deepEqual(geometry.lines[0].points.map(point => point.category), [0, 2, 3])
  assert.equal(geometry.lines[0].points[1].y, geometry.baseline)
  assert.equal(geometry.bars.length, 0)
})

test('negative stacks grow below zero independently of positive stacks', () => {
  const signed: ChartData = { labels: ['A'], series: [
    { label: 'One', values: [-2] }, { label: 'Two', values: [-3] }, { label: 'Three', values: [4] },
  ], annotations: [] }
  const geometry = chartGeometry(signed, 'bar', true)
  assert.deepEqual(geometry.bars.map(bar => [bar.start, bar.end]), [[0, -2], [-2, -5], [0, 4]])
  assert.ok(geometry.min <= -5 && geometry.max >= 4)
  assert.ok(geometry.bars.every(bar => bar.height >= 0 && Number.isFinite(bar.y)))
})

test('legend filtering rescales only visible series and preserves their indices', () => {
  const geometry = chartGeometry(data, 'bar', true, 640, 280, new Set([1]))
  assert.deepEqual([...new Set(geometry.bars.map(bar => bar.series))], [0])
  assert.equal(geometry.max, 2)
  const empty = chartGeometry(data, 'line', false, 640, 280, new Set([0, 1]))
  assert.equal(empty.lines.length, 0)
  assert.ok(empty.ticks.every(tick => Number.isFinite(tick.y)))
})

test('single, zero, missing, tiny and large values have finite contained geometry', () => {
  for (const value of [0, null, -1, 1e-300, 1e12]) {
    const single: ChartData = { labels: ['A'], series: [{ label: 'One', values: [value] }], annotations: [] }
    const geometry = chartGeometry(single, 'line', false, 280, 220)
    assert.ok(geometry.ticks.every(tick => Number.isFinite(tick.y) && tick.y >= geometry.top - 1 && tick.y <= geometry.bottom + 1))
    assert.ok(geometry.lines[0].points.every(point => Number.isFinite(point.x) && Number.isFinite(point.y)))
  }
})

test('bound data rejects unequal axes, malformed series and out of range annotations', () => {
  assert.ok(readChart(data.labels, data.series, data.annotations).data)
  const invalid = [
    readChart([], []),
    readChart(['A'], [{ label: 'One', values: [1, 2] }]),
    readChart(['A'], [{ label: 'One', values: ['1'] }]),
    readChart(['A'], [{ label: 'One', values: [NaN] }]),
    readChart(['A'], [{ label: 'One', values: [Infinity] }]),
    readChart(['A'], [{ label: 'One', values: [1e13] }]),
    readChart(['A'], [{ label: 'One', values: [1], color: 'red' }]),
    readChart(['A'], [{ label: 'One', values: [1] }], [{ index: 1, label: 'Outside' }]),
    readChart(data.labels, data.series, [], 'line', true),
    readChart(data.labels, data.series, [], 'pie'),
  ]
  assert.ok(invalid.every(result => result.data === null && result.error))
})
