import { test } from 'node:test'
import assert from 'node:assert/strict'
import { calendarHeatmap } from './calendarHeatmap.ts'

const day = (model: ReturnType<typeof calendarHeatmap>, date: string) => model.days.find(day => day.date === date)!

test('monthly calendar places weekdays in rows and weeks in columns', () => {
  const model = calendarHeatmap('2026-10-01', '2026-10-31', [{ date: '2026-10-06', count: 3 }])
  assert.equal(model.error, '')
  assert.equal(model.weeks, 5)
  assert.equal(model.days.filter(day => day.status !== 'outside').length, 31)
  assert.deepEqual([day(model, '2026-10-01').column, day(model, '2026-10-01').row], [0, 3])
  assert.deepEqual([day(model, '2026-10-06').column, day(model, '2026-10-06').row], [1, 1])
  assert.equal(day(model, '2026-10-06').level, 3)
  assert.equal(day(model, '2026-10-02').status, 'unknown')
  assert.equal(day(model, '2026-09-28').status, 'outside')
})

test('missing facts differ from explicit zero, analysis, pause and upcoming days', () => {
  const model = calendarHeatmap('2026-10-01', '2026-10-07', [
    { date: '2026-10-01', count: 0 }, { date: '2026-10-02', status: 'analysis', count: 0 },
    { date: '2026-10-03', status: 'pause' }, { date: '2026-10-04', status: 'upcoming' },
    { date: '2026-10-05' }, { date: '2026-10-07', count: 999, label: 'Many sessions' },
  ])
  assert.deepEqual(Array.from({ length: 7 }, (_, i) => day(model, `2026-10-0${i + 1}`).status), ['missed', 'analysis', 'pause', 'upcoming', 'unknown', 'unknown', 'activity'])
  assert.equal(day(model, '2026-10-07').level, 4)
  assert.equal(day(model, '2026-10-07').label, 'Many sessions')
})

test('Sunday start, leap days and UTC year boundaries retain exact calendar dates', () => {
  const sunday = calendarHeatmap('2026-10-01', '2026-10-04', [], 'sunday')
  assert.deepEqual([day(sunday, '2026-10-04').column, day(sunday, '2026-10-04').row], [1, 0])
  const leap = calendarHeatmap('2024-02-01', '2024-02-29', [])
  assert.equal(leap.days.filter(day => day.status !== 'outside').length, 29)
  assert.equal(day(leap, '2024-02-29').row, 3)
  const boundary = calendarHeatmap('2025-12-29', '2026-01-04', [])
  assert.equal(boundary.weeks, 1)
  assert.equal(new Set(boundary.months.map(month => month.column)).size, boundary.months.length)
  for (const year of ['0001', '9999']) {
    const model = calendarHeatmap(`${year}-01-01`, `${year}-01-07`, [])
    assert.equal(model.error, '')
    assert.equal(new Set(model.days.map(day => day.date)).size, model.days.length)
  }
})

test('malformed, duplicate and unbounded data reports errors instead of crashing', () => {
  for (const [start, end] of [['2026-02-30', '2026-03-01'], ['2026-10-02', '2026-10-01'], ['2024-01-01', '2025-01-01'], ['', ''], ['2026-1-1', '2026-01-02'], ['0000-01-01', '0000-01-02']]) {
    assert.notEqual(calendarHeatmap(start, end, []).error, '')
  }
  for (const entries of [null, {}, [{ date: '2026-10-01', count: -1 }], [{ date: '2026-10-01', count: 1.5 }], [{ date: '2026-10-01', status: 'maybe' }], [{ date: '2026-02-30' }], [{ date: '2026-10-01', extra: true }], [{ date: '2026-10-01' }, { date: '2026-10-01' }]]) {
    assert.notEqual(calendarHeatmap('2026-10-01', '2026-10-31', entries).error, '')
  }
  assert.equal(calendarHeatmap('2024-01-01', '2024-12-31', []).error, '')
  assert.equal(calendarHeatmap('2026-10-01', '2026-10-31', [{ date: '2026-09-01', count: 1 }]).error, '')
})
